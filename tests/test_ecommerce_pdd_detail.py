"""详情页字段提取 (extract_goods_detail) 单测 — 纯逻辑, 不碰浏览器."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ecommerce_pdd_parser import GoodsDetail, extract_goods_detail  # noqa: E402


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
    assert d == GoodsDetail(shop_name="艾码影音馆", single_sales=94000, comment_count=14870)


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
