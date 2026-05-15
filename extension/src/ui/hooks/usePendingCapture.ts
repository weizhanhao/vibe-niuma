// Phase G：订阅 background 的 pendingCapture。
// - 初始拉一次 GET_PENDING_CAPTURE（覆盖 SW 死过又复活的场景）
// - 监听 PENDING_CAPTURE_CHANGED 接增量
// 注意：service-worker 不持久化截图（PNG base64 体积大、chrome.storage 有配额）；
// 所以 SW 死了 pendingCapture 也丢了，业务员需要重新框。MVP 可接受。
import { useEffect, useState } from 'react';
import { MSG, type Message } from '../../lib/messages';
import type { PendingCapture } from '../../lib/types';

export function usePendingCapture(): PendingCapture | null {
  const [pending, setPending] = useState<PendingCapture | null>(null);

  useEffect(() => {
    let mounted = true;
    chrome.runtime.sendMessage({ type: MSG.GET_PENDING_CAPTURE } as Message)
      .then((p: PendingCapture | null) => {
        if (mounted) setPending(p ?? null);
      })
      .catch(() => { /* background 可能还没起 */ });

    const listener = (msg: Message) => {
      if (msg.type === MSG.PENDING_CAPTURE_CHANGED) setPending(msg.pending);
    };
    chrome.runtime.onMessage.addListener(listener as (msg: unknown) => void);
    return () => {
      mounted = false;
      chrome.runtime.onMessage.removeListener(listener as (msg: unknown) => void);
    };
  }, []);

  return pending;
}
