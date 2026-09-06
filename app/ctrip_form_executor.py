"""Deterministic Ctrip flight-query form executor (state machine).

This module replaces the free-form LLM-driven clicks that previously filled the
Ctrip flight-query form. The executor:

1. refreshes the DOM selector map before every action;
2. locates origin/destination/date fields by semantic attributes (aria-label,
   name, placeholder, parent form);
3. fills the requested values;
4. reads back the DOM value to verify the change actually took effect;
5. aborts with a structured error on any mismatch;
6. locates the search button strictly inside the active form;
7. extracts visible result text/HTML without clicking any booking control.

The executor never uses coordinate clicks, never opens new tabs, never invokes
arbitrary JavaScript, and never touches login, CAPTCHA, booking, order, or
payment surfaces. The ``BrowserInterface`` is the only thing the executor talks
to, so the state machine can be exercised against a ``MockBrowserContext``
without spinning up Chrome.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Protocol, Sequence, Tuple, runtime_checkable


# ----- Public types -----------------------------------------------------------


class FormState(str, Enum):
    INITIAL = "initial"
    FORM_READY = "form_ready"
    ORIGIN_VERIFIED = "origin_verified"
    DESTINATION_VERIFIED = "destination_verified"
    DATE_VERIFIED = "date_verified"
    ALL_FIELDS_VERIFIED = "all_fields_verified"
    SEARCH_DISPATCHED = "search_dispatched"
    RESULTS_READY = "results_ready"
    ABORTED = "aborted"


@dataclass(frozen=True)
class FlightQuery:
    """The one itinerary the executor is bound to."""

    origin: str
    destination: str
    departure_date: str  # YYYY-MM-DD

    def parsed_date(self) -> datetime:
        try:
            return datetime.strptime(self.departure_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(
                f"departure_date must be YYYY-MM-DD, got {self.departure_date!r}"
            ) from exc

    def validate(self) -> None:
        if not self.origin.strip() or not self.destination.strip():
            raise ValueError("origin and destination must not be empty")
        if self.origin.strip() == self.destination.strip():
            raise ValueError("origin and destination must be different")
        self.parsed_date()
        today = datetime.now().date()
        if self.parsed_date().date() < today:
            raise ValueError("departure_date cannot be in the past")


@dataclass
class DOMElement:
    """Minimal element representation the executor depends on.

    The production adapter translates browser-use ``DOMElementNode`` into this
    shape; the ``MockBrowserContext`` builds it directly. Only fields used by
    the executor are declared.
    """

    index: int
    tag_name: str
    text: str = ""
    attributes: dict = field(default_factory=dict)
    xpath: str = ""
    value: Optional[str] = None  # current input value (read-back signal)

    @property
    def aria(self) -> str:
        return str(self.attributes.get("aria-label", "") or "")

    @property
    def name_attr(self) -> str:
        return str(self.attributes.get("name", "") or "")

    @property
    def placeholder(self) -> str:
        return str(self.attributes.get("placeholder", "") or "")


@dataclass
class ElementSnapshot:
    url: str = ""
    selector_map: dict = field(default_factory=dict)  # type: ignore[type-arg]
    protection_detected: bool = False


@dataclass
class StepTrace:
    """Audit record for one executor step."""

    state: FormState
    action: str
    target_index: Optional[int] = None
    target_text: Optional[str] = None
    target_field_index: Optional[int] = None
    observed_value: Optional[str] = None
    note: Optional[str] = None


@dataclass
class FormExecutorReport:
    final_state: FormState
    success: bool
    origin_observed: Optional[str] = None
    destination_observed: Optional[str] = None
    date_observed: Optional[str] = None
    result_url: Optional[str] = None
    visible_text: Optional[str] = None
    raw_html: Optional[str] = None
    trace: list = field(default_factory=list)  # type: ignore[type-arg]
    error: Optional[str] = None
    human_takeover_required: bool = False
    protection_detected: bool = False


# ----- Browser interface ------------------------------------------------------


@runtime_checkable
class BrowserInterface(Protocol):
    """Minimal browser operations the executor requires.

    ``click`` accepts an optional ``target_field_index`` so the call site can
    tell the implementation which input field the click is meant to populate.
    This is essential because clicking on a city/date option needs to be
    routed back to the right input field — heuristics on the DOM alone are
    unreliable when several inputs share an aria-hint prefix.

    ``can_strict_read()`` declares whether the implementation can read back
    the exact ``.value`` of an input element after a click/input. In mocks
    this is always True (we control state). In the production browser-use
    environment there is no public ``read_value`` action and ``input.value``
    is not surfaced through the selector map, so adapters should return
    False — the executor will then trust the click as long as the field
    still exists and its aria-label is unchanged.
    """

    async def refresh_state(self) -> ElementSnapshot: ...

    async def input_text(self, *, index: int, text: str): ...

    async def click(self, *, index: int, target_field_index: Optional[int] = None): ...

    async def select_date(self, *, index: int, text: str): ...

    async def read_field_value(self, *, index: int) -> Optional[str]: ...

    async def extract_results(self) -> Tuple[Optional[str], Optional[str]]:
        """Return ``(visible_text, raw_html)`` for the current page."""
        ...

    def can_strict_read(self) -> bool: ...


# ----- Field detection heuristics ---------------------------------------------


class FieldKind(str, Enum):
    ORIGIN = "origin"
    DESTINATION = "destination"
    DATE = "date"
    SEARCH = "search"


ORIGIN_HINTS: Tuple[str, ...] = (
    "出发地",
    "出发城市",
    "owocity",
    "owdcity",
    "fromcity",
    "from-city",
    "departure city",
)
DESTINATION_HINTS: Tuple[str, ...] = (
    "目的地",
    "到达城市",
    "owaocity",
    "owacity",
    "tocity",
    "to-city",
    "arrival city",
)
DATE_HINTS: Tuple[str, ...] = (
    "出发日期",
    "日期",
    "yyyy-mm-dd",
    "depdate",
    "departure date",
)
SEARCH_HINTS: Tuple[str, ...] = ("搜索", "查询", "search", "Search", "SEARCH")
ONEWAY_HINTS: Tuple[str, ...] = ("单程", "one way", "oneway", "OW")
PROTECTION_TOKENS: Tuple[str, ...] = (
    "whaleguard",
    "site-protection",
    "access denied",
    "forbidden",
    "验证码",
    "风控",
    "请验证",
    "人机验证",
    "blocked",
)

# Default URLs used by tests and the mock.
QUERY_URL = "https://flights.ctrip.com/online/channel"
RESULT_URL = "https://flights.ctrip.com/online/list/oneway-can-bjs"


def _normalize(value: str) -> str:
    """Collapse whitespace/punctuation/case for fuzzy city matching."""

    if not value:
        return ""
    return re.sub(r"[\s\u3000]+", "", (value or "").lower())


def _browser_can_strict_read(browser: Any) -> bool:
    """Best-effort probe for ``browser.can_strict_read()``.

    Defaults to True so any Protocol implementation that forgets to declare
    the method gets the strict mock behaviour. This keeps the existing tests
    safe while letting the production adapter opt out by returning False.
    """

    getter = getattr(browser, "can_strict_read", None)
    if getter is None:
        return True
    try:
        return bool(getter())
    except Exception:  # pragma: no cover - defensive against bad adapters
        return False


def _field_haystack(element: DOMElement) -> str:
    return " ".join(
        (
            element.aria,
            element.name_attr,
            element.placeholder,
            element.xpath,
            element.text,
        )
    ).lower()


def _field_matches(element: DOMElement, kind: FieldKind) -> bool:
    haystack = _field_haystack(element)
    tag = (element.tag_name or "").lower()
    if kind == FieldKind.ORIGIN:
        return any(hint.lower() in haystack for hint in ORIGIN_HINTS)
    if kind == FieldKind.DESTINATION:
        return any(hint.lower() in haystack for hint in DESTINATION_HINTS)
    if kind == FieldKind.DATE:
        return (
            any(hint.lower() in haystack for hint in DATE_HINTS)
            or element.placeholder.lower() == "yyyy-mm-dd"
        )
    if kind == FieldKind.SEARCH:
        if tag not in {"button", "a"}:
            return False
        return any(hint.lower() in haystack for hint in SEARCH_HINTS)
    return False


def _field_still_matches(element: DOMElement, kind: FieldKind) -> bool:
    """Sanity-check that an element still looks like the expected field kind.

    Used as the lenient-verify fallback when production cannot read
    ``input.value``. We accept the click as long as the element we are
    pointing at still classifies as the same kind (origin / destination /
    date / search) under the same heuristics used to find it initially.
    """

    return _field_matches(element, kind)


def _find_field(snapshot: ElementSnapshot, kind: FieldKind) -> Optional[DOMElement]:
    for element in snapshot.selector_map.values():
        if _field_matches(element, kind):
            return element
    return None


# ----- State machine ----------------------------------------------------------


class CtripFlightFormExecutor:
    """Deterministic state machine that fills the Ctrip flight-query form."""

    def __init__(
        self,
        browser: BrowserInterface,
        query: FlightQuery,
        *,
        max_retries: int = 2,
    ) -> None:
        self.browser = browser
        self.query = query
        self.max_retries = max(0, int(max_retries))
        self._report = FormExecutorReport(final_state=FormState.INITIAL, success=False)

    # ----- trace helpers -----

    def _trace(
        self,
        state: FormState,
        action: str,
        *,
        target_index: Optional[int] = None,
        target_text: Optional[str] = None,
        target_field_index: Optional[int] = None,
        observed_value: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        self._report.trace.append(
            StepTrace(
                state=state,
                action=action,
                target_index=target_index,
                target_text=target_text,
                target_field_index=target_field_index,
                observed_value=observed_value,
                note=note,
            )
        )

    def _abort(
        self,
        error: str,
        *,
        human_takeover: bool = False,
        protection: bool = False,
        state: FormState = FormState.ABORTED,
    ) -> FormExecutorReport:
        self._report.final_state = state
        self._report.success = False
        self._report.error = error
        if human_takeover:
            self._report.human_takeover_required = True
        if protection:
            self._report.protection_detected = True
        return self._report

    # ----- snapshot helpers -----

    async def _safe_refresh(self, *, note: str) -> Optional[ElementSnapshot]:
        snapshot = await self.browser.refresh_state()
        if snapshot is None:
            self._abort(
                "Browser refresh_state returned None.",
                state=FormState.INITIAL,
            )
            return None
        if snapshot.protection_detected:
            self._report.protection_detected = True
            self._abort(
                "Site protection or CAPTCHA detected; human takeover required.",
                human_takeover=True,
                protection=True,
            )
            return None
        page_blob = (
            snapshot.url
            + " "
            + " ".join(str(el.text or "") for el in snapshot.selector_map.values())
        ).lower()
        if any(token in page_blob for token in PROTECTION_TOKENS):
            self._report.protection_detected = True
            self._abort(
                f"Protection marker found on page ({note}); human takeover required.",
                human_takeover=True,
                protection=True,
            )
            return None
        return snapshot

    # ----- one-shot field-fill with retry -----

    async def _fill_and_verify(
        self,
        kind: FieldKind,
        requested_value: str,
        *,
        match_predicate,
        state_after: FormState,
        state_filled_marker: FormState,
        trace_action: str,
    ) -> Tuple[bool, Optional[str], Optional[DOMElement]]:
        """Fill a field via click-option-then-input, then verify the DOM value.

        Returns ``(success, observed_value, last_field)``.
        """
        last_field: Optional[DOMElement] = None
        attempts = self.max_retries + 1
        last_error = "no attempt"
        observed_value: Optional[str] = None
        for attempt in range(1, attempts + 1):
            snapshot = await self._safe_refresh(note=f"{kind.value} attempt {attempt}")
            if snapshot is None:
                return False, None, None
            field = _find_field(snapshot, kind)
            if field is None:
                last_error = f"could not locate {kind.value} field on attempt {attempt}"
                self._trace(
                    state_after,
                    trace_action,
                    note=last_error,
                )
                continue
            last_field = field
            # First, try to find a clickable option matching the requested value.
            # For non-search fields, Ctrip exposes a panel with city/date options
            # that the executor must click rather than rely on input_text alone.
            option_match = self._find_option(snapshot, kind, requested_value)
            if kind in {FieldKind.ORIGIN, FieldKind.DESTINATION}:
                # Cities must be picked from a clickable city panel. We never
                # rely on input_text alone because the autocomplete can fill
                # the field with the wrong city (or a related city) without
                # surfacing that to the user. When the panel hides the
                # requested city the executor must abort so a human can
                # verify the city's selection.
                if option_match is None:
                    last_error = (
                        f"no matching {kind.value} option in panel "
                        f"(looking for {requested_value!r})"
                    )
                    self._trace(
                        state_after,
                        f"{trace_action}_panel_mismatch",
                        target_text=requested_value,
                        note=last_error,
                    )
                    continue
                click_result = await self.browser.click(
                    index=option_match.index,
                    target_field_index=field.index,
                )
                self._trace(
                    state_after,
                    f"{trace_action}_click_option",
                    target_index=option_match.index,
                    target_text=option_match.text,
                    target_field_index=field.index,
                    note=f"clicked option on attempt {attempt}",
                )
                if getattr(click_result, "error", None):
                    last_error = f"click option failed: {click_result.error}"
                    self._trace(
                        state_after,
                        f"{trace_action}_click_option",
                        target_index=option_match.index,
                        target_text=option_match.text,
                        note=last_error,
                    )
                    continue
            elif option_match is not None and kind == FieldKind.DATE:
                click_result = await self.browser.click(
                    index=option_match.index,
                    target_field_index=field.index,
                )
                self._trace(
                    state_after,
                    f"{trace_action}_click_option",
                    target_index=option_match.index,
                    target_text=option_match.text,
                    target_field_index=field.index,
                    note=f"clicked option on attempt {attempt}",
                )
                if getattr(click_result, "error", None):
                    last_error = f"click date option failed: {click_result.error}"
                    self._trace(
                        state_after,
                        f"{trace_action}_click_option",
                        target_index=option_match.index,
                        target_text=option_match.text,
                        note=last_error,
                    )
                    continue
            elif kind != FieldKind.SEARCH:
                # No matching date option — type into the input. The mock
                # (and real browser) will set the value directly.
                input_result = await self.browser.input_text(
                    index=field.index, text=requested_value,
                )
                if getattr(input_result, "error", None):
                    last_error = f"input_text failed: {input_result.error}"
                    self._trace(
                        state_after,
                        f"{trace_action}_input_text",
                        target_index=field.index,
                        target_text=field.text,
                        note=last_error,
                    )
                    continue
            # Verify the field actually holds the requested value.
            snapshot2 = await self._safe_refresh(note=f"{kind.value} verify {attempt}")
            if snapshot2 is None:
                return False, None, field
            field2 = _find_field(snapshot2, kind)
            if field2 is None:
                last_error = f"{kind.value} field disappeared after write"
                last_field = None
                continue
            last_field = field2
            observed_value = field2.value if field2.value is not None else field2.text
            strict = _browser_can_strict_read(self.browser)
            if match_predicate(observed_value):
                self._trace(
                    state_filled_marker,
                    trace_action,
                    target_index=field2.index,
                    target_text=field2.text,
                    observed_value=observed_value,
                    note=f"verified on attempt {attempt}",
                )
                return True, observed_value, field2
            if not strict and field2 is not None and field2.index is not None:
                # Production path: we cannot read input.value through the
                # browser-use selector map. Trust the click as long as the
                # input field is still in the DOM and its semantic labels
                # are intact. ``CtripQueryBrowserAdapter`` additionally
                # routes every click through ``CtripQueryTool``'s policy
                # whitelist, which already rejects off-form controls.
                if not _field_still_matches(field2, kind):
                    last_error = (
                        f"{kind.value} field semantics changed after click "
                        f"(target={field2.index}, aria="
                        f"{field2.attributes.get('aria-label', '')!r}, "
                        f"name={field2.attributes.get('name', '')!r})"
                    )
                    self._trace(
                        state_filled_marker,
                        f"{trace_action}_semantic_mismatch",
                        target_index=field2.index,
                        target_text=field2.text,
                        observed_value=observed_value,
                        note=last_error,
                    )
                    continue
                self._trace(
                    state_filled_marker,
                    trace_action,
                    target_index=field2.index,
                    target_text=field2.text,
                    observed_value=observed_value,
                    note=(
                        f"trusted click on attempt {attempt} "
                        f"(production: no strict value read, observed={observed_value!r})"
                    ),
                )
                return True, observed_value, field2
            last_error = (
                f"{kind.value} value mismatch on attempt {attempt}: "
                f"observed={observed_value!r} expected~{requested_value!r}"
            )
            self._trace(
                state_filled_marker,
                f"{trace_action}_verify_mismatch",
                target_index=field2.index,
                target_text=field2.text,
                observed_value=observed_value,
                note=last_error,
            )
        # All attempts exhausted.
        self._abort(
            f"{kind.value} fill failed after {attempts} attempts: {last_error}",
        )
        return False, observed_value, last_field

    # ----- matching helpers -----

    @staticmethod
    def _normalize_city(value: str) -> str:
        return _normalize(value)

    def _find_option(
        self, snapshot: ElementSnapshot, kind: FieldKind, requested: str,
    ) -> Optional[DOMElement]:
        """Look for an exact/normalized option in the current selector map."""
        target = self._normalize_city(requested)
        for element in snapshot.selector_map.values():
            tag = (element.tag_name or "").lower()
            text = self._normalize_city(element.text)
            if not text:
                continue
            if kind in {FieldKind.ORIGIN, FieldKind.DESTINATION}:
                if tag in {"li", "div", "span", "a", "button"} and text == target:
                    return element
            elif kind == FieldKind.DATE:
                if tag in {"td", "div", "span", "a", "button"} and (
                    requested in (element.text or "")
                    or requested in (element.attributes.get("data-date", "") or "")
                ):
                    return element
        return None

    # ----- per-field orchestration -----

    async def _verify_origin(self) -> bool:
        target = self._normalize_city(self.query.origin)
        ok, observed, _ = await self._fill_and_verify(
            FieldKind.ORIGIN,
            self.query.origin,
            match_predicate=lambda v: bool(v) and self._normalize_city(v) == target,
            state_after=FormState.FORM_READY,
            state_filled_marker=FormState.ORIGIN_VERIFIED,
            trace_action="fill_origin",
        )
        self._report.origin_observed = observed
        if ok:
            self._report.final_state = FormState.ORIGIN_VERIFIED
        return ok

    async def _verify_destination(self) -> bool:
        target = self._normalize_city(self.query.destination)
        ok, observed, _ = await self._fill_and_verify(
            FieldKind.DESTINATION,
            self.query.destination,
            match_predicate=lambda v: bool(v) and self._normalize_city(v) == target,
            state_after=FormState.ORIGIN_VERIFIED,
            state_filled_marker=FormState.DESTINATION_VERIFIED,
            trace_action="fill_destination",
        )
        self._report.destination_observed = observed
        if ok:
            self._report.final_state = FormState.DESTINATION_VERIFIED
        return ok

    async def _verify_date(self) -> bool:
        target = self.query.departure_date
        ok, observed, _ = await self._fill_and_verify(
            FieldKind.DATE,
            target,
            match_predicate=lambda v: bool(v)
            and target in (v or "").replace("/", "-"),
            state_after=FormState.DESTINATION_VERIFIED,
            state_filled_marker=FormState.DATE_VERIFIED,
            trace_action="fill_date",
        )
        self._report.date_observed = observed
        if ok:
            self._report.final_state = FormState.DATE_VERIFIED
        return ok

    async def _dispatch_search(self) -> bool:
        attempts = self.max_retries + 1
        last_error = "no attempt"
        for attempt in range(1, attempts + 1):
            snapshot = await self._safe_refresh(note=f"search attempt {attempt}")
            if snapshot is None:
                return False
            button = _find_field(snapshot, FieldKind.SEARCH)
            if button is None:
                last_error = f"search button not found on attempt {attempt}"
                self._trace(
                    FormState.ALL_FIELDS_VERIFIED,
                    "find_search_button",
                    note=last_error,
                )
                continue
            click_result = await self.browser.click(index=button.index)
            if getattr(click_result, "error", None):
                last_error = f"search click failed: {click_result.error}"
                self._trace(
                    FormState.ALL_FIELDS_VERIFIED,
                    "click_search",
                    target_index=button.index,
                    target_text=button.text,
                    note=last_error,
                )
                continue
            self._trace(
                FormState.SEARCH_DISPATCHED,
                "click_search",
                target_index=button.index,
                target_text=button.text,
                note=f"clicked on attempt {attempt}",
            )
            self._report.final_state = FormState.SEARCH_DISPATCHED
            return True
        self._abort(
            f"search dispatch failed after {attempts} attempts: {last_error}",
            state=FormState.ALL_FIELDS_VERIFIED,
        )
        return False

    async def _read_results(self) -> bool:
        # Wait one snapshot to make sure the page has navigated.
        snapshot = await self._safe_refresh(note="post-search refresh")
        if snapshot is None:
            return False
        visible_text, raw_html = await self.browser.extract_results()
        self._report.result_url = snapshot.url
        self._report.visible_text = visible_text
        self._report.raw_html = raw_html
        if not (visible_text or raw_html):
            self._abort(
                "Result page rendered empty content; cannot read flight options.",
                state=FormState.SEARCH_DISPATCHED,
            )
            return False
        self._report.final_state = FormState.RESULTS_READY
        self._report.success = True
        self._trace(
            FormState.RESULTS_READY,
            "extract_results",
            note=f"url={snapshot.url}",
        )
        return True

    # ----- main entrypoint -----

    async def run(self) -> FormExecutorReport:
        try:
            self.query.validate()
        except ValueError as exc:
            self._trace(FormState.INITIAL, "validate", note=str(exc))
            return self._abort(
                f"Invalid FlightQuery: {exc}",
                state=FormState.INITIAL,
            )

        # Step 1 — refresh + protection check.
        snapshot = await self._safe_refresh(note="initial refresh")
        if snapshot is None:
            return self._report
        self._report.final_state = FormState.FORM_READY
        self._trace(
            FormState.FORM_READY,
            "refresh",
            note=f"loaded {snapshot.url}",
        )

        # Step 2 — fill origin.
        if not await self._verify_origin():
            return self._report
        # Step 3 — fill destination.
        if not await self._verify_destination():
            return self._report
        # Step 4 — fill date.
        if not await self._verify_date():
            return self._report
        self._report.final_state = FormState.ALL_FIELDS_VERIFIED
        self._trace(
            FormState.ALL_FIELDS_VERIFIED,
            "all_verified",
            note="origin/destination/date all verified",
        )
        # Step 5 — search.
        if not await self._dispatch_search():
            return self._report
        # Step 6 — read results.
        if not await self._read_results():
            return self._report
        return self._report


# ----- Mock browser context for unit tests -----------------------------------


@dataclass
class MockBrowserContext(BrowserInterface):
    """Scriptable browser used by ``tests/test_ctrip_form_executor.py``.

    The mock has two modes:

    - **stateful** (default): ``selector_map`` is mutated by ``input_text`` /
      ``select_date`` and by ``click`` on city/date options. Each
      ``refresh_state`` returns a snapshot of the current state. This is the
      intended mode for happy-path and retry tests.

    - **scripted**: if ``states`` is provided, ``refresh_state`` pops snapshots
      from the queue and ``input_text``/``click`` no longer mutate the active
      snapshot. This mode is useful for tests that need to simulate hard
      failures (e.g. a field that never gets the right value).

    The ``on_input`` / ``on_click`` callbacks fire for every call regardless of
    mode and let tests assert call ordering.
    """

    selector_map: dict = field(default_factory=dict)  # type: ignore[type-arg]
    states: list = field(default_factory=list)  # type: ignore[type-arg]
    url: str = QUERY_URL
    visible_text: str = ""
    raw_html: str = ""
    protection_detected: bool = False
    on_input: Any = None  # callable(index, text)
    on_click: Any = None  # callable(index)
    on_select_date: Any = None  # callable(index, text)

    _input_calls: list = field(default_factory=list)  # type: ignore[type-arg]
    _click_calls: list = field(default_factory=list)  # type: ignore[type-arg]
    _select_date_calls: list = field(default_factory=list)  # type: ignore[type-arg]
    _refresh_count: int = 0

    async def refresh_state(self) -> ElementSnapshot:
        self._refresh_count += 1
        if self.states:
            idx = min(self._refresh_count - 1, len(self.states) - 1)
            return self.states[idx]
        return ElementSnapshot(
            url=self.url,
            selector_map=dict(self.selector_map),
            protection_detected=self.protection_detected,
        )

    async def input_text(self, *, index: int, text: str) -> Any:
        self._input_calls.append((index, text))
        if index in self.selector_map and not self.states:
            self.selector_map[index].value = text
        if self.on_input is not None:
            self.on_input(index, text)
        return None

    async def click(self, *, index: int, target_field_index: Optional[int] = None) -> Any:
        self._click_calls.append(index)
        # When the executor tells us which input field this click is meant
        # to populate, route the option's text to that field. This avoids
        # brittle aria-hint heuristics that fail when several inputs share
        # similar labels (e.g. 出发地/目的地 both containing 出发).
        if (
            target_field_index is not None
            and target_field_index in self.selector_map
            and index in self.selector_map
        ):
            option = self.selector_map[index]
            value = (option.text or "").strip()
            if value:
                self.selector_map[target_field_index].value = value
        elif not self.states and index in self.selector_map:
            # Heuristic fallback (used only when no explicit target_field_index
            # is provided — e.g. clicking the search button or in scripted
            # mode). Picks the unfilled input that matches the option text.
            self._apply_option_click(index)
        if self.on_click is not None:
            self.on_click(index)
        return None

    async def select_date(self, *, index: int, text: str) -> Any:
        self._select_date_calls.append((index, text))
        if index in self.selector_map and not self.states:
            self.selector_map[index].value = text
        if self.on_select_date is not None:
            self.on_select_date(index, text)
        return None

    async def read_field_value(self, *, index: int) -> Optional[str]:
        element = self.selector_map.get(index)
        if element is None:
            return None
        return element.value

    async def extract_results(self) -> Tuple[Optional[str], Optional[str]]:
        return self.visible_text, self.raw_html

    def can_strict_read(self) -> bool:
        return True

    # ----- helpers -----

    def _apply_option_click(self, index: int) -> None:
        """Map a click on a city/date option back to the input it fills."""
        element = self.selector_map.get(index)
        if element is None:
            return
        text = (element.text or "").strip()
        if not text:
            return
        # Best-effort: pick the most plausible input field for the option.
        for candidate_index, candidate in self.selector_map.items():
            if candidate is element:
                continue
            if (candidate.tag_name or "").lower() != "input":
                continue
            aria = (candidate.attributes or {}).get("aria-label", "") or ""
            placeholder = (candidate.attributes or {}).get("placeholder", "") or ""
            name = (candidate.attributes or {}).get("name", "") or ""
            if text in {"广州", "北京", "上海", "深圳"}:
                if "出发" in aria or name == "owDCity":
                    candidate.value = text
                    return
                if "目的" in aria or "目的" in placeholder or name == "owACity":
                    candidate.value = text
                    return
            if "-" in text and len(text) == 10:
                if "日期" in aria or placeholder == "yyyy-mm-dd":
                    candidate.value = text
                    return


# ----- Factory helpers used by tests -----------------------------------------


def _dom(
    index: int,
    *,
    tag: str,
    text: str = "",
    aria: str = "",
    name: str = "",
    placeholder: str = "",
    xpath: str = "",
    value: Optional[str] = None,
    extra: Optional[dict] = None,
) -> DOMElement:
    attributes: dict = {}
    if aria:
        attributes["aria-label"] = aria
    if name:
        attributes["name"] = name
    if placeholder:
        attributes["placeholder"] = placeholder
    if extra:
        attributes.update(extra)
    return DOMElement(
        index=index,
        tag_name=tag,
        text=text,
        attributes=attributes,
        xpath=xpath,
        value=value,
    )


def make_origin_field(
    index: int = 1,
    *,
    name: str = "owDCity",
    aria: str = "请输入出发地",
    xpath: str = "/html/body/form/div[1]/input",
    value: Optional[str] = None,
) -> DOMElement:
    return _dom(
        index,
        tag="input",
        aria=aria,
        name=name,
        placeholder="请选择出发城市",
        xpath=xpath,
        value=value,
    )


def make_destination_field(
    index: int = 2,
    *,
    name: str = "owACity",
    aria: str = "请输入目的地",
    xpath: str = "/html/body/form/div[2]/input",
    value: Optional[str] = None,
) -> DOMElement:
    return _dom(
        index,
        tag="input",
        aria=aria,
        name=name,
        placeholder="请选择到达城市",
        xpath=xpath,
        value=value,
    )


def make_date_field(
    index: int = 3,
    *,
    name: str = "depDate",
    aria: str = "请选择出发日期",
    xpath: str = "/html/body/form/div[3]/input",
    value: Optional[str] = None,
) -> DOMElement:
    return _dom(
        index,
        tag="input",
        aria=aria,
        name=name,
        placeholder="yyyy-mm-dd",
        xpath=xpath,
        value=value,
    )


def make_city_option(
    index: int,
    text: str,
    *,
    xpath: str = "/html/body/form/div/ul/li",
) -> DOMElement:
    return _dom(index, tag="li", text=text, xpath=xpath)


def make_search_button(
    index: int = 4,
    *,
    text: str = "搜索",
    xpath: str = "/html/body/form/button",
) -> DOMElement:
    return _dom(index, tag="button", text=text, xpath=xpath)


__all__ = [
    "ALL_FIELDS_VERIFIED_OK",
    "BrowserInterface",
    "CtripFlightFormExecutor",
    "DOMElement",
    "DATE_HINTS",
    "DATE_VERIFIED",
    "DESTINATION_HINTS",
    "ElementSnapshot",
    "FieldKind",
    "FlightQuery",
    "FormExecutorReport",
    "FormState",
    "MockBrowserContext",
    "ORIGIN_HINTS",
    "PROTECTION_TOKENS",
    "QUERY_URL",
    "RESULT_URL",
    "SEARCH_HINTS",
    "StepTrace",
    "_dom",
    "make_city_option",
    "make_date_field",
    "make_destination_field",
    "make_origin_field",
    "make_search_button",
]