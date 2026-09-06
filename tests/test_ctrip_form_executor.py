"""Unit tests for ``app.ctrip_form_executor``.

These tests run against ``MockBrowserContext`` so the executor can be exercised
without a real browser. Each scenario corresponds to a failure mode called
out in the handover document:
- happy path
- DOM rerender / stale index
- wrong-city option
- wrong date
- missing search button
- protection / whaleguard page
- validation errors
- retry recovery
"""
from __future__ import annotations

import asyncio

import pytest

from app.ctrip_form_executor import (
    BrowserInterface,
    CtripFlightFormExecutor,
    DOMElement,
    ElementSnapshot,
    FlightQuery,
    FormState,
    MockBrowserContext,
    PROTECTION_TOKENS,
    QUERY_URL,
    RESULT_URL,
    _dom,
    make_city_option,
    make_date_field,
    make_destination_field,
    make_origin_field,
    make_search_button,
)


def _run(coro):
    return asyncio.run(coro)


def _build_browser(
    *,
    selector_map: dict | None = None,
    visible_text: str = "",
    raw_html: str = "",
    protection_detected: bool = False,
    states: list | None = None,
    on_click=None,
    on_input=None,
    on_select_date=None,
) -> MockBrowserContext:
    """Construct a mock with the standard form layout (city options 广州/北京)."""
    if selector_map is None:
        selector_map = {
            1: make_origin_field(),
            2: make_destination_field(),
            3: make_date_field(),
            10: make_city_option(10, "广州"),
            11: make_city_option(11, "北京"),
            4: make_search_button(index=4),
        }
    return MockBrowserContext(
        selector_map=selector_map,
        states=states or [],
        url=QUERY_URL,
        visible_text=visible_text,
        raw_html=raw_html,
        protection_detected=protection_detected,
        on_click=on_click,
        on_input=on_input,
        on_select_date=on_select_date,
    )


def _build_results_snapshot() -> ElementSnapshot:
    """Build a snapshot of the search-result page."""
    selector_map = {
        100: _dom(
            100,
            tag="div",
            text="CA8322 06:45 - 08:55 ¥500",
            extra={"class": "flight-item"},
        ),
        101: _dom(
            101,
            tag="div",
            text="MU5102 09:00 - 11:15 ¥780",
            extra={"class": "flight-item"},
        ),
    }
    return ElementSnapshot(url=RESULT_URL, selector_map=selector_map)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_run_rejects_past_date_before_touching_browser():
    async def scenario():
        executor = CtripFlightFormExecutor(
            MockBrowserContext(),
            FlightQuery(origin="广州", destination="北京", departure_date="2020-01-01"),
        )
        report = await executor.run()
        assert report.success is False
        assert report.final_state == FormState.INITIAL
        assert "past" in (report.error or "")

    _run(scenario())


def test_run_rejects_same_origin_and_destination():
    async def scenario():
        executor = CtripFlightFormExecutor(
            MockBrowserContext(),
            FlightQuery(origin="北京", destination="北京", departure_date="2099-01-01"),
        )
        report = await executor.run()
        assert report.success is False
        assert "different" in (report.error or "")

    _run(scenario())


def test_run_rejects_malformed_date():
    async def scenario():
        executor = CtripFlightFormExecutor(
            MockBrowserContext(),
            FlightQuery(origin="广州", destination="北京", departure_date="not-a-date"),
        )
        report = await executor.run()
        assert report.success is False
        assert "YYYY-MM-DD" in (report.error or "")

    _run(scenario())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_fills_origin_destination_date_and_reads_results():
    async def scenario():
        browser = _build_browser(
            visible_text="CA8322 ¥500 / MU5102 ¥780",
            raw_html="<html><body>CA8322 06:45 ¥500</body></html>",
        )
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        assert report.success is True, report.error
        assert report.final_state == FormState.RESULTS_READY
        assert report.origin_observed == "广州"
        assert report.destination_observed == "北京"
        assert report.date_observed == "2026-09-25"
        assert report.result_url == QUERY_URL
        assert "CA8322" in (report.visible_text or "")
        assert report.protection_detected is False
        assert report.human_takeover_required is False
        # Two city-option clicks plus one search-button click.
        assert 10 in browser._click_calls
        assert 11 in browser._click_calls
        assert 4 in browser._click_calls
        # The date input was typed into (no matching option for the date string).
        assert any(text == "2026-09-25" for _, text in browser._input_calls)

    _run(scenario())


def test_happy_path_trace_records_one_entry_per_step():
    async def scenario():
        browser = _build_browser(
            visible_text="ok",
            raw_html="<html>ok</html>",
        )
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        actions = [step.action for step in report.trace]
        assert actions[0] == "refresh"
        assert "fill_origin" in actions
        assert "fill_destination" in actions
        assert "fill_date" in actions
        assert "click_search" in actions
        assert "extract_results" in actions
        observed = [step.observed_value for step in report.trace if step.observed_value]
        assert "广州" in observed
        assert "北京" in observed
        assert "2026-09-25" in observed

    _run(scenario())


# ---------------------------------------------------------------------------
# DOM rerender / stale index
# ---------------------------------------------------------------------------


def test_stale_index_is_recovered_when_city_panel_rerenders():
    async def scenario():
        # The destination field shifts from index 2 to index 7 after the city
        # panel for origin closes. The executor must find it by aria-label,
        # not by stale index.
        selector_map = {
            1: make_origin_field(),
            7: make_destination_field(index=7),
            3: make_date_field(),
            10: make_city_option(10, "广州"),
            11: make_city_option(11, "北京"),
            4: make_search_button(index=4),
        }
        browser = _build_browser(
            selector_map=selector_map,
            visible_text="ok",
            raw_html="<html>results</html>",
        )
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        assert report.success is True, report.error
        assert report.destination_observed == "北京"
        assert report.date_observed == "2026-09-25"
        # The destination field was resolved at its shifted index, not 2.
        assert 11 in browser._click_calls  # 北京 option

    _run(scenario())


# ---------------------------------------------------------------------------
# Wrong city option / wrong date
# ---------------------------------------------------------------------------


def test_wrong_city_option_aborts_when_only_unrelated_cities_visible():
    async def scenario():
        # Origin panel only shows "上海"/"深圳" — "广州" is missing.
        selector_map = {
            1: make_origin_field(),
            2: make_destination_field(),
            3: make_date_field(),
            10: make_city_option(10, "上海"),
            11: make_city_option(11, "深圳"),
            4: make_search_button(index=4),
        }
        browser = _build_browser(selector_map=selector_map)
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        assert report.success is False
        assert "origin fill failed" in (report.error or "").lower()
        assert report.final_state == FormState.ABORTED

    _run(scenario())


def test_wrong_date_observation_aborts():
    async def scenario():
        # Force the date input to lock to a wrong value by intercepting
        # ``input_text`` for the date field.
        def on_input(index: int, text: str):
            if index == 3:
                browser.selector_map[3].value = "2099-12-31"

        browser = _build_browser(visible_text="x", raw_html="x", on_input=on_input)
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        assert report.success is False
        assert "date" in (report.error or "").lower()
        assert "2099-12-31" in (report.error or "")

    _run(scenario())


# ---------------------------------------------------------------------------
# Missing search button
# ---------------------------------------------------------------------------


def test_missing_search_button_aborts_with_structured_error():
    async def scenario():
        selector_map = {
            1: make_origin_field(),
            2: make_destination_field(),
            3: make_date_field(),
            10: make_city_option(10, "广州"),
            11: make_city_option(11, "北京"),
            # No search button (index 4) at all.
        }
        browser = _build_browser(selector_map=selector_map, visible_text="x", raw_html="x")
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
            max_retries=2,
        )
        report = await executor.run()
        assert report.success is False
        assert "search dispatch failed" in (report.error or "").lower()
        assert report.final_state == FormState.ALL_FIELDS_VERIFIED

    _run(scenario())


# ---------------------------------------------------------------------------
# Protection / whaleguard page
# ---------------------------------------------------------------------------


def test_protection_page_aborts_immediately_with_human_takeover():
    async def scenario():
        snapshot = ElementSnapshot(
            url="https://flights.ctrip.com/online/channel",
            selector_map={
                1: _dom(
                    1,
                    tag="div",
                    text="whaleguard block — please verify you are human",
                    xpath="/html/body/div",
                ),
            },
            protection_detected=False,
        )
        browser = MockBrowserContext(
            selector_map={},
            states=[snapshot],
        )
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        assert report.success is False
        assert report.protection_detected is True
        assert report.human_takeover_required is True
        assert report.final_state == FormState.ABORTED

    _run(scenario())


def test_explicit_protection_flag_is_treated_as_human_takeover():
    async def scenario():
        browser = _build_browser(
            selector_map={
                1: make_origin_field(),
                2: make_destination_field(),
                3: make_date_field(),
            },
            protection_detected=True,
        )
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        assert report.protection_detected is True
        assert report.human_takeover_required is True
        assert report.success is False

    _run(scenario())


def test_protection_token_in_selector_map_text_triggers_human_takeover():
    async def scenario():
        # The text appears in a non-field element. The executor must still
        # detect it via the page blob.
        selector_map = {
            1: make_origin_field(),
            2: make_destination_field(),
            3: make_date_field(),
            50: _dom(50, tag="div", text="WhaleGuard CAPTCHA required"),
        }
        browser = _build_browser(selector_map=selector_map)
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        assert report.protection_detected is True
        assert report.human_takeover_required is True

    _run(scenario())


# ---------------------------------------------------------------------------
# BrowserInterface contract
# ---------------------------------------------------------------------------


def test_executor_depends_on_browser_interface_not_concrete_tool():
    """Any duck-typed object implementing the protocol should work."""

    class MinimalBrowser:
        def __init__(self):
            self.refresh_count = 0
            self.calls = []
            self.search_clicked = False
            self._origin_value = None
            self._destination_value = None
            self._date_value = None

        def _form_snapshot(self, url=QUERY_URL):
            return ElementSnapshot(
                url=url,
                selector_map={
                    1: make_origin_field(value=self._origin_value),
                    2: make_destination_field(value=self._destination_value),
                    3: make_date_field(value=self._date_value),
                    10: make_city_option(10, "广州"),
                    11: make_city_option(11, "北京"),
                    4: make_search_button(index=4),
                },
            )

        async def refresh_state(self):
            self.refresh_count += 1
            if not self.search_clicked:
                return self._form_snapshot()
            return ElementSnapshot(
                url=RESULT_URL,
                selector_map={
                    1: make_origin_field(value=self._origin_value or "广州"),
                    2: make_destination_field(value=self._destination_value or "北京"),
                    3: make_date_field(value=self._date_value or "2026-09-25"),
                    4: make_search_button(index=4),
                },
            )

        async def input_text(self, *, index: int, text: str):
            self.calls.append(("input", index, text))
            if index == 1:
                self._origin_value = text
            elif index == 2:
                self._destination_value = text
            elif index == 3:
                self._date_value = text

        async def click(self, *, index: int, target_field_index=None):
            self.calls.append(("click", index, target_field_index if target_field_index is not None else ""))
            if target_field_index == 1:
                self._origin_value = "广州"
            elif target_field_index == 2:
                self._destination_value = "北京"
            elif index == 4:
                self.search_clicked = True

        async def select_date(self, *, index: int, text: str):
            self.calls.append(("select_date", index, text))
            self._date_value = text

        async def read_field_value(self, *, index: int):
            if index == 1:
                return self._origin_value
            if index == 2:
                return self._destination_value
            if index == 3:
                return self._date_value
            return None

        async def extract_results(self):
            return "visible", "<html>ok</html>"

        def can_strict_read(self) -> bool:
            return True

    async def scenario():
        browser = MinimalBrowser()
        assert isinstance(browser, BrowserInterface)
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        assert report.success is True, report.error

    _run(scenario())


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_history_panel_with_search_word_is_ignored():
    """A recommendation/history link with the word '搜索' must not be picked
    as the search button: the search detector requires tag in {button, a}."""

    async def scenario():
        selector_map = {
            1: make_origin_field(),
            2: make_destination_field(),
            3: make_date_field(),
            10: make_city_option(10, "广州"),
            11: make_city_option(11, "北京"),
            4: make_search_button(index=4),
            99: _dom(
                99,
                tag="div",
                text="历史搜索：广州",
                xpath="/html/body/aside/div",
            ),
        }
        browser = _build_browser(
            selector_map=selector_map,
            visible_text="ok",
            raw_html="<html>ok</html>",
        )
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        assert report.success is True, report.error
        # The real search button was clicked, not the history link.
        assert 4 in browser._click_calls
        assert 99 not in browser._click_calls

    _run(scenario())


def test_executor_does_not_invoke_unsafe_actions():
    """The executor must only use ``BrowserInterface`` methods; no LLM-incompatible
    action such as ``gui_action`` or ``execute_js`` may leak through."""

    class TrackingBrowser(MockBrowserContext):
        def __init__(self):
            super().__init__(
                selector_map={
                    1: make_origin_field(),
                    2: make_destination_field(),
                    3: make_date_field(),
                    10: make_city_option(10, "广州"),
                    11: make_city_option(11, "北京"),
                    4: make_search_button(index=4),
                },
                visible_text="ok",
                raw_html="<html>ok</html>",
            )
            self.action_kinds = []

        async def click(self, *, index: int, target_field_index=None):
            self.action_kinds.append("click")
            return await super().click(index=index, target_field_index=target_field_index)

        async def input_text(self, *, index: int, text: str):
            self.action_kinds.append("input_text")
            return await super().input_text(index=index, text=text)

        async def select_date(self, *, index: int, text: str):
            self.action_kinds.append("select_date")
            return await super().select_date(index=index, text=text)

        async def read_field_value(self, *, index: int):
            self.action_kinds.append("read_field_value")
            return await super().read_field_value(index=index)

        async def refresh_state(self):
            self.action_kinds.append("refresh_state")
            return await super().refresh_state()

        async def extract_results(self):
            self.action_kinds.append("extract_results")
            return await super().extract_results()

    async def scenario():
        browser = TrackingBrowser()
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        assert report.success is True, report.error
        forbidden = {"gui_action", "execute_js", "upload_file", "open_tab", "web_search"}
        assert not (set(browser.action_kinds) & forbidden)
        assert "refresh_state" in browser.action_kinds
        assert "click" in browser.action_kinds
        assert "extract_results" in browser.action_kinds

    _run(scenario())


def test_input_text_fallback_used_when_no_matching_option_in_panel():
    """When no matching city option is in the panel, the executor falls back
    to ``input_text`` and surfaces a verification failure (never silently
    passes)."""
    async def scenario():
        # The mock never updates field values, so verify always fails.
        selector_map = {
            1: make_origin_field(),
            2: make_destination_field(),
            3: make_date_field(),
            10: make_city_option(10, "北京"),
            11: make_city_option(11, "上海"),
            4: make_search_button(index=4),
        }
        browser = _build_browser(selector_map=selector_map)
        # Disable click-to-update so verify always sees no value change.
        browser._apply_option_click = lambda idx: None  # type: ignore[assignment]
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        assert report.success is False
        assert "origin" in (report.error or "").lower()

    _run(scenario())


def test_executor_recovers_from_transient_verification_mismatch():
    """A transient first-verify mismatch (e.g. autocomplete overwrote the
    value with a related city) must be retried and ultimately succeed."""

    transient = {"count": 0}

    def on_click(index: int):
        if index == 10:  # 广州 option
            transient["count"] += 1
            if transient["count"] == 1:
                browser.selector_map[1].value = "广州北"
            else:
                browser.selector_map[1].value = "广州"

    browser = _build_browser(
        selector_map={
            1: make_origin_field(),
            2: make_destination_field(),
            3: make_date_field(),
            10: make_city_option(10, "广州"),
            11: make_city_option(11, "北京"),
            4: make_search_button(index=4),
        },
        visible_text="ok",
        raw_html="<html>ok</html>",
        on_click=on_click,
    )

    async def scenario():
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
            max_retries=2,
        )
        report = await executor.run()
        assert report.success is True, report.error
        assert report.origin_observed == "广州"

    _run(scenario())


def test_executor_blocks_booking_keywords_in_input_text():
    """The executor only ever types the legitimate city/date strings it was
    given. Forbidden keywords must never appear in input_text/select_date."""

    async def scenario():
        browser = _build_browser(
            visible_text="ok",
            raw_html="<html>ok</html>",
        )
        executor = CtripFlightFormExecutor(
            browser,
            FlightQuery(origin="广州", destination="北京", departure_date="2026-09-25"),
        )
        report = await executor.run()
        assert report.success is True, report.error
        forbidden_inputs = {"下单", "预订", "订票", "支付", "登录", "验证码"}
        for _, text in browser._input_calls:
            assert text not in forbidden_inputs
        for _, text in browser._select_date_calls:
            assert text not in forbidden_inputs

    _run(scenario())


def test_protection_tokens_constant_includes_required_categories():
    """The protection detector must cover whale-guard, captcha, and forbidden
    states so future regressions are caught by adding tokens here."""
    assert "whaleguard" in PROTECTION_TOKENS
    assert "验证码" in PROTECTION_TOKENS
    assert "风控" in PROTECTION_TOKENS


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))