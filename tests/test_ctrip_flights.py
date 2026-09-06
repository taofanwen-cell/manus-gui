from pathlib import Path
from app.ctrip_flights import FlightPreference, parse_flight_html, rank_flights

FIXTURE = Path(__file__).parent / "fixtures" / "ctrip" / "legacy_sha_bjs_result_20260121.html"


def _options():
    return parse_flight_html(FIXTURE.read_text(encoding="utf-8"))


def test_parser_extracts_structured_direct_flights_from_teacher_fixture():
    options = _options()
    assert len(options) >= 10
    first = options[0]
    assert first.airline == "中国国航"
    assert first.flight_number == "CA8322"
    assert first.departure_time == "06:45"
    assert first.arrival_time == "08:55"
    assert first.departure_airport == "浦东国际机场"
    assert first.arrival_airport == "大兴国际机场"
    assert first.price_cny == 500
    assert first.duration_minutes == 130


def test_price_order_is_explainable_and_does_not_use_booking_controls():
    ranked = rank_flights(_options(), FlightPreference(sort_by="price"))
    prices = [item.price_cny for item in ranked if item.price_cny is not None]
    assert prices == sorted(prices)
    assert ranked[0].flight_number == "CA8322"
    assert "订票" in ranked[0].raw_text


def test_hard_constraints_filter_before_ranking():
    ranked = rank_flights(
        _options(),
        FlightPreference(max_price_cny=800, earliest_departure="07:00", latest_arrival="10:30", exclude_shared=True),
    )
    assert ranked
    assert all(item.price_cny is not None and item.price_cny <= 800 for item in ranked)
    assert all(item.departure_time >= "07:00" and item.arrival_time <= "10:30" for item in ranked)
    assert all(not item.is_shared for item in ranked)


def test_parser_returns_missing_fields_instead_of_guessing_for_partial_markup():
    options = parse_flight_html('<div class="flight-item"><span class="plane-No">CA1001</span></div>')
    assert len(options) == 1
    assert options[0].flight_number == "CA1001"
    assert options[0].price_cny is None
    assert options[0].duration_minutes is None
