"""ui_label_extractor 单测 —— 锁住「实时从前端源码 grep UI 标签」契约。

防回归的关键场景：
- 真实 OrderTable.tsx 风格的 <th> 能被抓出
- placeholder / aria-label 等属性能被抓出
- 模板插值 / 变量名 / 英文字段名 必须**不**被错抓
- diff_with_repo_doc + needs_resync 行为正确（自愈触发条件）
"""
from __future__ import annotations

from orchestrator.adapters.impl.ui_label_extractor import (
    diff_with_repo_doc,
    extract_labels_from_text,
    extract_ui_labels,
    needs_resync,
    render_ui_labels_for_prompt,
)


# ── 基础抽取 ─────────────────────────────────────────────────────────


def test_jsx_th_labels_extracted():
    src = """
    <thead><tr>
      <th style={{padding: 1}}>订单号</th>
      <th>客户</th>
      <th style={{textAlign: 'right'}}>金额</th>
    </tr></thead>
    """
    out = extract_labels_from_text(src)
    assert out == ["订单号", "客户", "金额"]


def test_placeholder_and_aria_label_attrs_extracted():
    src = '<input placeholder="搜索客户名称…" aria-label="搜索客户" />'
    out = extract_labels_from_text(src)
    assert "搜索客户名称…" in out
    assert "搜索客户" in out


def test_chinese_literal_in_quotes_extracted():
    src = 'const tip = "请输入金额"; return <div>{tip}</div>;'
    out = extract_labels_from_text(src)
    assert "请输入金额" in out


# ── 必须**不**被抓的反例（防误报） ────────────────────────────────


def test_english_identifiers_not_extracted():
    src = 'const x = "order_status"; const y = "page-1";'
    assert extract_labels_from_text(src) == []


def test_template_interpolation_not_extracted():
    src = 'const msg = `客户 ${name} 的订单`;'
    out = extract_labels_from_text(src)
    assert "客户 ${name} 的订单" not in out


def test_jsx_text_with_inner_expression_skipped():
    # <p>状态：{order.status}</p> —— 含 {} 表达式，整段不抓
    # 这是 intentional：让 vision + AGENTS.md 兜底，避免误抓「状态：」当 UI 列名
    src = '<p>状态：{order.status}</p>'
    out = extract_labels_from_text(src)
    assert out == []


def test_overlong_strings_dropped():
    src = '<p>这是一段非常长的描述文字超过了四十个字符的边界因此必须被丢弃否则会污染 prompt</p>'
    out = extract_labels_from_text(src)
    assert out == []


# ── multi-file aggregation ─────────────────────────────────────────


def test_extract_ui_labels_aggregates_and_filters_empty():
    files = {
        "OrderTable.tsx": "<th>订单号</th><th>客户</th>",
        "EmptyFile.tsx": "const x = 1;",
        "OrderList.tsx": '<input placeholder="搜索"/>',
    }
    out = extract_ui_labels(files)
    assert set(out.keys()) == {"OrderTable.tsx", "OrderList.tsx"}
    assert "EmptyFile.tsx" not in out


# ── prompt 渲染 ─────────────────────────────────────────────────────


def test_render_for_prompt_format():
    by_file = {
        "B.tsx": ["b1", "b2"],
        "A.tsx": ["a1"],
    }
    txt = render_ui_labels_for_prompt(by_file)
    assert txt.index("A.tsx") < txt.index("B.tsx")
    assert "  - a1" in txt
    assert "  - b1" in txt


def test_render_empty_falls_back_to_hint():
    txt = render_ui_labels_for_prompt({})
    assert "没匹配到" in txt


# ── 自愈触发（diff + needs_resync） ──────────────────────────────


def test_diff_with_repo_doc_finds_missing():
    by_file = {"X.tsx": ["金额", "客户", "订单号"]}
    # doc 里只提了客户/订单号；提的「订单总额」不算「金额」
    repo_doc = "AGENTS.md 写了：客户、订单号、订单总额"
    missing, _ = diff_with_repo_doc(by_file, repo_doc)
    assert missing == {"金额"}


def test_needs_resync_when_doc_empty():
    """首次部署 AGENTS.md 还没生成时，第一次 brainstorm 应触发 init。"""
    assert needs_resync({"X.tsx": ["金额"]}, "") is True


def test_needs_resync_false_when_all_labels_in_doc():
    by_file = {"X.tsx": ["金额", "客户"]}
    repo_doc = "页面有 金额 和 客户 两列"
    assert needs_resync(by_file, repo_doc) is False


def test_needs_resync_true_when_one_label_missing():
    by_file = {"X.tsx": ["金额", "客户", "新加的列"]}
    repo_doc = "页面有 金额 和 客户"
    assert needs_resync(by_file, repo_doc) is True


# ── 真实事故的反例锁（防回归到「订单总金额」幻觉） ──────────────


def test_real_ordertable_does_not_yield_db_term():
    """OrderTable.tsx 真实代码：列名叫「金额」不叫「订单总金额」。
    extractor 必须只抓「金额」，不能错抓 DB 字段中文注释。"""
    src = (
        '<th style={{padding: \'var(--space-2) var(--space-3)\', '
        'fontWeight: 500, textAlign: \'right\'}}>金额</th>'
    )
    out = extract_labels_from_text(src)
    assert "金额" in out
    assert "订单总金额" not in out
