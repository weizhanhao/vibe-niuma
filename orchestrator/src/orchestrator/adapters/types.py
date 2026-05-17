"""adapter 层的共享数据类型 —— 跨计划契约。Plan 3 的真实 adapter 实现必须遵守这些类型。"""
from dataclasses import dataclass, field
from typing import Awaitable, Callable

# Phase F：dev runner / adapter 把进度文字推回扩展的回调签名。
# Pipeline 注入一个绑定 request_id+phase 的闭包；fake / 缺省时是 no-op。
LogSink = Callable[[str], Awaitable[None]]


async def _noop_log(_: str) -> None:
    """默认 log sink：什么都不做。"""
    return None


@dataclass
class RawRequest:
    """业务员的原始捕获 —— 扩展 POST 上来的负载。"""

    url: str
    screenshot_b64: str
    box_coords: dict
    viewport: dict
    request_text: str


@dataclass
class HtmlMockup:
    """澄清「重路径」生成的一套轻量 HTML 方案，仅用于传达意图。"""

    id: str
    title: str
    html: str


@dataclass
class VariantSelection:
    """业务员对 HTML 方案的选择；selected_id 为 None 表示全否。"""

    selected_id: str | None


@dataclass
class RequestBrief:
    """澄清产出的业务级需求。"""

    original_text: str
    clarifications: list[dict] = field(default_factory=list)
    selected_mockup: HtmlMockup | None = None


@dataclass
class LocateResult:
    """URL → 源码定位结果；entry_files 为空表示定位失败。"""

    entry_files: list[str]
    route_path: str


@dataclass
class DevContext:
    """组装给 dev runner 的上下文包。

    `entry_file_contents` 在 Plan 3 加入：StackAdapter.context_pack 把入口源文件
    （及一层本地 import）的内容读出来塞进去，省得 DevRunner 再去读盘 / 猜路径。

    Phase F：`log` 是 Pipeline 注入的回调；dev runner 把子进程 stdout/stderr
    每行喂给它，实时推到扩展 SSE。缺省 no-op（测试/单元场景不强制注入）。
    """

    brief: RequestBrief
    locate_result: LocateResult
    screenshot_b64: str
    box_coords: dict
    entry_file_contents: dict[str, str] = field(default_factory=dict)
    log: LogSink = field(default=_noop_log)
    # Plan 9 Task 5：conversation 历史（compaction 后的视图），dev runner 用来
    # 维持跨 CR 上下文连续。条目形如 {type: user|ai|summary, ts, content, ...}
    chat_history: list[dict] = field(default_factory=list)
    # Plan 10 Task 5+6：refine_cr 路径下 'refine'；new_cr / 缺省时 'new'
    mode: str = "new"
    # Plan 10：refine 模式下 dev_runner 在这个已存在 branch 上续 commit；new 时 None
    base_branch: str | None = None


@dataclass
class RunResult:
    """dev runner 跑完的结果；changed 为 False 表示没产出改动。"""

    changed: bool
    commit_sha: str | dict[str, str] | None  # 多仓项目用 dict[repo_name → sha]；单仓 str
    log: str


@dataclass
class BuildResult:
    """构建结果。"""

    ok: bool
    log: str


@dataclass
class PreviewInstance:
    """预览环境句柄。handle 是 adapter 内部用的标识（容器 id 等）。"""

    preview_id: str
    url: str
    handle: str
