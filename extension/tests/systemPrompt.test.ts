// Plan 7 · Task 4：systemPrompt 拼装器 import 联通性测试。
//
// 不测 markdown 文案（容易随手改、维护成本高），只测：
//   - 6 个 `?raw` import 全部存活、buildSystemPrompt 不抛异常
//   - 各 section 至少有一个 「锚点字符串」 出现在最终输出里
//     → 一旦哪个 prompts/*.md 被误删 / 误改文件名，本测试会红。
import { describe, expect, it } from 'vitest';
import { buildSystemPrompt } from '../src/ai/systemPrompt';

describe('buildSystemPrompt', () => {
  it('拼接 local 路径时包含 handbook / action-protocol / path-local / 正反例锚点', () => {
    const prompt = buildSystemPrompt('local');
    // handbook
    expect(prompt).toContain('vibe-niuma 部署助手');
    expect(prompt).toContain('4 项配齐');
    // action-protocol
    expect(prompt).toContain('Action 协议');
    expect(prompt).toContain('<actions>');
    expect(prompt).toContain('copy_command');
    // path-local
    expect(prompt).toContain('Path A');
    expect(prompt).toContain('本地 Docker');
    // 不应该包含 Path B 章节
    expect(prompt).not.toContain('Path B · 阿里云 ECS 部署脚本');
    // examples
    expect(prompt).toContain('正例对话片段');
    expect(prompt).toContain('反例');
  });

  it('拼接 ecs 路径时包含 path-ecs section', () => {
    const prompt = buildSystemPrompt('ecs');
    expect(prompt).toContain('Path B');
    expect(prompt).toContain('阿里云');
    // 不应该包含 Path A 章节
    expect(prompt).not.toContain('Path A · 本地 Docker 部署脚本');
  });

  it('path=null 时省略所有 path-* section，只剩 4 篇通用 prompt', () => {
    const prompt = buildSystemPrompt(null);
    expect(prompt).toContain('vibe-niuma 部署助手');
    expect(prompt).toContain('Action 协议');
    expect(prompt).toContain('正例对话片段');
    expect(prompt).toContain('反例');
    expect(prompt).not.toContain('Path A · 本地 Docker 部署脚本');
    expect(prompt).not.toContain('Path B · 阿里云 ECS 部署脚本');
  });

  it('节与节之间用 \\n\\n---\\n\\n 分隔', () => {
    const localPrompt = buildSystemPrompt('local');
    const nullPrompt = buildSystemPrompt(null);
    // local 比 null 多 1 段（path-local），所以多至少 1 个加入分隔符。
    // 注意 markdown 内部本身就含 `---` 水平线，所以不能断言精确数；
    // 用「local 严格多于 null」来证明 join 时确实多塞了一个分隔符。
    const localCount = localPrompt.split('\n\n---\n\n').length - 1;
    const nullCount = nullPrompt.split('\n\n---\n\n').length - 1;
    expect(localCount).toBeGreaterThan(nullCount);
  });
});
