"""详情页字段仓库: 读 ``data/pdd_detail_<ts>.json``, 提供"单品销量"等真实字段.

为什么单独一个模块
------------------
列表页 ``salesTip`` 是**店铺/品牌累计销量** (如 "品牌热销4026.3万+件"), 不是单品销量。
详情页正文里的 "热销/已抢/总售 N 件" 才是**单品销量**, 由
``scripts/pdd_detail_enrich.py`` 采集, 落盘在 ``data/pdd_detail_<ts>.json``::

    {
      "华为": {
        "top_model": "...", "top_price": 360,
        "list_sales": 50000000,     # 店铺/品牌累计 (参考, 不可当单品)
        "single_sales": 53000,      # ✅ 单品销量 (真实)
        "shop_name": null,
        "comment_count": 35508,
        "goods_id": "982649707880"
      }, ...
    }

消费方 (``_brand_row`` / API / insight) 通过本模块拿数据, **不要自己 glob data/**,
避免"最新的那份是哪个"在多处各写一遍。

只读 & 容错
----------
- 只读本地 JSON, 不发网络请求 (sandbox 可跑, 测试可依赖).
- 文件缺失 / 坏 JSON / 非 dict 一律返回空 dict, 调用方降级到列表页销量。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: ``scripts/pdd_detail_enrich.py`` 产物的文件名模式
DETAIL_GLOB = "pdd_detail_*.json"

#: 单个品牌的详情字段 (允许缺字段, 全部可空)
DetailFields = dict[str, Any]


def find_latest_detail_file(data_dir: Path) -> Path | None:
    """在 ``data_dir`` 下找最新 (mtime 最大) 的 ``pdd_detail_*.json``."""
    if not data_dir.is_dir():
        return None
    files = list(data_dir.glob(DETAIL_GLOB))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def load_latest_detail(data_dir: Path) -> dict[str, DetailFields]:
    """加载最新一份详情数据 → ``{品牌: DetailFields}``.

    任何异常都吞掉返回 ``{}`` —— 详情页是**增强**数据, 缺了不该让主流程挂。
    """
    path = find_latest_detail_file(data_dir)
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    # 只保留 value 是 dict 的条目, 防止坏数据污染下游
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def detail_for(brand: str, data_dir: Path) -> DetailFields | None:
    """取单个品牌的详情字段; 没采集过返回 ``None`` (调用方需降级)."""
    return load_latest_detail(data_dir).get(brand.strip())


def get_single_sales(brand: str, data_dir: Path) -> int | None:
    """便捷函数: 单品销量, 没采到返回 ``None``.

    注意 ``single_sales`` 可能是 ``None`` (详情页没匹配到销量文案), 也可能缺失 ——
    两种情况都当"未采到"处理, 让调用方降级。
    """
    d = detail_for(brand, data_dir)
    if not d:
        return None
    v = d.get("single_sales")
    return v if isinstance(v, int) else None
