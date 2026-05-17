"""ui_label_extractor —— 从前端源码 grep 出业务员**真实能在 UI 上看到的标签**。

为什么需要：
- AGENTS.md 是个快照，业务员每次改 UI 后会过期
- brainstorm 时如果 AI 凭 AGENTS.md 给 options，可能给出业务员页面上没有的字
  （真实事故：「订单列表页的『订单总金额』列」—— 页面上叫「金额」）
- 解法：每次 brainstorm 前实时从源码 grep 真 UI label，覆盖 AGENTS.md 旧映射

输入：dict[文件名 → 源码文本]
输出：dict[文件名 → 去重后的中文 UI label 列表]

设计：宁缺勿滥。只抓**含中文字符** + **长度 1-40** + **静态字面量**（无模板插值）
的标签，确保抓到的都是业务员真能看到的字。
"""
from __future__ import annotations

import re


# JSX 元素纯文本：<th>订单号</th>、<button>合并</button> 等
# 关键约束：开 tag 之后到闭 tag 之前**不含** < > { } —— 避开嵌套 / 表达式
_JSX_TEXT_TAGS = (
    "th", "td", "label", "button", "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "span", "a", "li", "option", "legend", "caption", "summary",
)
_JSX_TEXT_RE = re.compile(
    r"<(" + "|".join(_JSX_TEXT_TAGS) + r")\b[^>]*>\s*([^<>{}\n]+?)\s*</\1>",
    re.IGNORECASE,
)

# 属性形式：placeholder="搜索..." / aria-label="状态" / title / alt
_ATTR_RE = re.compile(
    r"""\b(placeholder|aria-label|title|alt)\s*=\s*["']([^"'\n]+)["']""",
    re.IGNORECASE,
)

# 字面量中文字符串：必须**首尾都是中文字符**才认（避开 "page-1"、"order_status"）
_LITERAL_RE = re.compile(
    r"""["']([一-鿿][^"'\n]*?[一-鿿、。！？])["']""",
)

# 噪声过滤：模板插值、转义、过长等
_DYNAMIC_MARKER_RE = re.compile(r"[\${}\\]|\.\.\.")


def _has_chinese(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


def _clean_label(raw: str) -> str | None:
    """Normalize a raw matched fragment; return None if it should be dropped."""
    s = raw.strip()
    if not s or len(s) > 40:
        return None
    if _DYNAMIC_MARKER_RE.search(s):
        return None
    if not _has_chinese(s):
        return None
    return s


def extract_labels_from_text(text: str) -> list[str]:
    """从单个文件源码里抓所有 UI 候选 label。结果按出现顺序去重。"""
    found: list[str] = []
    seen: set[str] = set()

    def _push(raw: str) -> None:
        cleaned = _clean_label(raw)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            found.append(cleaned)

    for m in _JSX_TEXT_RE.finditer(text):
        _push(m.group(2))
    for m in _ATTR_RE.finditer(text):
        _push(m.group(2))
    for m in _LITERAL_RE.finditer(text):
        _push(m.group(1))

    return found


def extract_ui_labels(files: dict[str, str]) -> dict[str, list[str]]:
    """主入口：扫一组文件，返回 {文件名: [labels]} 字典。

    空 labels 的文件会被过滤掉（reduce prompt noise）。
    """
    out: dict[str, list[str]] = {}
    for name, content in files.items():
        labels = extract_labels_from_text(content)
        if labels:
            out[name] = labels
    return out


def render_ui_labels_for_prompt(by_file: dict[str, list[str]]) -> str:
    """把 extract_ui_labels 的结果序列化成喂给 LLM 的紧凑文本。

    格式（紧凑，节省 token）：
        frontend/src/pages/OrderList.tsx:
          - 订单号
          - 客户
          - 金额
    """
    if not by_file:
        return "（这次 brainstorm 没匹配到当前 URL 对应的源码，按 screen_context / repo_doc 判断）"
    lines: list[str] = []
    for name in sorted(by_file.keys()):
        lines.append(f"{name}:")
        for label in by_file[name]:
            lines.append(f"  - {label}")
    return "\n".join(lines)


def diff_with_repo_doc(
    by_file: dict[str, list[str]], repo_doc: str,
) -> tuple[set[str], set[str]]:
    """对比实时 UI labels 跟 AGENTS.md 文本里出现的 label。

    返回 (in_ui_not_in_doc, in_doc_for_these_files_but_not_in_ui)
    第二个方向需要 doc 结构化解析才能可靠判断；第一版只返空集，后续再补
    """
    all_ui_labels: set[str] = set()
    for labels in by_file.values():
        all_ui_labels.update(labels)
    in_ui_not_in_doc = {
        label for label in all_ui_labels
        if label not in repo_doc
    }
    return in_ui_not_in_doc, set()


def needs_resync(
    by_file: dict[str, list[str]], repo_doc: str, *, min_missing: int = 1,
) -> bool:
    """是否值得异步触发 RepoInitializer.ensure(force=True) 重写 AGENTS.md。

    心智：哪怕只漏了一个新 label，重新 init 一次也比让 AGENTS.md 长期偏离强。
    repo_doc 为空（还没 init 过）也返 True —— 让首次 brainstorm 触发 init。
    """
    if not repo_doc.strip():
        return True
    missing, _ = diff_with_repo_doc(by_file, repo_doc)
    return len(missing) >= min_missing
