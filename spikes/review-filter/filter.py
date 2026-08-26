"""Review 发现的过滤/合并层 —— §9.10 第一层的可运行参考实现。

由来：实测发现 OCR 内置的 review_filter_task 在 DeepSeek 端点上 100% 失败
（HTTP 400，见 §9.7 ①），且失败是静默 fail-open。这一层把过滤拿回自己手里。

结构化输出走 response_format=json_object —— §6.3 的平台规约：
目标模型不支持 json_schema，也不支持强制 tool_choice。

实测结果（2026-08-24，deepseek-v4-pro，输入为 §9.6 两跑的 5 条未过滤发现）：
    保留 3（全是真 bug）· 丢弃 2（维护性建议 + 测试改进）
    置信度全 high · 5,057 token（约 1k/条）

用法：
    OCR_FILTER_API_KEY=sk-... python3 filter.py review.json [more.json ...]
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

ENDPOINT = os.environ.get("OCR_FILTER_ENDPOINT", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("OCR_FILTER_MODEL", "deepseek-v4-pro")
API_KEY = os.environ.get("OCR_FILTER_API_KEY", "")

# 判据是可调旋钮 —— 收紧会丢掉真发现，放松会让审核页被噪音淹没然后被人整体忽略。
SYSTEM = """你是代码审查发现的裁决者。别人给你一条 AI 审查发现，你判断它值不值得放到人工审核页上。

丢弃的标准（任意一条命中就丢）：
- 无法给出具体失败场景，只是"最佳实践"或风格偏好
- 描述的问题在给出的代码里并不成立
- 纯粹的重构建议，没有正确性/安全/性能后果

保留的标准：
- 能说清「什么输入/状态 → 什么错误结果」
- 有正确性、安全或数据完整性后果

默认怀疑。拿不准就丢弃 —— 审核页被假阳性淹没，人就会开始全部忽略。

只输出一个 json 对象，不要别的：
{"keep": bool, "confidence": "high|medium|low",
 "severity": "critical|high|medium|low", "reason": "一句话说明"}"""


class FilterError(RuntimeError):
    """本模块的失败一律以此暴露 —— 不让底层 HTTP / 解析异常泄漏给调用方。"""


def judge(finding: dict) -> tuple[dict, dict]:
    """对单条发现给出裁决。返回 (verdict, usage)。"""
    if not API_KEY:
        raise FilterError("OCR_FILTER_API_KEY 未设置")
    payload = json.dumps({
        "model": MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": (
                f"文件：{finding.get('path')}:{finding.get('start_line')}\n"
                f"分类：{finding.get('category')}  原始 severity：{finding.get('severity')}\n"
                f"涉及代码：\n{finding.get('existing_code') or '(未提供)'}\n\n"
                f"发现内容：\n{finding.get('content')}"
            )},
        ],
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        ENDPOINT, data=payload,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.load(resp)
        verdict = json.loads(body["choices"][0]["message"]["content"])
    except Exception as exc:  # noqa: BLE001 —— 统一收口成 FilterError
        raise FilterError(f"裁决失败: {exc}") from exc
    return verdict, body.get("usage", {})


def load_ocr_output(path: str) -> tuple[list[dict], int]:
    """读 `ocr review --format json` 的输出。

    返回 (comments, failed_requests)。**failed_requests > 0 必须当降级运行上报** ——
    §9.7 ②：OCR 在 3/10 请求失败时仍返回 status=complete、退出码 0、coverage.failed=[]，
    唯一痕迹在 retry_report 里。只看退出码等于蒙眼跑。
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    failed = int(data.get("retry_report", {}).get("failed_requests", 0))
    return data.get("comments", []), failed


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    findings: list[dict] = []
    degraded = 0
    for path in argv:
        comments, failed = load_ocr_output(path)
        degraded += failed
        for c in comments:
            c["_source"] = path
        findings.extend(comments)

    if degraded:
        print(f"⚠ 降级运行：上游有 {degraded} 个请求失败（retry_report.failed_requests）\n")

    tin = tout = 0
    kept: list[dict] = []
    for f in findings:
        verdict, usage = judge(f)
        tin += usage.get("prompt_tokens", 0)
        tout += usage.get("completion_tokens", 0)
        mark = "保留" if verdict["keep"] else "丢弃"
        print(f"[{mark}] {f.get('path')}:{f.get('start_line')} "
              f"({f.get('category')}/{f.get('severity')} → {verdict['severity']}, "
              f"置信 {verdict['confidence']})")
        print(f"       {verdict['reason']}")
        if verdict["keep"]:
            f["_verdict"] = verdict
            kept.append(f)

    print(f"\n保留 {len(kept)} · 丢弃 {len(findings) - len(kept)}"
          f"   token 入 {tin:,} / 出 {tout:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
