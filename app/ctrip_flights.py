"""携程航班结果标准化、校验与可解释排序。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from bs4 import BeautifulSoup

_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_FLIGHT_RE = re.compile(r"\b([A-Z]{2}\d{3,4})\b")
_PRICE_RE = re.compile(r"[¥￥]\s*([0-9][0-9,]*)")
_DISCOUNT_RE = re.compile(r"经济舱\s*([0-9.]+)折")


@dataclass(frozen=True)
class FlightOption:
    """单条航班候选；缺失字段使用 None，不猜测。"""

    airline: str | None
    flight_number: str | None
    aircraft: str | None
    departure_time: str | None
    arrival_time: str | None
    departure_airport: str | None
    arrival_airport: str | None
    departure_terminal: str | None
    arrival_terminal: str | None
    price_cny: int | None
    cabin: str | None
    discount: float | None
    duration_minutes: int | None
    is_shared: bool = False
    is_transfer: bool = False
    remaining_seats: int | None = None
    labels: tuple[str, ...] = field(default_factory=tuple)
    raw_text: str = ""


@dataclass(frozen=True)
class FlightPreference:
    """排序偏好；固定权重使排名可复现、可解释。

    字段分两类:
      * 硬过滤 (filter): 不满足的航班直接剔除
      * 排序偏好 (sort): 在剩余航班里排序

    过滤与排序独立: rank_flights 先按 filter 集合筛, 再按 sort_by 排序。
    空结果保留原始空集返回, 不抛错。
    """

    sort_by: str = "balanced"
    max_price_cny: int | None = None
    min_price_cny: int | None = None
    earliest_departure: str | None = None
    latest_arrival: str | None = None
    depart_window: tuple[int, int] | None = None  # (start_min, end_min) 半开 [start, end)
    arrive_window: tuple[int, int] | None = None
    max_duration_minutes: int | None = None
    min_seats: int | None = None
    # 航司过滤: 互斥, 任一为空另一必空; 都为空=不限制
    airlines_whitelist: tuple[str, ...] = ()
    airlines_blacklist: tuple[str, ...] = ()
    # 经停过滤: True=只要直飞, False=只要经停, None=都接受
    direct_flight_only: bool = False
    # 共享航班: True=只要共享, False=只要非共享, None=都接受
    exclude_shared: bool = False


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _first_time(text: str) -> str | None:
    match = _TIME_RE.search(text)
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else None


def _duration(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    departure = datetime.strptime(start, "%H:%M")
    arrival = datetime.strptime(end, "%H:%M")
    if arrival < departure:
        arrival += timedelta(days=1)
    return int((arrival - departure).total_seconds() // 60)


def _airport(box) -> tuple[str | None, str | None]:
    if not box:
        return None, None
    return _text(box.select_one(".airport .name")) or None, _text(box.select_one(".airport .terminal")) or None


def parse_flight_item(item) -> FlightOption:
    airline = _text(item.select_one(".flight-airline > .airline-name > span")) or None
    plane_text = _text(item.select_one(".plane-No"))
    flight_match = _FLIGHT_RE.search(plane_text)
    flight_number = flight_match.group(1) if flight_match else None
    aircraft = plane_text.replace(flight_number or "", "", 1).strip() or None
    detail = item.select_one(".flight-detail")
    boxes = detail.select(".depart-box, .arrive-box") if detail else []
    departure_time = _first_time(_text(boxes[0].select_one(".time"))) if len(boxes) > 0 else None
    arrival_time = _first_time(_text(boxes[1].select_one(".time"))) if len(boxes) > 1 else None
    departure_airport, departure_terminal = _airport(boxes[0] if len(boxes) > 0 else None)
    arrival_airport, arrival_terminal = _airport(boxes[1] if len(boxes) > 1 else None)
    full_text = _text(item)
    price_match = _PRICE_RE.search(_text(item.select_one(".flight-price .price")))
    discount_match = _DISCOUNT_RE.search(_text(item.select_one(".sub-price-detail")))
    seats_match = re.search(r"剩\s*(\d+)\s*张", full_text)
    labels = tuple(_text(node) for node in item.select(".flight-tags .tag, .tag-best-choice-flights") if _text(node))
    return FlightOption(
        airline=airline, flight_number=flight_number, aircraft=aircraft,
        departure_time=departure_time, arrival_time=arrival_time,
        departure_airport=departure_airport, arrival_airport=arrival_airport,
        departure_terminal=departure_terminal, arrival_terminal=arrival_terminal,
        price_cny=int(price_match.group(1).replace(",", "")) if price_match else None,
        cabin="经济舱" if "经济舱" in full_text else None,
        discount=float(discount_match.group(1)) if discount_match else None,
        duration_minutes=_duration(departure_time, arrival_time),
        is_shared="共享" in full_text,
        is_transfer="中转" in full_text or "经停" in full_text,
        remaining_seats=int(seats_match.group(1)) if seats_match else None,
        labels=labels, raw_text=full_text,
    )


def parse_flight_html(html: str, *, max_items: int | None = None) -> list[FlightOption]:
    """解析 HTML 快照，不抓取网页；结构变化时保留已识别字段。"""
    items = BeautifulSoup(html, "html.parser").select(".flight-list .flight-item, .flight-item")
    options = [parse_flight_item(item) for item in items]
    return options[:max_items] if max_items is not None else options


def _clock_minutes(value: str | None) -> int | None:
    if not value:
        return None
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _in_window(value: str | None, window: tuple[int, int] | None) -> bool:
    """判断 ``HH:MM`` 时间是否在 [start, end) 半开窗口内。

    跨天处理: end <= start 时表示"跨天到次日", 例如 22:00-06:00 表示 22:00-23:59 + 00:00-05:59。
    """
    if window is None:
        return True
    minutes = _clock_minutes(value)
    if minutes is None:
        return False
    start, end = window
    if start <= end:
        return start <= minutes < end
    # 跨天窗口: [start, 24:00) ∪ [00:00, end)
    return minutes >= start or minutes < end


def rank_flights(options: Iterable[FlightOption], preference: FlightPreference = FlightPreference()) -> list[FlightOption]:
    """先按 ``preference`` 硬过滤, 再按 ``sort_by`` 排序。

    硬过滤顺序（按字段语义）:
      1. 价格区间 (min_price_cny, max_price_cny) - 缺失价格的保留(避免误杀)
      2. 航司白/黑名单 - 缺失航司的当白名单处理
      3. 直飞/经停 (direct_flight_only) - 缺失此字段的当非直飞, 直飞模式直接剔除
      4. 共享航班 (exclude_shared) - 缺失此字段默认保留
      5. 时间窗口 (depart_window, arrive_window) - 缺失时间的直接剔除
      6. 起降最值 (earliest_departure, latest_arrival) - 缺失时间的直接剔除
      7. 时长上限 (max_duration_minutes) - 缺失时长的直接剔除
      8. 最低余票 (min_seats) - 缺失余票默认保留

    排序: 缺字段排在已知字段之后 (原有逻辑)。
    """
    candidates = list(options)

    if preference.max_price_cny is not None:
        candidates = [x for x in candidates if x.price_cny is not None and x.price_cny <= preference.max_price_cny]
    if preference.min_price_cny is not None:
        candidates = [x for x in candidates if x.price_cny is not None and x.price_cny >= preference.min_price_cny]

    if preference.airlines_whitelist:
        wanted = {a.strip() for a in preference.airlines_whitelist if a.strip()}
        if wanted:
            candidates = [x for x in candidates if x.airline in wanted]
    if preference.airlines_blacklist:
        blocked = {a.strip() for a in preference.airlines_blacklist if a.strip()}
        if blocked:
            candidates = [x for x in candidates if x.airline not in blocked]

    if preference.direct_flight_only:
        # 缺失 is_transfer 字段保守视为经停; 直飞模式直接剔除
        candidates = [x for x in candidates if x.is_transfer is False]
    if preference.exclude_shared:
        candidates = [x for x in candidates if not x.is_shared]

    if preference.depart_window is not None:
        candidates = [x for x in candidates if _in_window(x.departure_time, preference.depart_window)]
    if preference.arrive_window is not None:
        candidates = [x for x in candidates if _in_window(x.arrival_time, preference.arrive_window)]

    earliest = _clock_minutes(preference.earliest_departure)
    latest = _clock_minutes(preference.latest_arrival)
    if earliest is not None:
        candidates = [x for x in candidates if (value := _clock_minutes(x.departure_time)) is not None and value >= earliest]
    if latest is not None:
        candidates = [x for x in candidates if (value := _clock_minutes(x.arrival_time)) is not None and value <= latest]

    if preference.max_duration_minutes is not None:
        candidates = [x for x in candidates if x.duration_minutes is not None and x.duration_minutes <= preference.max_duration_minutes]

    if preference.min_seats is not None:
        candidates = [x for x in candidates if x.remaining_seats is None or x.remaining_seats >= preference.min_seats]

    def key(item: FlightOption):
        price = item.price_cny if item.price_cny is not None else 10**9
        duration = item.duration_minutes if item.duration_minutes is not None else 10**9
        departure = _clock_minutes(item.departure_time) if item.departure_time else 10**9
        transfer_penalty = 1 if item.is_transfer else 0
        if preference.sort_by == "price":
            return price, transfer_penalty, duration, departure
        if preference.sort_by == "duration":
            return transfer_penalty, duration, price, departure
        if preference.sort_by == "departure":
            return transfer_penalty, departure, price, duration
        return transfer_penalty, price * 0.55 + duration * 0.35 + departure * 0.10, price, duration

    return sorted(candidates, key=key)


def explain_filter_rejection(option: FlightOption, preference: FlightPreference) -> str:
    """说明单个航班为什么被过滤掉。返回空字符串 = 没被过滤。

    用于 trace 报告, 让用户知道为什么自己心水的航班不在列表里。
    """
    reasons: list[str] = []
    if preference.max_price_cny is not None and option.price_cny is not None and option.price_cny > preference.max_price_cny:
        reasons.append(f"价格¥{option.price_cny}超过上限¥{preference.max_price_cny}")
    if preference.min_price_cny is not None and option.price_cny is not None and option.price_cny < preference.min_price_cny:
        reasons.append(f"价格¥{option.price_cny}低于下限¥{preference.min_price_cny}")
    if preference.airlines_whitelist and option.airline not in {a.strip() for a in preference.airlines_whitelist}:
        reasons.append(f"航司{option.airline}不在白名单")
    if preference.airlines_blacklist and option.airline in {a.strip() for a in preference.airlines_blacklist}:
        reasons.append(f"航司{option.airline}在黑名单")
    if preference.direct_flight_only and option.is_transfer:
        reasons.append("要求直飞, 但此航班经停")
    if preference.exclude_shared and option.is_shared:
        reasons.append("要求排除共享航班")
    if preference.depart_window is not None and not _in_window(option.departure_time, preference.depart_window):
        reasons.append(f"出发时间{option.departure_time}不在窗口")
    if preference.arrive_window is not None and not _in_window(option.arrival_time, preference.arrive_window):
        reasons.append(f"到达时间{option.arrival_time}不在窗口")
    if preference.max_duration_minutes is not None and option.duration_minutes is not None and option.duration_minutes > preference.max_duration_minutes:
        reasons.append(f"时长{option.duration_minutes}分钟超过上限{preference.max_duration_minutes}")
    if preference.min_seats is not None and option.remaining_seats is not None and option.remaining_seats < preference.min_seats:
        reasons.append(f"余票{option.remaining_seats}张低于下限{preference.min_seats}")
    return "; ".join(reasons)


def explain_ranking(option: FlightOption) -> str:
    """只根据已解析字段产生说明，不推测库存和价格。"""
    details = []
    if option.price_cny is not None:
        details.append(f"价格¥{option.price_cny}")
    if option.duration_minutes is not None:
        details.append(f"时长{option.duration_minutes}分钟")
    if option.departure_time and option.arrival_time:
        details.append(f"{option.departure_time}-{option.arrival_time}")
    if option.is_shared:
        details.append("共享航班")
    if option.remaining_seats is not None:
        details.append(f"余{option.remaining_seats}张")
    return "；".join(details) or "可用字段不足，未做推断"

