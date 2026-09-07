"""横向对比脚本的纯函数单测 (不碰 CDP, sandbox 可跑).

只测 ``ecommerce_brand_compare`` 里两个可测的纯函数:
``_brand_row`` (AnalysisReport -> 一行对比数据) 和 ``_render_markdown``
(rows -> Markdown 报告)。扫描部分依赖真实 Chrome, 不在单测范围。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from app.ecommerce_analyzer import (  # noqa: E402
    AnalysisReport,
    Competitor,
    PriceDistribution,
    TopKeyword,
)
from ecommerce_brand_compare import _brand_row, _render_markdown  # noqa: E402


def _make_report(median=83, top_price=85, top_sales=40_263_000, n_kw=3):
    comp = Competitor(
        goods_id="1",
        title="X1蓝牙耳机小奶豆真无线半入耳",
        price_cny=top_price,
        monthly_sales=top_sales,
        feature_tags=("真无线", "降噪"),
    )
    pd = PriceDistribution(
        bucket_under_100=19, bucket_100_300=1, bucket_300_800=0, bucket_over_800=0,
        median=median, p25=80, p75=90, min=74, max=131,
    )
    kws = [
        TopKeyword("真无线", 10, 0.5),
        TopKeyword("降噪", 8, 0.4),
        TopKeyword("游戏", 6, 0.3),
    ][:n_kw]
    return AnalysisReport(
        sample_size=20, filtered_size=20, summary="x", price_dist=pd,
        top_keywords=tuple(kws), top_shops=(), top_competitors=(comp,), warnings=(),
    )


def test_brand_row_normal():
    row = _brand_row("漫步者", _make_report(), 20)
    assert row["brand"] == "漫步者"
    assert row["filtered"] == 20
    assert row["median"] == 83
    assert row["b_u100"] == 19
    assert row["kw1"] == "真无线"
    assert row["kw2"] == "降噪"
    assert row["top_model"].startswith("X1蓝牙耳机")
    assert row["top_price"] == 85
    assert row["top_sales"] == 40_263_000


def test_brand_row_missing_price_dist():
    # 价格数据全空 -> None, 不崩
    rep = AnalysisReport(
        sample_size=0, filtered_size=0, summary="x", price_dist=None,
        top_keywords=(), top_shops=(), top_competitors=(), warnings=(),
    )
    row = _brand_row("空品牌", rep, 0)
    assert row["median"] is None
    assert row["min"] is None
    assert row["max"] is None
    assert row["kw1"] == "—"
    assert row["top_model"] == "—"


def test_render_markdown_contains_table_headers():
    rows = [_brand_row("华为", _make_report(median=257, top_price=360, top_sales=50_000_000), 20)]
    md = _render_markdown(rows, ["华为"], [], "note")
    assert "# 拼多多蓝牙耳机竞品横向对比报告" in md
    assert "价格带横向对比" in md
    assert "| 华为 |" in md
    assert "5000万" in md  # :g 去掉尾随 0


def test_render_markdown_median_banding():
    # 中位数 <100 归低价, >=100 <300 归中端
    rows = [
        _brand_row("倍思", _make_report(median=73), 20),
        _brand_row("华为", _make_report(median=257), 20),
    ]
    md = _render_markdown(rows, ["倍思", "华为"], [], "note")
    assert "倍思" in md and "华为" in md
    # 定位结论: 最低价位品牌 vs 最高价位品牌
    assert "倍思 切入最低价位" in md
    assert "华为 价位最高" in md


def test_render_markdown_small_sales_not_wan():
    # 销量 < 1万 用原始整数, 不转"万"
    rows = [_brand_row("QCY", _make_report(top_sales=6100), 20)]
    md = _render_markdown(rows, ["QCY"], [], "note")
    assert "6100" in md
    assert "0.61万" not in md
