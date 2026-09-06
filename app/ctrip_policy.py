"""Code-level safety policy for the Ctrip query assistant MVP."""
from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_HOST_SUFFIXES = ("ctrip.com", "trip.com")
READ_ONLY_ACTIONS = {
    "go_to_url",
    "go_back",
    "refresh",
    "wait",
    "extract_content",
    "execute_js",
    "scroll_down",
    "scroll_up",
    "scroll_to_text",
    "switch_tab",
    "close_tab",
    "close_tabs",
}
# Permitted only during query/form preparation - never on payment, order, or login flows.
QUERY_INPUT_ACTIONS = {"click_element", "input_text", "focus_element", "select_date", "send_keys"}
BLOCKED_URL_TERMS = (
    "login",
    "passport",
    "account",
    "captcha",
    "verify",
    "payment",
    "pay",
    "order/create",
    "submitorder",
    "booking/confirm",
)
BLOCKED_TEXT_TERMS = (
    "登录",
    "注册",
    "验证码",
    "支付",
    "付款",
    "提交订单",
    "立即预订",
    "去下单",
    "确认订单",
    "乘机人",
    "预订",
    "订票",
    "下单",
)


def host_is_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in ALLOWED_HOST_SUFFIXES)


def decision(action: str, *, url: str | None = None, text: str | None = None, current_url: str | None = None) -> tuple[bool, str]:
    """Return whether an action is allowed in Ctrip query mode and why."""
    if action in {"gui_action", "upload_file", "paste_image", "open_tab", "web_search"}:
        return False, f"Ctrip query mode blocks {action}; it is unnecessary or unsafe for this MVP."
    if action not in READ_ONLY_ACTIONS | QUERY_INPUT_ACTIONS:
        return False, f"Ctrip query mode blocks browser action: {action}."

    candidate_url = url or current_url or ""
    lowered_url = candidate_url.lower()
    if candidate_url and not host_is_allowed(candidate_url):
        return False, "Ctrip query mode only permits ctrip.com or trip.com pages."
    if any(term in lowered_url for term in BLOCKED_URL_TERMS):
        return False, "Ctrip query mode stops before login, verification, order, or payment pages."

    lowered_text = (text or "").lower()
    if any(term in lowered_text for term in BLOCKED_TEXT_TERMS):
        return False, "Ctrip query mode blocks account, passenger, order, and payment-related input."
    return True, "allowed"
