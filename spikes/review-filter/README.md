# review-filter spike

§9.10 第一层的可运行参考实现 —— **已实测跑通**，是 `CodeReviewAdapter` 过滤/合并层的种子。

## 为什么需要它

`ocr` 内置的 `review_filter_task` 在 DeepSeek 端点上 **100% 失败**（HTTP 400），
因为它要 `response_format: json_schema` 而 DeepSeek 不提供（§6.3）。

更麻烦的是**失败是静默的**：`status: "complete"`、退出码 0、`coverage.failed: []`，
唯一痕迹埋在 `retry_report.failed_requests` 里（§9.7 ②）。

好在**失败是 fail-open** —— 发现不丢，只是没降噪。所以把过滤拿回自己手里就解决了。

而且我们**本来就需要这一层**：OCR 的缺陷轴与 `code-review` 的规格轴要合并去重，
那就是同一个落点。

## 实测结果

2026-08-24，`deepseek-v4-pro` + `response_format: json_object`，
输入为 §9.6 两跑产出的 5 条未过滤发现：

| 裁决 | 发现 | 理由 |
|---|---|---|
| 丢弃 | 硬编码 endpoint 路径（maintainability） | 纯维护性建议，无具体失败场景 |
| **保留** | `webhookUrl` 未 trim（bug/low） | 空白串绕过禁用逻辑 |
| 丢弃 | 测试未断言 `link_url`（test/low） | 测试改进，非实际缺陷 |
| **保留** | `alert.py:90` JSONDecodeError（bug/medium） | 破坏 `AlertError` 契约 |
| **保留** | `alert.py:117` 同上 | 同上 |

**3 条真 bug 全留、2 条弱发现全丢、置信度全 `high`**，5,057 token（约 1k/条）。
相对两跑 review 本身的 246k token 是零头。

## 用法

```bash
ocr review --repo <repo> --commit <sha> --format json --audience agent > review.json
OCR_FILTER_API_KEY=sk-... python3 filter.py review.json
```

环境变量：`OCR_FILTER_API_KEY`（必填）、`OCR_FILTER_ENDPOINT`、`OCR_FILTER_MODEL`。

## 两条要带进正式实现的约束

1. **必须读 `retry_report.failed_requests`**，`> 0` 一律当降级运行上报。
   不能只看 `status` 和退出码。

2. **结构化输出只用 `response_format: json_object`**（§6.3 平台规约）。
   目标模型既不支持 `json_schema`，也不支持强制 `tool_choice`（thinking 模式限制）。

## 可调的旋钮

`SYSTEM` 里的判据。收紧会丢掉真发现，放松会让审核页被噪音淹没然后被人整体忽略。
上面那条「测试未断言 link_url」被丢是**策略决定不是错误** —— 判据写的是
「没有具体失败场景就丢」。这个旋钮现在在我们手上。
