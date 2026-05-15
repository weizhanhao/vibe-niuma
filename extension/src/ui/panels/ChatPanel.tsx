// Plan 7 Task 5: ChatPanel —— LLM 流式 chat 界面。
//
// 设计要点：
// - 消息列表：user 蓝、assistant 灰、error 红边
// - 流式渲染：边收边追加，<actions> 块走 parseActionsFromAssistant 剥离再渲染
// - 输入：Enter 发送 / Shift+Enter 换行
// - abort：流式期间显示「停止」按钮，AbortController 取消下层 fetch
// - error：DeepSeekAuthError → 「key 错了，去设置改」+ 跳转 SettingsPanel 按钮
//
// 调用方：DeploymentAssistantPanel（Task 7），传入 DeepSeekClient + system prompt + history。
import React, { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { DeepSeekAuthError, type ChatMessage, type DeepSeekClient } from '../../ai/DeepSeekClient';
import { parseActionsFromAssistant } from '../../ai/actions';

export interface ChatPanelProps {
  client: DeepSeekClient;
  systemPrompt: string;
  history: ChatMessage[];
  onAppend: (msg: ChatMessage) => void;
  onAssistantComplete?: (full: string) => void;
  disabled?: boolean;
  onAuthError?: () => void;
}

interface ErrorState {
  message: string;
  isAuth: boolean;
}

export function ChatPanel({
  client, systemPrompt, history, onAppend, onAssistantComplete, disabled, onAuthError,
}: ChatPanelProps) {
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<ErrorState | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [history, streaming]);

  const send = async () => {
    const text = input.trim();
    if (!text || isStreaming || disabled) return;
    setInput('');
    setError(null);
    const userMsg: ChatMessage = { role: 'user', content: text };
    onAppend(userMsg);

    const messages: ChatMessage[] = [
      { role: 'system', content: systemPrompt },
      ...history,
      userMsg,
    ];
    const ac = new AbortController();
    abortRef.current = ac;
    setIsStreaming(true);
    setStreaming('');

    let full = '';
    try {
      for await (const chunk of client.chat(messages, { signal: ac.signal })) {
        full += chunk;
        setStreaming(full);
      }
      onAppend({ role: 'assistant', content: full });
      onAssistantComplete?.(full);
    } catch (e) {
      if (ac.signal.aborted) {
        if (full) onAppend({ role: 'assistant', content: full });
      } else if (e instanceof DeepSeekAuthError) {
        setError({ message: 'DeepSeek API Key 不对。点齿轮去设置改。', isAuth: true });
      } else {
        setError({ message: e instanceof Error ? e.message : String(e), isAuth: false });
      }
    } finally {
      setIsStreaming(false);
      setStreaming('');
      abortRef.current = null;
    }
  };

  const abort = () => abortRef.current?.abort();

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  const renderAssistant = (text: string) => {
    const { prose } = parseActionsFromAssistant(text);
    return <ReactMarkdown>{prose}</ReactMarkdown>;
  };

  return (
    <div className="chat-panel">
      <div className="chat-msg-list" ref={scrollRef} role="log" aria-live="polite">
        {history.map((m, i) => (
          <div
            key={i}
            className={m.role === 'user' ? 'chat-msg-user' : 'chat-msg-assistant'}
            data-role={m.role}
          >
            {m.role === 'assistant' ? renderAssistant(m.content) : m.content}
          </div>
        ))}
        {isStreaming && streaming && (
          <div className="chat-msg-assistant" data-streaming="true">
            {renderAssistant(streaming)}
            <span className="chat-streaming-dot" aria-hidden="true">▍</span>
          </div>
        )}
        {error && (
          <div className="chat-msg-error" role="alert">
            <div>{error.message}</div>
            {error.isAuth && onAuthError && (
              <button className="btn btn-small btn-secondary" onClick={onAuthError}>
                去设置
              </button>
            )}
          </div>
        )}
      </div>

      <div className="chat-input-row">
        <textarea
          className="chat-input"
          aria-label="对话输入"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
          disabled={isStreaming || disabled}
          placeholder={disabled ? '处理中…' : '说点什么 / Enter 发送 · Shift+Enter 换行'}
        />
        <div className="chat-input-actions">
          {isStreaming ? (
            <button className="btn btn-small btn-ghost" onClick={abort}>停止</button>
          ) : (
            <button
              className="btn btn-small btn-primary"
              onClick={() => void send()}
              disabled={!input.trim() || disabled}
            >发送</button>
          )}
        </div>
      </div>
    </div>
  );
}
