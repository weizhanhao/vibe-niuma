// background service worker：消息编排中枢。
// content ↔ background ↔ ui ↔ Orchestrator REST/SSE 全在这里串起来。
// Phase E：从「单 mirror」升级为「多 mirror map + activeId」。
// 只对 active 的那一条订阅 SSE，节省连接数 & 配额。
import { MSG, type Message } from '../lib/messages';
import type { ChangeRequestOut, RequestStateMirror, SSEEvent } from '../lib/types';
import { orchestratorClient } from './orchestrator-client';
import {
  applyEvent, applySnapshot, clearPending, evictWithLRU, initialState, isTerminal,
  loadAll, removeMirror, saveMirrors, sortByActivity, upsertMirror,
} from './request-store';

interface SessionState {
  pendingRequestText: string | null;
  mirrors: Record<string, RequestStateMirror>;
  activeId: string | null;
  unsubscribe: (() => void) | null;
  // 当前已订阅的 mirror.id —— 避免 active 未变时重复 attach。
  subscribedId: string | null;
}

const session: SessionState = {
  pendingRequestText: null,
  mirrors: {},
  activeId: null,
  unsubscribe: null,
  subscribedId: null,
};

// MV3 service worker 30s 闲置即被 Chrome kill。多轮澄清等用户答题时 SW 必死。
// 用 chrome.alarms 周期性触发：alarm 投递的副作用就是唤醒 SW；唤醒时全局
// 作用域重跑、loadAll 重新挂 SSE，所以业务员下一题来得及推过来。
const KEEPALIVE_ALARM = 'doskill-sse-keepalive';

function anyInFlight(mirrors: Record<string, RequestStateMirror>): boolean {
  for (const m of Object.values(mirrors)) if (!isTerminal(m)) return true;
  return false;
}

function maintainKeepalive(mirrors: Record<string, RequestStateMirror>): void {
  if (!chrome.alarms) return; // jsdom 测试环境没 alarms
  if (anyInFlight(mirrors)) {
    chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: 0.5 });
  } else {
    chrome.alarms.clear(KEEPALIVE_ALARM);
  }
}

chrome.alarms?.onAlarm.addListener((alarm) => {
  if (alarm.name !== KEEPALIVE_ALARM) return;
  // 兜底：闹钟把 SW 唤醒；如果 SSE 在唤醒前因死亡断开，这里重连。
  const active = session.activeId ? session.mirrors[session.activeId] : null;
  if (active && !isTerminal(active)) {
    attachSubscription(active.id);
  } else if (!anyInFlight(session.mirrors)) {
    chrome.alarms.clear(KEEPALIVE_ALARM);
  }
});

// 启动：从 storage 恢复多对话 + activeId，对 active 那条（如果非终态）挂 SSE。
loadAll().then(({ mirrors, activeId }) => {
  session.mirrors = mirrors;
  session.activeId = activeId;
  const active = activeId ? mirrors[activeId] : null;
  if (active && !isTerminal(active)) attachSubscription(active.id);
  maintainKeepalive(mirrors);
});

async function broadcastActive() {
  const active = session.activeId ? session.mirrors[session.activeId] ?? null : null;
  const msg: Message = {
    type: MSG.REQUEST_STATE_CHANGED,
    state: active,
    activeId: session.activeId,
  };
  try { await chrome.runtime.sendMessage(msg); } catch { /* no listener */ }
}

async function broadcastList() {
  const msg: Message = {
    type: MSG.MIRRORS_CHANGED,
    mirrors: session.mirrors,
    activeId: session.activeId,
  };
  try { await chrome.runtime.sendMessage(msg); } catch { /* no listener */ }
}

async function persist() {
  await saveMirrors(session.mirrors, session.activeId);
}

// 写回一条 mirror 并广播。
async function setMirror(next: RequestStateMirror) {
  session.mirrors = evictWithLRU(upsertMirror(session.mirrors, next));
  maintainKeepalive(session.mirrors);
  await persist();
  await broadcastActive();
  await broadcastList();
}

function detach() {
  if (session.unsubscribe) {
    session.unsubscribe();
    session.unsubscribe = null;
  }
  session.subscribedId = null;
}

function attachSubscription(requestId: string) {
  if (session.subscribedId === requestId && session.unsubscribe) return;
  detach();
  session.subscribedId = requestId;
  session.unsubscribe = orchestratorClient.subscribeEvents(
    requestId,
    async (evt: SSEEvent) => {
      const target = session.mirrors[requestId];
      if (!target) return;
      await setMirror(applyEvent(target, evt));
    },
    async (snap: ChangeRequestOut) => {
      const target = session.mirrors[requestId];
      if (!target) return;
      await setMirror(applySnapshot(target, snap));
    },
  );
}

async function setActive(id: string | null) {
  session.activeId = id;
  detach();
  if (id && session.mirrors[id] && !isTerminal(session.mirrors[id])) {
    attachSubscription(id);
  }
  await persist();
  await broadcastActive();
  await broadcastList();
}

async function deleteConversation(id: string) {
  const wasActive = session.activeId === id;
  session.mirrors = removeMirror(session.mirrors, id);
  if (wasActive) {
    detach();
    const sorted = sortByActivity(session.mirrors);
    const nextInFlight = sorted.find((m) => !isTerminal(m));
    const next = nextInFlight ?? sorted[0] ?? null;
    session.activeId = next?.id ?? null;
    if (next && !isTerminal(next)) attachSubscription(next.id);
  }
  maintainKeepalive(session.mirrors);
  await persist();
  await broadcastActive();
  await broadcastList();
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
      const next = initialState(cr);
      session.mirrors = evictWithLRU(upsertMirror(session.mirrors, next));
      session.activeId = next.id;
      detach();
      attachSubscription(next.id);
      maintainKeepalive(session.mirrors);
      await persist();
      await broadcastActive();
      await broadcastList();
      return { ok: true, id: cr.id };
    }
    case MSG.CAPTURE_CANCEL: {
      session.pendingRequestText = null;
      return { ok: true };
    }
    case MSG.SUBMIT_ANSWER: {
      await orchestratorClient.submitAnswer(msg.requestId, msg.questionId, msg.answer);
      const target = session.mirrors[msg.requestId];
      if (target) await setMirror(clearPending(target));
      return { ok: true };
    }
    case MSG.MERGE: {
      const cr = await orchestratorClient.merge(msg.requestId);
      const target = session.mirrors[msg.requestId];
      if (target) await setMirror(applySnapshot(target, cr));
      return { ok: true };
    }
    case MSG.DISCARD: {
      const cr = await orchestratorClient.discard(msg.requestId);
      const target = session.mirrors[msg.requestId];
      if (target) await setMirror(applySnapshot(target, cr));
      return { ok: true };
    }
    case MSG.RETRY: {
      const cr = await orchestratorClient.retry(msg.requestId);
      const next = initialState(cr);
      session.mirrors = evictWithLRU(upsertMirror(session.mirrors, next));
      session.activeId = next.id;
      detach();
      attachSubscription(next.id);
      maintainKeepalive(session.mirrors);
      await persist();
      await broadcastActive();
      await broadcastList();
      return { ok: true, id: cr.id };
    }
    case MSG.SET_ACTIVE: {
      await setActive(msg.id);
      return { ok: true };
    }
    case MSG.DELETE_CONVERSATION: {
      await deleteConversation(msg.id);
      return { ok: true };
    }
    case MSG.NEW_CONVERSATION: {
      // 切到 null —— UI 渲染 CapturePanel；不预创建 mirror。
      await setActive(null);
      return { ok: true };
    }
    case MSG.GET_MIRRORS: {
      return { mirrors: session.mirrors, activeId: session.activeId };
    }
    case MSG.GET_REQUEST_STATE: {
      const active = session.activeId ? session.mirrors[session.activeId] ?? null : null;
      return active;
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
