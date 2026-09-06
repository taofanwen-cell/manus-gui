"""Adapter from :class:`app.tool.ctrip_query_tool.CtripQueryTool` to the
:class:`app.ctrip_form_executor.BrowserInterface` protocol.

The adapter is intentionally thin: it does **not** invent any new
capabilities, it only re-shapes the existing policy-restricted browser
surface so the deterministic state machine can drive it.

Design notes
------------

- The policy whitelist (clickable-text filter, allowed domains, blocked
  text terms) lives in ``CtripQueryTool``; the executor must keep going
  through ``execute(...)`` so every action is vetted.
- The production ``browser-use`` ``DOMElement`` does not expose ``.value``
  through its Pydantic schema — only ``attributes`` is reliably returned,
  and Chrome sometimes omits ``value`` for inputs whose value was set
  programmatically. We therefore declare ``can_strict_read()=False``; the
  executor will treat a click on a verified-by-policy option as
  authoritative as long as the target field still exists with its expected
  aria-label.
- ``refresh_state`` reads the selector map directly from the underlying
  browser context (the same source ``CtripQueryTool._check_form_target``
  uses) so we get a live snapshot, not the JSON serialisation that
  ``BrowserUseTool.get_current_state`` would return.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from app.ctrip_form_executor import (
    DOMElement,
    ElementSnapshot,
    FlightQuery,
    FormExecutorReport,
    FormState,
)
from app.tool.ctrip_query_tool import CtripQueryTool


@dataclass
class _AdapterResult:
    """Carrier that exposes ``.error`` so the executor can detect failures.

    A successful action returns ``None`` (mirroring the mock). A failed
    action returns ``_AdapterResult(error=...)``.
    """

    error: Optional[str] = None


def _wrap(result: Any) -> Optional[_AdapterResult]:
    if getattr(result, "error", None):
        return _AdapterResult(error=str(result.error))
    return None


class CtripQueryBrowserAdapter:
    """Wrap a :class:`CtripQueryTool` for the deterministic executor."""

    def __init__(self, ctrip_tool: CtripQueryTool) -> None:
        self._tool = ctrip_tool
        self._browser_tool = ctrip_tool.browser_tool

    # ----- state ------------------------------------------------------------

    async def refresh_state(self) -> ElementSnapshot:
        context = await self._browser_tool._ensure_browser_initialized()
        page = await context.get_current_page()
        # Match the compatibility shim used by ``BrowserUseTool.get_current_state``.
        try:
            state = await context.get_state()
        except TypeError as exc:
            if "cache_clickable_elements_hashes" not in str(exc):
                raise
            state = await context.get_state(cache_clickable_elements_hashes={})

        selector_map: dict = {}
        for idx, element in (state.selector_map or {}).items():
            attributes = dict(getattr(element, "attributes", {}) or {})
            selector_map[int(idx)] = DOMElement(
                index=int(idx),
                tag_name=str(getattr(element, "tag_name", "") or ""),
                text=str(getattr(element, "text", "") or ""),
                attributes=attributes,
                xpath=str(getattr(element, "xpath", "") or ""),
                value=attributes.get("value"),
            )

        url = getattr(page, "url", "") or getattr(state, "url", "") or ""
        return ElementSnapshot(
            url=url,
            selector_map=selector_map,
        )

    # ----- writes -----------------------------------------------------------

    async def input_text(self, *, index: int, text: str) -> Optional[_AdapterResult]:
        result = await self._tool.execute(action="input_text", index=index, text=text)
        return _wrap(result)

    async def click(
        self, *, index: int, target_field_index: Optional[int] = None,
    ) -> Optional[_AdapterResult]:
        # ``target_field_index`` is a semantic hint recorded by the executor
        # for the trace. The production click goes through the policy
        # whitelist (``_check_form_target``) which already enforces that
        # clicks land on the right form control, so we don't need to
        # forward the hint as an action parameter.
        del target_field_index
        result = await self._tool.execute(action="click_element", index=index)
        return _wrap(result)

    async def select_date(self, *, index: int, text: str) -> Optional[_AdapterResult]:
        result = await self._tool.execute(action="select_date", index=index, text=text)
        return _wrap(result)

    # ----- reads ------------------------------------------------------------

    async def read_field_value(self, *, index: int) -> Optional[str]:
        """Best-effort value read.

        Returns ``attributes["value"]`` when Chrome happened to surface it;
        otherwise ``None``. The executor treats ``None`` as "unreadable but
        still trusted" when ``can_strict_read()`` is False.
        """

        snapshot = await self.refresh_state()
        element = snapshot.selector_map.get(index)
        if element is None:
            return None
        return element.value

    async def extract_results(self) -> Tuple[Optional[str], Optional[str]]:
        result = await self._tool.execute(action="extract_content", goal="flight list")
        if getattr(result, "error", None):
            return None, None
        text = str(result.output or "") if result is not None else ""
        # Production has no DOM-HTML dump; reuse the visible text so the
        # executor's trace still carries a payload.
        return text, text

    def can_strict_read(self) -> bool:
        return False


__all__ = ["CtripQueryBrowserAdapter"]