"""Least-privilege browser interface for the Ctrip flight-query MVP."""
from __future__ import annotations

from typing import Optional

from pydantic import Field

from app.ctrip_policy import BLOCKED_TEXT_TERMS, QUERY_INPUT_ACTIONS, READ_ONLY_ACTIONS, decision
from app.llm import LLM
from app.tool.base import BaseTool, ToolResult
from app.tool.browser_use_tool import BrowserUseTool

# Arbitrary JavaScript and coordinate actions are intentionally excluded.
_ALLOWED_ACTIONS = tuple(sorted((READ_ONLY_ACTIONS - {"execute_js"}) | QUERY_INPUT_ACTIONS))
_BLOCKED_CLICK_TEXT = tuple(BLOCKED_TEXT_TERMS) + ("预订", "订票", "下单")


class CtripQueryTool(BaseTool):
    """Ctrip query browser with state-verified form-only clicks."""

    name: str = "browser_use"
    description: str = (
        "Ctrip/Trip.com flight-query browser. It can fill the requested cities and "
        "date, query flights, read visible results, and inspect a screenshot. It "
        "cannot use coordinates, login, book, submit an order, or pay."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_ALLOWED_ACTIONS) + ["vision_inspect"]},
            "url": {"type": "string"},
            "index": {"type": "integer"},
            "text": {"type": "string"},
            "scroll_amount": {"type": "integer"},
            "tab_id": {"type": "integer"},
            "goal": {"type": "string"},
            "keys": {"type": "string"},
            "seconds": {"type": "integer"},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    browser_tool: BrowserUseTool = Field(default_factory=BrowserUseTool, exclude=True)
    visual_llm: LLM = Field(default_factory=lambda: LLM("gui"), exclude=True)
    protection_detected: bool = Field(default=False, exclude=True)
    allowed_city_names: tuple[str, ...] = Field(default_factory=tuple, exclude=True)

    @staticmethod
    def _target_text(element) -> str:
        return " ".join(
            str(value or "")
            for value in (
                getattr(element, "text", ""),
                getattr(element, "attributes", ""),
                getattr(element, "xpath", ""),
            )
        ).lower()

    def _is_safe_query_click(self, element) -> tuple[bool, str]:
        """Allow only the active search form, never recommendation/history cards."""
        attributes = getattr(element, "attributes", {}) or {}
        xpath = str(getattr(element, "xpath", "") or "")
        text = str(getattr(element, "text", "") or "").strip()
        tag_name = str(getattr(element, "tag_name", "") or "").lower()
        target_text = self._target_text(element)
        if any(term.lower() in target_text for term in _BLOCKED_CLICK_TEXT):
            return False, "target is related to login, booking, order, or payment"
        aria = str(attributes.get("aria-label", ""))
        name = str(attributes.get("name", ""))
        if tag_name == "input" and ("出发地" in aria or "目的地" in aria or name in {"owDCity", "owACity"}):
            return True, "city input inside search form"
        if tag_name == "input" and ("日期" in aria or attributes.get("placeholder") == "yyyy-mm-dd"):
            return True, "date input inside search form"
        if text in self.allowed_city_names:
            return True, "requested city option"
        if "搜索" in text and "/form/" in xpath:
            return True, "search button inside form"
        if "单程" in text and "/form/" in xpath:
            return True, "one-way switch inside form"
        return False, "only current search-form controls and requested city options may be clicked"

    async def _check_form_target(self, action: str, index: Optional[int]) -> Optional[ToolResult]:
        if index is None:
            return ToolResult(error=f"Index is required for {action}")
        context = await self.browser_tool._ensure_browser_initialized()
        page = await context.get_current_page()
        allowed, reason = decision(action, current_url=getattr(page, "url", ""))
        if not allowed:
            return ToolResult(error=f"Ctrip query policy blocked action: {reason}")
        # Refresh the selector map immediately before every interaction. A previous
        # index becomes unsafe when a city panel opens/closes or cards rerender.
        state = await context.get_state(cache_clickable_elements_hashes={})
        element = state.selector_map.get(index)
        if element is None:
            return ToolResult(error="Ctrip query blocked stale or missing DOM index; inspect current state before retrying.")
        attributes = getattr(element, "attributes", {}) or {}
        aria = str(attributes.get("aria-label", ""))
        if action == "input_text" and not ("出发地" in aria or "目的地" in aria or attributes.get("name") in {"owDCity", "owACity"}):
            return ToolResult(error="Ctrip query blocked text input outside the current origin/destination fields.")
        if action == "select_date" and not ("日期" in aria or attributes.get("placeholder") == "yyyy-mm-dd"):
            return ToolResult(error="Ctrip query blocked date selection outside the current departure-date field.")
        if action == "click_element":
            permitted, reason = self._is_safe_query_click(element)
            if not permitted:
                return ToolResult(error=f"Ctrip query blocked click: {reason}.")
        return None

    async def _vision_inspect(self, goal: Optional[str]) -> ToolResult:
        state = await self.browser_tool.get_current_state()
        if state.error:
            return ToolResult(error=f"Unable to capture current browser state: {state.error}")
        if not state.base64_image:
            return ToolResult(error="No screenshot available for vision inspection.")
        prompt = (
            "You are a read-only visual inspector for a flight-query assistant. "
            "Describe visible evidence only: page type, form labels/results, and any "
            "login, CAPTCHA, verification, or site-protection notice. Do not provide "
            "coordinate actions or bypass advice. User question: " + (goal or "Classify the page.")
        )
        try:
            answer = await self.visual_llm.ask_with_images(
                messages=[{"role": "user", "content": prompt}],
                images=[f"data:image/png;base64,{state.base64_image}"],
                stream=False,
                temperature=0,
            )
        except Exception as exc:
            return ToolResult(error=f"Visual inspection failed: {type(exc).__name__}: {exc}")
        return ToolResult(output=f"[vision_inspect / gui-plus]\n{answer}")

    async def execute(self, *, action: str, url: Optional[str] = None, index: Optional[int] = None,
                      text: Optional[str] = None, scroll_amount: Optional[int] = None,
                      tab_id: Optional[int] = None, goal: Optional[str] = None,
                      keys: Optional[str] = None, seconds: Optional[int] = None) -> ToolResult:
        if self.protection_detected:
            return ToolResult(error="Ctrip query stopped: site protection or verification was detected; human takeover is required.")
        if action == "vision_inspect":
            result = await self._vision_inspect(goal)
            combined = str(result.output or result.error or "").lower()
            if any(term in combined for term in ("whaleguard", "site-protection", "防护", "风控", "验证码", "verification", "captcha", "blocked")):
                self.protection_detected = True
            return result
        policy_text = keys if action == "send_keys" else text
        allowed, reason = decision(action, url=url, text=policy_text)
        if not allowed:
            return ToolResult(error=f"Ctrip query policy blocked action: {reason}")
        if action in {"click_element", "input_text", "select_date"}:
            blocked = await self._check_form_target(action, index)
            if blocked is not None:
                return blocked
        return await self.browser_tool.execute(
            action=action, url=url, index=index, text=text, scroll_amount=scroll_amount,
            tab_id=tab_id, goal=goal, keys=keys, seconds=seconds, ctrip_query_mode=True,
        )

    async def get_current_state(self) -> ToolResult:
        return await self.browser_tool.get_current_state()

    async def cleanup(self) -> None:
        await self.browser_tool.cleanup()
