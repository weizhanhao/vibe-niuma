/** 每个环节在做什么、谁在做、你能做什么。
 *
 * 界面之前只画了一条进度轨，用户看到「clarify」三个字并不知道
 * 该干嘛 —— 尤其澄清环节其实<b>在等他回话</b>，不说清楚需求就静静地卡住。 */
export interface StageGuide {
  /** 这一环在做什么（一句话，给提需求的人看，不讲实现） */
  doing: string;
  /** 谁在做 */
  who: "ai" | "you" | "both" | "system";
  /** 你在这一环能做什么。空 = 只能等。 */
  can: string;
}

export const STAGE_GUIDE: Record<string, StageGuide> = {
  triage: {
    who: "ai", doing: "读你的需求，判断规模、影响哪些仓，决定走不走大重构序列。",
    can: "还能补充说明 —— 越具体，后面拆得越准。",
  },
  clarify: {
    who: "both",
    doing: "AI 把它没把握的地方提出来问你。问清楚了才开工，避免做完才发现方向错。",
    can: "在下面「对话」里回答。觉得够了可以点「✓ 够了直接干」直接跳过追问。",
  },
  decompose: {
    who: "ai",
    doing: "把需求切成可并行的垂直切片，标出彼此的依赖和各自会动哪些文件。",
    can: "对切法有意见就在对话里说，AI 会重拆。",
  },
  implement: {
    who: "ai",
    doing: "每个切片一个独立工作区、一个独立会话，同时开写。互不干扰。",
    can: "中途改主意可以在对话里追加，会并到当前这轮。",
  },
  verify: {
    who: "ai",
    doing: "在容器里真跑 lint / test / build。挂了先跟基线比，是新引入的才算回归。",
    can: "等结果。失败会自动带着报错回去修，最多两轮。",
  },
  ai_review: {
    who: "ai",
    doing: "另起一个不知道代码是谁写的 session 复核：缺陷轴、规格轴、规范轴。",
    can: "看下面三轴的结论。它召回不稳，别拿它替代你自己看。",
  },
  preview: {
    who: "system",
    doing: "把这条需求单独拉起一个预览环境，可以点开真看效果。",
    can: "点预览地址体验，有问题在对话里说。",
  },
  browser_check: {
    who: "ai",
    doing: "用 ego 浏览器打开预览环境，像真人一样把这条需求涉及的路径点一遍。",
    can: "看下面的结论。lint/test/build 全过，页面照样可能白屏 —— 这一环补的就是这个。",
  },
  review: {
    who: "you",
    doing: "流程停在这里等你拍板。停着<b>不占并行工位</b>，别的需求照跑。",
    can: "通过 / 打回改 / 拒绝。打回要写清楚哪不对，AI 带着原会话接着改。",
  },
  merge: {
    who: "ai",
    doing: "按仓排队合入目标分支。冲突三级处理：git 自动 → mergiraf 语法级 → AI 带原会话理解意图。",
    can: "看合并队列。AI 解不了的冲突会退回来找你。",
  },
  deploy_test: {
    who: "system", doing: "部到测试环境。", can: "等部署完成。",
  },
  integrate: {
    who: "system",
    doing: "在测试环境跑端到端。这一步是跟别人的改动合在一起跑的。",
    can: "挂了会退回，可以在对话里给线索。",
  },
  release: {
    who: "you",
    doing: "上生产前的最后一道人工闸门。",
    can: "确认无误就放行。这一步不可撤销，看清楚再点。",
  },
};

export function guideFor(key: string): StageGuide {
  return STAGE_GUIDE[key] ?? {
    who: "system", doing: "自定义环节。", can: "",
  };
}

export const WHO_LABEL: Record<StageGuide["who"], string> = {
  ai: "AI 在跑", you: "等你", both: "AI 和你一起", system: "平台在跑",
};
