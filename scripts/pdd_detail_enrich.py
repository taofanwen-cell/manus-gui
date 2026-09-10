"""详情页字段增强: 读各品牌 top1 热度商品详情页, 补单品销量/店铺名/评论数.

列表页 (search_result.html) 只有店铺累计销量、无店铺名/评论数; 详情页
(goods.html) 免登录能读, 里面有**单品销量** / 店铺名 / 评论数。
本脚本连一次 CDP, 对每个品牌读其列表页 top1 商品的详情页, 提取这三个字段。

单品销量怎么取 (2026-09-10 改)
------------------------------
**首选 ``window.rawData`` 结构化字段** (``extract_goods_detail_best``):
页面 ``goods.sideSalesTip`` = "已拼5391件" 语义明确, 且店铺/品牌累计另有
``mall.mallSales`` / ``brandSales`` 纯数值字段, 不会混。

**正文正则只作回退**: 详情页正文里同时有单品销量 ("已拼5391件") 和品牌累计
("热销148.2万+"), 正则很容易抓错 —— 这正是历史上 "OPPO 销量1707万" 的来源。
回退时结果标 ``sales_source="text"``, 下游知道来源弱。

只读: 只 ``page.goto`` + 滚动 + ``page.content()`` / ``inner_text``,
不点击/不输入/不写 PDD。

用法::

    .\\.venv\\Scripts\\python.exe scripts\\pdd_detail_enrich.py --brands 华为 小米 倍思 QCY 万魔 漫步者

输出 ``data/pdd_detail_<ts>.json`` (品牌 -> 详情字段), 并打印汇总表。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ecommerce_analyzer import AnalysisPreference, analyze  # noqa: E402
from app.ecommerce_pdd_parser import extract_competitors, extract_goods_detail_best  # noqa: E402

CDP_URL_ENV = "PDD_CDP_URL"
DEFAULT_CDP = "http://127.0.0.1:9223"
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_BRANDS = ("华为", "小米", "倍思", "QCY", "万魔", "漫步者")


def _check_cdp(cdp_url: str) -> None:
    parsed = urlparse(cdp_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(f"拒绝连接: {cdp_url} 不是回环 CDP 地址")
    try:
        with urlopen(f"{cdp_url}/json/version", timeout=5) as resp:
            version = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError) as exc:
        raise SystemExit(f"CDP 连不上 {cdp_url}: {exc}\n先跑 start_pdd_cdp_chrome.ps1") from exc
    print(f"[cdp] attached to {version.get('Browser', 'unknown')}")


def _find_latest_list_html(out_dir: Path, brand: str) -> Path | None:
    files = sorted(out_dir.glob(f"pdd_raw_{brand}_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def main() -> int:
    parser = argparse.ArgumentParser(description="读详情页补单品销量/店铺名/评论数")
    parser.add_argument("--brands", nargs="+", default=list(DEFAULT_BRANDS))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--cdp-url", default="", help=f"CDP 地址; 默认读环境变量 {CDP_URL_ENV} 或 {DEFAULT_CDP}")
    parser.add_argument("--wait-ms", type=int, default=5000)
    parser.add_argument("--sleep-s", type=float, default=2.0)
    args = parser.parse_args()

    cdp_url = (args.cdp_url or os.getenv(CDP_URL_ENV, DEFAULT_CDP)).rstrip("/")
    _check_cdp(cdp_url)

    out_dir: Path = args.out_dir
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    result: dict[str, dict] = {}

    return _run_playwright(args, cdp_url, out_dir, timestamp, result)


def _collect_one(brand: str, out_dir: Path):
    """解析某品牌的列表页 HTML, 返回 (top商品, 详情URL) 或 None."""
    list_html = _find_latest_list_html(out_dir, brand)
    if list_html is None:
        print(f"[detail] {brand}: 找不到列表页 HTML, 跳过")
        return None
    competitors = extract_competitors(list_html.read_text(encoding="utf-8", errors="replace"))
    if not competitors:
        print(f"[detail] {brand}: 列表页解析出 0 商品, 跳过")
        return None
    # top1 热度 = 店铺累计销量最高的 (与横向对比报告的"热度机型"一致)
    top = max(competitors, key=lambda c: c.monthly_sales or 0)
    if not top.url:
        print(f"[detail] {brand}: top1 无 url, 跳过")
        return None
    return top


def _record(record: dict, brand: str, top, detail) -> None:
    """把提取到的字段写进结果 (含口径来源标记)."""
    record[brand] = {
        "top_model": top.title,
        "top_price": top.price_cny,
        "list_sales": top.monthly_sales,       # 店铺/品牌累计 (参考)
        "single_sales": detail.single_sales,   # 单品销量 (真实)
        "single_sales_text": detail.single_sales_text,   # 原文, 便于核对
        "sales_source": detail.sales_source,   # json=结构化可信 / text=正则回退
        "shop_sales": detail.shop_sales,       # 店铺累计 (纯数值, 来自 mall.mallSales)
        "brand_sales": detail.brand_sales,     # 品牌累计 (纯数值)
        "app_client_only": detail.app_client_only,  # APP专享标记
        "shop_name": detail.shop_name,
        "comment_count": detail.comment_count,
        "goods_id": top.goods_id,
    }
    print(
        f"[detail] {brand}: 单品销量 {detail.single_sales} "
        f"({detail.single_sales_text or '—'}, src={detail.sales_source}) | "
        f"店铺 {detail.shop_name} | 评论 {detail.comment_count}"
        + (" | ⚠ APP专享隐藏" if detail.app_client_only else "")
    )


def _write(record: dict, out_dir: Path, timestamp: str) -> int:
    out_path = out_dir / f"pdd_detail_{timestamp}.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[detail] -> {out_path}")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


def _run_playwright(args, cdp_url: str, out_dir: Path, timestamp: str, result: dict) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()

        for brand in args.brands:
            brand = brand.strip()
            if not brand:
                continue
            got = _collect_one(brand, out_dir)
            if got is None:
                continue
            top = got
            print(f"[detail] {brand}: {top.title[:24]} -> {top.url}")
            try:
                page.goto(top.url, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(args.wait_ms)
                for _ in range(6):
                    page.mouse.wheel(0, 2000)
                    page.wait_for_timeout(600)
                html = page.content()
                text = page.inner_text("body")
            except Exception as exc:  # noqa: BLE001
                print(f"[detail] {brand}: 详情页读取失败: {exc}")
                continue

            _record(result, brand, top, extract_goods_detail_best(html, text))
            time.sleep(args.sleep_s)

        # 注意: 不要 browser.close() —— 这是 attach 到用户 Chrome 的 CDP 连接,
        # close() 会把用户可见的 Chrome 一起关掉。连接随 sync_playwright 退出而断开。

    return _write(result, out_dir, timestamp)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
