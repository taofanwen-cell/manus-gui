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
    """价格 -> 元 (四舍五入到整元).

    优先 ``priceInfo`` (券后显示价字符串, 如 "74.6" / "1.88"): 这是页面上
    **用户实际看到**的价格, 语义最准。没有再用 ``price`` (整数分, 原价口径)。

    历史 bug (2026-09-10 修): 旧实现 ``int(float(pinfo))`` 对小数**截断**,
    "124.99" -> 124, "1.88" -> 1, "60.16" -> 60 —— 这就是"价格不明细"的来源。
    改成 ``round()`` 后 "1.88" -> 2, 虽然整数元仍丢小数, 但至少不做单向截断。

    注意 ``priceInfo`` 与 ``price`` 可能**不一致** (如 priceInfo="50.4" vs
    price=5960 分 =59.6 元), 前者是券后价、后者是原价, 不可互换。
    本函数固定取 ``priceInfo`` 优先, 回退 ``price`` 时用 round 而非截断。
    """
    pinfo = (goods.get("priceInfo") or "").strip()
    if pinfo and pinfo not in {"0", "0.0", "0.00"}:
        try:
            return int(round(float(pinfo)))
        except ValueError:
            pass
    price = goods.get("price")
    if isinstance(price, (int, float)) and price:
        # price 单位是分, 转元四舍五入
        return int(round(price / 100))
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

    # --- 结构化来源 (window.rawData) 的附加字段; 正则路径拿不到时为 None ---
    #: 销量数据来自哪里: "json" = window.rawData 结构化字段 (可信);
    #: "text" = 正文正则 (可能误抓品牌累计, 见下); None = 没拿到
    sales_source: Optional[str] = None
    #: 单品销量原文 (如 "已拼5391件"), 便于排查/展示
    single_sales_text: Optional[str] = None
    #: 店铺/品牌累计销量 (纯数值, 来自 mall.mallSales)
    shop_sales: Optional[int] = None
    #: 品牌累计销量 (纯数值, 来自 oakData.burialPointInfo.brandSales)
    brand_sales: Optional[int] = None
    #: 商品是否「APP专享」(网页端隐藏价格/销量) —— 结构化标记, 比正文匹配可靠
    app_client_only: Optional[bool] = None


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


def extract_goods_detail_from_json(raw: dict) -> Optional[GoodsDetail]:
    """从 ``window.rawData`` 结构化数据提取详情页字段 —— **首选路径**.

    为什么需要它
    ------------
    正文正则 ``(?:热销|已抢|总售)\\s*([\\d.]+万?)\\+?\\s*件`` 有个致命歧义:
    详情页正文里**同时**存在单品销量 ("已拼5391件") 和品牌累计数
    ("热销148.2万+"), 正则若先命中品牌那个, 就会把 148.2万 当成单品销量。
    这正是 "OPPO 销量 1707万" 荒谬数字的来源之一。

    ``window.rawData.store.initDataObj`` 里字段是**语义明确**的 (2026-09-10 实测
    真实商品页 978586813372 解出)::

        goods.sideSalesTip            = "已拼5391件"          # ✅ 单品销量
        goods.sales_tip / salesTip    = "已拼5391件"          # ✅ 单品销量 (冗余)
        goods.appClientOnly           = 0                     # APP专享标记
        goods.minGroupPrice           = 98.9
        mall.mallSales                = 128000                # 店铺累计 (纯数值)
        mall.salesTipV2               = "本店已拼12.8万+件"    # 店铺累计
        oakData.sectionList[*].data.burialPointInfo.brandSales = 1482182  # 品牌累计 (纯数值)

    取单品销量优先级: ``sideSalesTip`` → ``sales_tip`` → ``salesTip``。
    三条都是"单品"语义, 互为冗余 (页面不同版本/埋点各存一份)。

    解析不到关键节点返回 ``None`` (让调用方回退正则路径)。
    """
    if not isinstance(raw, dict):
        return None
    try:
        init = raw["store"]["initDataObj"]
    except (KeyError, TypeError):
        return None
    if not isinstance(init, dict):
        return None

    goods = init.get("goods") if isinstance(init.get("goods"), dict) else {}
    mall = init.get("mall") if isinstance(init.get("mall"), dict) else {}

    # --- 单品销量: 三个冗余字段, 按可信度取第一个能解析出数的 ---
    single_sales: Optional[int] = None
    single_text: Optional[str] = None
    for key in ("sideSalesTip", "sales_tip", "salesTip"):
        v = goods.get(key)
        if isinstance(v, str) and v.strip():
            parsed = parse_sales_tip(v)
            if parsed is not None:
                single_sales = parsed
                single_text = v.strip()
                break

    # --- 店铺累计 / 品牌累计: 纯数值字段 (不需要解析中文) ---
    shop_sales = mall.get("mallSales") if isinstance(mall.get("mallSales"), int) else None
    brand_sales: Optional[int] = None
    sections = (init.get("oakData") or {}).get("sectionList") if isinstance(init.get("oakData"), dict) else None
    if isinstance(sections, list):
        for sec in sections:
            bp = (sec or {}).get("data", {}).get("burialPointInfo") if isinstance(sec, dict) else None
            if isinstance(bp, dict) and isinstance(bp.get("brandSales"), int):
                brand_sales = bp["brandSales"]
                break

    # --- APP专享标记 ---
    app_only: Optional[bool] = None
    if "appClientOnly" in goods:
        app_only = bool(goods.get("appClientOnly"))

    shop_name = mall.get("mallName") if isinstance(mall.get("mallName"), str) else None

    # 一个有效字段都没有 → 判定为"没解析出", 交给调用方回退
    if single_sales is None and shop_sales is None and shop_name is None:
        return None

    return GoodsDetail(
        shop_name=shop_name,
        single_sales=single_sales,
        comment_count=None,  # 评论数在 rawData 里没有可靠位置, 保留给正文正则
        sales_source="json",
        single_sales_text=single_text,
        shop_sales=shop_sales,
        brand_sales=brand_sales,
        app_client_only=app_only,
    )


def extract_goods_detail(text: str) -> GoodsDetail:
    """详情页可见正文 -> :class:`GoodsDetail` (店铺名/单品销量/评论数).

    .. note::
        **已不推荐**用于单品销量 —— 正文正则会把品牌累计数 ("热销148.2万+")
        误当单品销量 ("已拼5391件")。优先用 :func:`extract_goods_detail_from_json`,
        只在 rawData 不可用时回退到这里。此时 ``sales_source="text"`` 提醒下游
        这个数是正则猜的。

    拿不到评分 (星级数字详情页不展示), 评论数作为热度替代.
    字段抓不到就 ``None``, 不抛错.
    """
    single_sales: Optional[int] = None
    single_text: Optional[str] = None
    m = _DETAIL_SALES_RE.search(text)
    if m:
        single_sales = parse_sales_tip(m.group(1))
        single_text = m.group(0)

    comment_count: Optional[int] = None
    m = _DETAIL_COMMENT_RE.search(text)
    if m:
        comment_count = int(m.group(1).replace(",", ""))

    return GoodsDetail(
        shop_name=_extract_shop_name(text),
        single_sales=single_sales,
        comment_count=comment_count,
        sales_source="text" if single_sales is not None else None,
        single_sales_text=single_text,
    )


def extract_goods_detail_best(html: str, text: str = "") -> GoodsDetail:
    """详情页字段提取 —— **首选结构化, 回退正则** 的统一入口.

    - 先 ``window.rawData`` 结构化解析 (单品销量语义明确)
    - 结构化拿不到单品销量时, 用正文正则补齐 (评论数 / 店铺名), 但标
      ``sales_source="text"`` 让下游知道来源弱
    - 两者都没拿到 → 返回空 ``GoodsDetail`` (全 None)
    """
    raw = extract_raw_data(html) if html else None
    jd = extract_goods_detail_from_json(raw) if raw else None
    td = extract_goods_detail(text) if text else GoodsDetail(None, None, None)

    if jd is None:
        return td

    # 结构化数据里没有评论数 → 从正文补
    comment = jd.comment_count if jd.comment_count is not None else td.comment_count
    # 结构化没给出单品销量 → 退正则的数 (并标 text 来源)
    if jd.single_sales is None and td.single_sales is not None:
        return GoodsDetail(
            shop_name=jd.shop_name or td.shop_name,
            single_sales=td.single_sales,
            comment_count=comment,
            sales_source="text",
            single_sales_text=td.single_sales_text,
            shop_sales=jd.shop_sales,
            brand_sales=jd.brand_sales,
            app_client_only=jd.app_client_only,
        )

    return GoodsDetail(
        shop_name=jd.shop_name or td.shop_name,
        single_sales=jd.single_sales,
        comment_count=comment,
        sales_source=jd.sales_source,
        single_sales_text=jd.single_sales_text,
        shop_sales=jd.shop_sales,
        brand_sales=jd.brand_sales,
        app_client_only=jd.app_client_only,
    )


__all__ = [
    "extract_raw_data",
    "parse_sales_tip",
    "extract_feature_tags",
    "goods_to_competitor",
    "extract_competitors",
    "GoodsDetail",
    "extract_goods_detail",
    "extract_goods_detail_from_json",
    "extract_goods_detail_best",
]
