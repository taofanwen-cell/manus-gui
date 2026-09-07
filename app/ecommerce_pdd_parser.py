"""拼多多搜索列表 HTML -> Competitor 解析器（只读，无任何写操作）。

数据来源
--------
拼多多移动端搜索页 ``https://mobile.yangkeduo.com/search_result.html``
把搜索结果以 JSON 形式直接嵌在 HTML 的 ``window.rawData = {...}`` 变量里。
**这比解析渲染后的 DOM 干净得多**：字段是结构化的 ``goodsName`` / ``price`` /
``priceInfo`` / ``salesTip`` / ``goodsID``。真机扫描脚本 ``pdd_cdp_extract.py``
存下的原始 HTML 就是这个模块的输入。

为什么这样设计（复用 ctrip 的"先钉 selector 再写提取"铁律）
-------------------------------------------------------------
1. 元素 class selector 拼多多随时变, 但内嵌 JSON 的字段名长期稳定.
2. 抓 DOM 要精确到每张卡片, 内嵌 JSON 直接给整个 list, 一次拿全.
3. 免登录就能读 (实测 ``login_wall_detected: false``).

字段映射 (诚实标注)
-------------------
* 有的 -- ``goodsID``(商品ID) / ``goodsName``(标题) / ``priceInfo``(券后显示价) /
  ``salesTip``(销量文案) / ``tagList``(促销标签) / ``linkURL``(商品链接)
* 列表页没有的 -- **店铺名 / 评分 / 评论数 / 上架天数**。但**详情页免登录能读**
  (实测 ``goods.html?goods_id=...`` 免登录, 店铺名/评论数/单品销量在渲染后的正文里),
  用 :func:`extract_goods_detail` 从详情页正文提取。列表页 ``shop_name`` 等字段保留 ``None``.
* ``supply`` -- ``price`` 是原价(分), ``priceInfo`` 是券后显示价, **取 ``priceInfo``**.
* **salesTip 语义** -- 「本店已拼 / 全店总售 / 品牌热销」是**店铺或品牌累计销量**,
  「N人想拼」是想拼人数, **都不是单品月销量**. 本模块把它解析成整数放进
  ``monthly_sales`` 字段是**借用字段名** (分析引擎只有这一个销量字段), 报告层必须
  标注真实含义, 不能当成单品月销量对外讲.

公开 API
--------
- :func:`extract_raw_data`  -- 从 HTML 抠出 ``window.rawData`` JSON
- :func:`parse_sales_tip`   -- 销量文案 -> 整数 (处理 万/亿/+/想拼/件)
- :func:`extract_feature_tags` -- 从标题抠蓝牙耳机卖点标签
- :func:`goods_to_competitor`  -- 单个商品 dict -> Competitor
- :func:`extract_competitors`  -- HTML -> list[Competitor]
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from app.ecommerce_analyzer import Competitor

# 内嵌数据容器所在的固定路径: rawData['stores']['store']['data']['ssrListData']['list']
_SSR_LIST_PATH = ("stores", "store", "data", "ssrListData", "list")

# 销量文案解析: 数字 + 可选万/亿。例如:
#   "本店已拼7148"      -> 7148
#   "本店已拼2.2万"     -> 22000
#   "本店已拼297万+"    -> 2970000
#   "17人想拼"          -> 17
#   "总售2.7万+件"      -> 27000
_SALES_TIP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万|亿)?")

# 蓝牙耳机卖点字典: (触发子串 -> 规范标签). canonical 去重.
# 注意: 入耳类型单独用 _pick_ear_type 处理 (不入耳/半入耳/入耳互斥),
# 因为 "不入耳" 是子串包含 "入耳", 标题里出现 "不入耳" 时不该再标 "入耳".
_FEATURE_TRIGGERS = (
    ("骨传导", "骨传导"),
    ("夹耳", "夹耳式"),
    ("挂脖", "挂脖式"),
    ("挂耳", "挂耳式"),
    ("头戴", "头戴式"),
    ("真无线", "真无线"),
    ("降噪", "降噪"),
    ("数显", "数显"),
    ("超长续航", "长续航"),
    ("长续航", "长续航"),
    ("续航", "长续航"),
    ("游戏", "游戏"),
    ("运动", "运动"),
    ("高音质", "高音质"),
    ("音质", "高音质"),
    ("通话", "通话"),
    ("无延迟", "低延迟"),
    ("低延迟", "低延迟"),
    ("防水", "防水"),
)
_BT_VERSION_RE = re.compile(r"蓝牙\s*\d+(?:\.\d+)?")


def extract_raw_data(html: str) -> Optional[dict]:
    """从 HTML 里抠出 ``window.rawData = {...}`` 并解析成 dict.

    找不到返回 ``None`` (不抛错, 让上层决定是报错还是给空集).
    用花括号配对 (字符串感知) 而不是简单 ``split``, 避免 JSON 内部的
    ``{}`` 干扰.
    """
    if not html:
        return None
    m = re.search(r"window\.rawData\s*=\s*", html)
    if not m:
        return None
    start = m.end()
    depth = 0
    in_str = False
    esc = False
    i = start
    while i < len(html):
        c = html[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
        i += 1
    if depth != 0:
        return None
    blob = html[start : i + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def _iter_goods_objects(obj) -> Iterable[dict]:
    """递归找所有含 goodsName+price+salesTip 的商品 dict.

    优先走 ``ssrListData.list`` (搜索结果的权威列表); 找不到再全量递归兜底
    (推荐位等也可能内嵌商品).
    """
    node = obj
    for key in _SSR_LIST_PATH:
        if not isinstance(node, dict):
            break
        node = node.get(key)
    if isinstance(node, list):
        return [g for g in node if isinstance(g, dict) and "goodsName" in g]

    # 兜底: 全量递归
    found: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            if "goodsName" in o and "price" in o and "salesTip" in o:
                found.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return found


def parse_sales_tip(text: Optional[str]) -> Optional[int]:
    """销量文案 -> 整数; 解析不出返回 ``None``.

    处理: ``万`` / ``亿`` / 后缀 ``+`` / ``人想拼`` / ``件``.
    不猜: 文案为空或没有数字就 ``None``.
    """
    if not text:
        return None
    m = _SALES_TIP_RE.search(text)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    if unit == "万":
        num *= 10_000
    elif unit == "亿":
        num *= 100_000_000
    return int(num)


def _pick_ear_type(title: str) -> Optional[str]:
    """入耳类型: 不入耳 / 半入耳 / 入耳 三者互斥, 至多返回一个.

    "不入耳" 是 "入耳" 的子串, 若直接按子串匹配会把 (不入耳, 入耳) 两个
    矛盾卖点都标出来. 所以这里做了优先级: 不入耳 > 半入耳 > 入耳.
    """
    if "不入耳" in title:
        return "不入耳"
    if "半入耳" in title:
        return "半入耳"
    if "入耳" in title:
        return "入耳"
    return None


def extract_feature_tags(title: Optional[str]) -> tuple[str, ...]:
    """从标题抠蓝牙耳机卖点标签 (返回 canonical tuple, 去重).

    不做分词, 纯子串匹配 + 蓝牙版本正则. 反馈到 ``extract_top_keywords``.
    入耳类型互斥 (见 :func:`_pick_ear_type`). 标题为空返回空 tuple.
    """
    if not title:
        return ()
    tags: list[str] = []
    seen: set[str] = set()

    m = _BT_VERSION_RE.search(title)
    if m:
        # "蓝牙 5.3" -> "蓝牙5.3" (只去空白, 保留小数点)
        ver = re.sub(r"\s+", "", m.group(0))
        tags.append(ver)
        seen.add(ver)

    ear = _pick_ear_type(title)
    if ear:
        tags.append(ear)
        seen.add(ear)

    for trigger, canon in _FEATURE_TRIGGERS:
        if trigger in title and canon not in seen:
            seen.add(canon)
            tags.append(canon)
    return tuple(tags)


def _price_to_yuan(goods: dict) -> Optional[int]:
    """价格 -> 整数元.

    优先 ``priceInfo`` (券后显示价字符串, 如 "1.88"); 没有再用 ``price`` (分).
    解析不出或为空返回 ``None``.
    """
    pinfo = (goods.get("priceInfo") or "").strip()
    if pinfo and pinfo not in {"0", "0.0", "0.00"}:
        try:
            return int(float(pinfo))
        except ValueError:
            pass
    price = goods.get("price")
    if isinstance(price, (int, float)) and price:
        return int(price) // 100
    return None


def goods_to_competitor(goods: dict) -> Competitor:
    """单个商品 dict -> :class:`Competitor`.

    字段映射:
      goods_id       <- goodsID
      title          <- goodsName
      price_cny      <- priceInfo (券后价, 整数元)
      monthly_sales  <- salesTip 解析
      url            <- linkURL
      feature_tags   <- extract_feature_tags(title)
      # 店铺名/评分/评论数/上架天数 列表页没有 -> None
    """
    goods_id = str(goods.get("goodsID") or "").strip()
    title = goods.get("goodsName") or ""
    link_url = (goods.get("linkURL") or "").strip() or None
    if link_url and not link_url.startswith("http"):
        link_url = "https://mobile.yangkeduo.com/" + link_url.lstrip("/")
    return Competitor(
        goods_id=goods_id,
        title=title,
        price_cny=_price_to_yuan(goods),
        monthly_sales=parse_sales_tip(goods.get("salesTip")),
        rating=None,
        comment_count=None,
        shop_name=None,
        url=link_url,
        days_listed=None,
        feature_tags=extract_feature_tags(title),
    )


def extract_competitors(html: str) -> list[Competitor]:
    """HTML -> list[Competitor].

    解析不出 ``window.rawData`` 返回空列表 (不抛错).
    """
    raw = extract_raw_data(html)
    if raw is None:
        return []
    return [goods_to_competitor(g) for g in _iter_goods_objects(raw)]


# ---------------------------------------------------------------------------
# 详情页字段提取 (goods.html, 免登录, 从渲染后正文提取)
# ---------------------------------------------------------------------------

# 详情页单品销量文案: "热销9.4万+件" / "已抢11.3万+件" / "总售5.3万+件" / "热销128件"
# (都是单品累计销量, 区别于列表页"本店已拼900万+件"的店铺累计——只匹配这三个前缀)
_DETAIL_SALES_RE = re.compile(r"(?:热销|已抢|总售)\s*([\d.]+万?\+?)\s*件")

# 评论数: "商品评价(14,870)"
_DETAIL_COMMENT_RE = re.compile(r"商品评价\s*\(\s*([\d,]+)\s*\)")

# 店铺名在"进店逛逛"上方, 跳过这些含销量/评价/标签关键词的行
_SHOP_SKIP_KW = (
    "本店", "全店", "已拼", "热销", "粉丝", "种草", "评价",
    "正品", "音质", "外观", "质量", "续航", "耳机", "清晰", "低音",
    "功能", "戴起来", "杂音", "浑厚",
)


@dataclass(frozen=True)
class GoodsDetail:
    """详情页能拿到的补充字段 (列表页没有)."""

    shop_name: Optional[str]   # 店铺名
    single_sales: Optional[int]   # 单品销量 (热销N件)
    comment_count: Optional[int]  # 评论数 (商品评价(N))


def _extract_shop_name(text: str) -> Optional[str]:
    """从详情页正文提取店铺名 ("进店逛逛"上方第一行非销量/标签文本)."""
    idx = text.find("进店逛逛")
    if idx < 0:
        return None
    for line in reversed(text[:idx].split("\n")):
        line = line.strip()
        if not line:
            continue
        if any(k in line for k in _SHOP_SKIP_KW):
            continue
        return line
    return None


def extract_goods_detail(text: str) -> GoodsDetail:
    """详情页可见正文 -> :class:`GoodsDetail` (店铺名/单品销量/评论数).

    拿不到评分 (星级数字详情页不展示), 评论数作为热度替代.
    字段抓不到就 ``None``, 不抛错.
    """
    single_sales: Optional[int] = None
    m = _DETAIL_SALES_RE.search(text)
    if m:
        single_sales = parse_sales_tip(m.group(1))

    comment_count: Optional[int] = None
    m = _DETAIL_COMMENT_RE.search(text)
    if m:
        comment_count = int(m.group(1).replace(",", ""))

    return GoodsDetail(
        shop_name=_extract_shop_name(text),
        single_sales=single_sales,
        comment_count=comment_count,
    )


__all__ = [
    "extract_raw_data",
    "parse_sales_tip",
    "extract_feature_tags",
    "goods_to_competitor",
    "extract_competitors",
    "GoodsDetail",
    "extract_goods_detail",
]
