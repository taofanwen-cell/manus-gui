from app.ctrip_policy import decision, host_is_allowed


def test_allowed_hosts():
    assert host_is_allowed("https://flights.ctrip.com/")
    assert host_is_allowed("https://www.trip.com/")
    assert not host_is_allowed("https://example.com/")


def test_blocks_sensitive_actions_and_urls():
    assert decision("gui_action", current_url="https://flights.ctrip.com/")[0] is False
    assert decision("go_to_url", url="https://passport.ctrip.com/login")[0] is False
    assert decision("go_to_url", url="https://example.com/")[0] is False


def test_blocks_order_and_payment_input():
    assert decision("input_text", text="提交订单", current_url="https://flights.ctrip.com/")[0] is False
    assert decision("input_text", text="支付", current_url="https://flights.ctrip.com/")[0] is False
    assert decision("input_text", text="订票", current_url="https://flights.ctrip.com/")[0] is False
    assert decision("select_date", text="2026-09-25", current_url="https://flights.ctrip.com/")[0] is True
