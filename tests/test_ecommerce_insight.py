"""拼多多竞品调研 — LLM 洞察层单测.

测试策略 (跨项目硬规则第 5 条: 协议完整签名 / 第 2 条: 严格相等)
* Stub 是默认实现, sandbox 跑得通
* DashScope 用假 key → 走 fallback, 不发网络
* 模板断言用 ``in``, 因为文案是模板拼接
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from app.ecommerce_insight import (
    DashScopeInsightGenerator,
    Insight,
    StubInsightGenerator,
    _insight_keyword_gap,
    _insight_price_tiers,
    _insight_price_spread,
    _tier_of,
    default_insight_generator,
    render_insights_markdown,
)


# ---------------------------------------------------------------------------
# _tier_of 价位分桶
# ---------------------------------------------------------------------------


class TestTierOf:
    @pytest.mark.parametrize("median,expected", [
        (50, "低价走量 (¥100以内)"),
        (99, "低价走量 (¥100以内)"),
        (100, "中端 (¥100-300)"),
        (200, "中端 (¥100-300)"),
        (300, "高端 (¥300以上)"),
        (9999, "高端 (¥300以上)"),
        (None, "数据不足"),
    ])
    def test_tier_label(self, median, expected):
        assert _tier_of(median) == expected


# ---------------------------------------------------------------------------
# StubInsightGenerator 生成
# ---------------------------------------------------------------------------


def _row(brand, median, top_sales=None, top_product=None, keywords=None, warnings=None):
    return {
        "brand": brand,
        "median": median,
        "min": max(1, (median or 100) - 30),
        "max": (median or 100) + 30,
        "p25": (median or 100) - 10,
        "p75": (median or 100) + 10,
        "bucket_under_100": 0,
        "bucket_100_300": 0,
        "bucket_300_800": 0,
        "bucket_over_800": 0,
        "sample_size": 20,
        "filtered_size": 20,
        "top_keywords": [{"keyword": k, "count": c, "pct": 0.5}
                        for k, c in (keywords or [])],
        "top_product": top_product,
        "top_sales": top_sales,
        "warnings": warnings or [],
    }


class TestStubGenerator:
    def test_empty_rows_returns_warning(self):
        ins = StubInsightGenerator().generate([])
        assert len(ins) >= 1
        assert ins[0].kind == "warning"
        assert "没有可比品牌数据" in ins[0].body

    def test_generates_tier_hot_gap_spread(self):
        rows = [
            _row("小米", 105, top_sales=259_000, top_product="Redmi Buds 6",
                 keywords=[("降噪", 15), ("半入耳", 10)]),
            _row("漫步者", 83, top_sales=113_000, top_product="X1",
                 keywords=[("真无线", 12), ("降噪", 10)]),
            _row("华为", 257, top_sales=53_000, top_product="FreeArc",
                 keywords=[("降噪", 18)]),
        ]
        gen = StubInsightGenerator()
        ins = gen.generate(rows)
        kinds = [i.kind for i in ins]
        # 必须有: tier (梯队) + tier (极差) + hot + gap(可不) + warning(可不)
        assert "tier" in kinds
        assert "hot" in kinds

        hot = next(i for i in ins if i.kind == "hot")
        assert hot.body.startswith("【热度冠军】")
        assert "小米" in hot.body
        assert "Redmi Buds 6" in hot.body
        assert "25.9万件" in hot.body

        # 价位极差 华为 ¥257 vs 漫步者 ¥83
        spread = next(i for i in ins if "价位极差" in i.body)
        assert "漫步者" in spread.body
        assert "华为" in spread.body
        assert "3.1倍" in spread.body

    def test_price_tier_grouping(self):
        rows = [
            _row("A", 80),
            _row("B", 90),
            _row("C", 250),
        ]
        tiers = _insight_price_tiers(rows)
        # 低价走量有 2 个品牌 (A,B), 中端 1 个 (C)
        assert any("低价走量" in t.body and "A" in t.body and "B" in t.body for t in tiers)
        assert any("中端" in t.body and "C" in t.body for t in tiers)

    def test_keyword_gap_finds_underused_keywords(self):
        # 6 品牌. 主流门槛=(6+1)//2=3 家. < 3 家布局 = 空缺.
        # 降噪/游戏 全部品牌都做(6/6) → 主流. 半入耳 仅 2 个品牌做 → 空缺.
        rows = []
        for i in range(6):
            if i in (0, 3):
                # 这 2 个品牌布局: 降噪 + 游戏 + 半入耳
                rows.append(_row(f"brand{i}", 100, keywords=[
                    ("降噪", 10),
                    ("游戏", 8),
                    ("半入耳", 1),
                ]))
            else:
                # 这 4 个品牌布局: 降噪 + 游戏 (不布局半入耳)
                rows.append(_row(f"brand{i}", 100, keywords=[
                    ("降噪", 10),
                    ("游戏", 8),
                ]))

        gap = _insight_keyword_gap(rows)
        assert gap is not None
        assert gap.kind == "gap"
        # "降噪" 出现 6 次(主流) → 不在空缺 body 里
        assert "降噪" not in gap.body
        # "游戏" 出现 6 次(主流) → 不在空缺 body 里
        assert "游戏" not in gap.body
        # "半入耳" 出现 2 次(< 3 门槛) → 在空缺 body 里
        assert "半入耳" in gap.body

    def test_warnings_filter_none_and_empty(self):
        rows = [
            _row("A", 100, warnings=[None, "", "20/20 缺评分", 0]),
        ]
        gen = StubInsightGenerator()
        ins = gen.generate(rows)
        warnings = [i for i in ins if i.kind == "warning"]
        # 只应该有 "20/20 缺评分" 一条 (None/""/0 被过滤)
        assert len(warnings) == 1
        assert "20/20 缺评分" in warnings[0].body

    def test_spread_when_only_one_priced(self):
        rows = [_row("A", None), _row("B", None)]
        spread = _insight_price_spread(rows)
        assert spread.kind == "tier"
        assert "数据不足" in spread.body


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def test_groups_by_kind_in_order(self):
        ins = [
            Insight("warning", "W1"),
            Insight("tier", "T1"),
            Insight("hot", "H1"),
            Insight("gap", "G1"),
        ]
        md = render_insights_markdown(ins)
        # 顺序: tier → hot → gap → warning
        assert md.index("💰 价位梯队") < md.index("🏆 热度冠军")
        assert md.index("🏆 热度冠军") < md.index("🎯 空缺机会")
        assert md.index("🎯 空缺机会") < md.index("⚠️ 数据限制")

    def test_evidence_subscript_present(self):
        ins = [Insight("hot", "X", (("品牌", "小米"), ("销量", "10万")))]
        md = render_insights_markdown(ins)
        assert "<sub>📎" in md
        assert "品牌: 小米" in md

    def test_empty_returns_empty_string(self):
        assert render_insights_markdown([]) == ""


# ---------------------------------------------------------------------------
# DashScope fallback
# ---------------------------------------------------------------------------


class TestDashScopeFallback:
    def test_no_key_falls_back_to_stub(self, monkeypatch):
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        gen = DashScopeInsightGenerator(api_key=None)
        # 没 key 应该走 stub 等价结果
        rows = [_row("A", 100)]
        ins = gen.generate(rows)
        # stub 至少输出 1 条
        assert len(ins) >= 1

    def test_bad_key_falls_back_to_stub_on_error(self):
        gen = DashScopeInsightGenerator(api_key="sk-fake")
        rows = [_row("A", 100)]
        # 网络请求会失败 → fallback
        ins = gen.generate(rows)
        assert len(ins) >= 1


class TestDefaultInsightGenerator:
    def test_default_is_stub_without_env_key(self, monkeypatch):
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        gen = default_insight_generator()
        assert isinstance(gen, StubInsightGenerator)

    def test_default_is_dashscope_with_env_key(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake")
        gen = default_insight_generator()
        assert isinstance(gen, DashScopeInsightGenerator)
