// Plan 10 Task 12: 客户端 intent classifier mirror.
//
// 这是「轻量启发式版」—— 不调 LLM，跑在 SW 里给业务员一个即时 UX hint：
// 「我准备按 X 模式发出去，不对点这里改」。最终决策仍以 server `POST /messages`
// 内的 LLM classifier 为准（server 比这里精准）。
//
// 设计目的：让 UI 输入框旁边能显示 mode badge，业务员心智「打字时就知道要发啥」。
// 不需要 100% 精准，但要 deterministic + 与 server 常见 case 同步。
import type { ConversationMessage, IntentMode } from './types';

export interface IntentDecision {
  mode: IntentMode;
  confidence: number; // 0..1
  reason: string;
}

export interface ClassifyInput {
  messageText: string;
  conversationMessages: ConversationMessage[];
  lastCrState: string | null;
  override?: IntentMode;
}

/** 信心阈值：< 0.6 → 标 unsure，UI 提示业务员手动确认。与 server 一致。 */
const UNSURE_THRESHOLD = 0.6;

export function isUnsure(d: IntentDecision): boolean {
  return d.confidence < UNSURE_THRESHOLD;
}

// 关键词集合 —— 跟 server prompt 里写的「判断原则」对齐。
// 客户端只做启发式匹配；命中关键词 → 高 confidence，否则 → 兜底 new_cr 低 confidence。

/** 追加修饰类（refine_cr 信号）。 */
const REFINE_KEYWORDS = [
  '再', '更', '稍', '微调', '换成', '颜色再', '字号', '大一点', '小一点',
  '改成', '变成', '调整', '调一下', '深一点', '淡一点', '改下', '换个',
];

/** 疑问 / 评价类（chat_only 信号）。 */
const CHAT_KEYWORDS = [
  '怎么样', '为啥', '为什么', '能不能', '可以吗', '你觉得', '你能',
  '什么意思', '是不是', '吗？', '?', '？',
];

/** 新动作动词（new_cr 信号）—— 命中即压过 refine/chat。 */
const NEW_VERBS = [
  '加', '新增', '增加', '加个', '加一个', '新加', '新建', '做一个',
  '帮我做', '帮我加', '帮我写', '实现', '生成',
];

/**
 * 启发式 classifyIntent —— 不调 LLM。
 *
 * 决策优先级：
 *   1. override → 直接返
 *   2. NEW_VERBS 命中 → new_cr (confidence 0.85)
 *   3. CHAT_KEYWORDS 命中 → chat_only (confidence 0.8)
 *   4. REFINE_KEYWORDS 命中 + last CR 是 preview-ready/merged → refine_cr (0.85)
 *   5. 都没命中：
 *      - 没历史 → new_cr (0.55, unsure)
 *      - 有历史但 text 很短（≤2 字）→ refine_cr (0.5, unsure)
 *      - 否则 → new_cr (0.55, unsure)
 */
export function classifyIntent(input: ClassifyInput): IntentDecision {
  if (input.override) {
    return { mode: input.override, confidence: 1, reason: `用户强制 ${input.override}` };
  }

  const text = input.messageText.trim();
  if (!text) {
    return { mode: 'new_cr', confidence: 0.5, reason: '空消息兜底' };
  }

  // 短词优先判 unsure：「再来」「嗯」这类 ≤2 字回应有歧义，不该被「再」「更」
  // 这种单字 refine 关键词误吞为高 confidence。
  // 例外：命中明确新动词（「加」算新动词，但 1 字也属真实意图）仍走正常路径。
  const hasHistory = input.conversationMessages.length > 0;
  if (hasHistory && text.length <= 2 && !NEW_VERBS.some((kw) => text === kw)) {
    return { mode: 'refine_cr', confidence: 0.5, reason: '短词回应，可能续改（unsure）' };
  }

  if (NEW_VERBS.some((kw) => text.includes(kw))) {
    return { mode: 'new_cr', confidence: 0.85, reason: '命中新动作动词' };
  }

  if (CHAT_KEYWORDS.some((kw) => text.includes(kw))) {
    return { mode: 'chat_only', confidence: 0.8, reason: '命中疑问/评价词' };
  }

  const refineOk = REFINE_KEYWORDS.some((kw) => text.includes(kw));
  if (refineOk && (input.lastCrState === 'preview-ready' || input.lastCrState === 'merged')) {
    return { mode: 'refine_cr', confidence: 0.85, reason: '命中追加修饰 + 上一 CR 可续改' };
  }

  return { mode: 'new_cr', confidence: 0.55, reason: '未命中关键词，保守新需求（unsure）' };
}
