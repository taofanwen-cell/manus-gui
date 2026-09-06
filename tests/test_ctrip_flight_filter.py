"""多维度过滤单测。

覆盖 FlightPreference 的 8 个硬过滤维度 + 1 个排序保留行为 + 1 个 reject 解释器。
"""
from __future__ import annotations

from app.ctrip_flights import (
    FlightOption,
    FlightPreference,
    explain_filter_rejection,
    rank_flights,
)


def _opt(
    *,
    airline="南航",
    flight_number="CZ3110",
    departure_time="06:30",
    arrival_time="08:55",
    duration_minutes=145,
    price_cny=980,
    is_transfer=False,
    is_shared=False,
    remaining_seats=10,
    cabin="经济舱",
):
    return FlightOption(
        airline=airline,
        flight_number=flight_number,
        aircraft=None,
        departure_time=departure_time,
        arrival_time=arrival_time,
        departure_airport=None,
        arrival_airport=None,
        departure_terminal=None,
        arrival_terminal=None,
        price_cny=price_cny,
        cabin=cabin,
        discount=None,
        duration_minutes=duration_minutes,
        is_shared=is_shared,
        is_transfer=is_transfer,
        remaining_seats=remaining_seats,
        labels=(),
        raw_text="",
    )


class TestAirlineFilter:
    def test_whitelist_keeps_only_named(self):
        a = _opt(airline="南航", flight_number="CZ1")
        b = _opt(airline="国航", flight_number="CA1")
        c = _opt(airline="东航", flight_number="MU1")
        ranked = rank_flights([a, b, c], FlightPreference(airlines_whitelist=("南航", "东航")))
        assert [x.flight_number for x in ranked] == ["CZ1", "MU1"]

    def test_blacklist_removes_named(self):
        a = _opt(airline="南航", flight_number="CZ1")
        b = _opt(airline="国航", flight_number="CA1")
        ranked = rank_flights([a, b], FlightPreference(airlines_blacklist=("南航",)))
        assert [x.flight_number for x in ranked] == ["CA1"]

    def test_no_airline_filter_keeps_all(self):
        a = _opt(airline="南航")
        b = _opt(airline="国航")
        ranked = rank_flights([a, b], FlightPreference())
        assert len(ranked) == 2


class TestTimeWindow:
    def test_morning_window_keeps_only_am(self):
        morning = _opt(flight_number="AM1", departure_time="07:00")
        noon = _opt(flight_number="NM1", departure_time="13:00")
        evening = _opt(flight_number="EV1", departure_time="20:00")
        # 06:00-12:00 视为上午窗口
        ranked = rank_flights([morning, noon, evening], FlightPreference(depart_window=(360, 720)))
        assert [x.flight_number for x in ranked] == ["AM1"]

    def test_overnight_window_crosses_midnight(self):
        late = _opt(flight_number="LT1", departure_time="23:30")
        early = _opt(flight_number="ER1", departure_time="02:00")
        mid = _opt(flight_number="MD1", departure_time="12:00")
        # 22:00-06:00 跨天窗口
        ranked = rank_flights([late, early, mid], FlightPreference(depart_window=(1320, 360)))
        assert {x.flight_number for x in ranked} == {"LT1", "ER1"}

    def test_arrival_window_filters_by_landing_time(self):
        early_land = _opt(flight_number="EL", arrival_time="08:00")
        late_land = _opt(flight_number="LL", arrival_time="20:00")
        ranked = rank_flights([early_land, late_land], FlightPreference(arrive_window=(360, 1080)))
        assert [x.flight_number for x in ranked] == ["EL"]

    def test_missing_time_excluded_from_window_filter(self):
        no_time = _opt(flight_number="NT", departure_time=None)
        has_time = _opt(flight_number="HT", departure_time="10:00")
        ranked = rank_flights([no_time, has_time], FlightPreference(depart_window=(360, 720)))
        assert [x.flight_number for x in ranked] == ["HT"]


class TestDurationAndSeats:
    def test_max_duration_caps_long_flights(self):
        short = _opt(flight_number="S", duration_minutes=120)
        long = _opt(flight_number="L", duration_minutes=300)
        ranked = rank_flights([short, long], FlightPreference(max_duration_minutes=180))
        assert [x.flight_number for x in ranked] == ["S"]

    def test_min_seats_filters_out_low_availability(self):
        plenty = _opt(flight_number="P", remaining_seats=20)
        few = _opt(flight_number="F", remaining_seats=2)
        none_info = _opt(flight_number="U", remaining_seats=None)
        ranked = rank_flights([plenty, few, none_info], FlightPreference(min_seats=5))
        assert {x.flight_number for x in ranked} == {"P", "U"}


class TestPriceRange:
    def test_min_and_max_form_range(self):
        cheap = _opt(flight_number="CH", price_cny=400)
        mid = _opt(flight_number="MD", price_cny=800)
        expensive = _opt(flight_number="EX", price_cny=1500)
        ranked = rank_flights([cheap, mid, expensive], FlightPreference(min_price_cny=500, max_price_cny=1000))
        assert [x.flight_number for x in ranked] == ["MD"]


class TestDirectAndTransfer:
    def test_direct_only_drops_transfer(self):
        direct = _opt(flight_number="D", is_transfer=False)
        transfer = _opt(flight_number="T", is_transfer=True)
        ranked = rank_flights([direct, transfer], FlightPreference(direct_flight_only=True))
        assert [x.flight_number for x in ranked] == ["D"]

    def test_unknown_transfer_flag_treated_as_transfer_in_direct_mode(self):
        # is_transfer=None 视为非直飞, 直飞模式直接剔除 (保守)
        unknown = _opt(flight_number="U", is_transfer=None)
        ranked = rank_flights([unknown], FlightPreference(direct_flight_only=True))
        assert ranked == []

    def test_exclude_shared(self):
        shared = _opt(flight_number="S", is_shared=True)
        nonshared = _opt(flight_number="N", is_shared=False)
        ranked = rank_flights([shared, nonshared], FlightPreference(exclude_shared=True))
        assert [x.flight_number for x in ranked] == ["N"]


class TestComposite:
    def test_all_filters_compose(self):
        target = _opt(
            flight_number="T1", airline="南航", departure_time="07:00",
            arrival_time="09:00", duration_minutes=120, price_cny=800,
            is_transfer=False, remaining_seats=15,
        )
        wrong_airline = _opt(flight_number="W1", airline="国航")
        wrong_time = _opt(flight_number="W2", airline="南航", departure_time="20:00")
        too_expensive = _opt(flight_number="W3", airline="南航", price_cny=2000)
        too_long = _opt(flight_number="W4", airline="南航", duration_minutes=400)
        transfer = _opt(flight_number="W5", airline="南航", is_transfer=True)
        ranked = rank_flights(
            [target, wrong_airline, wrong_time, too_expensive, too_long, transfer],
            FlightPreference(
                airlines_whitelist=("南航",),
                depart_window=(360, 720),
                max_price_cny=1500,
                max_duration_minutes=240,
                direct_flight_only=True,
            ),
        )
        assert [x.flight_number for x in ranked] == ["T1"]

    def test_empty_result_kept_silently(self):
        all = [_opt(flight_number="X", price_cny=9999)]
        ranked = rank_flights(all, FlightPreference(max_price_cny=500))
        assert ranked == []

    def test_sort_by_after_filter(self):
        cheap_direct = _opt(flight_number="C", price_cny=500, is_transfer=False)
        expensive_direct = _opt(flight_number="E", price_cny=1500, is_transfer=False)
        cheap_transfer = _opt(flight_number="CT", price_cny=600, is_transfer=True)
        ranked = rank_flights(
            [cheap_direct, expensive_direct, cheap_transfer],
            FlightPreference(direct_flight_only=True, sort_by="price"),
        )
        # 直飞模式过滤掉 CT; 剩余按价格: C 在前
        assert [x.flight_number for x in ranked] == ["C", "E"]


class TestExplainRejection:
    def test_explain_lists_all_reasons(self):
        opt = _opt(
            airline="国航", price_cny=2000, departure_time="23:00",
            duration_minutes=400, is_transfer=True,
        )
        pref = FlightPreference(
            airlines_whitelist=("南航",), max_price_cny=1500,
            depart_window=(360, 720), max_duration_minutes=240,
            direct_flight_only=True,
        )
        msg = explain_filter_rejection(opt, pref)
        # 至少包含 4 条: 航司 / 价格 / 时段 / 时长 / 经停
        assert "航司" in msg
        assert "价格" in msg
        assert "时段" in msg or "时间" in msg
        assert "时长" in msg
        assert "经停" in msg

    def test_explain_empty_when_passes(self):
        opt = _opt()
        assert explain_filter_rejection(opt, FlightPreference()) == ""