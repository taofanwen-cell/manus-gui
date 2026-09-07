"""``app/ecommerce_pdd_parser.py`` 的单元测试.

覆盖:
  * :func:`parse_sales_tip` -- 万/亿/+/人想拼/件/未知格式
  * :func:`extract_raw_data` -- 花括号配对提取 / 找不到 / 解析失败
  * :func:`extract_feature_tags` -- 蓝牙版本 / 入耳互斥 / 去重 / 空标题
  * :func:`goods_to_competitor` -- priceInfo 优先 / price 兜底 / url 归一化
  * :func:`extract_competitors` -- 端到端 (合成 rawData fixture)

全部是纯逻辑, 不依赖真实浏览器.
"""
from __future__ import annotations

import json

import pytest

from app.ecommerce_pdd_parser import (
    _pick_ear_type,
    _price_to_yuan,
    extract_competitors,
    extract_feature_tags,
    extract_raw_data,
    goods_to_competitor,
    parse_sales_tip,
)


# ---------------------------------------------------------------------------
# parse_sales_tip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("本店已拼7148", 7148),
        ("本店已拼2.2万", 22000),
        ("本店已拼297万+", 2_970_000),
        ("本店已拼34.6万+", 346_000),
        ("17人想拼", 17),
        ("23人想拼", 23),
        ("已拼82件", 82),
        ("总售2.7万+件", 27_000),
        ("全店总售340万+", 3_400_000),
        ("已拼18500", 18500),
        ("已拼1.2亿", 120_000_000),
    ],
)
def test_parse_sales_tip_valid(text: str, expected: int) -> None:
    assert parse_sales_tip(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "abc", "暂无", None, "¥"])
def test_parse_sales_tip_unknown(text) -> None:
    assert parse_sales_tip(text) is None


# ---------------------------------------------------------------------------
# extract_raw_data
# ---------------------------------------------------------------------------


def _wrap(raw: dict) -> str:
    return f"<html><script>window.rawData = {json.dumps(raw, ensure_ascii=False)};</script></html>"


def test_extract_raw_data_valid() -> None:
    raw = {"stores": {"store": {"data": {"ssrListData": {"list": []}}}}}
    assert extract_raw_data(_wrap(raw)) == raw


def test_extract_raw_data_missing_return_none() -> None:
    assert extract_raw_data("<html>no rawData here</html>") is None


def test_extract_raw_data_empty() -> None:
    assert extract_raw_data("") is None
    assert extract_raw_data(None) is None


def test_extract_raw_data_handles_braces_in_strings() -> None:
    # 字符串值里带 {} 不会破坏花括号配对
    raw = {"a": "text with {brace}", "b": {"nested": [1, 2, {"x": "}"}]}}
    assert extract_raw_data(_wrap(raw)) == raw


def test_extract_raw_data_invalid_json_returns_none() -> None:
    assert extract_raw_data("<script>window.rawData = {not valid json};</script>") is None


# ---------------------------------------------------------------------------
# extract_feature_tags / _pick_ear_type
# ---------------------------------------------------------------------------


def test_feature_tags_bluetooth_version_keeps_dot() -> None:
    assert extract_feature_tags("蓝牙5.3真无线降噪耳机") == ("蓝牙5.3", "真无线", "降噪")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("不入耳骨传导运动耳机", ("不入耳", "骨传导", "运动")),
        ("半入耳式蓝牙耳机高音质", ("半入耳", "高音质")),
        ("入耳式蓝牙耳机降噪", ("入耳", "降噪")),
        ("普通标题无特征", ()),
    ],
)
def test_feature_tags_ear_exclusive(title: str, expected) -> None:
    assert extract_feature_tags(title) == expected


def test_feature_tags_no_contradictory_ear_pair() -> None:
    tags = extract_feature_tags("不入耳不入耳")
    assert "入耳" not in tags
    assert "不入耳" in tags


def test_feature_tags_empty_title() -> None:
    assert extract_feature_tags("") == ()
    assert extract_feature_tags(None) == ()


def test_feature_tags_dedup() -> None:
    tags = extract_feature_tags("高音质音质高清高保真")
    assert tags.count("高音质") == 1


def test_pick_ear_type_priority() -> None:
    # 不入耳 > 半入耳 > 入耳, 同时出现只取最高优先级
    assert _pick_ear_type("不入耳半入耳入耳") == "不入耳"
    assert _pick_ear_type("半入耳入耳") == "半入耳"
    assert _pick_ear_type("入耳") == "入耳"
    assert _pick_ear_type("夹耳式") is None


# ---------------------------------------------------------------------------
# _price_to_yuan / goods_to_competitor
# ---------------------------------------------------------------------------


def test_price_prefers_priceInfo() -> None:
    # price=50290(分) 是原价 ¥502.9, priceInfo="402.9" 是券后价 -> 取 402.
    assert _price_to_yuan({"price": 50290, "priceInfo": "402.9"}) == 402


def test_price_fallback_to_cents() -> None:
    assert _price_to_yuan({"price": 188, "priceInfo": ""}) == 1
    assert _price_to_yuan({"price": 691}) == 6


def test_price_unknown() -> None:
    assert _price_to_yuan({"price": None, "priceInfo": ""}) is None
    assert _price_to_yuan({}) is None


def test_goods_to_competitor_field_mapping() -> None:
    goods = {
        "goodsID": 994664413472,
        "goodsName": "柏林之声无线蓝牙耳机2026新款夹耳式骨传导高音质不入耳通话降噪",
        "price": 188,
        "priceInfo": "1.88",
        "salesTip": "本店已拼7148",
        "linkURL": "goods.html?goods_id=994664413472&x=1",
        "tagList": [],
    }
    c = goods_to_competitor(goods)
    assert c.goods_id == "994664413472"
    assert c.price_cny == 1
    assert c.monthly_sales == 7148
    assert c.rating is None
    assert c.comment_count is None
    assert c.shop_name is None
    assert c.days_listed is None
    assert c.url.startswith("https://mobile.yangkeduo.com/goods.html?goods_id=994664413472")
    assert "不入耳" in c.feature_tags
    assert "入耳" not in c.feature_tags


# ---------------------------------------------------------------------------
# extract_competitors 端到端
# ---------------------------------------------------------------------------

FIXTURE_RAW = {
    "stores": {
        "store": {
            "data": {
                "ssrListData": {
                    "list": [
                        {
                            "goodsID": 111,
                            "goodsName": "蓝牙5.3真无线降噪半入耳耳机",
                            "price": 188,
                            "priceInfo": "1.88",
                            "salesTip": "本店已拼7148",
                            "linkURL": "goods.html?goods_id=111",
                            "tagList": [],
                        },
                        {
                            "goodsID": 222,
                            "goodsName": "骨传导不入耳运动耳机",
                            "price": 691,
                            "priceInfo": "6.91",
                            "salesTip": "本店已拼2.2万",
                            "linkURL": "goods.html?goods_id=222",
                            "tagList": [],
                        },
                        {
                            "goodsID": 333,
                            "goodsName": "K10游戏耳机无延迟",
                            "price": 50290,
                            "priceInfo": "402.9",
                            "salesTip": "17人想拼",
                            "linkURL": "goods.html?goods_id=333",
                            "tagList": [],
                        },
                    ]
                }
            }
        }
    }
}
FIXTURE_HTML = _wrap(FIXTURE_RAW)


def test_extract_competitors_count() -> None:
    comps = extract_competitors(FIXTURE_HTML)
    assert len(comps) == 3


def test_extract_competitors_mapping() -> None:
    comps = extract_competitors(FIXTURE_HTML)
    c0, c1, c2 = comps
    assert c0.goods_id == "111" and c0.price_cny == 1 and c0.monthly_sales == 7148
    assert "蓝牙5.3" in c0.feature_tags
    assert c1.goods_id == "222" and c1.price_cny == 6 and c1.monthly_sales == 22000
    assert "不入耳" in c1.feature_tags and "入耳" not in c1.feature_tags
    assert c2.goods_id == "333" and c2.price_cny == 402 and c2.monthly_sales == 17


def test_extract_competitors_empty_when_no_rawdata() -> None:
    assert extract_competitors("<html></html>") == []


def test_extract_competitors_supports_related_recs_fallback() -> None:
    # ssrListData.list 缺失时, 全量递归仍能兜住带 goodsName 的商品对象
    raw = {"data": {"recommend": [{"goodsName": "a", "price": 100, "salesTip": "已拼1"}]}}
    comps = extract_competitors(_wrap(raw))
    assert len(comps) == 1
    assert comps[0].title == "a"
