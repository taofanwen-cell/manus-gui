"""携程机票 URL 路由查询（无需走日历/表单）。

设计来源
--------
本模块吸收 ``case/OpenManus-rag/app/tool/url_helper.py`` 的思路：直接拼
``https://flights.ctrip.com/online/list/oneway-{dep}-{arr}?depdate=...``
跳转结果页，跳过 launch 页 + 日历选择器 + 表单填写的完整状态机。

为什么这条路线对本任务更优
--------------------------
1. **任务就是查询标准机票列表** —— 携程在线列表页（online/list）是为机器消费设计的，
   URL 参数完全覆盖单程/往返/舱位/乘客，绕过 DOM 全部交互。
2. **sandbox 可端到端验证** —— URL 构造是纯逻辑，不需要真实 Chrome / CDP / 9222。
   状态机路线只能在用户本地真机跑；URL 路线在 CI 就能跑通。
3. **policy 友好** —— 只走 ``go_to_url`` 一个 action，无 ``click_element`` /
   ``select_date``，policy 拦截压力降到最低。
4. **不替代状态机** —— 若 URL 被携程改版、结果页结构变了、城市没有机场代码，
   自动 fallback 到 ``CtripFlightFormExecutor`` 状态机路线。

公开 API
--------
- :func:`build_flight_url` —— 从结构化参数构造 URL
- :func:`build_flight_url_from_query` —— 从自然语言查询构造 URL
- :func:`parse_date` —— 把 "1月30日"/"明天"/"2026-09-25" 解析成 ``YYYY-MM-DD``
- :func:`get_city_code` —— 中文/英文城市名 → IATA 代码
- :class:`URLOnlyPolicy` —— 强制只走 go_to_url，禁止 click_element/select_date
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode


# 80 个常用城市：国内主要机场 + 国际门户。来源 = 老师 url_helper.py。
CITY_CODES: dict[str, str] = {
    # 国内主要城市
    "上海": "sha",
    "北京": "pek",
    "广州": "can",
    "深圳": "szx",
    "成都": "ctu",
    "杭州": "hgh",
    "南京": "nkg",
    "武汉": "wuh",
    "西安": "sia",
    "重庆": "ckg",
    "青岛": "tao",
    "大连": "dlc",
    "厦门": "xmn",
    "昆明": "kmg",
    "长沙": "csx",
    "郑州": "cgo",
    "天津": "tsn",
    "沈阳": "she",
    "哈尔滨": "hrb",
    "三亚": "syx",
    "海口": "hak",
    "福州": "foc",
    "济南": "tna",
    "太原": "tyn",
    "贵阳": "kwe",
    "南宁": "nng",
    "合肥": "hfe",
    "无锡": "wux",
    "宁波": "ngb",
    "温州": "wnz",
    # 国际主要城市
    "香港": "hkg",
    "澳门": "mfm",
    "台北": "tpe",
    "东京": "tyo",
    "大阪": "osa",
    "首尔": "sel",
    "新加坡": "sin",
    "曼谷": "bkk",
    "吉隆坡": "kul",
    "伦敦": "lon",
    "巴黎": "par",
    "纽约": "nyc",
    "洛杉矶": "lax",
    "悉尼": "syd",
    "墨尔本": "mel",
}

# 反向表：小写 IATA → 中文名，方便日志。
_IATA_TO_CITY: dict[str, str] = {code: name for name, code in CITY_CODES.items()}

# 相对日期关键词。
DATE_KEYWORDS: dict[str, int] = {
    "今天": 0,
    "明天": 1,
    "后天": 2,
    "大后天": 3,
}

# 携程结果页固定 base
CTRIP_FLIGHTS_BASE = "https://flights.ctrip.com/online/list"

# 不可接受的舱位默认值（经济舱）
DEFAULT_CABIN = "y"


@dataclass(frozen=True)
class FlightSearchParams:
    """机票查询参数。"""

    departure_city: str
    arrival_city: str
    departure_date: str  # YYYY-MM-DD
    return_date: Optional[str] = None  # None=单程
    cabin: str = DEFAULT_CABIN
    adult: int = 1
    child: int = 0
    infant: int = 0


@dataclass(frozen=True)
class URLOnlyPolicy:
    """URL 路线专属 policy：强制只走 go_to_url，禁止走日历/按钮交互。

    字段
    ----
    allowed_actions: 白名单（必须是 ``go_to_url`` / ``wait`` / ``extract_content``）
    forbidden_actions: 黑名单（``click_element`` / ``select_date`` / ``input_text``）
    """

    allowed_actions: frozenset[str] = field(
        default_factory=lambda: frozenset({"go_to_url", "wait", "extract_content"})
    )
    forbidden_actions: frozenset[str] = field(
        default_factory=lambda: frozenset({"click_element", "select_date", "input_text"})
    )

    def check(self, action: str) -> tuple[bool, str]:
        if action in self.forbidden_actions:
            return False, f"URL-only policy forbids {action}; use state machine path instead."
        if action not in self.allowed_actions:
            return False, f"URL-only policy only permits {sorted(self.allowed_actions)}."
        return True, "allowed"


def get_city_code(city_name: str) -> Optional[str]:
    """城市名 → IATA 代码。未识别返回 None。"""
    if not city_name:
        return None
    name = city_name.strip()
    if name in CITY_CODES:
        return CITY_CODES[name]
    # 用户给"pek"/"can" 也接受（大小写不敏感）
    lowered = name.lower()
    for cn, code in CITY_CODES.items():
        if code == lowered:
            return code
    # 最后兜底：英文/拼音当 IATA 用
    if re.fullmatch(r"[a-z]{3}", lowered):
        return lowered
    return None


def parse_date(date_str: str, *, today: Optional[datetime] = None) -> Optional[str]:
    """把多种日期格式解析成 ``YYYY-MM-DD``。

    支持:
      - ``今天/明天/后天/大后天``
      - ``1月30日`` / ``1月30号``
      - ``2026-01-30`` / ``2026/01/30``
      - ``01-30`` / ``01/30``（无年份默认今年；过期则明年）
    """
    if not date_str:
        return None
    text = date_str.strip()
    base = today if today is not None else datetime.now()

    # 按 keyword 长度倒序匹配, 否则 "后天" 会先命中 "大后天"
    for keyword in sorted(DATE_KEYWORDS, key=len, reverse=True):
        if keyword in text:
            return (base + timedelta(days=DATE_KEYWORDS[keyword])).strftime("%Y-%m-%d")

    # X月X日/号
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]?", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            target = datetime(base.year, month, day)
        except ValueError:
            return None
        if target.date() < base.date():
            try:
                target = datetime(base.year + 1, month, day)
            except ValueError:
                return None
        return target.strftime("%Y-%m-%d")

    # YYYY-MM-DD / YYYY/MM/DD
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        try:
            return f"{int(m.group(1))}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        except ValueError:
            return None

    # MM-DD / MM/DD
    m = re.search(r"(\d{1,2})[-/](\d{1,2})", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            target = datetime(base.year, month, day)
            if target.date() < base.date():
                target = datetime(base.year + 1, month, day)
            return target.strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def build_flight_url(params: FlightSearchParams) -> str:
    """构造携程结果页 URL。

    示例
    ----
    >>> p = FlightSearchParams("广州", "北京", "2026-09-25")
    >>> build_flight_url(p)
    'https://flights.ctrip.com/online/list/oneway-can-pek?depdate=2026-09-25&cabin=y&adult=1&child=0&infant=0'
    """
    dep = get_city_code(params.departure_city)
    arr = get_city_code(params.arrival_city)
    if not dep or not arr:
        raise ValueError(
            f"无法识别城市代码: departure={params.departure_city!r}, arrival={params.arrival_city!r}"
        )
    base_query = {
        "cabin": params.cabin,
        "adult": params.adult,
        "child": params.child,
        "infant": params.infant,
    }
    if params.return_date:
        route = f"round-{dep}-{arr}"
        qs = {"depdate": params.departure_date, "rdate": params.return_date, **base_query}
    else:
        route = f"oneway-{dep}-{arr}"
        qs = {"depdate": params.departure_date, **base_query}
    return f"{CTRIP_FLIGHTS_BASE}/{route}?{urlencode(qs)}"


def build_flight_url_from_query(query: str) -> Optional[str]:
    """从自然语言查询构造 URL。失败返回 None。

    支持的查询样式
    --------------
    - ``1月30日从上海到北京的机票``
    - ``用携程查明天从北京到广州的单程机票``
    - ``北京到上海 2026-09-25``
    - ``sha-pek 2026-09-25``（直接给 IATA 也接受）

    任何一步失败（无日期 / 城市不在表里）都返回 None，让调用方 fallback 到状态机。
    """
    if not query:
        return None
    text = query.strip()
    date = parse_date(text)
    if not date:
        return None
    params = _extract_route(text, date)
    if params is None:
        return None
    # 显式校验城市代码, 避免拼出 oneway-pek-火星 这种死链
    if not get_city_code(params.departure_city) or not get_city_code(params.arrival_city):
        return None
    try:
        return build_flight_url(params)
    except ValueError:
        return None


def _extract_route(text: str, date: str) -> Optional[FlightSearchParams]:
    """从文本里抠出 (出发, 到达) 城市 + 已解析好的日期。"""
    # 模式 1：从 X 到 Y
    m = re.search(r"从\s*([^\s到,，]+?)\s*到\s*([^\s的,，]+)", text)
    if m:
        return FlightSearchParams(
            departure_city=m.group(1).strip(),
            arrival_city=m.group(2).strip(),
            departure_date=date,
        )

    # 模式 2：X 到 Y（无"从"字）
    m = re.search(r"([^\s,，]+?)\s*到\s*([^\s的,，]+)", text)
    if m:
        return FlightSearchParams(
            departure_city=m.group(1).strip(),
            arrival_city=m.group(2).strip(),
            departure_date=date,
        )

    # 模式 3：IATA-IATA "sha-pek" / "sha pek"
    m = re.search(r"\b([a-z]{3})\s*[-–]\s*([a-z]{3})\b", text.lower())
    if m:
        return FlightSearchParams(
            departure_city=m.group(1),
            arrival_city=m.group(2),
            departure_date=date,
        )

    return None


def list_supported_cities() -> list[str]:
    """返回所有支持的中文城市名（按字母序）。"""
    return sorted(CITY_CODES.keys())


def iata_to_city(code: str) -> Optional[str]:
    """IATA 代码 → 中文城市名（仅命中已知项）。"""
    return _IATA_TO_CITY.get((code or "").lower())


__all__ = [
    "CITY_CODES",
    "CTRIP_FLIGHTS_BASE",
    "DEFAULT_CABIN",
    "FlightSearchParams",
    "URLOnlyPolicy",
    "build_flight_url",
    "build_flight_url_from_query",
    "get_city_code",
    "iata_to_city",
    "list_supported_cities",
    "parse_date",
]