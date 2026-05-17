// Plan 10 Task 17: useActiveConversation —— 拉当前 active conversation 的 messages。
//
// 业务员视角：切 tab → ChatStream 立刻显示该会话的历史 messages（包括 user/ai/summary）。
// 长池子的 messages 由 conversation_repo 维护；这里只是 fetch + refresh。
import { useEffect, useState } from 'react';
import { getConversation, type Conversation } from '../../lib/conversations';
import type { ConversationMessage } from '../../lib/types';

export function useActiveConversation(
  convId: string | null,
  refreshKey: unknown = 0,
): { messages: ConversationMessage[]; conv: Conversation | null; loading: boolean } {
  const [conv, setConv] = useState<Conversation | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!convId) {
      setConv(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void getConversation(convId)
      .then((c) => {
        if (!cancelled) setConv(c);
      })
      .catch(() => {
        if (!cancelled) setConv(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [convId, refreshKey]);

  // Server Message superset → client ConversationMessage —— wire-compatible cast.
  const messages = (conv?.messages ?? []) as unknown as ConversationMessage[];
  return { messages, conv, loading };
}
