// Plan 7 · Task 4：把 prompts/*.md 拼成 DeepSeek 的 system 消息。
//
// 设计要点：
//   - 6 篇 markdown 全部走 `?raw` import bundle 进 dist，**运行时不 fetch**
//     （部署助手运行时 orchestrator 可能还没起，不能依赖网络拿 prompt）。
//   - 路径无关的 4 篇（handbook / protocol / good / bad）每次都拼上；
//     路径相关的 path-local.md / path-ecs.md 按入参择一拼上。
//     入参 null 时（用户还在 choosing_path 阶段未选）省略路径相关 section。
//   - 节与节用 `\n\n---\n\n` 分隔，方便 LLM 视觉上识别边界。
//
// 调用方：extension/src/ui/panels/DeploymentAssistantPanel.tsx（Plan 7 · Task 7）
//          形如：buildSystemPrompt(state.phase === 'collecting_info' ? state.path : null)
import handbook from './prompts/vibe-niuma-handbook.md?raw';
import protocol from './prompts/action-protocol.md?raw';
import pathLocal from './prompts/path-local.md?raw';
import pathEcs from './prompts/path-ecs.md?raw';
import good from './prompts/examples-good.md?raw';
import bad from './prompts/examples-bad.md?raw';

export type DeploymentPath = 'local' | 'ecs';

const SECTION_DELIMITER = '\n\n---\n\n';

/**
 * 拼装 vibe-niuma 部署助手的 system prompt。
 *
 * @param path 当前用户选定的部署路径；null 表示还没选（choosing_path 阶段）。
 * @returns 完整 system 消息字符串，可直接喂 DeepSeek chat messages[0].content。
 */
export function buildSystemPrompt(path: DeploymentPath | null): string {
  const sections: string[] = [handbook, protocol];
  if (path === 'local') {
    sections.push(pathLocal);
  } else if (path === 'ecs') {
    sections.push(pathEcs);
  }
  sections.push(good, bad);
  return sections.join(SECTION_DELIMITER);
}
