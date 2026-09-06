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

# --- Real selector map sourced from teacher's debug_html fixtures ---
# !!! WARNING: these are *recommended* defaults, not hard bindings.
# Source: D:/It/Test_Project/case/OpenManus-gui/debug_html/*.elements.txt
# Sampled 2026-09-06: 343 launch-page hits, 85 list_result-page hits.
# Empirical observations (see scripts/inspect_ctrip_selectors.py):
#   * launch 页 (online/channel/domestic)  origin  最常在 [37]; 飘到 [36/43/93/96] 也常见
#                                            dest    最常在 [38/39];      [42-49] 多为首页底部"低价速报"误命中
#                                            search  最常在 [48]          (启动 1 次 [43], 历史 A/B [46/49/50/54/56])
#   * list_result 页 origin=[30]/[14], dest=[32]/[16/22], depart=[23]
#   * DOM 改一点 index 就漂 → 协议必须有 target_field_index 显式归属, 不能靠 attribute 启发式
# 本常量存在的意义：让 adapter / CLI 默认先尝这些 index, 失败再回退到 inspector 重新定位。
# 真正生产跑前必须在本地 Chrome 跑 scripts/ctrip_cdp_happy_path.py 重新验证。
RECOMMENDED_FIELD_INDEX = {
    "launch": {
        "oneway_tab": 32,
        "origin": 37,
        "destination": 38,
        "depart_date": 40,
        "return_date": 41,
        "search": 48,
    },
    "list_result": {
        "origin": 30,
        "destination": 32,
        "depart_date": 33,
    },
}


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
