"""Day 2: 分析引擎单测。

跟 Day 1 同风格: 三段式 (排序/聚合/解释) + 诚实边界 case。
"""
from __future__ import annotations

import pytest

from app.ecommerce_analyzer import (
    AnalysisPreference,
    AnalysisReport,
    Competitor,
    analyze,
    compute_price_distribution,
    explain_competitor_rejection,
    extract_top_keywords,
    extract_top_shops,
    rank_competitors,
)


# 复用同一种样本: 一个真实场景的"20 个蓝牙耳机"模拟数据
SAMPLE = [
    Competitor("1001", "蓝牙耳机 主动降噪 蓝牙5.3", price_cny=189, monthly_sales=5000,
               rating=4.8, comment_count=3200, shop_name="数码先锋店", days_listed=15,
               feature_tags=("主动降噪", "蓝牙5.3", "长续航")),
    Competitor("1002", "无线蓝牙耳机 续航30小时", price_cny=99, monthly_sales=12000,
               rating=4.6, comment_count=8500, shop_name="数码先锋店", days_listed=60,
               feature_tags=("蓝牙5.3", "长续航")),
    Competitor("1003", "AirPro 真无线 主动降噪", price_cny=399, monthly_sales=2500,
               rating=4.9, comment_count=1200, shop_name="高端数码", days_listed=180,
               feature_tags=("主动降噪", "高端")),
    Competitor("1004", "运动蓝牙耳机 IPX7防水", price_cny=149, monthly_sales=3000,
               rating=4.5, comment_count=900, shop_name="运动达人", days_listed=30,
               feature_tags=("防水", "运动")),
    Competitor("1005", "头戴式降噪耳机", price_cny=899, monthly_sales=400,
               rating=4.7, comment_count=300, shop_name="高端数码", days_listed=240,
               feature_tags=("主动降噪", "头戴式")),
    Competitor("1006", "入耳式蓝牙耳机", price_cny=49, monthly_sales=25000,
               rating=4.3, comment_count=15000, shop_name="平价优选", days_listed=20,
               feature_tags=("蓝牙5.3",)),
    Competitor("1007", "蓝牙耳机 主动降噪", price_cny=259, monthly_sales=800,
               rating=4.6, comment_count=600, shop_name=None, days_listed=90,
               feature_tags=("主动降噪",)),  # 缺店名 = 匿名
    Competitor("1008", "骨传导蓝牙耳机 运动", price_cny=329, monthly_sales=1200,
               rating=4.4, comment_count=450, shop_name="运动达人", days_listed=120,
               feature_tags=("运动", "骨传导")),
    Competitor("1009", "蓝牙耳机", price_cny=None, monthly_sales=None,
               rating=None, comment_count=None, shop_name="野鸡店", days_listed=None,
               feature_tags=()),  # 全空, 数据黑洞
    Competitor("1010", "高端蓝牙耳机 蓝牙5.3", price_cny=1299, monthly_sales=80,
               rating=4.9, comment_count=50, shop_name="高端数码", days_listed=300,
               feature_tags=("蓝牙5.3", "高端")),
]


# ---------------------------------------------------------------------------
# AnalysisPreference 校验
# ---------------------------------------------------------------------------


class TestPreferenceValidation:
    def test_default_valid(self):
        pref = AnalysisPreference()
        pref.validate()  # 不抛

    def test_top_n_must_be_positive(self):
        with pytest.raises(ValueError, match="top_n 必须"):
            AnalysisPreference(top_n=0).validate()

    def test_sort_by_unknown_raises(self):
        with pytest.raises(ValueError, match="sort_by 必须是"):
            AnalysisPreference(sort_by="fake").validate()

    def test_min_price_negative_raises(self):
        with pytest.raises(ValueError, match="min_price_cny 不能为负"):
            AnalysisPreference(min_price_cny=-1).validate()

    def test_max_less_than_min_raises(self):
        with pytest.raises(ValueError, match="max_price_cny"):
            AnalysisPreference(min_price_cny=200, max_price_cny=100).validate()

    def test_min_rating_out_of_range(self):
        with pytest.raises(ValueError, match="min_rating 必须在"):
            AnalysisPreference(min_rating=6.0).validate()


# ---------------------------------------------------------------------------
# 价格分布
# ---------------------------------------------------------------------------


class TestPriceDistribution:
    def test_classic_4_buckets(self):
        # 4 个典型价格档: <100, 100-300, 300-800, >=800
        prices = [49, 99, 149, 189, 259, 329, 399, 899, 1299]
        dist = compute_price_distribution(prices)
        assert dist.bucket_under_100 == 2  # 49, 99
        assert dist.bucket_100_300 == 3  # 149, 189, 259
        assert dist.bucket_300_800 == 2  # 329, 399
        assert dist.bucket_over_800 == 2  # 899, 1299
        assert dist.median == 259  # 5th of 9
        assert dist.min == 49
        assert dist.max == 1299

    def test_skip_none_prices(self):
        """缺价不参与分位数, 但也不抛错."""
        prices = [None, 100, 200, None, 300]
        dist = compute_price_distribution(prices)
        assert dist.median == 200
        assert dist.bucket_under_100 == 0  # 100 不算 "<100"
        assert dist.bucket_100_300 == 2  # 100, 200

    def test_empty_returns_none_stats(self):
        dist = compute_price_distribution([])
        assert dist.median is None
        assert dist.p25 is None
        assert dist.bucket_under_100 == 0


# ---------------------------------------------------------------------------
# 高频卖点 + 头部店铺
# ---------------------------------------------------------------------------


class TestKeywordAggregation:
    def test_top3_features_in_order(self):
        """主动降噪 (1001/1003/1005/1007) = 4; 蓝牙5.3 (1001/1002/1006/1010) = 4;
        运动 (1004/1008) = 2; 长续航 (1001/1002) = 2."""
        result = extract_top_keywords(SAMPLE, top_n=3)
        assert len(result) == 3
        # 两个第一名频次相同 (4), 但 Counter.most_common 保持插入顺序
        assert {result[0].keyword, result[1].keyword} == {"主动降噪", "蓝牙5.3"}
        assert result[2].keyword in {"运动", "长续航", "防水", "头戴式", "高端", "骨传导"}
        assert all(r.count == 4 for r in result[:2])

    def test_pct_calculation(self):
        """pct 是占总样本的比例, 不是占总标签数."""
        result = extract_top_keywords(SAMPLE, top_n=1)
        assert result[0].count == 4
        assert abs(result[0].pct - 4 / len(SAMPLE)) < 1e-9  # 4/10 = 0.4

    def test_empty_returns_empty_tuple(self):
        assert extract_top_keywords([], top_n=5) == ()


class TestShopAggregation:
    def test_top_shops_by_total_sales(self):
        result = extract_top_shops(SAMPLE, top_n=3)
        # 平价优选(25000) 数码先锋店(17000) 高端数码(2980) 运动达人(4200)
        assert result[0].shop_name == "平价优选"
        assert result[1].shop_name == "数码先锋店"
        assert result[0].goods_count == 1
        assert result[0].total_monthly_sales == 25000

    def test_anonymous_competitors_excluded(self):
        """1007 缺 shop_name = 匿名, 不参与聚合."""
        result = extract_top_shops(SAMPLE, top_n=10)
        names = [r.shop_name for r in result]
        assert None not in names
        assert all(r.goods_count >= 1 for r in result)

    def test_avg_rating_skips_missing(self):
        result = extract_top_shops(SAMPLE, top_n=10)
        # 1009 是野鸡店, rating=None → 不参与均值
        row = next(r for r in result if r.shop_name == "野鸡店")
        assert row.avg_rating is None


# ---------------------------------------------------------------------------
# rank_competitors (复用 rank_flights 思路)
# ---------------------------------------------------------------------------


class TestRanking:
    def test_sort_by_sales_desc(self):
        pref = AnalysisPreference(sort_by="sales", top_n=3)
        ranked = rank_competitors(SAMPLE, pref)
        assert ranked[0].goods_id == "1006"  # 25000
        assert ranked[1].goods_id == "1002"  # 12000

    def test_sort_by_price_asc(self):
        pref = AnalysisPreference(sort_by="price", top_n=3)
        ranked = rank_competitors(SAMPLE, pref)
        assert ranked[0].goods_id == "1006"  # 49
        assert ranked[1].goods_id == "1002"  # 99

    def test_filter_min_sales_strict(self):
        pref = AnalysisPreference(min_sales=2000)
        ranked = rank_competitors(SAMPLE, pref)
        ids = {c.goods_id for c in ranked}
        assert "1003" in ids  # 2500
        assert "1010" not in ids  # 80
        assert "1005" not in ids  # 400
        # 1001=5000 应该保留
        assert "1001" in ids

    def test_filter_min_rating_excludes_missing_by_default(self):
        """默认 exclude_missing_rating=True; 1009 评分缺失会被剔除."""
        pref = AnalysisPreference(min_rating=4.0)
        ranked = rank_competitors(SAMPLE, pref)
        ids = {c.goods_id for c in ranked}
        assert "1009" not in ids  # 评分 None → 剔除

    def test_filter_min_rating_keeps_missing_if_explicit(self):
        pref = AnalysisPreference(min_rating=4.0, exclude_missing_rating=False)
        ranked = rank_competitors(SAMPLE, pref)
        ids = {c.goods_id for c in ranked}
        # 用户显式说不剔除缺失评分, 1009 也进
        assert "1009" in ids

    def test_filter_exclude_missing_sales(self):
        """默认 exclude_missing_sales=False; 显式开才剔除缺失销量的."""
        pref_default = AnalysisPreference(min_sales=1000)
        pref_strict = AnalysisPreference(min_sales=1000, exclude_missing_sales=True)
        # 默认: 1009 销量缺失 → 保留 (因为 None 当作"不知道" = 通过)
        assert any(c.goods_id == "1009" for c in rank_competitors(SAMPLE, pref_default))
        # 严格: 1009 销量缺失 → 剔除
        assert not any(c.goods_id == "1009" for c in rank_competitors(SAMPLE, pref_strict))

    def test_explain_rejection_lists_reasons(self):
        pref = AnalysisPreference(min_rating=4.5, min_sales=2000)
        reason = explain_competitor_rejection(
            Competitor("1010", "高端蓝牙耳机 蓝牙5.3", price_cny=1299,
                       monthly_sales=80, rating=4.9, shop_name="高端数码"),
            pref,
        )
        assert "月销量80低于下限2000" in reason

    def test_explain_no_rejection_returns_empty(self):
        pref = AnalysisPreference(min_rating=4.0)
        reason = explain_competitor_rejection(
            Competitor("1001", "蓝牙耳机", rating=4.8),
            pref,
        )
        assert reason == ""


# ---------------------------------------------------------------------------
# analyze 入口
# ---------------------------------------------------------------------------


class TestAnalyze:
    def test_returns_report_with_all_fields(self):
        pref = AnalysisPreference(min_rating=4.0)
        report = analyze(SAMPLE, pref)
        assert isinstance(report, AnalysisReport)
        assert report.sample_size == 10
        # 9 个商品评分未知 → 1 个 (1009); 缺评分的还会被 min_rating=4.0 剔除 → 9 个进过滤
        # 实际: 1001(4.8) 1002(4.6) 1003(4.9) 1004(4.5) 1005(4.7) 1006(4.3) 1007(4.6) 1008(4.4) 1009(缺, 剔除) 1010(4.9)
        assert report.filtered_size == 9
        assert report.price_dist is not None
        assert isinstance(report.top_keywords, tuple)
        assert isinstance(report.top_shops, tuple)
        assert isinstance(report.top_competitors, tuple)
        assert isinstance(report.warnings, tuple)

    def test_empty_dataset_returns_empty_report(self):
        report = analyze([])
        assert report.sample_size == 0
        assert report.filtered_size == 0
        assert "样本为空" in report.warnings[0]
        assert "过滤后没有商品" in report.summary

    def test_top_n_respected(self):
        pref = AnalysisPreference(top_n=3)
        report = analyze(SAMPLE, pref)
        assert len(report.top_competitors) == 3

    def test_warnings_mark_missing_data_quality_issues(self):
        """1009 全 None → 触发多个 warning."""
        report = analyze(SAMPLE)
        warning_text = "; ".join(report.warnings)
        # 缺价格的商品 (1009)
        assert "缺价格" in warning_text

    def test_summary_mentions_median_price_and_top_keyword(self):
        report = analyze(SAMPLE)
        # summary 至少包含价格 + 高频卖点
        assert "价格" in report.summary
        assert "高频卖点" in report.summary

    def test_no_duplicate_warning_for_acceptable_missing(self):
        """销量缺失但占比 < 50% → 不报 warning (跟 rating 不同)."""
        only_missing_sales = [
            Competitor("2001", "a", rating=4.5, monthly_sales=None),
            Competitor("2002", "b", rating=4.5, monthly_sales=1000),
        ]
        report = analyze(only_missing_sales)
        warning_text = "; ".join(report.warnings)
        # 1/2 缺销量, = 50%, 阈值是 > 50% 才报 → 不应报 "月销量"
        assert "月销量" not in warning_text
