// 订阅 background 的 REQUEST_STATE_CHANGED；初始拉一次 GET_REQUEST_STATE。
import { useEffect, useState } from 'react';
import { MSG, type Message } from '../../lib/messages';
import type { RequestStateMirror } from '../../lib/types';

export function useRequestState(): RequestStateMirror | null {
  const [state, setState] = useState<RequestStateMirror | null>(null);

  useEffect(() => {
    let mounted = true;
    chrome.runtime.sendMessage({ type: MSG.GET_REQUEST_STATE } as Message)
      .then((s: RequestStateMirror | null) => {
        if (mounted) setState(s ?? null);
      })
      .catch(() => { /* background 可能还没起 */ });

    const listener = (msg: Message) => {
      if (msg.type === MSG.REQUEST_STATE_CHANGED) setState(msg.state);
    };
    chrome.runtime.onMessage.addListener(listener as (msg: unknown) => void);
    return () => {
      mounted = false;
      chrome.runtime.onMessage.removeListener(listener as (msg: unknown) => void);
    };
  }, []);

  return state;
}
