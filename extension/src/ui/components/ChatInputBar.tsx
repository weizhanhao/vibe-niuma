// Plan 9 后续 UX：Cursor 风格底部输入栏 —— sticky 在 .app-footer，永远可用。
//
// 替代老 CapturePanel 在 body 里的位置。CapturePanel 的「STEP 01 标题 +
// 描述 + hint」一并去掉 —— 那是首次安装 onboarding，反复出现噪音大。
//
// 跟 CapturePanel 一样的两个动作：
//   - 框选 → 起 overlay 拖选 → ReviewCapturePanel 确认 → 提交
//   - 直接提交 → 仅文字，AI 按 URL 自定位
import { useState } from 'react';
import { MSG, type Message } from '../../lib/messages';

const send = (msg: Message) => chrome.runtime.sendMessage(msg);

export function ChatInputBar() {
  const [text, setText] = useState('');
  const startFrame = () => {
    send({ type: MSG.UI_START_CAPTURE, requestText: text });
    setText('');
  };
  const submitTextOnly = () => {
    send({ type: MSG.SUBMIT_TEXT_ONLY, requestText: text });
    setText('');
  };
  const disabled = !text.trim();

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Cmd/Ctrl + Enter 直接提交（Cursor 体感）
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !disabled) {
      e.preventDefault();
      submitTextOnly();
    }
  };

  return (
    <div className="chat-input-bar">
      <textarea
        aria-label="业务需求"
        className="chat-input-textarea"
        rows={2}
        placeholder="想改这个页面的哪里？⌘↵ 直接提交"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        maxLength={500}
      />
      <div className="chat-input-actions">
        <button
          type="button"
          className="btn btn-secondary btn-small"
          onClick={startFrame}
          disabled={disabled}
          aria-label="框选区域"
          title="精确定位：在页面上拖一个框"
        >
          <span className="ico">▢</span> 框选
        </button>
        <button
          type="button"
          className="btn btn-primary btn-small"
          onClick={submitTextOnly}
          disabled={disabled}
          aria-label="提交"
        >
          → 提交
        </button>
      </div>
    </div>
  );
}
