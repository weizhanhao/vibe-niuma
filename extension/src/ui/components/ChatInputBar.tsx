// Plan 10 Task 16: cursor-like 底部输入栏 —— AttachmentTray + mode badge + SUBMIT_MESSAGE.
//
// 业务员视角：
//  - 0-3 张附件 chip 列在输入框左侧；× 去掉
//  - 输入框上方有 mode badge：「新需求」「续改」「聊天」—— 实时根据 text 判
//  - + 按钮 = 添加附件（截图 / 框选 / 文件选择器；满 3 张禁用）
//  - ▢ 框选按钮保留：触发 demo 页面 overlay 拖框（产物 → attachments）
//  - 发送 → SUBMIT_MESSAGE，SW 拿当前 active conv + attachments → POST /messages
//
// 老的 SUBMIT_TEXT_ONLY / UI_START_CAPTURE 短期内仍 work（v0.5 路径），但新的发送按钮
// 走 SUBMIT_MESSAGE。框选保留 UI_START_CAPTURE 是因为 overlay 还在那条链路上工作。
// props 都可选 —— App.tsx 改造完成前老调用 `<ChatInputBar />` 仍能跑（空 attachments）。
import { useMemo, useState } from 'react';
import { MAX_ATTACHMENTS } from '../../lib/attachments';
import { classifyIntent } from '../../lib/intent';
import { MSG, type Message } from '../../lib/messages';
import type { Attachment, IntentMode } from '../../lib/types';

const send = (msg: Message) => chrome.runtime.sendMessage(msg);

const MODE_LABEL: Record<IntentMode, string> = {
  new_cr: '新需求',
  refine_cr: '续改',
  chat_only: '聊天',
};

interface Props {
  attachments?: Attachment[];
  onAttachmentsChange?: (next: Attachment[]) => void;
  initialText?: string;
}

export function ChatInputBar({
  attachments = [],
  onAttachmentsChange = () => {},
  initialText = '',
}: Props = {}) {
  const [text, setText] = useState(initialText);

  const decision = useMemo(
    () => classifyIntent({
      messageText: text,
      conversationMessages: [],
      lastCrState: null,
    }),
    [text],
  );

  const disabled = !text.trim();
  const atLimit = attachments.length >= MAX_ATTACHMENTS;

  const submit = () => {
    if (disabled) return;
    send({
      type: MSG.SUBMIT_MESSAGE,
      text,
      ...(attachments.length > 0 ? { attachments } : {}),
    });
    setText('');
    onAttachmentsChange([]);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !disabled) {
      e.preventDefault();
      submit();
    }
  };

  const startFrame = () => {
    send({ type: MSG.UI_START_CAPTURE, requestText: text });
  };

  const removeAttachment = (idx: number) => {
    onAttachmentsChange(attachments.filter((_, i) => i !== idx));
  };

  return (
    <div className="chat-input-bar">
      <div className="chat-input-meta">
        <span className={`mode-badge mode-${decision.mode}`}>
          {MODE_LABEL[decision.mode]}
        </span>
        {decision.confidence < 0.6 && (
          <span className="mode-unsure" title={decision.reason}>不确定</span>
        )}
      </div>
      {attachments.length > 0 && (
        <div className="attachment-tray">
          {attachments.map((a, i) => (
            <span key={i} className="attachment-chip" title={a.name ?? a.kind}>
              <span className="attachment-chip__icon" aria-hidden="true">
                {a.kind === 'attached_file' ? '📎' : '🖼'}
              </span>
              <span className="attachment-chip__name">
                {a.name ?? a.kind}
              </span>
              <button
                type="button"
                aria-label={`移除附件 ${i + 1}`}
                onClick={() => removeAttachment(i)}
                className="attachment-chip__close"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="chat-input-row">
        <button
          type="button"
          aria-label="添加附件"
          className="btn btn-ghost btn-small"
          disabled={atLimit}
          title={atLimit ? `最多 ${MAX_ATTACHMENTS} 个附件` : '添加附件'}
          // 实际选择器接入留给 Task 17 MainShell（DOM 层 file input + 剪贴板事件）
        >
          +
        </button>
        <textarea
          aria-label="业务需求"
          className="chat-input-textarea"
          rows={2}
          placeholder="想改这个页面的哪里？⌘↵ 发送"
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
            aria-label="框选区域"
            title="精确定位：在页面上拖一个框"
          >
            <span className="ico">▢</span> 框选
          </button>
          <button
            type="button"
            className="btn btn-primary btn-small"
            onClick={submit}
            disabled={disabled}
            aria-label="发送"
          >
            → 发送
          </button>
        </div>
      </div>
    </div>
  );
}
