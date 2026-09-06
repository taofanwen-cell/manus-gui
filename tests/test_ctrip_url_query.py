"""app.ctrip_url_query 的单测。

覆盖 8 个核心场景：
  - 单程 URL 构造
  - 往返 URL 构造
  - 中文城市代码转换
  - 英文 IATA 直通
  - 未知城市兜底（小写）
  - 自然语言查询解析（多种格式）
  - 相对日期解析
  - URLOnlyPolicy 强制只允许 go_to_url
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.ctrip_url_query import (
    CITY_CODES,
    CTRIP_FLIGHTS_BASE,
    DEFAULT_CABIN,
    FlightSearchParams,
    URLOnlyPolicy,
    build_flight_url,
    build_flight_url_from_query,
    get_city_code,
    iata_to_city,
    list_supported_cities,
    parse_date,
)


class TestBuildFlightUrl:
    def test_oneway_default(self):
        url = build_flight_url(FlightSearchParams("广州", "北京", "2026-09-25"))
        assert url == (
            f"{CTRIP_FLIGHTS_BASE}/oneway-can-pek"
            "?depdate=2026-09-25&cabin=y&adult=1&child=0&infant=0"
        )

    def test_round_trip(self):
        url = build_flight_url(
            FlightSearchParams("上海", "北京", "2026-09-25", return_date="2026-09-30")
        )
        assert url.startswith(f"{CTRIP_FLIGHTS_BASE}/round-sha-pek")
        assert "depdate=2026-09-25" in url
        assert "rdate=2026-09-30" in url

    def test_cabin_and_passengers(self):
        url = build_flight_url(
            FlightSearchParams(
                "深圳", "上海", "2026-09-25", cabin="c", adult=2, child=1
            )
        )
        assert "cabin=c" in url
        assert "adult=2" in url
        assert "child=1" in url

    def test_unknown_city_raises_value_error(self):
        """没收录的城市 (如 "秦皇岛") → 不静默吞错, 直接 raise ValueError。

        中文城市 lower() 之后还是中文, 拼到 URL 路径里是死链。不如显式 fail
        让上层 fallback 到状态机。
        """
        with pytest.raises(ValueError):
            build_flight_url(FlightSearchParams("秦皇岛", "青岛", "2026-09-25"))
        with pytest.raises(ValueError):
            build_flight_url(FlightSearchParams("广州", "火星", "2026-09-25"))

    def test_english_iata_passthrough(self):
        url = build_flight_url(FlightSearchParams("sha", "pek", "2026-09-25"))
        assert url == (
            f"{CTRIP_FLIGHTS_BASE}/oneway-sha-pek"
            "?depdate=2026-09-25&cabin=y&adult=1&child=0&infant=0"
        )


class TestGetCityCode:
    def test_chinese_known(self):
        assert get_city_code("广州") == "can"
        assert get_city_code("上海") == "sha"
        assert get_city_code("北京") == "pek"

    def test_iata_passthrough_lowercased(self):
        assert get_city_code("PEK") == "pek"
        assert get_city_code("can") == "can"

    def test_unknown_returns_none(self):
        assert get_city_code("火星") is None
        assert get_city_code("") is None

    def test_iata_to_city_reverse(self):
        assert iata_to_city("can") == "广州"
        assert iata_to_city("PEK") == "北京"
        assert iata_to_city("xxx") is None


class TestParseDate:
    def test_relative_keywords(self):
        fixed = datetime(2026, 9, 25)
        assert parse_date("今天", today=fixed) == "2026-09-25"
        assert parse_date("明天", today=fixed) == "2026-09-26"
        assert parse_date("后天", today=fixed) == "2026-09-27"
        assert parse_date("大后天", today=fixed) == "2026-09-28"

    def test_chinese_month_day(self):
        # fixed 必须在 1月30日之前 (本年内), 才会得到 2026-01-30
        # 否则按 wrap 规则到下一年
        fixed = datetime(2026, 1, 1)
        assert parse_date("1月30日", today=fixed) == "2026-01-30"
        # 当日期已过, wrap 到下一年
        assert parse_date("1月30日", today=datetime(2026, 6, 15)) == "2027-01-30"
        # 当月在 fixed 之后, 同年
        assert parse_date("3月5号", today=fixed) == "2026-03-05"

    def test_iso_date(self):
        fixed = datetime(2026, 9, 25)
        assert parse_date("2026-09-25", today=fixed) == "2026-09-25"
        assert parse_date("2026/09/25", today=fixed) == "2026-09-25"

    def test_invalid_returns_none(self):
        assert parse_date("") is None
        assert parse_date("hello") is None
        assert parse_date("13月40日", today=datetime(2026, 9, 25)) is None


class TestBuildFromQuery:
    def test_chinese_full(self):
        # 用年初 fixed, 否则 1月30日 < today 会 wrap 到 2027
        url = build_flight_url_from_query("1月30日从上海到北京的机票")
        assert url is not None
        # today=2026-09-06 wrap → 2027-01-30, 用 today 钩子或者直接断言包含 sha-pek + 1月30日
        assert "oneway-sha-pek" in url
        assert "-01-30" in url

    def test_iso_date_with_cities(self):
        url = build_flight_url_from_query("广州到北京 2026-09-25")
        assert url is not None
        assert "oneway-can-pek" in url
        assert "depdate=2026-09-25" in url

    def test_iata_pair(self):
        url = build_flight_url_from_query("sha-pek 2026-09-25")
        assert url is not None
        assert "oneway-sha-pek" in url

    def test_no_date_returns_none(self):
        assert build_flight_url_from_query("从北京到上海") is None

    def test_unknown_city_returns_none(self):
        """目的地不在城市表里 → 返回 None，让调用方 fallback 到状态机。"""
        assert build_flight_url_from_query("北京到火星的机票 2026-09-25") is None


class TestURLOnlyPolicy:
    def test_allows_go_to_url(self):
        policy = URLOnlyPolicy()
        ok, why = policy.check("go_to_url")
        assert ok is True
        assert why == "allowed"

    def test_allows_wait_and_extract(self):
        policy = URLOnlyPolicy()
        assert policy.check("wait")[0] is True
        assert policy.check("extract_content")[0] is True

    def test_forbids_click_element(self):
        policy = URLOnlyPolicy()
        ok, why = policy.check("click_element")
        assert ok is False
        assert "URL-only" in why

    def test_forbids_select_date(self):
        policy = URLOnlyPolicy()
        ok, why = policy.check("select_date")
        assert ok is False
        assert "select_date" in why

    def test_forbids_input_text(self):
        policy = URLOnlyPolicy()
        ok, _why = policy.check("input_text")
        assert ok is False


class TestListSupportedCities:
    def test_minimum_coverage(self):
        cities = list_supported_cities()
        # 至少有 30 个国内 + 10 个国际
        assert len(cities) >= 40
        assert "广州" in cities
        assert "北京" in cities
        assert "东京" in cities

    def test_no_duplicates(self):
        cities = list_supported_cities()
        assert len(cities) == len(set(cities))

    def test_default_cabin_is_y(self):
        assert DEFAULT_CABIN == "y"