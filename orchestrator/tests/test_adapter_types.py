from orchestrator.adapters.types import (
    BuildResult,
    DevContext,
    HtmlMockup,
    LocateResult,
    PreviewInstance,
    RawRequest,
    RequestBrief,
    RunResult,
    VariantSelection,
)


def test_raw_request_fields():
    r = RawRequest(
        url="http://x/orders",
        screenshot_b64="abc",
        box_coords={"x": 1, "y": 2, "width": 3, "height": 4},
        viewport={"width": 1280, "height": 800},
        request_text="把按钮改成蓝色",
    )
    assert r.url == "http://x/orders"
    assert r.box_coords["width"] == 3


def test_request_brief_defaults():
    b = RequestBrief(original_text="改个颜色")
    assert b.selected_mockup is None
    assert b.clarifications == []


def test_locate_result_empty_means_not_found():
    miss = LocateResult(entry_files=[], route_path="")
    hit = LocateResult(entry_files=["src/pages/OrderList.tsx"], route_path="/orders")
    assert miss.entry_files == []
    assert hit.entry_files == ["src/pages/OrderList.tsx"]


def test_dev_context_composes_brief_and_locate():
    brief = RequestBrief(original_text="x")
    loc = LocateResult(entry_files=["a.tsx"], route_path="/a")
    ctx = DevContext(
        brief=brief, locate_result=loc, screenshot_b64="img", box_coords={}
    )
    assert ctx.brief is brief
    assert ctx.locate_result is loc


def test_run_build_preview_result_fields():
    assert RunResult(changed=True, commit_sha="abc123", log="ok").changed is True
    assert BuildResult(ok=False, log="boom").ok is False
    pi = PreviewInstance(preview_id="p1", url="http://x:5101", handle="container-1")
    assert pi.url == "http://x:5101"


def test_html_mockup_and_variant_selection():
    m = HtmlMockup(id="v1", title="方案一", html="<div/>")
    assert m.id == "v1"
    assert VariantSelection(selected_id="v1").selected_id == "v1"
    assert VariantSelection(selected_id=None).selected_id is None
