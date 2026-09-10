"""销量语义回归测试 —— 钉死"单品销量优先于店铺累计"这条红线.

背景 (2026-09-08 修的真实 bug)
------------------------------
``top_sales`` 原本直接取列表页 ``salesTip``, 那是**店铺/品牌累计销量**
("品牌热销4026.3万+件"), 却被 API/前端/insight 三处当"单品销量"展示 ——
结果华为/小米/倍思全显示 5000万, 漫步者显示 4026.3万(正是它的品牌累计数)。

修法: 详情页单品销量 (``data/pdd_detail_*.json`` 的 ``single_sales``) 优先,
拿不到才降级, 且**必须带来源标记** ``top_sales_source`` 让下游按口径标标签。

本文件的每个用例都是在防这个坑复发。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ecommerce_detail_store import (
    detail_for,
    find_latest_detail_file,
    get_single_sales,
    load_detail_merged,
    load_latest_detail,
)
from scripts.ecommerce_brand_compare import (
    SALES_SRC_SHOP_TOTAL,
    SALES_SRC_SINGLE,
    SALES_SRC_UNKNOWN,
    _resolve_sales,
)


# ---------------------------------------------------------------------------
# fake top 商品 (只需要 monthly_sales 属性)
# ---------------------------------------------------------------------------


class _FakeTop:
    def __init__(self, monthly_sales: int | None):
        self.monthly_sales = monthly_sales


# ---------------------------------------------------------------------------
# _resolve_sales: 优先级 + 来源标记
# ---------------------------------------------------------------------------


def test_detail_single_sales_wins_over_list_total():
    """详情页有单品销量 → 必须用它, 不能拿列表页 4000万累计顶上。"""
    sales, src, shop = _resolve_sales(_FakeTop(40_263_000), {"single_sales": 113_000})
    assert sales == 113_000          # 单品 11.3万
    assert src == SALES_SRC_SINGLE
    assert shop == 40_263_000        # 累计数仍作为参考列保留


def test_fallback_to_shop_total_when_no_single_sales():
    """详情页没采到 → 降级用累计, 但 source 必须是 shop_total (让下游标 ⚠)。"""
    sales, src, shop = _resolve_sales(_FakeTop(1_472_000), {"single_sales": None})
    assert sales == 1_472_000
    assert src == SALES_SRC_SHOP_TOTAL


def test_no_detail_at_all_falls_back_and_marks_shop_total():
    """压根没跑详情页采集 → 也走 shop_total, 绝不能冒充 single。"""
    sales, src, _ = _resolve_sales(_FakeTop(50_000_000), None)
    assert sales == 50_000_000
    assert src == SALES_SRC_SHOP_TOTAL


def test_single_sales_none_and_no_list_sales_is_unknown():
    """两个都没有 → unknown, 别编数字。"""
    sales, src, shop = _resolve_sales(_FakeTop(None), {})
    assert sales is None
    assert src == SALES_SRC_UNKNOWN
    assert shop is None


def test_single_sales_zero_is_kept_not_falsy_skipped():
    """single_sales=0 是有效值, 不能被 `if not single` 当成缺失跳过 (bool 短路坑)。"""
    sales, src, _ = _resolve_sales(_FakeTop(9_999), {"single_sales": 0})
    assert sales == 0
    assert src == SALES_SRC_SINGLE


def test_single_sales_non_int_ignored():
    """详情页字段类型不对 (比如字符串) → 当没采到, 降级。"""
    sales, src, _ = _resolve_sales(_FakeTop(610_000), {"single_sales": "5万"})
    assert sales == 610_000
    assert src == SALES_SRC_SHOP_TOTAL


def test_shop_sales_prefers_detail_list_sales():
    """参考列优先用详情页记录的 list_sales, 没有才用当前 top 的。"""
    _, _, shop = _resolve_sales(_FakeTop(1), {"single_sales": 5, "list_sales": 40_263_000})
    assert shop == 40_263_000


# ---------------------------------------------------------------------------
# detail store: 加载 data/pdd_detail_*.json
# ---------------------------------------------------------------------------


def _write_detail(tmp_path: Path, name: str, payload: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_latest_detail_picks_newest(tmp_path: Path):
    _write_detail(tmp_path, "pdd_detail_20260101T000000.json", {"华为": {"single_sales": 1}})
    _write_detail(tmp_path, "pdd_detail_20260907T174522.json", {"华为": {"single_sales": 53000}})
    # mtime 不一定按名字序, 显式确认拿到的是内容为新值的那份
    data = load_latest_detail(tmp_path)
    assert data["华为"]["single_sales"] == 53000


def test_load_latest_detail_missing_dir_returns_empty(tmp_path: Path):
    assert load_latest_detail(tmp_path / "nope") == {}


def test_load_latest_detail_bad_json_returns_empty(tmp_path: Path):
    (tmp_path / "pdd_detail_x.json").write_text("{not json", encoding="utf-8")
    assert load_latest_detail(tmp_path) == {}


def test_load_latest_detail_non_dict_entry_filtered(tmp_path: Path):
    _write_detail(tmp_path, "pdd_detail_x.json", {"华为": {"single_sales": 1}, "坏数据": "oops"})
    data = load_latest_detail(tmp_path)
    assert "坏数据" not in data
    assert "华为" in data


def test_detail_for_and_get_single_sales(tmp_path: Path):
    _write_detail(tmp_path, "pdd_detail_x.json", {"漫步者": {"single_sales": 113_000}})
    assert detail_for("漫步者", tmp_path)["single_sales"] == 113_000
    assert get_single_sales("漫步者", tmp_path) == 113_000
    # 没采过的品牌
    assert detail_for("不存在的品牌", tmp_path) is None


# ---------------------------------------------------------------------------
# 合并视图 —— 分批补采不能丢旧品牌 (2026-09-10 真实 bug 的回归防线)
# ---------------------------------------------------------------------------


def test_merged_keeps_old_brand_when_new_file_only_has_new_brand(tmp_path: Path):
    """核心回归: 补采 OPPO 落新文件后, 华为的单品销量必须还在。

    真实场景: 先采 6 个品牌 → 生成 detail_A.json; 再单独补采 OPPO + 万魔 →
    生成 detail_B.json (只有 OPPO/万魔)。旧实现只读最新一份 → 华为/小米等全部
    "消失", 被迫降级成店铺累计口径, 跨品牌再也没法统一比较。
    """
    _write_detail(tmp_path, "pdd_detail_20260907T174522.json",
                  {"华为": {"single_sales": 53_000}, "小米": {"single_sales": 259_000}})
    _write_detail(tmp_path, "pdd_detail_20260910T191830.json",
                  {"OPPO": {"single_sales": 156_000}, "万魔": {"single_sales": 1_472_000}})

    merged = load_detail_merged(tmp_path)
    assert merged["华为"]["single_sales"] == 53_000   # 旧品牌没丢
    assert merged["小米"]["single_sales"] == 259_000
    assert merged["OPPO"]["single_sales"] == 156_000  # 新品牌也进来了
    assert merged["万魔"]["single_sales"] == 1_472_000
    # detail_for 走合并视图, 同样能查到旧品牌
    assert detail_for("华为", tmp_path)["single_sales"] == 53_000


def test_merged_same_brand_newest_wins(tmp_path: Path):
    """同一品牌采了两次 → 保留最新那份 (按 (mtime, 文件名) 升序覆盖)。"""
    import os
    import time

    old = _write_detail(tmp_path, "pdd_detail_20260101T000000.json", {"华为": {"single_sales": 1}})
    time.sleep(0.02)
    new = _write_detail(tmp_path, "pdd_detail_20260907T174522.json", {"华为": {"single_sales": 53_000}})
    os.utime(old, (old.stat().st_atime, old.stat().st_mtime - 10))  # 确保 old 更旧

    assert load_detail_merged(tmp_path)["华为"]["single_sales"] == 53_000


def test_merged_bad_file_skipped_others_survive(tmp_path: Path):
    """单个坏文件不能连累其它文件 (合并语义要比单文件更宽容)。"""
    (tmp_path / "pdd_detail_bad.json").write_text("{not json", encoding="utf-8")
    _write_detail(tmp_path, "pdd_detail_good.json", {"华为": {"single_sales": 53_000}})

    merged = load_detail_merged(tmp_path)
    assert merged["华为"]["single_sales"] == 53_000


def test_merged_missing_dir_returns_empty(tmp_path: Path):
    assert load_detail_merged(tmp_path / "nope") == {}
    assert get_single_sales("不存在的品牌", tmp_path) is None


def test_find_latest_detail_file_none_when_empty(tmp_path: Path):
    assert find_latest_detail_file(tmp_path) is None


# ---------------------------------------------------------------------------
# insight: 文案必须随口径变 (降级时不能说"单品销量")
# ---------------------------------------------------------------------------


def test_insight_hot_top_labels_single_source():
    from app.ecommerce_insight import StubInsightGenerator

    rows = [
        {"brand": "小米", "top_sales": 259_000, "top_sales_source": "single", "top_product": "Redmi Buds 6"},
        {"brand": "华为", "top_sales": 53_000, "top_sales_source": "single", "top_product": "FreeArc"},
    ]
    ins = [i for i in StubInsightGenerator().generate(rows) if i.kind == "hot"][0]
    assert "单品销量" in ins.body
    assert "小米" in ins.body


def test_insight_hot_top_downgraded_source_says_cumulative():
    """降级口径下, 文案必须出现"累计"且明确否定单品 —— 否则读者会以为单品卖了4000万。"""
    from app.ecommerce_insight import StubInsightGenerator

    rows = [
        {"brand": "漫步者", "top_sales": 40_263_000, "top_sales_source": "shop_total", "top_product": "X1"},
        {"brand": "QCY", "top_sales": 610_000, "top_sales_source": "shop_total", "top_product": "N60"},
    ]
    ins = [i for i in StubInsightGenerator().generate(rows) if i.kind == "hot"][0]
    assert "累计" in ins.body
    assert "非该单品销量" in ins.body
    assert "单品销量最高" not in ins.body


def test_insight_hot_top_does_not_mix_sources():
    """混口径时只在同一种口径内选冠军: 单品 25.9万 应赢过 累计? 不 —— 累计口径里
    的 4026万 不该跟单品口径的 25.9万 混着比。这里期望选 single 组的冠军。"""
    from app.ecommerce_insight import StubInsightGenerator

    rows = [
        {"brand": "小米", "top_sales": 259_000, "top_sales_source": "single", "top_product": "Redmi Buds 6"},
        {"brand": "漫步者", "top_sales": 40_263_000, "top_sales_source": "shop_total", "top_product": "X1"},
    ]
    ins = [i for i in StubInsightGenerator().generate(rows) if i.kind == "hot"][0]
    assert "小米" in ins.body
    assert "单品销量" in ins.body


def test_brand_row_exposes_top_product_alias():
    """``_brand_row`` 必须同时给 ``top_model`` 和 ``top_product``。

    insight 层读的是 ``top_product``, 以前只有 ``top_model`` → 热度冠军一直显示
    "小米 的 —"。两个 key 都出, 谁读都不空。
    """
    from scripts.ecommerce_brand_compare import _brand_row

    class _Rep:  # _brand_row 只读这四个属性
        price_dist = None
        top_keywords = []
        top_competitors = []
        warnings = []
        filtered_size = 0

    row = _brand_row("小米", _Rep(), 0, detail={"top_model": "Redmi Buds 6", "single_sales": 259_000})
    assert row["top_model"] == "Redmi Buds 6"
    assert row["top_product"] == "Redmi Buds 6"


@pytest.mark.parametrize(
    "src,expect",
    [("single", "单品销量"), ("shop_total", "店铺/品牌累计(非单品销量)"), ("unknown", "销量(口径未知)")],
)
def test_llm_prompt_carries_source_label(src: str, expect: str):
    """喂给 LLM 的 prompt 必须带口径 —— 只给数字它会把累计数当单品写进结论。"""
    from app.ecommerce_insight import _build_prompt

    prompt = _build_prompt([{"brand": "X", "top_sales": 1, "top_sales_source": src, "top_keywords": []}])
    assert expect in prompt
