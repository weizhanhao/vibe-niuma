// content script 入口：监听 background 的 START_CAPTURE / START_ANNOTATE。
// - START_CAPTURE：老路径，拖框 + 坐标返回。保留兼容。
// - START_ANNOTATE：新路径，全屏标注 overlay，烘焙 PNG 返回。
import { CaptureOverlay } from './capture-overlay';
import { AnnotateOverlay } from './annotate-overlay';
import { MSG, type Message } from '../lib/messages';

let captureOverlay: CaptureOverlay | null = null;
let annotateOverlay: AnnotateOverlay | null = null;

console.log('[doskill content] loaded on', window.location.href);

chrome.runtime.onMessage.addListener((msg: Message, _sender, _sendResponse) => {
  console.log('[doskill content] msg received', msg.type);

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
      console.error('[doskill content] annotate overlay error', err);
      chrome.runtime.sendMessage({ type: MSG.ANNOTATE_CANCEL });
    });
    return;
  }
});
