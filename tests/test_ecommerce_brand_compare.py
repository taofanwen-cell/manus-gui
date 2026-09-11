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
from ecommerce_brand_compare import _brand_row, _render_markdown, detect_category_drift  # noqa: E402


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


def _make_report_ex(goods_id="1", title="X1蓝牙耳机小奶豆真无线半入耳", price_cny=85,
                    median=83, top_sales=40_263_000, n_kw=3):
    """可指定 top1 商品 goods_id/title/price 的 report 构造器 (供 B-78 对齐测试用)."""
    comp = Competitor(
        goods_id=goods_id, title=title, price_cny=price_cny,
        monthly_sales=top_sales, feature_tags=("真无线", "降噪"),
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


# --- 问题②: 热度机型行 goods_id 对齐 (B-78) ---

def test_brand_row_top_aligned_same_goods_mixes_detail_name_and_list_price():
    # detail.goods_id == 列表页 top1.goods_id → 混合: 机型名用详情页(更完整), 价格用列表页当天价
    rep = _make_report_ex(goods_id="960370019979", title="OPPO Enco Free4 蓝牙耳机", price_cny=349)
    detail = {"goods_id": "960370019979",
              "top_model": "【试用7天】OPPO Enco Free4入耳式主动降噪旗舰蓝牙耳机",
              "top_price": 349, "single_sales": 216000}
    row = _brand_row("OPPO", rep, 20, detail=detail)
    assert row["top_model"] == "【试用7天】OPPO Enco Free4入耳式主动降噪旗舰蓝牙耳机"
    assert row["top_price"] == 349  # 列表页当天价 (同一商品)
    assert row["top_product"] == row["top_model"]


def test_brand_row_top_misaligned_uses_list_top_only():
    # detail.goods_id != 列表页 top1.goods_id → 整行只用列表页当天 top1 (名字+价格自洽)
    rep = _make_report_ex(goods_id="Reno15", title="OPPO Reno15 5G手机 12+256G", price_cny=2677)
    detail = {"goods_id": "960370019979", "top_model": "OPPO Enco Free4 蓝牙耳机", "top_price": 349}
    row = _brand_row("OPPO", rep, 20, detail=detail)
    assert row["top_model"] == "OPPO Reno15 5G手机 12+256G"  # 列表页名, 不是详情页耳机名
    assert row["top_price"] == 2677  # 列表页价, 与名字同一商品
    assert "Enco" not in row["top_model"]


def test_brand_row_no_detail_uses_list_top():
    rep = _make_report_ex(goods_id="1", title="X1蓝牙耳机小奶豆", price_cny=85)
    row = _brand_row("漫步者", rep, 20)  # 无 detail
    assert row["top_model"] == "X1蓝牙耳机小奶豆"
    assert row["top_price"] == 85


def test_brand_row_no_top_no_detail_blank():
    rep = AnalysisReport(
        sample_size=0, filtered_size=0, summary="x", price_dist=None,
        top_keywords=(), top_shops=(), top_competitors=(), warnings=(),
    )
    row = _brand_row("空", rep, 0)
    assert row["top_model"] == "—"
    assert row["top_price"] is None


def test_brand_row_misaligned_sales_not_from_detail():
    # goods_id 不一致时, 整行锚定列表页 top1: 销量取自列表页月销 (shop_total),
    # 绝不把详情页另一件商品的单品销量 (216000) 拼进这一行 (B-78 补完核心回归)
    rep = _make_report_ex(goods_id="Reno15", title="OPPO Reno15 5G手机 12+256G",
                          price_cny=2677, top_sales=1_200_000)
    detail = {"goods_id": "960370019979", "top_model": "OPPO Enco Free4 蓝牙耳机",
              "top_price": 349, "single_sales": 216000}
    row = _brand_row("OPPO", rep, 20, detail=detail)
    # 机型名/价格来自列表页 top1 (Reno15), 自洽
    assert row["top_model"] == "OPPO Reno15 5G手机 12+256G"
    assert row["top_price"] == 2677
    # 销量必须来自列表页 top1 (120万), 而不是详情页的 216000 (Enco Free4 耳机)
    assert row["top_sales"] == 1_200_000
    assert row["top_sales"] != 216000
    assert row["top_sales_source"] == "shop_total"


def test_brand_row_aligned_sales_from_detail():
    # same_goods 时, 销量取自详情页单品销量 (与展示机型同源, 才标"单品销量")
    rep = _make_report_ex(goods_id="960370019979", title="OPPO Enco Free4 蓝牙耳机", price_cny=349)
    detail = {"goods_id": "960370019979", "top_model": "OPPO Enco Free4 蓝牙耳机",
              "top_price": 349, "single_sales": 216000}
    row = _brand_row("OPPO", rep, 20, detail=detail)
    assert row["top_sales"] == 216000
    assert row["top_sales_source"] == "single"


# --- 问题①: 品类一致性检查 (B-79) ---

def test_detect_category_drift_classifies_and_triggers():
    titles = (
        ["OPPO Reno15 5G手机", "OPPO Find X9 手机", "OPPO 手机官方旗舰"]
        + ["OPPO K12 手机"] * 13   # 共 16 手机
        + ["OPPO Enco Free4 蓝牙耳机", "OPPO 降噪耳机", "OPPO 真无线耳机", "OPPO 耳麦"]  # 4 耳机
    )
    d = detect_category_drift(titles)
    assert d["total"] == 20
    assert d["phone"] == 16
    assert d["earphone"] == 4
    assert d["triggered"] is True
    assert d["expected"] == "蓝牙耳机"


def test_detect_category_drift_all_earphone_not_triggered():
    titles = ["X1蓝牙耳机", "Redmi Buds 蓝牙耳机", "QCY 蓝牙耳机"] * 6  # 18 耳机
    d = detect_category_drift(titles)
    assert d["phone"] == 0
    assert d["triggered"] is False


def test_detect_category_drift_threshold_and_empty():
    assert detect_category_drift(None)["triggered"] is False
    assert detect_category_drift([])["triggered"] is False
    # 阈值: 占比过半但绝对数 < 3 不触发
    d = detect_category_drift(["A手机", "B蓝牙耳机"])
    assert d["phone"] == 1
    assert d["triggered"] is False


# --- 问题①: 品类一致性检查 (B-79) ---

def test_brand_row_category_drift_field():
    titles = ["OPPO Reno15 手机"] * 16 + ["OPPO Enco 蓝牙耳机"] * 4
    rep = _make_report_ex()
    row = _brand_row("OPPO", rep, 20, titles=titles)
    d = row["category_drift"]
    assert d["total"] == 20 and d["phone"] == 16 and d["triggered"] is True


def test_render_markdown_emits_category_warning():
    titles = ["OPPO Reno15 手机"] * 16 + ["OPPO Enco 蓝牙耳机"] * 4
    rep = _make_report_ex()
    row = _brand_row("OPPO", rep, 20, titles=titles)
    md = _render_markdown([row], ["OPPO"], [], "note")
    assert "品类一致性提醒" in md
    assert "疑似手机" in md
    assert "16 个" in md


def test_render_markdown_no_category_warning_when_clean():
    titles = ["X1蓝牙耳机"] * 20
    rep = _make_report_ex()
    row = _brand_row("漫步者", rep, 20, titles=titles)
    md = _render_markdown([row], ["漫步者"], [], "note")
    assert "品类一致性提醒" not in md

