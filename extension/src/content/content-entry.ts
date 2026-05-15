// content script 入口：监听 background 的 START_CAPTURE，启动 overlay，
// 完成后把 CAPTURE_RESULT 发回 background。
import { CaptureOverlay } from './capture-overlay';
import { MSG, type Message } from '../lib/messages';

let overlay: CaptureOverlay | null = null;

chrome.runtime.onMessage.addListener((msg: Message, _sender, _sendResponse) => {
  if (msg.type === MSG.START_CAPTURE) {
    if (overlay) overlay.dispose();
    overlay = new CaptureOverlay();
    overlay.start().then((result) => {
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
      });
    });
  }
});
