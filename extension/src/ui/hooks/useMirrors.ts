// Phase E：订阅 background 维护的多对话快照。
// - 初始拉一次 GET_MIRRORS
// - 监听 MIRRORS_CHANGED 接增量
// 返回按 lastActivity 降序的 mirrors 数组 + activeId。
import { useEffect, useState } from 'react';
import { MSG, type Message } from '../../lib/messages';
import type { RequestStateMirror } from '../../lib/types';

export interface MirrorsView {
  mirrors: RequestStateMirror[];
  activeId: string | null;
}

function sortByActivity(mirrors: Record<string, RequestStateMirror>): RequestStateMirror[] {
  return Object.values(mirrors).sort((a, b) => {
    if (a.lastActivity === b.lastActivity) return 0;
    return a.lastActivity < b.lastActivity ? 1 : -1;
  });
}

export function useMirrors(): MirrorsView {
  const [view, setView] = useState<MirrorsView>({ mirrors: [], activeId: null });

  useEffect(() => {
    let mounted = true;
    chrome.runtime.sendMessage({ type: MSG.GET_MIRRORS } as Message)
      .then((resp: { mirrors: Record<string, RequestStateMirror>; activeId: string | null } | null) => {
        if (!mounted) return;
        if (!resp) return;
        setView({ mirrors: sortByActivity(resp.mirrors ?? {}), activeId: resp.activeId ?? null });
      })
      .catch(() => { /* background 可能还没起 */ });

    const listener = (msg: Message) => {
      if (msg.type === MSG.MIRRORS_CHANGED) {
        setView({ mirrors: sortByActivity(msg.mirrors), activeId: msg.activeId });
      }
    };
    chrome.runtime.onMessage.addListener(listener as (msg: unknown) => void);
    return () => {
      mounted = false;
      chrome.runtime.onMessage.removeListener(listener as (msg: unknown) => void);
    };
  }, []);

  return view;
}
