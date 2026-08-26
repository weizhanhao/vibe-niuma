"""过滤 + 合并层（§9.10 第一层）—— **已实测跑通**。

为什么这层必须存在，两个理由叠加：

1. ocr 内置过滤在 DeepSeek 端点上 100% 静默失效（§9.7 ①）；即便换到 DashScope
   能用，它也贵 17 倍（85k vs ~1k token 做同一件事，§9.11）。
2. **我们本来就需要一个合并点** —— 缺陷轴（ocr）与规格轴/规范轴（code-review
   skill）的发现要去重、统一定级，那就是同一个落点。

结构化输出走 `response_format: json_object`（§6.3 平台规约）：
目标模型不支持 `json_schema`，也不支持强制 `tool_choice`（thinking 模式限制）。

实测（deepseek-v4-pro，5 条真实发现）：留 3 丢 2，置信度全 high，5,057 token。
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from vplatform.review.adapter import Finding, ReviewResult

logger = logging.getLogger(__name__)

# 判据是**可调旋钮**：收紧会丢真发现，放松会让审核页被噪音淹没然后被人整体忽略。
SYSTEM_PROMPT = """你是代码审查发现的裁决者。别人给你一条 AI 审查发现，你判断它值不值得放到人工审核页上。

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
    """本层失败统一以此暴露。"""


class FindingFilter:
    def __init__(self, *, endpoint: str, api_key: str, model: str,
                 client: httpx.AsyncClient | None = None, concurrency: int = 4,
                 timeout: float = 180):
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self._client = client
        self.concurrency = concurrency
        self.timeout = timeout

    def _c(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def _judge(self, f: Finding) -> dict:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"文件：{f.path}:{f.start_line}\n"
                    f"轴：{f.axis}  分类：{f.category}  原始 severity：{f.severity}\n"
                    f"涉及代码：\n{f.existing_code or '(未提供)'}\n\n"
                    f"发现内容：\n{f.claim}\n{f.failure_scenario}"
                )},
            ],
            # §6.3：json_object 是兼容性下限，任何端点都能跑
            "response_format": {"type": "json_object"},
        }
        try:
            r = await self._c().post(
                self.endpoint, json=body,
                headers={"Authorization": f"Bearer {self.api_key}"})
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            verdict = json.loads(content)
        except Exception as exc:  # noqa: BLE001
            # **兜住所有异常**。之前只捕四种，模型返回合法 JSON 但不是对象
            # （`"ok"` / `[...]` / `123`）时 v.get() 抛 AttributeError，
            # 一路冒到 handler 把整个复核环节炸掉 —— 与"不能因为过滤器抽风
            # 就把真 bug 吞掉"的意图正好相反。
            raise FilterError(f"裁决失败 {f.anchor}: {type(exc).__name__}: {exc}") from exc
        if not isinstance(verdict, dict):
            raise FilterError(f"裁决格式非法 {f.anchor}: 期望对象，得到 {type(verdict).__name__}")
        return verdict

    async def apply(self, findings: list[Finding]) -> list[Finding]:
        """逐条裁决，写回 kept / verdict_reason / confidence。

        **单条裁决失败 fail-open**（保留该发现），不能因为过滤器抽风就把真 bug 吞掉。
        """
        sem = asyncio.Semaphore(self.concurrency)

        async def one(f: Finding) -> Finding:
            async with sem:
                try:
                    v = await self._judge(f)
                except FilterError as exc:
                    logger.warning("%s —— fail-open 保留该发现", exc)
                    f.verdict_reason = f"裁决失败，保守保留：{exc}"
                    f.kept = True
                    return f
            # `bool("false")` 是 True —— 模型返回字符串时该丢的会被保留
            keep = v.get("keep", True)
            f.kept = keep if isinstance(keep, bool) else \
                str(keep).strip().lower() not in ("false", "0", "no", "否")
            f.confidence = str(v.get("confidence", ""))
            f.verdict_reason = str(v.get("reason", ""))
            if v.get("severity"):
                f.severity = str(v["severity"]).lower()
            return f

        # return_exceptions：单条裁决把整批炸掉是最坏的结果
        out = await asyncio.gather(*(one(f) for f in findings),
                                   return_exceptions=True)
        result: list[Finding] = []
        for f, r in zip(findings, out):
            if isinstance(r, BaseException):
                logger.warning("裁决 %s 异常，fail-open 保留：%s", f.anchor, r)
                f.kept = True
                f.verdict_reason = f"裁决异常，保守保留：{r}"
                result.append(f)
            else:
                result.append(r)
        return result

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_SEV_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}


def merge_axes(*results: ReviewResult) -> list[Finding]:
    """合并多轴发现并去重。

    同一处（文件 + 行）被两个轴同时报出时，保留 severity 更高的那条，
    并把另一轴的说明并进来 —— 而不是给人看两条几乎一样的。
    """
    merged: dict[tuple[str, int], Finding] = {}
    for res in results:
        for f in res.findings:
            key = (f.path, f.start_line)
            cur = merged.get(key)
            if cur is None:
                merged[key] = f
                continue
            if _SEV_RANK.get(f.severity, 0) > _SEV_RANK.get(cur.severity, 0):
                f.claim = f"{f.claim}\n[另见 {cur.axis} 轴] {cur.claim}"
                merged[key] = f
            else:
                cur.claim = f"{cur.claim}\n[另见 {f.axis} 轴] {f.claim}"
    return sorted(merged.values(),
                  key=lambda x: (-_SEV_RANK.get(x.severity, 0), x.path, x.start_line))


def gate_decision(findings: list[Finding], *, block_on: tuple[str, ...] = ("critical",)) -> str:
    """审核门槛。返回 "block"（自动打回 coder）或 "pass"（进人工审核）。

    默认只有 critical 打回 —— 其余作为审核页上的参考，不阻塞人。
    """
    kept = [f for f in findings if f.kept]
    return "block" if any(f.severity in block_on for f in kept) else "pass"
