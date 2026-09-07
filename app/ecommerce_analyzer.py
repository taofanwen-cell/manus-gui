"""拼多多竞品调研 - 多维度分析引擎。

设计原则 (复用 ``app/ctrip_flights.py``)
----------------------------------------
* ``rank_flights`` 是"先过滤后排序", 本模块 ``analyze`` 同样:
  1. 按 ``AnalysisPreference`` 的硬过滤字段筛掉不合格商品
  2. 对剩余商品做多维度聚合 (价格分布 / 高频卖点 / 店铺排行)
  3. 按 ``sort_by`` 排序选 top_n
* **严格相等谓词 + bool 短路** (跨项目硬规则第 2 条):
  销量 ``0`` 与 ``None`` 严格区分, 价格 ``None`` 不参与分位数.
* **缺失字段保守处理** (复用 ctrip_flights 的策略):
  评分为 ``None`` 视为"未知", 不参与平均;``min_rating`` 过滤时**剔除未知评分**.
  因为"评分低"和"还没评分"是两种不同的商品状态, 不能混用.
* **不做 LLM 推断** (跨项目硬规则第 11 条):
  上架时间字段没解析出来就保留 ``None``, 不猜"3 天前就是 N 天".

字段验证说明 (诚实标注)
------------------------
拼多多页面结构未公开文档化, 以下字段分两类:

* **未验证** —— 真机采集前不要用于生产:
  ``monthly_sales`` 解析正则 / ``days_listed`` 解析正则 / ``feature_tags`` 切分逻辑
* **类型签名可靠** —— 这些字段类型稳定:
  商品 ID / 标题 / 价格 / 店名 / 评分区间 [0.0, 5.0]
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Competitor:
    """单个竞品商品。

    Attributes
    ----------
    goods_id:
        拼多多商品 ID (纯数字字符串), 必填, 用于去重.
    title:
        商品标题, 必填, 用于关键词频次分析与去重.
    price_cny:
        价格 (单位: 元), 可空. 拼多多动态定价, 缺价也是真实状态.
    monthly_sales:
        月销量 (整数, "已售 X 件" 解析), 可空. ``None`` 即不知道.
    rating:
        店铺 / 商品评分, 范围 [0.0, 5.0], 可空.
    comment_count:
        评论数, 整数, 可空. 跟 ``monthly_sales`` 是不同指标, 不要并.
    shop_name:
        店铺名, 可空. 多个商品可能同店, 聚合时按此聚合.
    url:
        商品详情 URL, 可空, 用于追溯.
    days_listed:
        上架天数, 整数, 可空. 没有就 ``None``, 不猜.
    feature_tags:
        卖点标签元组 (例如 ``("降噪", "蓝牙 5.3")``), 可空 tuple.
    """

    goods_id: str
    title: str
    price_cny: int | None = None
    monthly_sales: int | None = None
    rating: float | None = None
    comment_count: int | None = None
    shop_name: str | None = None
    url: str | None = None
    days_listed: int | None = None
    feature_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """轻量校验: 不抛错, 只让 dataclass 不破坏 frozen contract.

        真校验在 :func:`competitor_from_dict` / :class:`AnalysisPreference.validate` 里做.
        """
        if not self.goods_id or not str(self.goods_id).isdigit():
            # 不抛错让分析能跑完, 但给 warning 用
            object.__setattr__(self, "_invalid_id", True)
        if self.rating is not None and not (0.0 <= self.rating <= 5.0):
            object.__setattr__(self, "_invalid_rating", True)


@dataclass(frozen=True)
class AnalysisPreference:
    """分析师偏好, 跟 :class:`ctrip_flights.FlightPreference` 同构.

    字段分两类:
      * **硬过滤** (``min_price_cny`` / ``max_price_cny`` / ``min_sales`` /
        ``min_rating`` / ``exclude_missing_rating`` / ``exclude_missing_sales``)
      * **排序 / 输出** (``sort_by`` / ``top_n`` / ``keyword_top_n``)

    ``min_rating`` 默认 ``None`` (=不限); 如果传 4.0, 缺评分的商品**默认剔除**
    (因为"评分低于 4.0"和"还没评分"不是同一状态, 不能并集处理).
    """

    sort_by: str = "balanced"  # balanced / sales / price / rating / newest
    top_n: int = 20
    keyword_top_n: int = 15

    min_price_cny: int | None = None
    max_price_cny: int | None = None
    min_sales: int | None = None
    min_rating: float | None = None
    # 显式控制"缺字段是否被剔除". 默认: 缺评分剔除, 缺销量保留.
    exclude_missing_rating: bool = True
    exclude_missing_sales: bool = False

    def validate(self) -> None:
        """启动期校验. 不合法直接报错, 不静默兜底."""
        if self.top_n < 1:
            raise ValueError(f"top_n 必须 >= 1, 收到 {self.top_n}")
        if self.keyword_top_n < 1:
            raise ValueError(f"keyword_top_n 必须 >= 1, 收到 {self.keyword_top_n}")
        if self.sort_by not in {"balanced", "sales", "price", "rating", "newest"}:
            raise ValueError(
                f"sort_by 必须是 balanced/sales/price/rating/newest, 收到 {self.sort_by!r}"
            )
        if self.min_price_cny is not None and self.min_price_cny < 0:
            raise ValueError(f"min_price_cny 不能为负, 收到 {self.min_price_cny}")
        if self.max_price_cny is not None and self.min_price_cny is not None:
            if self.max_price_cny < self.min_price_cny:
                raise ValueError(
                    f"max_price_cny({self.max_price_cny}) < min_price_cny({self.min_price_cny})"
                )
        if self.min_rating is not None and not (0.0 <= self.min_rating <= 5.0):
            raise ValueError(f"min_rating 必须在 [0.0, 5.0], 收到 {self.min_rating}")


# ---------------------------------------------------------------------------
# 报告数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceDistribution:
    """价格分布, 给报告的"4 档价格带"图表用."""

    bucket_under_100: int  # <¥100
    bucket_100_300: int  # ¥100-300
    bucket_300_800: int  # ¥300-800
    bucket_over_800: int  # >¥800
    median: int | None
    p25: int | None
    p75: int | None
    min: int | None
    max: int | None


@dataclass(frozen=True)
class TopKeyword:
    """高频卖点条目."""

    keyword: str
    count: int
    pct: float  # 0.0 - 1.0, 占总样本比例


@dataclass(frozen=True)
class TopShop:
    """头部店铺条目."""

    shop_name: str
    goods_count: int
    total_monthly_sales: int
    avg_rating: float | None


@dataclass(frozen=True)
class AnalysisReport:
    """分析报告. 给 Day 3 报告生成器喂数据."""

    sample_size: int  # 过滤前样本数
    filtered_size: int  # 过滤后样本数
    summary: str  # 一句话摘要, 模板渲染时直接用
    price_dist: PriceDistribution | None
    top_keywords: tuple[TopKeyword, ...]
    top_shops: tuple[TopShop, ...]
    top_competitors: tuple[Competitor, ...]  # 排序后的 top_n
    warnings: tuple[str, ...]  # 数据质量问题


# ---------------------------------------------------------------------------
# 过滤 / 排序
# ---------------------------------------------------------------------------


def _price_filter(c: Competitor, pref: AnalysisPreference) -> bool:
    if pref.min_price_cny is not None and c.price_cny is not None and c.price_cny < pref.min_price_cny:
        return False
    if pref.max_price_cny is not None and c.price_cny is not None and c.price_cny > pref.max_price_cny:
        return False
    return True


def _sales_filter(c: Competitor, pref: AnalysisPreference) -> bool:
    """返回 True=通过过滤. 缺销量时:
    - ``exclude_missing_sales=True``  → 不通过 (False)
    - ``exclude_missing_sales=False`` → 通过 (True, 把"未知"当"通过")
    """
    if pref.min_sales is None:
        return True
    if c.monthly_sales is None:
        return not pref.exclude_missing_sales
    return c.monthly_sales >= pref.min_sales


def _rating_filter(c: Competitor, pref: AnalysisPreference) -> bool:
    """返回 True=通过过滤. 缺评分时:
    - ``exclude_missing_rating=True``  → 不通过 (False, 默认, "评分低" ≠ "还没评分")
    - ``exclude_missing_rating=False`` → 通过 (True, 用户显式接受缺失)
    """
    if pref.min_rating is None:
        return True
    if c.rating is None:
        return not pref.exclude_missing_rating
    return c.rating >= pref.min_rating


def explain_competitor_rejection(c: Competitor, pref: AnalysisPreference) -> str:
    """复用 ``ctrip_flights.explain_filter_rejection`` 的设计.

    返回 ``""`` 表示未被过滤, 用户读 trace 一眼知道为啥某个商品不在列表里.
    """
    reasons: list[str] = []
    if pref.min_price_cny is not None and c.price_cny is not None and c.price_cny < pref.min_price_cny:
        reasons.append(f"价格¥{c.price_cny}低于下限¥{pref.min_price_cny}")
    if pref.max_price_cny is not None and c.price_cny is not None and c.price_cny > pref.max_price_cny:
        reasons.append(f"价格¥{c.price_cny}高于上限¥{pref.max_price_cny}")
    if pref.min_sales is not None:
        if c.monthly_sales is None and pref.exclude_missing_sales:
            reasons.append("月销量未知, 默认剔除")
        elif c.monthly_sales is not None and c.monthly_sales < pref.min_sales:
            reasons.append(f"月销量{c.monthly_sales}低于下限{pref.min_sales}")
    if pref.min_rating is not None:
        if c.rating is None and pref.exclude_missing_rating:
            reasons.append("评分未知, 默认剔除")
        elif c.rating is not None and c.rating < pref.min_rating:
            reasons.append(f"评分{c.rating:.1f}低于下限{pref.min_rating:.1f}")
    return "; ".join(reasons)


def rank_competitors(
    competitors: Iterable[Competitor], pref: AnalysisPreference = AnalysisPreference()
) -> list[Competitor]:
    """复用 ``ctrip_flights.rank_flights`` 思路: 先过滤再排序.

    排序键:
      * ``sales``    → 月销量降序, 缺销量排最后
      * ``price``    → 价格升序, 缺价格排最后
      * ``rating``   → 评分降序, 缺评分排最后
      * ``newest``   → 上架天数升序 (新 → 旧), 缺 ``days_listed`` 排最后
      * ``balanced`` → 综合 (0.55·价格 + 0.35·销量 + 0.10·评分)
    """
    pref.validate()
    candidates = list(competitors)

    candidates = [c for c in candidates if _price_filter(c, pref)]
    candidates = [c for c in candidates if _sales_filter(c, pref)]
    candidates = [c for c in candidates if _rating_filter(c, pref)]

    def key(c: Competitor):
        price = c.price_cny if c.price_cny is not None else 10**9
        sales = c.monthly_sales if c.monthly_sales is not None else 0
        rating = c.rating if c.rating is not None else 0.0
        days = c.days_listed if c.days_listed is not None else 10**9
        if pref.sort_by == "sales":
            return (-sales, price, -rating)
        if pref.sort_by == "price":
            return (price, -sales, -rating)
        if pref.sort_by == "rating":
            return (-rating, -sales, price)
        if pref.sort_by == "newest":
            return (days, -sales, price)
        # balanced
        return (price * 0.55 - sales * 0.35 - rating * 0.10, price, -sales)

    return sorted(candidates, key=key)


# ---------------------------------------------------------------------------
# 多维度聚合
# ---------------------------------------------------------------------------


def _percentile(values: list[int], pct: float) -> int | None:
    """算分位数. 跟 numpy.percentile 同语义, ``linear`` 插值; 这里用 ``nearest``.

    空列表返回 ``None`` (参与比较时不抛错).
    """
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    sorted_vals = sorted(values)
    # 用最近索引法 (跟"中位数取中点"等价, 比 numpy 简单)
    idx = max(0, min(len(sorted_vals) - 1, int(round((pct / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def compute_price_distribution(prices: list[int | None]) -> PriceDistribution:
    """算价格分布 + 4 档价格带.

    价格为 ``None`` 的不参与 (缺价无法定档).
    """
    valid = sorted([p for p in prices if p is not None])
    return PriceDistribution(
        bucket_under_100=sum(1 for p in valid if p < 100),
        bucket_100_300=sum(1 for p in valid if 100 <= p < 300),
        bucket_300_800=sum(1 for p in valid if 300 <= p < 800),
        bucket_over_800=sum(1 for p in valid if p >= 800),
        median=_percentile(valid, 50),
        p25=_percentile(valid, 25),
        p75=_percentile(valid, 75),
        min=valid[0] if valid else None,
        max=valid[-1] if valid else None,
    )


def extract_top_keywords(competitors: Iterable[Competitor], top_n: int) -> tuple[TopKeyword, ...]:
    """聚合 ``feature_tags``, 频次降序排 top_n.

    不对标题做分词 (中文分词不可靠且会带来 dict 依赖); 仅聚合显式标签.
    真机数据如果 feature_tags 经常为空, 应该用词频分析 - 那是 Day 4 的事.
    """
    counter: Counter[str] = Counter()
    total = 0
    for c in competitors:
        total += 1
        # 严格相等: tuple 里每个 tag 完整算一次 (不做归一化 / 去重)
        for tag in c.feature_tags:
            tag = (tag or "").strip()
            if tag:
                counter[tag] += 1
    if total == 0:
        return ()
    return tuple(
        TopKeyword(keyword=k, count=v, pct=v / total)
        for k, v in counter.most_common(top_n)
    )


def extract_top_shops(competitors: Iterable[Competitor], top_n: int) -> tuple[TopShop, ...]:
    """按 ``shop_name`` 聚合.

    按``总月销量``降序; 缺店名的商品视为匿名, 不聚合.
    """
    by_shop: dict[str, list[Competitor]] = {}
    for c in competitors:
        name = (c.shop_name or "").strip()
        if not name:
            continue
        by_shop.setdefault(name, []).append(c)

    rows: list[TopShop] = []
    for name, items in by_shop.items():
        total = sum((c.monthly_sales or 0) for c in items)
        ratings = [c.rating for c in items if c.rating is not None]
        avg = sum(ratings) / len(ratings) if ratings else None
        rows.append(
            TopShop(
                shop_name=name,
                goods_count=len(items),
                total_monthly_sales=total,
                avg_rating=avg,
            )
        )
    rows.sort(key=lambda r: (-r.total_monthly_sales, -r.goods_count))
    return tuple(rows[:top_n])


# ---------------------------------------------------------------------------
# 入口: analyze()
# ---------------------------------------------------------------------------


def _check_data_quality(competitors: list[Competitor]) -> tuple[str, ...]:
    """数据质量问题: 报告里给 user's warning, 不阻塞分析."""
    warnings: list[str] = []
    total = len(competitors)
    if total == 0:
        warnings.append("样本为空, 整个报告没有任何数据")
        return tuple(warnings)
    no_price = sum(1 for c in competitors if c.price_cny is None)
    no_sales = sum(1 for c in competitors if c.monthly_sales is None)
    no_rating = sum(1 for c in competitors if c.rating is None)
    if no_price > 0:
        warnings.append(f"{no_price}/{total} 个商品缺价格, 不参与价格分布")
    if no_sales > total // 2:
        warnings.append(f"{no_sales}/{total} 个商品缺月销量, 头部店铺榜可能不准确")
    if no_rating > total // 2:
        warnings.append(f"{no_rating}/{total} 个商品缺评分, 评分分布不可信")
    bad_id = sum(1 for c in competitors if hasattr(c, "_invalid_id") and c._invalid_id)
    if bad_id:
        warnings.append(f"{bad_id} 个商品 ID 不合法 (非纯数字)")
    return tuple(warnings)


def _build_summary(
    filtered: list[Competitor], price_dist: PriceDistribution | None, top_keywords: tuple[TopKeyword, ...]
) -> str:
    if not filtered:
        return "过滤后没有商品满足条件."
    price_txt = (
        f"价格区间 ¥{price_dist.min}-¥{price_dist.max} (中位数 ¥{price_dist.median})"
        if price_dist and price_dist.median is not None
        else "价格数据不全"
    )
    kw_txt = (
        f"高频卖点: {top_keywords[0].keyword}({top_keywords[0].count} 个商品), {top_keywords[1].keyword}({top_keywords[1].count})"
        if len(top_keywords) >= 2
        else (
            f"高频卖点: {top_keywords[0].keyword}({top_keywords[0].count} 个商品)"
            if top_keywords
            else "卖点数据为空"
        )
    )
    return f"{len(filtered)} 个商品达标. {price_txt}. {kw_txt}."


def analyze(
    competitors: Iterable[Competitor],
    preference: AnalysisPreference = AnalysisPreference(),
) -> AnalysisReport:
    """对竞品集做"先过滤后分析"。

    流程 (复用 ``rank_flights`` + ``FlightPreference`` 的工程化模式):
      1. 校验 ``preference``
      2. 过滤 (``price / sales / rating`` 三个硬约束)
      3. 计算价格分布 + 高频卖点 + 头部店铺
      4. 按 ``sort_by`` 排序, 取 ``top_n``
      5. 数据质量 warning
      6. 组装 ``AnalysisReport``
    """
    preference.validate()
    raw = list(competitors)

    filtered = [c for c in raw if _price_filter(c, preference)]
    filtered = [c for c in filtered if _sales_filter(c, preference)]
    filtered = [c for c in filtered if _rating_filter(c, preference)]

    ranked_full = rank_competitors(filtered, preference)
    top = ranked_full[: preference.top_n]

    price_dist = compute_price_distribution([c.price_cny for c in filtered])
    top_keywords = extract_top_keywords(filtered, preference.keyword_top_n)
    top_shops = extract_top_shops(filtered, top_n=10)

    warnings = _check_data_quality(raw)
    summary = _build_summary(filtered, price_dist, top_keywords)

    return AnalysisReport(
        sample_size=len(raw),
        filtered_size=len(filtered),
        summary=summary,
        price_dist=price_dist,
        top_keywords=top_keywords,
        top_shops=top_shops,
        top_competitors=tuple(top),
        warnings=warnings,
    )


__all__ = [
    "Competitor",
    "AnalysisPreference",
    "PriceDistribution",
    "TopKeyword",
    "TopShop",
    "AnalysisReport",
    "rank_competitors",
    "analyze",
    "compute_price_distribution",
    "extract_top_keywords",
    "extract_top_shops",
    "explain_competitor_rejection",
]
