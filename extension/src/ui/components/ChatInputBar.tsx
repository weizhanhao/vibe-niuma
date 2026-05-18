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
import { useState } from 'react';
import { MAX_ATTACHMENTS } from '../../lib/attachments';
import { MSG, type Message } from '../../lib/messages';
import type { Attachment, IntentMode } from '../../lib/types';

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const result = r.result as string;
      // dataURL = data:image/png;base64,xxxx → 剥 prefix 只留 base64
      const idx = result.indexOf(',');
      resolve(idx >= 0 ? result.slice(idx + 1) : result);
    };
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

const send = (msg: Message) => chrome.runtime.sendMessage(msg);

interface SubmittedInfo {
  cr_id?: string | null;
  mode?: IntentMode;
}

interface Props {
  attachments?: Attachment[];
  onAttachmentsChange?: (next: Attachment[]) => void;
  initialText?: string;
  /** 当前 active conversation id。UI 显式带，SW 不依赖 session 状态。 */
  conversationId?: string | null;
  /** 当前项目名 —— 显示在 commander footer 让业务员一眼看到「在哪个项目上提交」 */
  projectName?: string;
  /** SUBMIT_MESSAGE 成功送达 SW 后回调。带 cr_id/mode 让 MainShell 把
   *  cr_id patch 到最后那条乐观 user 消息上，InlineCard 早早 mount 看到流式日志。 */
  onSubmitted?: (info: SubmittedInfo) => void;
  /** 乐观 UI：点击发送的瞬间同步触发，MainShell 立刻渲染 user 气泡，
   *  不等服务器分类返回（典型 1-3s）。 */
  onOptimisticSend?: (text: string, attachments: Attachment[]) => void;
}

export function ChatInputBar({
  attachments = [],
  onAttachmentsChange = () => {},
  initialText = '',
  conversationId = null,
  projectName,
  onSubmitted,
  onOptimisticSend,
}: Props = {}) {
  const [text, setText] = useState(initialText);
  // 同界面 lightbox：点缩略图弹层看原图（chrome extension 不让 data: URL 开新标签）
  const [lightboxAtt, setLightboxAtt] = useState<Attachment | null>(null);

  const disabled = !text.trim();

  const submit = async () => {
    if (disabled) return;
    if (!conversationId) {
      // 没 conv：理论上 MainShell 会 disable 输入框；这里兜底
      console.warn('[vibe-niuma] submit without conversation_id');
      return;
    }
    const sent = text;
    const sentAtt = attachments;
    setText('');
    onAttachmentsChange([]);
    // 乐观 UI：点击瞬间通知 MainShell 渲染气泡，不等服务器
    onOptimisticSend?.(sent, sentAtt);
    try {
      const reply = await send({
        type: MSG.SUBMIT_MESSAGE,
        text: sent,
        conversation_id: conversationId,
        ...(sentAtt.length > 0 ? { attachments: sentAtt } : {}),
      }) as {
        ok: boolean; error?: string;
        cr_id?: string | null; mode?: IntentMode;
      } | undefined;
      if (reply && reply.ok === false) {
        console.warn('[vibe-niuma] SUBMIT_MESSAGE failed', reply.error);
        setText(sent);
        onAttachmentsChange(sentAtt);
      } else {
        onSubmitted?.({ cr_id: reply?.cr_id ?? null, mode: reply?.mode });
      }
    } catch (err) {
      console.warn('[vibe-niuma] SUBMIT_MESSAGE exception', err);
      setText(sent);
      onAttachmentsChange(sentAtt);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 回车直接发送；⇧+回车换行（避免误发的同时保留长文换行能力）
    if (e.key === 'Enter' && !e.shiftKey && !disabled) {
      e.preventDefault();
      submit();
    }
  };

  // 剪贴板贴图：扫 clipboard 里的 image item → 转 base64 → 加进 attachments
  const onPaste = async (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const imageItems: DataTransferItem[] = [];
    for (let i = 0; i < items.length; i++) {
      if (items[i].kind === 'file' && items[i].type.startsWith('image/')) {
        imageItems.push(items[i]);
      }
    }
    if (imageItems.length === 0) return;       // 没图 → 走默认粘贴（文本）
    e.preventDefault();
    const room = MAX_ATTACHMENTS - attachments.length;
    const slice = imageItems.slice(0, Math.max(0, room));
    if (slice.length === 0) return;
    const next: Attachment[] = [];
    for (const item of slice) {
      const file = item.getAsFile();
      if (!file) continue;
      const b64 = await fileToBase64(file);
      next.push({
        kind: 'pasted_image',
        mime: file.type || 'image/png',
        b64,
        name: file.name || '剪贴板图',
      });
    }
    if (next.length > 0) onAttachmentsChange([...attachments, ...next]);
  };

  const startAnnotate = () => {
    send({ type: MSG.UI_START_ANNOTATE });
  };

  const removeAttachment = (idx: number) => {
    onAttachmentsChange(attachments.filter((_, i) => i !== idx));
  };

  return (
    <div className="chat-input-bar">
      {attachments.length > 0 && (
        <div className="attachment-tray">
          {attachments.map((a, i) => {
            const isImage = a.mime.startsWith('image/');
            const dataUrl = `data:${a.mime};base64,${a.b64}`;
            return (
              <span key={i} className={`attachment-chip${isImage ? ' is-image' : ''}`}
                    title={a.name ?? a.kind}>
                {isImage ? (
                  <button
                    type="button"
                    className="attachment-chip__thumb"
                    aria-label={`查看附件 ${i + 1} 原图`}
                    onClick={() => setLightboxAtt(a)}
                  >
                    <img src={dataUrl} alt="" />
                  </button>
                ) : (
                  <span className="attachment-chip__icon" aria-hidden="true">📎</span>
                )}
                <span className="attachment-chip__name">
                  {a.name ?? (isImage ? '截图' : a.kind)}
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
            );
          })}
        </div>
      )}

      <textarea
        aria-label="输入需求"
        className="chat-input-textarea"
        rows={2}
        placeholder="想改哪里？"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        maxLength={500}
      />

      <div className="chat-input-footer">
        <div className="chat-input-toolbar">
          <button
            type="button"
            className="chat-input-tool"
            onClick={startAnnotate}
            aria-label="截图标注"
            title="截图标注（也可 ⌘V 粘贴图）"
          >
            <span aria-hidden="true">✂</span>
          </button>
        </div>

        {projectName && (
          <span className="chat-input-meta" title={projectName}>
            <span className="chat-input-meta__project">{projectName}</span>
          </span>
        )}

        <span className="chat-input-footer__spacer" />

        <button
          type="button"
          className="chat-input-send"
          onClick={submit}
          disabled={disabled}
          aria-label="发送"
          title="发送（回车）"
        >
          <span aria-hidden="true">↑</span>
        </button>
      </div>

      {lightboxAtt && (
        <div
          className="attachment-lightbox"
          role="dialog"
          aria-label="附件原图"
          onClick={() => setLightboxAtt(null)}
          onKeyDown={(e) => { if (e.key === 'Escape') setLightboxAtt(null); }}
          tabIndex={-1}
        >
          <button
            type="button"
            className="attachment-lightbox__close"
            aria-label="关闭"
            onClick={(e) => { e.stopPropagation(); setLightboxAtt(null); }}
          >×</button>
          <img
            src={`data:${lightboxAtt.mime};base64,${lightboxAtt.b64}`}
            alt={lightboxAtt.name ?? '附件原图'}
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}
