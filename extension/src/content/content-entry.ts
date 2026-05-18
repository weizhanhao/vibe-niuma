// content script 入口：监听 background 的 START_CAPTURE / START_ANNOTATE，
// 同时 passive monitor 当前页面的 window.error / unhandledrejection。
//
// runtime error reporter：业务员打开 preview 页（http://x:51xx）后，React
// 组件运行时崩（如 OrderTable.formatDate 取空字段）会被这里捕，posted 回
// SW，SW 查 previewUrlToCrId map 找对应 CR id，转发给 orchestrator
// 自动触发 self-heal（让 dev_runner 拿错误信息改一轮）。
import { CaptureOverlay } from './capture-overlay';
import { AnnotateOverlay } from './annotate-overlay';
import { MSG, type Message } from '../lib/messages';

let captureOverlay: CaptureOverlay | null = null;
let annotateOverlay: AnnotateOverlay | null = null;

console.log('[vibe-niuma content] loaded on', window.location.href);

// ── runtime error reporter ─────────────────────────────────────────
// 仅在首次出错后**节流报告**（5s 内 dedupe 相同 message），避免错误风暴打挂 SW。
const reportedErrors = new Set<string>();

function reportRuntimeError(payload: { message: string; stack?: string }) {
  const key = `${payload.message}::${payload.stack?.slice(0, 200) ?? ''}`;
  if (reportedErrors.has(key)) return;
  reportedErrors.add(key);
  setTimeout(() => reportedErrors.delete(key), 5000);  // 5s 内同错不重报
  try {
    chrome.runtime.sendMessage({
      type: MSG.RUNTIME_ERROR_REPORT,
      pageUrl: window.location.href,
      message: payload.message,
      stack: payload.stack,
      ts: new Date().toISOString(),
    });
  } catch (_e) {
    // SW dormant 时 sendMessage 可能抛；丢弃即可，下次错时会自然重试
  }
}

window.addEventListener('error', (e: ErrorEvent) => {
  reportRuntimeError({
    message: e.message || String(e.error),
    stack: e.error?.stack,
  });
});

window.addEventListener('unhandledrejection', (e: PromiseRejectionEvent) => {
  const reason = e.reason;
  const msg = reason instanceof Error
    ? reason.message
    : typeof reason === 'string' ? reason : JSON.stringify(reason);
  reportRuntimeError({
    message: `unhandled rejection: ${msg}`,
    stack: reason instanceof Error ? reason.stack : undefined,
  });
});

chrome.runtime.onMessage.addListener((msg: Message, _sender, _sendResponse) => {
  console.log('[vibe-niuma content] msg received', msg.type);

  if (msg.type === MSG.START_CAPTURE) {
    if (captureOverlay) captureOverlay.dispose();
    captureOverlay = new CaptureOverlay();
    captureOverlay.start().then((result) => {
      captureOverlay = null;
      if (result === null) {
        chrome.runtime.sendMessage({ type: MSG.CAPTURE_CANCEL });
        return;
      }
      chrome.runtime.sendMessage({
        type: MSG.CAPTURE_RESULT,
        url: window.location.href,
        boxCoords: result.boxCoords,
        viewport: result.viewport,
      });
    });
    return;
  }

  if (msg.type === MSG.START_ANNOTATE) {
    if (annotateOverlay) annotateOverlay.dispose();
    annotateOverlay = new AnnotateOverlay();
    annotateOverlay.start(msg.screenshotDataUrl).then((dataUrl) => {
      annotateOverlay = null;
      if (dataUrl === null) {
        chrome.runtime.sendMessage({ type: MSG.ANNOTATE_CANCEL });
        return;
      }
      // dataUrl 形如 "data:image/png;base64,iVBORw0KGgo..."，剥 prefix
      const idx = dataUrl.indexOf(',');
      const pngB64 = idx >= 0 ? dataUrl.slice(idx + 1) : dataUrl;
      chrome.runtime.sendMessage({ type: MSG.ANNOTATE_RESULT, pngB64 });
    }).catch((err) => {
      annotateOverlay = null;
      console.error('[vibe-niuma content] annotate overlay error', err);
      chrome.runtime.sendMessage({ type: MSG.ANNOTATE_CANCEL });
    });
    return;
  }
});
