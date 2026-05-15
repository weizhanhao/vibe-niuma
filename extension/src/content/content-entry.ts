// content script 入口：监听 background 的 START_CAPTURE，启动 overlay，
// 完成后把 CAPTURE_RESULT 发回 background。
import { CaptureOverlay } from './capture-overlay';
import { MSG, type Message } from '../lib/messages';

let overlay: CaptureOverlay | null = null;

console.log('[doskill content] loaded on', window.location.href);

chrome.runtime.onMessage.addListener((msg: Message, _sender, _sendResponse) => {
  console.log('[doskill content] msg received', msg);
  if (msg.type === MSG.START_CAPTURE) {
    if (overlay) overlay.dispose();
    overlay = new CaptureOverlay();
    console.log('[doskill content] overlay.start()');
    overlay.start().then((result) => {
      console.log('[doskill content] overlay resolved', result);
      overlay = null;
      if (result === null) {
        chrome.runtime.sendMessage({ type: MSG.CAPTURE_CANCEL });
        return;
      }
      chrome.runtime.sendMessage({
        type: MSG.CAPTURE_RESULT,
        url: window.location.href,
        boxCoords: result.boxCoords,
        viewport: result.viewport,
      }, (reply) => {
        console.log('[doskill content] CAPTURE_RESULT reply', reply, chrome.runtime.lastError);
      });
    });
  }
});
