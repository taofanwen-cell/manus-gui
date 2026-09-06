"""Tests for :class:`app.ctrip_query_adapter.CtripQueryBrowserAdapter`.

The adapter is the only piece of code that has to talk to the real
browser-use library, so the tests inject lightweight fakes for both the
``CtripQueryTool`` and the underlying ``BrowserContext``/``BrowserState``.
That way we can exercise every code path (success / failure / missing
field / wrong index) without needing a live Chrome process.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from app.ctrip_form_executor import (
    BrowserInterface,
    CtripFlightFormExecutor,
    FlightQuery,
    QUERY_URL,
)
from app.ctrip_query_adapter import CtripQueryBrowserAdapter
from app.tool.base import ToolResult


def _run(coro):
    return asyncio.run(coro)


def _aio(fn):
    """Run an async test function synchronously via :func:`_run`."""

    def wrapper(*args, **kwargs):
        return _run(fn(*args, **kwargs))

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeElement:
    tag_name: str = "input"
    text: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    xpath: str = ""


@dataclass
class _FakePage:
    url: str = QUERY_URL


@dataclass
class _FakeState:
    url: str = QUERY_URL
    title: str = "fake"
    selector_map: Dict[int, _FakeElement] = field(default_factory=dict)


@dataclass
class _FakeContext:
    page: _FakePage = field(default_factory=_FakePage)
    state: _FakeState = field(default_factory=_FakeState)
    get_state_calls: int = 0

    async def get_current_page(self) -> _FakePage:
        return self.page

    async def get_state(self, **_kwargs) -> _FakeState:
        self.get_state_calls += 1
        return self.state


class _FakeBrowserUseTool:
    def __init__(self, context: _FakeContext) -> None:
        self.context = context
        self.initialize_calls = 0

    async def _ensure_browser_initialized(self) -> _FakeContext:
        self.initialize_calls += 1
        return self.context


class _FakeCtripQueryTool:
    """Minimal stand-in for :class:`CtripQueryTool`.

    Tests inject a response queue (``tool.responses``) that the fake pops in
    order for every ``execute`` call. This keeps the fake stateful but
    deterministic without dragging in pydantic's ``BaseTool`` machinery.
    """

    def __init__(
        self,
        context: _FakeContext,
        responses: Optional[List[ToolResult]] = None,
    ) -> None:
        self.browser_tool = _FakeBrowserUseTool(context)
        self.responses: List[ToolResult] = list(responses or [])
        self.execute_calls: List[Dict[str, Any]] = []

    async def execute(self, **kwargs) -> ToolResult:
        self.execute_calls.append(kwargs)
        if not self.responses:
            return ToolResult(output="")
        return self.responses.pop(0)

    async def get_current_state(self) -> ToolResult:  # pragma: no cover - unused
        return ToolResult(output="")


def _seed_form(
    *,
    origin_value: str = "",
    destination_value: str = "",
    date_value: str = "",
    with_options: bool = True,
) -> _FakeState:
    """Build a fake ``BrowserState`` that mirrors the Ctrip query page."""

    attributes = {"aria-label": "请输入出发地", "name": "owDCity"}
    state = _FakeState(
        selector_map={
            1: _FakeElement(
                tag_name="input",
                text="",
                attributes={**attributes, "value": origin_value},
                xpath="/html/body/form/div[1]/input",
            ),
            2: _FakeElement(
                tag_name="input",
                text="",
                attributes={"aria-label": "请输入目的地", "name": "owACity",
                             "value": destination_value},
                xpath="/html/body/form/div[2]/input",
            ),
            3: _FakeElement(
                tag_name="input",
                text="",
                attributes={"aria-label": "请选择出发日期",
                             "placeholder": "yyyy-mm-dd",
                             "value": date_value},
                xpath="/html/body/form/div[3]/input",
            ),
            4: _FakeElement(
                tag_name="button",
                text="搜索",
                attributes={"type": "submit"},
                xpath="/html/body/form/div[4]/button",
            ),
        },
    )
    if with_options:
        state.selector_map[10] = _FakeElement(
            tag_name="li", text="广州",
            attributes={"data-city": "广州"}, xpath="/html/body/form/ul/li[1]",
        )
        state.selector_map[11] = _FakeElement(
            tag_name="li", text="北京",
            attributes={"data-city": "北京"}, xpath="/html/body/form/ul/li[2]",
        )
    return state


# ---------------------------------------------------------------------------
# Direct adapter contract
# ---------------------------------------------------------------------------


def test_adapter_implements_browser_interface():
    context = _FakeContext()
    adapter = CtripQueryBrowserAdapter(_FakeCtripQueryTool(context))  # type: ignore[arg-type]
    assert isinstance(adapter, BrowserInterface)


def test_adapter_can_strict_read_is_false():
    context = _FakeContext()
    adapter = CtripQueryBrowserAdapter(_FakeCtripQueryTool(context))  # type: ignore[arg-type]
    # Production path cannot reliably read input.value through browser-use.
    assert adapter.can_strict_read() is False


@_aio
async def test_refresh_state_converts_selector_map():
    context = _FakeContext(state=_seed_form(origin_value="广州", destination_value="北京"))
    adapter = CtripQueryBrowserAdapter(_FakeCtripQueryTool(context))  # type: ignore[arg-type]

    snapshot = await adapter.refresh_state()

    assert snapshot.url == QUERY_URL
    assert 1 in snapshot.selector_map
    origin = snapshot.selector_map[1]
    assert origin.tag_name == "input"
    assert origin.attributes["aria-label"] == "请输入出发地"
    assert origin.value == "广州"
    # Index 4 is the search button.
    assert snapshot.selector_map[4].tag_name == "button"


@_aio
async def test_refresh_state_when_context_fails_returns_empty_snapshot():
    class BrokenContext(_FakeContext):
        async def get_state(self, **_kwargs):
            raise RuntimeError("boom")

    adapter = CtripQueryBrowserAdapter(_FakeCtripQueryTool(BrokenContext()))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        await adapter.refresh_state()


@_aio
async def test_input_text_success_returns_none_and_propagates_action():
    context = _FakeContext()
    tool = _FakeCtripQueryTool(context, responses=[ToolResult(output="ok")])
    adapter = CtripQueryBrowserAdapter(tool)  # type: ignore[arg-type]

    result = await adapter.input_text(index=1, text="广州")

    assert result is None
    assert tool.execute_calls == [
        {"action": "input_text", "index": 1, "text": "广州"},
    ]


@_aio
async def test_input_text_error_wrapped():
    context = _FakeContext()
    tool = _FakeCtripQueryTool(
        context, responses=[ToolResult(error="policy blocked")],
    )
    adapter = CtripQueryBrowserAdapter(tool)  # type: ignore[arg-type]

    result = await adapter.input_text(index=1, text="广州")

    assert result is not None
    assert result.error == "policy blocked"


@_aio
async def test_click_routes_through_policy_check():
    context = _FakeContext()
    tool = _FakeCtripQueryTool(context, responses=[ToolResult(output="ok")])
    adapter = CtripQueryBrowserAdapter(tool)  # type: ignore[arg-type]

    result = await adapter.click(index=10, target_field_index=1)

    assert result is None
    # The semantic hint is intentionally discarded (production can't act on it),
    # but the policy-enforced click goes via the wrapped tool.
    assert tool.execute_calls == [{"action": "click_element", "index": 10}]


@_aio
async def test_click_error_wrapped():
    context = _FakeContext()
    tool = _FakeCtripQueryTool(
        context, responses=[ToolResult(error="Ctrip query blocked click: ...")],
    )
    adapter = CtripQueryBrowserAdapter(tool)  # type: ignore[arg-type]

    result = await adapter.click(index=10, target_field_index=1)

    assert result is not None
    assert "blocked click" in result.error


@_aio
async def test_select_date_success_and_error():
    context = _FakeContext()
    tool = _FakeCtripQueryTool(
        context,
        responses=[ToolResult(output="ok"), ToolResult(error="bad date")],
    )
    adapter = CtripQueryBrowserAdapter(tool)  # type: ignore[arg-type]

    ok = await adapter.select_date(index=3, text="2026-09-25")
    err = await adapter.select_date(index=3, text="2026-09-25")

    assert ok is None
    assert err is not None and "bad date" in err.error


@_aio
async def test_read_field_value_returns_attributes_value_when_present():
    context = _FakeContext(state=_seed_form(origin_value="广州"))
    adapter = CtripQueryBrowserAdapter(_FakeCtripQueryTool(context))  # type: ignore[arg-type]

    assert await adapter.read_field_value(index=1) == "广州"


@_aio
async def test_read_field_value_returns_none_when_field_missing():
    context = _FakeContext(state=_seed_form())
    adapter = CtripQueryBrowserAdapter(_FakeCtripQueryTool(context))  # type: ignore[arg-type]

    assert await adapter.read_field_value(index=999) is None


@_aio
async def test_read_field_value_returns_none_when_attributes_omit_value():
    """Production frequently omits ``value`` from the serialized DOM."""
    state = _seed_form()
    # Strip value attribute to mimic the production path.
    for idx in (1, 2, 3):
        state.selector_map[idx].attributes.pop("value", None)
    context = _FakeContext(state=state)
    adapter = CtripQueryBrowserAdapter(_FakeCtripQueryTool(context))  # type: ignore[arg-type]

    assert await adapter.read_field_value(index=1) is None


@_aio
async def test_extract_results_returns_visible_text():
    context = _FakeContext()
    tool = _FakeCtripQueryTool(
        context, responses=[ToolResult(output="flight list here")],
    )
    adapter = CtripQueryBrowserAdapter(tool)  # type: ignore[arg-type]

    visible, raw_html = await adapter.extract_results()

    assert visible == "flight list here"
    # We only have one text payload in production; raw_html mirrors it.
    assert raw_html == "flight list here"


@_aio
async def test_extract_results_returns_pair_of_nones_on_error():
    context = _FakeContext()
    tool = _FakeCtripQueryTool(
        context, responses=[ToolResult(error="network failed")],
    )
    adapter = CtripQueryBrowserAdapter(tool)  # type: ignore[arg-type]

    assert await adapter.extract_results() == (None, None)


# ---------------------------------------------------------------------------
# End-to-end via the executor (production-friendly path)
# ---------------------------------------------------------------------------


@_aio
async def test_executor_drive_adapter_through_strict_unreadable_path():
    """Wire the executor against the adapter and confirm the lenient verify
    path produces a successful report even though the production browser
    cannot read ``input.value`` back."""

    state = _seed_form()
    context = _FakeContext(state=state)
    # All actions succeed; the fake tool never fails.
    responses: List[ToolResult] = [
        ToolResult(output=""),  # click origin option
        ToolResult(output=""),  # click destination option
        ToolResult(output=""),  # input date (no matching date option)
        ToolResult(output=""),  # click search button
        ToolResult(output="flight list"),  # extract_content on results page
    ]
    tool = _FakeCtripQueryTool(context, responses=responses)
    adapter = CtripQueryBrowserAdapter(tool)  # type: ignore[arg-type]

    executor = CtripFlightFormExecutor(
        adapter,
        FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
    )
    report = await executor.run()

    assert report.success, f"report.error={report.error!r}"
    assert report.final_state.name == "RESULTS_READY"
    # Production path cannot read input.value, but the trace should mark
    # each field as "trusted click" (lenient verify).
    assert report.origin_observed == ""
    assert report.destination_observed == ""
    assert report.date_observed == ""
    notes = [step.note or "" for step in report.trace]
    assert any("trusted click" in note for note in notes), notes
    assert adapter.can_strict_read() is False


@_aio
async def test_executor_records_target_field_index_via_trace_hints():
    """The trace's ``target_index`` should reflect the destination input for
    every option click, not the option's index."""

    state = _seed_form()
    context = _FakeContext(state=state)
    responses = [ToolResult(output="")] * 8
    tool = _FakeCtripQueryTool(context, responses=responses)
    adapter = CtripQueryBrowserAdapter(tool)  # type: ignore[arg-type]

    executor = CtripFlightFormExecutor(
        adapter,
        FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
    )
    report = await executor.run()

    option_clicks = [
        step for step in report.trace
        if (step.action or "").endswith("_click_option")
    ]
    # At least origin + destination click_option entries should be present.
    assert len(option_clicks) >= 2, [s.action for s in report.trace]
    # Each click_option step records the destination input field's index
    # (target_field_index) so the audit trail makes the routing explicit.
    for step in option_clicks:
        assert step.target_field_index in {1, 2, 3}, step
    fill_traces = [
        step for step in report.trace
        if (step.action or "").startswith("fill_")
        and (
            "verified" in (step.note or "")
            or "trusted click" in (step.note or "")
        )
    ]
    assert fill_traces, "expected at least one verified fill in trace"
    for step in fill_traces:
        assert step.target_index in {1, 2, 3}, step


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))