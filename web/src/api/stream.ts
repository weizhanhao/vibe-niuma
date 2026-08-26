import { useEffect, useRef, useState } from "react";
import type { Client } from "./client";

export interface Step {
  kind: string;          // tool | text | reasoning | step | error
  text: string;
  tool?: string;
  title?: string;
  status?: string;
  detail?: string;
  /** 逐 token 增量所属的 part —— 同一个 part 的增量要拼成一段，不能一行一个 token */
  part_id?: string;
  delta?: boolean;
}

/** 连接健康度。**必须让用户看得见** —— 一轮 agent 跑 5~15 分钟，
 *  静默失联比任何别的问题都贵：人盯着一个永远转的圈，分不清是
 *  agent 在想难题还是页面已经死了。 */
export type Conn = "connecting" | "live" | "reconnecting" | "dead";

/** 把一条增量并进已有的步骤里。
 *
 * opencode 是**逐 token** 推的：一次运行 200+ 条增量，各自带 partID。
 * 不合并的话页面上会变成 200 多行、每行一个字 —— 那比不显示还糟。
 */
export function append(prev: Step[], d: Step): Step[] {
  if (!d.delta || !d.part_id) return [...prev, d];
  const last = prev[prev.length - 1];
  if (last && last.part_id === d.part_id) {
    return [...prev.slice(0, -1), { ...last, text: last.text + d.text }];
  }
  return [...prev, d];
}

const MAX_RETRY = 6;

/** 订阅一条需求的实时流。
 *
 * **必须用 addEventListener，不能用 onmessage。** 服务端发的是带
 * `event: <kind>` 的具名事件，`onmessage` 只在事件没有名字时触发。
 */
export function useAgentStream(api: Client, slug: string, reqId: string,
                               onChange?: () => void) {
  const [steps, setSteps] = useState<Step[]>([]);
  const [live, setLive] = useState(false);
  const [conn, setConn] = useState<Conn>("connecting");
  const cb = useRef(onChange);
  useEffect(() => { cb.current = onChange; }, [onChange]);

  useEffect(() => {
    setSteps([]); setLive(false); setConn("connecting");
    if (!slug || !reqId) return;

    // **lastId 必须放在 effect 里。**
    // 放外面用 useRef 的话，切到另一条需求时它还留着上一条的值；
    // 而后端是 `Event.id > last_event_id` 且 id 是**项目级全局自增** ——
    // 于是新需求那些 id 更小的历史事件全被跳过，页面一片空白。
    let lastId = 0;
    let es: EventSource | null = null;
    let retry = 0;
    let timer = 0;
    let stopped = false;

    const bump = (ev: MessageEvent) => {
      const id = Number(ev.lastEventId);
      if (Number.isFinite(id) && id > 0) lastId = id;
    };

    function open() {
      if (stopped) return;
      try { es = new EventSource(api.eventsUrl(slug, reqId, lastId)); }
      catch { setConn("dead"); return; }     // 环境不支持（jsdom）

      es.onopen = () => { retry = 0; setConn("live"); };

      es.addEventListener("agent_step", (ev) => {
        bump(ev as MessageEvent);
        try {
          setSteps(prev => append(prev, JSON.parse((ev as MessageEvent).data)));
          setLive(true);
        } catch { /* 坏帧跳过，别把整条流拖垮 */ }
      });
      const onState = (ev: Event) => {
        bump(ev as MessageEvent);
        // 一个环节走完了 —— 思考已经落到消息上，实时那份可以让位。
        // **必须清空**：不清的话下一轮开始前，页面顶着「实时」的标题
        // 展示上一轮的旧步骤，用户以为 AI 卡在原地重复。
        setLive(false);
        setSteps([]);
        cb.current?.();
      };
      for (const k of ["status", "draft"]) es.addEventListener(k, onState);

      es.onerror = () => {
        // **不要 close()。**
        // EventSource 只在连接自然中断时自动重连；显式 close() 会把
        // readyState 置为 CLOSED，**之后永远不再重连**。
        // 一轮跑 15 分钟，中间 WiFi 抖一下、代理超时踢一次，流就死了，
        // 而 UI 上没有任何提示 —— 这是这个产品最致命的一个 bug。
        if (!es || es.readyState !== EventSource.CLOSED) {
          setConn("reconnecting");        // 浏览器正在自己重连，别慌
          return;
        }
        es = null;
        if (retry >= MAX_RETRY) { setConn("dead"); return; }
        setConn("reconnecting");
        // 指数退避 + 抖动，避免服务端刚恢复就被一群客户端同时打爆
        const wait = Math.min(1000 * 2 ** retry++, 30_000) * (0.5 + Math.random());
        timer = window.setTimeout(open, wait);
      };
    }

    open();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      es?.close();
    };
  }, [api, slug, reqId]);

  return { steps, live, conn };
}
