"""详情页字段提取 (extract_goods_detail) 单测 — 纯逻辑, 不碰浏览器."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ecommerce_pdd_parser import (  # noqa: E402
    GoodsDetail,
    extract_goods_detail,
    extract_goods_detail_best,
    extract_goods_detail_from_json,
)


def _text(**kw):
    """拼一段模拟详情页正文, 未给的关键字段用默认值填充."""
    shop = kw.get("shop", "艾码影音馆")
    sales = kw.get("sales", "热销9.4万+件")
    comment = kw.get("comment", "商品评价(14,870)")
    return f"""规格型号
大促价¥80.2
{sales}
【漫步者】Zero Air真无线蓝牙耳机
24小时发货
{comment}
查看全部
该商品所属店铺评价
正品(6574)音质非常好(2万)
{shop}
本店已拼900万+件
进店逛逛
"""


def test_full():
    d = extract_goods_detail(_text())
    assert d == GoodsDetail(
        shop_name="艾码影音馆", single_sales=94000, comment_count=14870,
        # 正则路径: 单品销量是猜的, 标 text 提醒下游来源弱
        sales_source="text", single_sales_text="热销9.4万+件",
    )


def test_single_sales_plain_number():
    d = extract_goods_detail(_text(sales="热销128件"))
    assert d.single_sales == 128


def test_single_sales_yiqiang():
    # 漫步者 X1 用"已抢"文案
    d = extract_goods_detail(_text(sales="已抢11.3万+件"))
    assert d.single_sales == 113000


def test_single_sales_zongshou():
    # 华为 FreeArc 用"总售"文案
    d = extract_goods_detail(_text(sales="总售5.3万+件"))
    assert d.single_sales == 53000


def test_no_sales():
    d = extract_goods_detail(_text(sales="已售罄"))
    assert d.single_sales is None


def test_no_comment():
    d = extract_goods_detail(_text(comment="暂无评价"))
    assert d.comment_count is None


def test_no_shop():
    # 没有"进店逛逛" -> 店铺名 None
    txt = "热销9.4万+件\n商品评价(14,870)"
    d = extract_goods_detail(txt)
    assert d.shop_name is None


def test_empty_text():
    d = extract_goods_detail("")
    assert d == GoodsDetail(shop_name=None, single_sales=None, comment_count=None)


def test_comment_with_multiple_commas():
    d = extract_goods_detail(_text(comment="商品评价(2,000,000)"))
    assert d.comment_count == 2000000


def test_shop_name_with_dian_zi():
    # 店铺名含"店"字不该被误 skip
    d = extract_goods_detail(_text(shop="企鹅购超市-贝村店PDD"))
    assert d.shop_name == "企鹅购超市-贝村店PDD"


# ---------------------------------------------------------------------------
# 结构化路径 (window.rawData) —— 2026-09-10 新增, 修"正则误抓品牌累计"
# ---------------------------------------------------------------------------
#
# 真实数据来自 CDP 抓的万魔商品页 (goods_id=978586813372), 字段名/值都是实测的。


def _raw_html(goods: dict, mall: dict, brand_sales: int | None = 1482182) -> str:
    """拼一段含 window.rawData 的最小详情页 HTML."""
    section_list = []
    if brand_sales is not None:
        section_list.append({"data": {"burialPointInfo": {"brandSales": brand_sales}}})
    raw = {
        "store": {
            "initDataObj": {
                "goods": goods,
                "mall": mall,
                "oakData": {"sectionList": section_list},
            }
        }
    }
    return (
        "<html><body><script>"
        "window.rawData=" + json.dumps(raw, ensure_ascii=False) + ";"
        "</script></body></html>"
    )


def _real_goods():
    return {
        "goodsID": "978586813372",
        "goodsName": "【周杰伦同款】1MORE万魔 MIni10蓝牙耳机入耳式舒适跑步运动耳机",
        "sideSalesTip": "已拼5391件",
        "sales_tip": "已拼5391件",
        "appClientOnly": 0,
        "minGroupPrice": 98.9,
    }


def _real_mall():
    return {
        "mallSales": 128000,
        "salesTipV2": "本店已拼12.8万+件",
        "mallName": "万魔1 MORE影音旗舰店",
    }


def test_json_extract_real_payload():
    """实测万魔页: 单品 5391 / 店铺累计 128000 / 品牌累计 1482182 三者分清。

    这是核心回归 —— 历史上这三者会被混成一个数, 导致 "OPPO 1707万" 那种假结论。
    """
    d = extract_goods_detail_from_json(
        json.loads(_raw_html(_real_goods(), _real_mall()).split("window.rawData=")[1].split(";</script>")[0])
    )
    assert d is not None
    assert d.single_sales == 5391                     # ✅ 单品销量
    assert d.single_sales_text == "已拼5391件"
    assert d.shop_sales == 128000                     # 店铺累计 (纯数值)
    assert d.brand_sales == 1482182                   # 品牌累计 (纯数值)
    assert d.shop_name == "万魔1 MORE影音旗舰店"
    assert d.app_client_only is False
    assert d.sales_source == "json"


def test_json_prefers_side_sales_tip():
    """三个冗余字段共存时优先 sideSalesTip; 前一个缺失才用后面的。"""
    g = _real_goods()
    g["sideSalesTip"] = ""
    d = extract_goods_detail_from_json({"store": {"initDataObj": {"goods": g, "mall": _real_mall()}}})
    assert d.single_sales == 5391  # 从 sales_tip 拿到

    g2 = _real_goods()
    g2["sideSalesTip"] = "已拼1234件"   # sideSalesTip 有值时不看别的
    d2 = extract_goods_detail_from_json({"store": {"initDataObj": {"goods": g2, "mall": _real_mall()}}})
    assert d2.single_sales == 1234


def test_json_app_client_only_flag():
    """APP专享商品能被结构化标记出来 (比正文匹配"前往APP查看价格"可靠)。"""
    g = _real_goods()
    g["appClientOnly"] = 1
    g["sideSalesTip"] = ""      # 专享商品往往没销量文案
    g["sales_tip"] = ""
    raw = {"store": {"initDataObj": {"goods": g, "mall": _real_mall()}}}
    d = extract_goods_detail_from_json(raw)
    assert d is not None
    assert d.app_client_only is True
    assert d.single_sales is None   # 拿不到单品销量 → 下游降级


def test_json_missing_init_returns_none():
    assert extract_goods_detail_from_json({}) is None
    assert extract_goods_detail_from_json({"store": {}}) is None
    assert extract_goods_detail_from_json(None) is None
    assert extract_goods_detail_from_json({"store": {"initDataObj": "oops"}}) is None


def test_json_no_useful_field_returns_none():
    """节点在但一个有用字段都没有 → 返回 None, 让调用方回退正则。"""
    raw = {"store": {"initDataObj": {"goods": {"goodsName": "x"}, "mall": {}}}}
    assert extract_goods_detail_from_json(raw) is None


def test_best_prefers_json_over_text():
    """结构化能拿到 → 用结构化, sales_source='json'; 正文里的品牌累计不干扰。"""
    html = _raw_html(_real_goods(), _real_mall())
    # 正文故意塞一个"热销148.2万+件" (品牌累计) —— 正则很容易抓它
    text = "热销148.2万+件\n已拼5391件\n商品评价(161)\n万魔1 MORE影音旗舰店\n进店逛逛"
    d = extract_goods_detail_best(html, text)
    assert d.single_sales == 5391        # 不是 1482000
    assert d.sales_source == "json"
    assert d.comment_count == 161        # 评论数从正文补 (rawData 无此字段)


def test_best_falls_back_to_text_when_json_missing():
    """没有 rawData → 回退正文正则, 并标 sales_source='text' 提示来源弱。"""
    d = extract_goods_detail_best("<html><body>no rawdata</body></html>", "热销9.4万+件\n商品评价(100)")
    assert d.single_sales == 94000
    assert d.sales_source == "text"


def test_best_empty_inputs():
    d = extract_goods_detail_best("", "")
    assert d.single_sales is None
    assert d.sales_source is None
