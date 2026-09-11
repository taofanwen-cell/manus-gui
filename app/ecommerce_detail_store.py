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


def _snapshot_date_from_path(path: Path) -> str | None:
    """从 ``pdd_detail_YYYYmmddTHHMMSS.json`` 文件名反推采集日期 ``YYYY-mm-dd``.

    历史/未来详情文件可能没在 JSON 内显式标采集日期, 文件名里的时间戳是稳定判据,
    用它兜底, 保证报告总能标出快照日期 (与列表页可能跨天, 见 B-78 时效说明)。
    """
    stem = path.stem
    if not stem.startswith("pdd_detail_"):
        return None
    ts = stem[len("pdd_detail_"):]
    if len(ts) >= 8 and ts[:8].isdigit():
        y, m, d = ts[:4], ts[4:6], ts[6:8]
        return f"{y}-{m}-{d}"
    return None


def find_detail_files(data_dir: Path) -> list[Path]:
    """按 ``(mtime, 文件名)`` **升序**返回所有 ``pdd_detail_*.json``.

    升序是为了让 ``load_detail_merged`` 顺序覆盖 —— 后加载的 (更新的) 文件赢。
    """
    if not data_dir.is_dir():
        return []
    files = list(data_dir.glob(DETAIL_GLOB))
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))


def find_latest_detail_file(data_dir: Path) -> Path | None:
    """在 ``data_dir`` 下找最新的 ``pdd_detail_*.json``.

    排序键是 ``(mtime, 文件名)``: 只按 mtime 排时, 同一秒内写入的多个文件
    (测试 / 连续两次扫描) 结果会随机 —— 文件名里的 ``YYYYmmddTHHMMSS`` 正好作为
    稳定的第二判据。

    注意: **多数调用方要的是 ``load_detail_merged``**, 不是这个。单文件语义只适合
    "我就要最新一批"的场景; 跨品牌对比必须合并, 否则补采新品牌会把旧品牌单品销量
    丢掉 (2026-09-10 真实踩到: 补采 OPPO 后, 华为/小米等 5 个品牌单品销量全降级成
    店铺累计)。这个函数仍保留给需要精确单文件语义的调用方。
    """
    files = find_detail_files(data_dir)
    return files[-1] if files else None


def load_detail_merged(data_dir: Path) -> dict[str, DetailFields]:
    """合并 ``data_dir`` 下**所有** ``pdd_detail_*.json`` → ``{品牌: DetailFields}``.

    为什么是合并而不是取最新一份
    ----------------------------
    详情采集是**分批**的 (``--brands OPPO`` 单独补一轮), 每次落一个新时间戳文件。
    只读最新一份 → 补采哪个品牌, 其它品牌就"消失"了, 跨品牌对比直接失真 (旧品牌被迫
    降级成列表页累计口径)。

    合并规则: 按 ``(mtime, 文件名)`` 升序加载, **同品牌后加载的覆盖前面的** ——
    即"每个品牌保留最新一次采到的记录", 各品牌互不干扰。

    坏文件 (JSON 错 / 非 dict) 单个跳过, 不影响其它文件。
    """
    merged: dict[str, DetailFields] = {}
    for path in find_detail_files(data_dir):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        snap = _snapshot_date_from_path(path)  # 从文件名时间戳反推采集日期
        # 只保留 value 是 dict 的条目, 防止坏数据污染下游; 缺 snapshot_date 的注入兜底值
        for k, v in raw.items():
            if not isinstance(v, dict):
                continue
            if "snapshot_date" not in v:  # 优先用写入时显式标注的, 否则用文件名反推
                v = {**v, "snapshot_date": snap}
            merged[k] = v
    return merged


def load_latest_detail(data_dir: Path) -> dict[str, DetailFields]:
    """加载最新一份详情数据 → ``{品牌: DetailFields}``.

    .. deprecated:: 2026-09-10
        跨品牌对比请改用 :func:`load_detail_merged`。本函数只读单文件, 会因分批补采
        丢失其它品牌的单品销量。保留仅为向后兼容 (如确实只想看最新一批)。

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
    # 只保留 value 是 dict 的条目, 防止坏数据污染下游; 缺 snapshot_date 的注入兜底值
    snap = _snapshot_date_from_path(path)
    return {
        k: ({**v, "snapshot_date": snap} if isinstance(v, dict) and "snapshot_date" not in v else v)
        for k, v in raw.items() if isinstance(v, dict)
    }


def detail_for(brand: str, data_dir: Path) -> DetailFields | None:
    """取单个品牌的详情字段; 没采集过返回 ``None`` (调用方需降级).

    用合并视图, 保证"单独补采别的品牌"不会让这个品牌查不到。
    """
    return load_detail_merged(data_dir).get(brand.strip())


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
