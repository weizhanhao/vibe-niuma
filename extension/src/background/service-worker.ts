// background service worker：消息编排中枢。
// content ↔ background ↔ ui ↔ Orchestrator REST/SSE 全在这里串起来。
// Phase E：从「单 mirror」升级为「多 mirror map + activeId」。
// 只对 active 的那一条订阅 SSE，节省连接数 & 配额。
// Plan 6 Task 10：orchestrator client 不再 module-level hardcode；按 chrome.storage 里的
// 配置 lazy 构造 + 缓存，监听 chrome.storage.onChanged 在配置改了时 invalidate 重建。
import { loadConfig } from '../lib/config';
import { migrateLegacyChromeStorage } from '../lib/legacy-key-migration';
import { MSG, type Message } from '../lib/messages';
import type { ChangeRequestOut, PendingCapture, RequestStateMirror, SSEEvent } from '../lib/types';
import { createOrchestratorClient, type OrchestratorClient } from './orchestrator-client';
import {
  applyEvent, applySnapshot, clearPending, evictWithLRU, initialState, isTerminal,
  loadAll, removeMirror, saveMirrors, sortByActivity, upsertMirror,
} from './request-store';

// ── orchestrator client lazy cache ─────────────────────────────────
// 第一次调时读 chrome.storage 拿 baseUrl + token，构造 client 并缓存。
// chrome.storage.onChanged 监听到 `vibe_niuma_config_v2` 变了就清缓存，下次重新构造。
// 缺配置时返回 null：调用点必须 `if (!client) return ...` 才能 noop。
const CONFIG_STORAGE_KEY = 'vibe_niuma_config_v2';
let cachedClient: OrchestratorClient | null = null;
let pendingClientLoad: Promise<OrchestratorClient | null> | null = null;

function invalidateClientCache(): void {
  cachedClient = null;
  pendingClientLoad = null;
}

/**
 * 异步取当前 orchestrator client。缺 URL/token 返回 null（调用点必须 noop + warn）。
 * 暴露给测试：tests/onboarding-routing.test.tsx 直接 import 这个函数断言缓存行为。
 */
export async function getOrchestratorClient(): Promise<OrchestratorClient | null> {
  if (cachedClient) return cachedClient;
  if (pendingClientLoad) return pendingClientLoad;
  pendingClientLoad = (async () => {
    const cfg = await loadConfig();
    if (!cfg || cfg.orchestratorUrl.length === 0 || cfg.adminToken.length === 0) {
      // 不缓存 null —— 下次再尝试（用户可能刚配完）
      pendingClientLoad = null;
      return null;
    }
    cachedClient = createOrchestratorClient(cfg.orchestratorUrl, cfg.adminToken);
    pendingClientLoad = null;
    return cachedClient;
  })();
  return pendingClientLoad;
}

interface SessionState {
  pendingRequestText: string | null;
  // Phase G：业务员框完一个区域，先暂存这里弹 review 页；点「确认提交」才 POST。
  // 内存态——SW 死掉就丢，重新让业务员框一次；不持久化到 chrome.storage（截图 PNG base64 体积大）。
  pendingCapture: PendingCapture | null;
  mirrors: Record<string, RequestStateMirror>;
  activeId: string | null;
  unsubscribe: (() => void) | null;
  // 当前已订阅的 mirror.id —— 避免 active 未变时重复 attach。
  subscribedId: string | null;
  // Plan 10 Task 13：当前 active conversation。SUBMIT_MESSAGE 用它做
  // POST /messages 的 convId。内存态：SW 死了 UI 必须重发 SET_CONVERSATION。
  activeConversationId: string | null;
}

const session: SessionState = {
  pendingRequestText: null,
  pendingCapture: null,
  mirrors: {},
  activeId: null,
  unsubscribe: null,
  subscribedId: null,
  activeConversationId: null,
};

// MV3 service worker 30s 闲置即被 Chrome kill。多轮澄清等用户答题时 SW 必死。
// 用 chrome.alarms 周期性触发：alarm 投递的副作用就是唤醒 SW；唤醒时全局
// 作用域重跑、loadAll 重新挂 SSE，所以业务员下一题来得及推过来。
const KEEPALIVE_ALARM = 'vibe-niuma-sse-keepalive';

function anyInFlight(mirrors: Record<string, RequestStateMirror>): boolean {
  for (const m of Object.values(mirrors)) if (!isTerminal(m)) return true;
  return false;
}

/** 通过 pageUrl 的 origin（host:port）反查 mirror —— 容错末尾斜杠 / 子路径。
 *  preview 容器 URL 通常 `http://x:5100`，业务员打开 `/orders` 子页面也算同一 CR。 */
function findCrByPreviewOrigin(pageUrl: string): RequestStateMirror | null {
  let pageOrigin: string;
  try {
    pageOrigin = new URL(pageUrl).origin;
  } catch {
    return null;
  }
  for (const m of Object.values(session.mirrors)) {
    if (!m.previewUrl) continue;
    try {
      if (new URL(m.previewUrl).origin === pageOrigin) return m;
    } catch {
      continue;
    }
  }
  return null;
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
    void attachSubscription(active.id);
  } else if (!anyInFlight(session.mirrors)) {
    chrome.alarms.clear(KEEPALIVE_ALARM);
  }
});

// 启动：先迁移老品牌 storage key（幂等），再从 storage 恢复多对话 + activeId。
migrateLegacyChromeStorage()
  .catch(() => { /* 迁移失败不阻塞 SW 启动 */ })
  .then(() => loadAll())
  .then(({ mirrors, activeId }) => {
    session.mirrors = mirrors;
    session.activeId = activeId;
    const active = activeId ? mirrors[activeId] : null;
    if (active && !isTerminal(active)) void attachSubscription(active.id);
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

// Phase G：广播 pendingCapture 变化给 UI；UI 用它在 CapturePanel / ReviewCapturePanel 间切换。
async function broadcastPendingCapture() {
  const msg: Message = {
    type: MSG.PENDING_CAPTURE_CHANGED,
    pending: session.pendingCapture,
  };
  try { await chrome.runtime.sendMessage(msg); } catch { /* no listener */ }
}

async function persist() {
  await saveMirrors(session.mirrors, session.activeId);
}

// captureVisibleTab 出来的 PNG 在 Retina 屏上能到 5-10 MB，base64 再 ×4/3 → 十几 MB；
// 从国内家庭网络上传到 ECS 慢得离谱（35s+ 都没回）。这里 SW 在 OffscreenCanvas 里
// downscale + JPEG@0.75，典型能压到 < 300 KB，业务员一秒内就 POST 完。
// 截图给视觉模型看 + Review 缩略图，质量都够。
async function compressScreenshot(dataUrl: string, maxWidth = 1280, quality = 0.75): Promise<{ b64: string; mime: string }> {
  if (!dataUrl) return { b64: '', mime: 'image/png' };
  try {
    const blob = await (await fetch(dataUrl)).blob();
    const bitmap = await createImageBitmap(blob);
    const scale = Math.min(1, maxWidth / bitmap.width);
    const w = Math.max(1, Math.round(bitmap.width * scale));
    const h = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = new OffscreenCanvas(w, h);
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('no 2d context');
    ctx.drawImage(bitmap, 0, 0, w, h);
    const out = await canvas.convertToBlob({ type: 'image/jpeg', quality });
    const buf = await out.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = '';
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return { b64: btoa(binary), mime: 'image/jpeg' };
  } catch (err) {
    console.warn('[vibe-niuma sw] compressScreenshot failed, fallback raw PNG', err);
    return { b64: dataUrl.replace(/^data:image\/png;base64,/, ''), mime: 'image/png' };
  }
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

// 配置变化时 invalidate client 缓存 + 断开当前 SSE，下次 attach 用新 URL/token。
// jsdom 测试环境里 chrome.storage.onChanged 不存在 → optional chain 自动 noop。
chrome.storage?.onChanged?.addListener?.((changes, area) => {
  if (area !== 'local') return;
  if (CONFIG_STORAGE_KEY in changes) {
    console.log('[vibe-niuma sw] config changed, invalidating orchestrator client');
    invalidateClientCache();
    // 当前若有正订阅，断开让它在下次 attach 时用新 client
    detach();
    // 如果还有 active 且非终态，立即重连。
    const active = session.activeId ? session.mirrors[session.activeId] : null;
    if (active && !isTerminal(active)) {
      void attachSubscription(active.id);
    }
  }
});

async function attachSubscription(requestId: string) {
  if (session.subscribedId === requestId && session.unsubscribe) return;
  detach();
  const client = await getOrchestratorClient();
  if (!client) {
    console.warn('[vibe-niuma sw] cannot attach SSE: orchestrator not configured');
    return;
  }
  session.subscribedId = requestId;
  session.unsubscribe = client.subscribeEvents(
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
    await attachSubscription(id);
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
    if (next && !isTerminal(next)) await attachSubscription(next.id);
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
      console.log('[vibe-niuma sw] UI_START_CAPTURE → tab', tab?.id, tab?.url);
      if (tab?.id === undefined) {
        return { ok: false, error: 'no active tab' };
      }
      // MV3 坑：content script 只在扩展加载之后才打开/刷新的页里自动注入。
      // 用户重载扩展时若 demo 页已经开着 → content script 不在 → sendMessage 静默
      // 失败。先 sendMessage 探测；失败就用 chrome.scripting 手动注入再 retry。
      // 注：crxjs 会把 content_scripts.js 哈希成 assets/content-entry.ts-loader-XXX.js，
      // 所以这里从 runtime manifest 取真实路径，避免 hardcoded 路径在 prod build 失效。
      try {
        await chrome.tabs.sendMessage(tab.id, { type: MSG.START_CAPTURE });
      } catch (err) {
        console.warn('[vibe-niuma sw] content script not in tab, injecting...', err);
        const manifest = chrome.runtime.getManifest();
        const files = manifest.content_scripts?.[0]?.js ?? [];
        if (files.length === 0) {
          console.error('[vibe-niuma sw] no content_scripts in manifest');
          return { ok: false, error: 'no content_scripts in manifest' };
        }
        try {
          await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            files,
          });
          await chrome.tabs.sendMessage(tab.id, { type: MSG.START_CAPTURE });
          console.log('[vibe-niuma sw] content script injected + retry ok');
        } catch (err2) {
          console.error('[vibe-niuma sw] inject + retry failed', err2);
          return { ok: false, error: `inject failed: ${String(err2)}` };
        }
      }
      return { ok: true };
    }
    case MSG.UI_START_ANNOTATE: {
      // 截图标注流：SW 先 captureVisibleTab 拿 PNG，再把 dataURL 推给
      // content script 启全屏标注 overlay。content 用 4 工具标注后烘焙
      // PNG 回 SW（ANNOTATE_RESULT），SW 压缩 + 广播 CAPTURE_ATTACHED。
      const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      console.log('[vibe-niuma sw] UI_START_ANNOTATE → tab', tab?.id);
      if (tab?.id === undefined || tab.windowId === undefined) {
        return { ok: false, error: 'no active tab' };
      }
      // 先截图。如果 captureVisibleTab 因为没权限/不能截（chrome:// 等）
      // 会抛 —— 兜底返 error。
      let dataUrl = '';
      try {
        dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' });
      } catch (err) {
        console.error('[vibe-niuma sw] captureVisibleTab failed', err);
        return { ok: false, error: `截图失败：${String(err)}` };
      }
      if (!dataUrl) return { ok: false, error: '截图为空' };

      // 跟 START_CAPTURE 同款 ensure content script 已注入
      const sendStart = () => chrome.tabs.sendMessage(
        tab.id!, { type: MSG.START_ANNOTATE, screenshotDataUrl: dataUrl },
      );
      try {
        await sendStart();
      } catch (err) {
        console.warn('[vibe-niuma sw] content script 未注入，注入后重试', err);
        const manifest = chrome.runtime.getManifest();
        const files = manifest.content_scripts?.[0]?.js ?? [];
        if (files.length === 0) {
          return { ok: false, error: 'no content_scripts in manifest' };
        }
        try {
          await chrome.scripting.executeScript({ target: { tabId: tab.id }, files });
          await sendStart();
        } catch (err2) {
          return { ok: false, error: `inject failed: ${String(err2)}` };
        }
      }
      return { ok: true };
    }
    case MSG.ANNOTATE_RESULT: {
      // content 烘焙好 PNG 回来。SW 压一下广播给 UI 加到输入栏 chip。
      console.log('[vibe-niuma sw] ANNOTATE_RESULT pngB64.len=', msg.pngB64.length);
      const dataUrl = `data:image/png;base64,${msg.pngB64}`;
      const { b64: screenshotB64, mime } = await compressScreenshot(dataUrl);
      try {
        await chrome.runtime.sendMessage({
          type: MSG.CAPTURE_ATTACHED,
          attachment: {
            kind: 'annotated_screenshot',
            mime,
            b64: screenshotB64,
            name: '标注截图',
          },
        });
      } catch (err) {
        console.warn('[vibe-niuma sw] broadcast CAPTURE_ATTACHED failed', err);
      }
      return { ok: true };
    }
    case MSG.ANNOTATE_CANCEL: {
      console.log('[vibe-niuma sw] ANNOTATE_CANCEL');
      return { ok: true };
    }
    case MSG.RUNTIME_ERROR_REPORT: {
      // content script 在 preview 页捕到 window.error / unhandledrejection。
      // 找回对应 CR id（通过 previewUrl origin 匹配），转发给 orchestrator。
      const matched = findCrByPreviewOrigin(msg.pageUrl);
      if (!matched) {
        // 不是 vibe-niuma preview 页（业务员自己浏览的其他网站），忽略
        return { ok: true, matched: false };
      }
      const client = await getOrchestratorClient();
      if (!client) return { ok: false, error: 'no orchestrator client' };
      try {
        const resp = await client.postRuntimeErrors(matched.id, [{
          message: msg.message,
          stack: msg.stack,
          ts: msg.ts,
          pageUrl: msg.pageUrl,
        }]);
        console.log('[vibe-niuma sw] runtime error → orchestrator', matched.id, resp);
        return { ok: true, matched: true, will_self_heal: resp.will_self_heal };
      } catch (err) {
        console.warn('[vibe-niuma sw] postRuntimeErrors failed', err);
        return { ok: false, error: String(err) };
      }
    }
    case MSG.CAPTURE_RESULT: {
      // Plan 10 fix：cursor-like 流。框选完截屏 + 压缩 → 直接广播给 UI 加到
      // 输入栏 chip，不再跳到 ReviewCapturePanel 占满 body。
      // pendingCapture 老路径保留兼容（v0.5），但新 UI 不再走它。
      console.log('[vibe-niuma sw] CAPTURE_RESULT received', msg);
      session.pendingRequestText = null;
      const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      const dataUrl = tab?.windowId !== undefined
        ? await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' })
        : '';
      const { b64: screenshotB64, mime } = await compressScreenshot(dataUrl);
      console.log('[vibe-niuma sw] screenshot compressed:', dataUrl.length, '→', screenshotB64.length, mime);
      // 广播 attachment 给 UI；UI 把它加到当前输入栏 chip
      try {
        await chrome.runtime.sendMessage({
          type: MSG.CAPTURE_ATTACHED,
          attachment: {
            kind: 'framed_region',
            mime,
            b64: screenshotB64,
            url: msg.url,
            box: msg.boxCoords,
            viewport: msg.viewport,
          },
        });
      } catch (err) {
        console.warn('[vibe-niuma sw] broadcast CAPTURE_ATTACHED failed', err);
      }
      return { ok: true };
    }
    case MSG.CONFIRM_CAPTURE: {
      // Phase G：业务员在 review 页点了「确认提交」。这时才 POST。
      // requestText 取自 UI（业务员可能在 review 时改过文本）。
      const pc = session.pendingCapture;
      if (!pc) return { ok: false, error: 'no pending capture' };
      const finalText = msg.requestText ?? pc.requestText;
      console.log('[vibe-niuma sw] CONFIRM_CAPTURE → POST orchestrator');
      const client = await getOrchestratorClient();
      if (!client) {
        console.warn('[vibe-niuma sw] CONFIRM_CAPTURE noop: orchestrator not configured');
        session.pendingCapture = { ...pc, requestText: finalText };
        await broadcastPendingCapture();
        return { ok: false, error: '请先在设置里配置 orchestrator URL' };
      }
      let cr: ChangeRequestOut;
      try {
        cr = await client.createChangeRequest({
          url: pc.url,
          screenshot_b64: pc.screenshotB64,
          box_coords: pc.boxCoords,
          viewport: pc.viewport,
          request_text: finalText,
        });
      } catch (err) {
        // POST 失败：保留 pendingCapture（业务员可重试），把错广播让 UI 显示
        console.error('[vibe-niuma sw] POST failed, keeping pendingCapture', err);
        // 把 textarea 编辑过的文本回写进 pendingCapture，免得业务员重输
        session.pendingCapture = { ...pc, requestText: finalText };
        await broadcastPendingCapture();
        return { ok: false, error: String(err) };
      }
      // 成功才清 pendingCapture
      session.pendingCapture = null;
      console.log('[vibe-niuma sw] CR created', cr.id);
      const next = initialState(cr);
      session.mirrors = evictWithLRU(upsertMirror(session.mirrors, next));
      session.activeId = next.id;
      detach();
      await attachSubscription(next.id);
      maintainKeepalive(session.mirrors);
      await persist();
      await broadcastActive();
      await broadcastList();
      await broadcastPendingCapture();
      return { ok: true, id: cr.id };
    }
    case MSG.RETAKE_CAPTURE: {
      // Phase G：业务员点「重新框选」。丢掉 pendingCapture + pendingRequestText，回 CapturePanel。
      session.pendingCapture = null;
      session.pendingRequestText = null;
      await broadcastPendingCapture();
      return { ok: true };
    }
    case MSG.SUBMIT_TEXT_ONLY: {
      // 直接提交（不框选）：SW 截当前页 + 压缩 + 空 box，走 Review 流程。
      const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      const dataUrl = tab?.windowId !== undefined
        ? await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' })
        : '';
      const { b64: screenshotB64, mime } = await compressScreenshot(dataUrl);
      session.pendingCapture = {
        screenshotB64,
        screenshotMime: mime,
        url: tab?.url ?? '',
        boxCoords: { x: 0, y: 0, width: 0, height: 0 },
        viewport: { width: 0, height: 0 },
        requestText: msg.requestText,
      };
      console.log('[vibe-niuma sw] SUBMIT_TEXT_ONLY pendingCapture stashed (compressed:', screenshotB64.length, ')');
      await broadcastPendingCapture();
      return { ok: true };
    }
    case MSG.GET_PENDING_CAPTURE: {
      // Phase G：UI 启动时拉一次（SW 死了又活的场景，UI 不知道有没有未决 review）。
      return session.pendingCapture;
    }
    case MSG.CAPTURE_CANCEL: {
      session.pendingRequestText = null;
      return { ok: true };
    }
    case MSG.SUBMIT_ANSWER: {
      const client = await getOrchestratorClient();
      if (!client) {
        console.warn('[vibe-niuma sw] SUBMIT_ANSWER noop: orchestrator not configured');
        return { ok: false, error: '请先在设置里配置 orchestrator URL' };
      }
      await client.submitAnswer(msg.requestId, msg.questionId, msg.answer);
      const target = session.mirrors[msg.requestId];
      if (target) await setMirror(clearPending(target));
      return { ok: true };
    }
    case MSG.MERGE: {
      const client = await getOrchestratorClient();
      if (!client) {
        console.warn('[vibe-niuma sw] MERGE noop: orchestrator not configured');
        return { ok: false, error: '请先在设置里配置 orchestrator URL' };
      }
      const cr = await client.merge(msg.requestId);
      const target = session.mirrors[msg.requestId];
      if (target) await setMirror(applySnapshot(target, cr));
      return { ok: true };
    }
    case MSG.DISCARD: {
      const client = await getOrchestratorClient();
      if (!client) {
        console.warn('[vibe-niuma sw] DISCARD noop: orchestrator not configured');
        return { ok: false, error: '请先在设置里配置 orchestrator URL' };
      }
      const cr = await client.discard(msg.requestId);
      const target = session.mirrors[msg.requestId];
      if (target) await setMirror(applySnapshot(target, cr));
      return { ok: true };
    }
    case MSG.RETRY: {
      const client = await getOrchestratorClient();
      if (!client) {
        console.warn('[vibe-niuma sw] RETRY noop: orchestrator not configured');
        return { ok: false, error: '请先在设置里配置 orchestrator URL' };
      }
      const cr = await client.retry(msg.requestId);
      const next = initialState(cr);
      session.mirrors = evictWithLRU(upsertMirror(session.mirrors, next));
      session.activeId = next.id;
      detach();
      await attachSubscription(next.id);
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
      // 切到 null + 清掉 pendingCapture / pendingRequestText —— 不然 App 路由
      // 优先级 pendingCapture > activeId，会卡在 Review 看不到新 CapturePanel。
      session.pendingCapture = null;
      session.pendingRequestText = null;
      await broadcastPendingCapture();
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
    // ── Plan 10 Task 13: cursor-like 多轮 message ingress ──────────
    case MSG.SET_CONVERSATION: {
      session.activeConversationId = msg.id;
      return { ok: true };
    }
    case MSG.SUBMIT_MESSAGE: {
      // 优先用 payload 里的 conversation_id（UI 显式带），避免 SW 死了 / SET_CONVERSATION
      // 还没到的竞态。fallback 到 session 状态保持兼容。
      const convId = msg.conversation_id ?? session.activeConversationId;
      if (!convId) {
        return { ok: false, error: '请先打开或新建一个对话再发送' };
      }
      // 顺手把 session 同步上，下次 fallback 路径也能 work
      session.activeConversationId = convId;
      const client = await getOrchestratorClient();
      if (!client) {
        return { ok: false, error: '请先在设置里配置 orchestrator URL' };
      }
      let resp;
      try {
        resp = await client.postMessage(convId, {
          text: msg.text,
          ...(msg.attachments ? { attachments: msg.attachments } : {}),
          ...(msg.override_mode ? { override_mode: msg.override_mode } : {}),
        });
      } catch (err) {
        console.error('[vibe-niuma sw] SUBMIT_MESSAGE failed', err);
        return { ok: false, error: String(err) };
      }
      // chat_only：server 已经写入 ai message；UI 拉 GET /conversations 自己刷
      if (resp.mode === 'chat_only' || !resp.cr_id) {
        return { ok: true, mode: resp.mode, message_id: resp.message_id,
                 ai_message_id: resp.ai_message_id ?? null };
      }
      // new_cr / refine_cr：拉 CR snapshot 起 mirror + 订阅 SSE
      try {
        const cr = await client.getChangeRequest(resp.cr_id);
        const next = initialState(cr);
        session.mirrors = evictWithLRU(upsertMirror(session.mirrors, next));
        session.activeId = next.id;
        detach();
        await attachSubscription(next.id);
        maintainKeepalive(session.mirrors);
        await persist();
        await broadcastActive();
        await broadcastList();
      } catch (err) {
        console.warn('[vibe-niuma sw] SUBMIT_MESSAGE post-create snapshot failed', err);
      }
      return { ok: true, mode: resp.mode, cr_id: resp.cr_id, message_id: resp.message_id };
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
