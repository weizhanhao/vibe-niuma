// background service worker：消息编排中枢。
// content ↔ background ↔ ui ↔ Orchestrator REST/SSE 全在这里串起来。
import { MSG, type Message } from '../lib/messages';
import type { ChangeRequestOut, RequestStateMirror, SSEEvent } from '../lib/types';
import { orchestratorClient } from './orchestrator-client';
import {
  applyEvent, applySnapshot, clearPending, initialState,
  loadFromStorage, saveToStorage,
} from './request-store';

interface SessionState {
  pendingRequestText: string | null;
  mirror: RequestStateMirror | null;
  unsubscribe: (() => void) | null;
}

const session: SessionState = {
  pendingRequestText: null,
  mirror: null,
  unsubscribe: null,
};

loadFromStorage().then((m) => {
  if (m) {
    session.mirror = m;
    if (m.id && !['merged', 'failed', 'expired', 'discarded'].includes(m.state)) {
      attachSubscription(m.id);
    }
  }
});

async function broadcastState() {
  const msg: Message = { type: MSG.REQUEST_STATE_CHANGED, state: session.mirror };
  try { await chrome.runtime.sendMessage(msg); } catch { /* no listener */ }
}

async function setMirror(next: RequestStateMirror | null) {
  session.mirror = next;
  await saveToStorage(next);
  await broadcastState();
}

function attachSubscription(requestId: string) {
  if (session.unsubscribe) session.unsubscribe();
  session.unsubscribe = orchestratorClient.subscribeEvents(
    requestId,
    async (evt: SSEEvent) => {
      if (!session.mirror) return;
      await setMirror(applyEvent(session.mirror, evt));
    },
    async (snap: ChangeRequestOut) => {
      if (!session.mirror) return;
      await setMirror(applySnapshot(session.mirror, snap));
    },
  );
}

async function handleMessage(msg: Message): Promise<unknown> {
  switch (msg.type) {
    case MSG.UI_START_CAPTURE: {
      session.pendingRequestText = msg.requestText;
      const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      if (tab?.id !== undefined) {
        await chrome.tabs.sendMessage(tab.id, { type: MSG.START_CAPTURE });
      }
      return { ok: true };
    }
    case MSG.CAPTURE_RESULT: {
      const text = session.pendingRequestText ?? '';
      session.pendingRequestText = null;
      const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      const dataUrl = tab?.windowId !== undefined
        ? await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' })
        : '';
      const screenshotB64 = dataUrl.replace(/^data:image\/png;base64,/, '');
      const cr = await orchestratorClient.createChangeRequest({
        url: msg.url,
        screenshot_b64: screenshotB64,
        box_coords: msg.boxCoords,
        viewport: msg.viewport,
        request_text: text,
      });
      await setMirror(initialState(cr));
      attachSubscription(cr.id);
      return { ok: true, id: cr.id };
    }
    case MSG.CAPTURE_CANCEL: {
      session.pendingRequestText = null;
      return { ok: true };
    }
    case MSG.SUBMIT_ANSWER: {
      await orchestratorClient.submitAnswer(msg.requestId, msg.questionId, msg.answer);
      if (session.mirror) await setMirror(clearPending(session.mirror));
      return { ok: true };
    }
    case MSG.MERGE: {
      const cr = await orchestratorClient.merge(msg.requestId);
      if (session.mirror) await setMirror(applySnapshot(session.mirror, cr));
      return { ok: true };
    }
    case MSG.DISCARD: {
      const cr = await orchestratorClient.discard(msg.requestId);
      if (session.mirror) await setMirror(applySnapshot(session.mirror, cr));
      return { ok: true };
    }
    case MSG.RETRY: {
      const cr = await orchestratorClient.retry(msg.requestId);
      await setMirror(initialState(cr));
      attachSubscription(cr.id);
      return { ok: true, id: cr.id };
    }
    case MSG.GET_REQUEST_STATE: {
      return session.mirror;
    }
    default:
      return undefined;
  }
}

chrome.runtime.onMessage.addListener((msg: Message, _sender, sendResponse) => {
  handleMessage(msg).then((reply) => sendResponse(reply)).catch((err) => {
    sendResponse({ ok: false, error: String(err) });
  });
  return true;
});

// 点击扩展图标 → 打开 side panel
if (chrome.sidePanel?.open) {
  chrome.action?.onClicked?.addListener?.((tab) => {
    if (tab.windowId !== undefined) chrome.sidePanel.open({ windowId: tab.windowId });
  });
}
