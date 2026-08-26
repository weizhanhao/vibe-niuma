import pytest
import asyncio
import json

import httpx
import pytest

from vplatform.review.adapter import AXIS_DEFECT, AXIS_SPEC, Finding, ReviewError, ReviewResult
from vplatform.review.filter import FindingFilter, gate_decision, merge_axes
from vplatform.review.ocr import build_background, parse_ocr_json


# ── ocr 输出解析 ─────────────────────────────────────────────────
OCR_OK = json.dumps({
    "status": "complete",
    "summary": {"files_reviewed": 3, "comments": 2, "total_tokens": 142900, "elapsed": "3m48s"},
    "session_id": "sess-1",
    "comments": [
        {"path": "app/tasks/export_job.py", "start_line": 38, "end_line": 40,
         "category": "correctness", "severity": "high",
         "content": "分片写入失败时 job 状态停在 running",
         "existing_code": "await store.mark(...)", "suggestion_code": "try: ..."},
        {"path": "src/routes/export.ts", "start_line": 64, "end_line": 64,
         "category": "security", "severity": "medium", "content": "下载链接签名没设过期"},
    ],
    "retry_report": {"total_requests": 15, "failed_requests": 1,
                     "requests": [{"task_type": "review_filter_task", "outcome": "failed"}]},
})

OCR_CLEAN = json.dumps({
    "status": "complete",
    "summary": {"files_reviewed": 3, "comments": 0, "total_tokens": 165732, "elapsed": "4m57s"},
    "comments": [],
    # 注意：**没有 retry_report** —— 无失败时 ocr 整个键都不输出
})


def test_parse_extracts_findings_and_degraded_flag():
    r = parse_ocr_json(OCR_OK)
    assert len(r.findings) == 2
    assert r.findings[0].severity == "high"
    assert r.findings[0].axis == AXIS_DEFECT
    assert r.tokens == 142900
    # §9.7 ②：status=complete + 退出码 0 也可能有失败请求，必须暴露
    assert r.failed_requests == 1 and r.degraded is True


def test_missing_retry_report_means_zero_failures_not_unknown():
    """无失败时 ocr 不输出 retry_report。缺失要当零失败，
    否则每次成功都会被误报成降级运行。"""
    r = parse_ocr_json(OCR_CLEAN)
    assert r.failed_requests == 0
    assert r.degraded is False
    assert r.findings == []


def test_parse_rejects_garbage():
    with pytest.raises(ReviewError, match="不是合法 JSON"):
        parse_ocr_json("not json at all")


def test_background_carries_contracts_and_clarifications():
    """--background 是质量杠杆：reviewer 要知道这次改动本来该做什么。"""
    bg = build_background(
        title="订单导出支持自定义字段",
        body="财务每月手工加工，希望能自己勾字段",
        clarifications=[{"question": "要模板吗", "answer": "先不要"}],
        contracts=["POST /export/orders {fields} → {jobId}"],
    )
    assert "订单导出支持自定义字段" in bg
    assert "先不要" in bg
    assert "POST /export/orders" in bg
    assert "只报本次 diff 触及的行" in bg


# ── 过滤层 ───────────────────────────────────────────────────────
def _mk(axis=AXIS_DEFECT, sev="medium", path="a.py", line=1, claim="x"):
    return Finding(axis=axis, severity=sev, category="bug", path=path,
                   start_line=line, claim=claim)


def _filter_with(responses):
    """responses: 按调用顺序返回的裁决 dict，或抛出的异常。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = calls["n"]; calls["n"] += 1
        item = responses[min(i, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return httpx.Response(200, json={"choices": [
            {"message": {"content": json.dumps(item, ensure_ascii=False)}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FindingFilter(endpoint="http://x/v1/chat/completions", api_key="k",
                         model="m", client=client), calls


def test_filter_keeps_real_bugs_drops_style_notes():
    """复刻实测行为：有失败场景的留，纯维护性建议丢。"""
    f, _ = _filter_with([
        {"keep": True, "confidence": "high", "severity": "high", "reason": "破坏错误契约"},
        {"keep": False, "confidence": "high", "severity": "low", "reason": "纯维护性建议"},
    ])
    out = asyncio.run(f.apply([_mk(claim="JSONDecodeError 逃逸"), _mk(claim="硬编码路径")]))
    kept = [x for x in out if x.kept]
    assert len(kept) == 1 and "逃逸" in kept[0].claim
    assert kept[0].severity == "high"          # 裁决可以改级
    assert out[1].verdict_reason == "纯维护性建议"


def test_filter_failure_is_fail_open():
    """过滤器抽风不能把真 bug 吞掉 —— 保守保留。"""
    f, _ = _filter_with([httpx.ConnectError("boom")])
    out = asyncio.run(f.apply([_mk(claim="真 bug")]))
    assert out[0].kept is True
    assert "裁决失败" in out[0].verdict_reason


def test_filter_runs_concurrently_but_bounded():
    f, calls = _filter_with([{"keep": True, "confidence": "high",
                             "severity": "low", "reason": "ok"}])
    out = asyncio.run(f.apply([_mk(line=i) for i in range(10)]))
    assert len(out) == 10 and calls["n"] == 10


# ── 多轴合并 ─────────────────────────────────────────────────────
def test_merge_dedupes_same_anchor_keeping_higher_severity():
    """同一处被两个轴报出 → 合成一条，不给人看两条几乎一样的。"""
    a = ReviewResult(findings=[_mk(AXIS_DEFECT, "low", "x.py", 10, "缺陷轴说法")])
    b = ReviewResult(findings=[_mk(AXIS_SPEC, "high", "x.py", 10, "规格轴说法")])
    merged = merge_axes(a, b)
    assert len(merged) == 1
    assert merged[0].severity == "high" and merged[0].axis == AXIS_SPEC
    assert "另见 defect 轴" in merged[0].claim


def test_merge_sorts_by_severity_desc():
    r = ReviewResult(findings=[_mk(sev="low", line=1), _mk(sev="critical", line=2),
                               _mk(sev="medium", line=3)])
    assert [f.severity for f in merge_axes(r)] == ["critical", "medium", "low"]


def test_gate_blocks_only_on_critical_by_default():
    assert gate_decision([_mk(sev="high"), _mk(sev="medium", line=2)]) == "pass"
    assert gate_decision([_mk(sev="critical")]) == "block"


def test_gate_ignores_dropped_findings():
    """被过滤掉的发现不参与卡门槛 —— 否则过滤等于白做。"""
    dropped = _mk(sev="critical"); dropped.kept = False
    assert gate_decision([dropped]) == "pass"


# ── M8 回归：过滤层的 fail-open 必须兜住所有异常 ────────────────
def test_filter_survives_non_object_json():
    """模型返回合法 JSON 但不是对象时，不能把整个复核环节炸掉。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [
            {"message": {"content": '"just a string"'}}]})

    f = FindingFilter(endpoint="http://x", api_key="k", model="m",
                      client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = asyncio.run(f.apply([_mk(claim="真 bug")]))
    assert out[0].kept is True                       # fail-open
    assert "裁决" in out[0].verdict_reason


def test_filter_handles_string_boolean():
    """`bool("false")` 是 True —— 模型返回字符串时该丢的会被保留。"""
    f, _ = _filter_with([{"keep": "false", "confidence": "high",
                          "severity": "low", "reason": "风格建议"}])
    out = asyncio.run(f.apply([_mk()]))
    assert out[0].kept is False


def test_filter_one_bad_verdict_does_not_kill_the_batch():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"choices": [{"message": {"content":
            '{"keep": false, "confidence": "high", "severity": "low", "reason": "ok"}'}}]})

    f = FindingFilter(endpoint="http://x", api_key="k", model="m",
                      client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = asyncio.run(f.apply([_mk(line=1), _mk(line=2)]))
    assert len(out) == 2
    assert out[0].kept is True          # 失败的那条 fail-open
    assert out[1].kept is False         # 正常的那条照常裁决


# ── 工具没装 ≠ 复核不通过 ────────────────────────────────────────
def test_missing_ocr_binary_is_reported_not_crashed():
    """**没装就明说，别抛裸的 FileNotFoundError。**

    `No such file or directory: 'ocr'` 会让整条需求判失败，而报错跟
    「AI 复核」毫无关系 —— 排查的人得翻栈才知道是少了个命令行工具。
    实测走真需求时撞到过：实现和验证都过了，栽在这儿。
    """
    import asyncio

    from vplatform.review.adapter import ReviewNotInstalled
    from vplatform.review.ocr import OcrReviewAdapter

    a = OcrReviewAdapter(binary="绝对不存在的命令-xyz")
    with pytest.raises(ReviewNotInstalled, match="没找到复核工具"):
        asyncio.run(a.review(repo_path="/tmp", base="main", head="x"))


def test_ai_review_skips_instead_of_failing_when_not_installed(session, project):
    """跳过要如实写进结论，且**不能阻断需求** —— 人工审核照常进行。"""
    from vplatform.core.models import Requirement, Run, Task, Workspace, \
        next_requirement_seq
    from vplatform.orchestration.dag import default_pipeline
    from vplatform.orchestration.handlers import Capabilities
    from vplatform.orchestration.stages import StageRunner
    from vplatform.review.adapter import ReviewNotInstalled
    import asyncio
    from pathlib import Path

    r = Requirement(project_id=project.id, seq=next_requirement_seq(session, project.id),
                    title="x", requested_by="chen", stage="ai_review")
    session.add(r); session.flush()
    t = Task(project_id=project.id, requirement_id=r.id, key="T1", title="x",
             state="done")
    session.add(t); session.flush()
    run = Run(project_id=project.id, task_id=t.id, branch="cr/1-t1", state="done",
              commit_shas={"api": "abc"})
    session.add(run); session.flush()
    session.add(Workspace(project_id=project.id, run_id=run.id, path="/tmp",
                          state="ready", repos={"api": "/tmp"}))
    session.flush()

    class Reviewer:
        async def review(self, **kw):
            raise ReviewNotInstalled("没找到复核工具 `ocr` —— 这一环跳过。")

    class WS:
        async def exec(self, ws, argv, **kw):
            from vplatform.workspace.provider import ExecResult
            return ExecResult(0, "abc", "")

    out = asyncio.run(StageRunner(Capabilities(reviewer=Reviewer(), workspace=WS()),
                                  session).ai_review(default_pipeline().get("ai_review"), r))
    assert out.ok is True, "工具没装把需求判失败了"
    assert out.data.get("skipped") is True
