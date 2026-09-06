from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "ctrip" / "legacy_sha_bjs_result_20260121.html"


def test_legacy_result_fixture_is_a_query_result_not_an_order_or_payment_page():
    html = FIXTURE.read_text(encoding="utf-8")
    assert "上海到北京机票查询预订" in html
    assert "携程飞机票" in html
    assert "支付订单" not in html


def test_legacy_result_fixture_contains_page_data_for_offline_parser_work():
    html = FIXTURE.read_text(encoding="utf-8")
    assert len(html) > 400_000
    assert "flight" in html.lower()
