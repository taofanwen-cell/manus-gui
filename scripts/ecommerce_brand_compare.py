"""多品牌竞品横向对比 (CDP 扫描 + 分析 + 汇总报告, 本地只读).

一次连上本机 Chrome (``scripts/start_pdd_cdp_chrome.ps1`` 起的那个), 依次扫多个
品牌的 "XX蓝牙耳机" 搜索列表页, 每个品牌解析 -> 分析, 最后汇总成一张横向对比
Markdown 报告。

只读保证: 只 ``page.goto`` 搜索列表页 + 读 ``page.content()``, 不点击、不输入、
不写任何东西到拼多多。

用法::

    $env:PDD_CDP_URL = 'http://127.0.0.1:9223'
    .\\.venv\\Scripts\\python.exe scripts\\ecommerce_brand_compare.py
    # 可选: --brands 华为 小米 倍思 QCY 万魔 漫步者
    #       --out-dir ./data
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ecommerce_analyzer import AnalysisPreference, analyze  # noqa: E402
from app.ecommerce_pdd_parser import extract_competitors  # noqa: E402
from app.ecommerce_url_query import SearchParams, build_search_url  # noqa: E402

CDP_URL_ENV = "PDD_CDP_URL"
DEFAULT_CDP = "http://127.0.0.1:9223"
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_BRANDS = ("华为", "小米", "倍思", "QCY", "万魔", "漫步者")


def _check_cdp(cdp_url: str) -> None:
    parsed = urlparse(cdp_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(
            f"拒绝连接: {cdp_url} 不是回环 CDP 地址。请把 {CDP_URL_ENV} 设为 http://127.0.0.1:<port>."
        )
    try:
        with urlopen(f"{cdp_url}/json/version", timeout=5) as resp:
            version = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError) as exc:
        raise SystemExit(
            f"CDP 连不上 {cdp_url}: {exc}\n先跑 scripts\\start_pdd_cdp_chrome.ps1 并手动登录."
        ) from exc
    print(f"[cdp] attached to {version.get('Browser', 'unknown')}")


def _fmt_price_cell(price) -> str:
    return "—" if price is None else f"¥{price}"


def _brand_row(brand: str, rep, n_parsed: int) -> dict:
    """把单个品牌的 AnalysisReport 压成一行的对比数据.

    同时返回 ``kw1/kw2/kw3`` (兼容 markdown 报告) 和
    ``top_keywords`` (list[dict], 给 insight 层做卖点空缺分析).
    """
    pd = rep.price_dist
    # 词云要用所有特征词的频次聚合, 取 top10 给前端合并去重; markdown 报告仍只显示 TOP3
    kw = rep.top_keywords[:10]
    top = rep.top_competitors[0] if rep.top_competitors else None
    return {
        "brand": brand,
        "parsed": n_parsed,
        "filtered": rep.filtered_size,
        "min": pd.min if pd else None,
        "max": pd.max if pd else None,
        "median": pd.median if pd else None,
        "p25": pd.p25 if pd else None,
        "p75": pd.p75 if pd else None,
        "b_u100": pd.bucket_under_100 if pd else 0,
        "b_100_300": pd.bucket_100_300 if pd else 0,
        "b_300_800": pd.bucket_300_800 if pd else 0,
        "b_o800": pd.bucket_over_800 if pd else 0,
        "kw1": kw[0].keyword if len(kw) > 0 else "—",
        "kw2": kw[1].keyword if len(kw) > 1 else "—",
        "kw3": kw[2].keyword if len(kw) > 2 else "—",
        "top_model": (top.title[:24] + "…") if top and len(top.title) > 24 else (top.title if top else "—"),
        "top_price": top.price_cny if top else None,
        "top_sales": top.monthly_sales if top else None,
        # 给 insight 层消费的完整字段 (list[dict] 带 count/pct)
        "top_keywords": [
            {"keyword": k.keyword, "count": k.count, "pct": k.pct} for k in kw
        ],
        # 给 insight 层消费的数据质量警告
        "warnings": list(rep.warnings),
    }


def _render_markdown(rows: list[dict], scanned: list[str], failed: list[str], source_note: str) -> str:
    def price_row(r):
        return (
            f"| {r['brand']} | {r['filtered']} | "
            f"¥{r['min'] if r['min'] is not None else '—'}~¥{r['max'] if r['max'] is not None else '—'} | "
            f"¥{r['median'] if r['median'] is not None else '—'} | "
            f"¥{r['p25'] if r['p25'] is not None else '—'}~¥{r['p75'] if r['p75'] is not None else '—'} | "
            f"{r['b_u100']} | {r['b_100_300']} | {r['b_300_800']} | {r['b_o800']} |"
        )

    def kw_row(r):
        return f"| {r['brand']} | {r['kw1']} | {r['kw2']} | {r['kw3']} |"

    def hot_row(r):
        sales = r["top_sales"]
        sales_txt = f"{sales/10000:g}万" if sales and sales >= 10000 else (str(sales) if sales else "—")
        return (
            f"| {r['brand']} | {r['top_model']} | "
            f"¥{r['top_price'] if r['top_price'] is not None else '—'} | {sales_txt} |"
        )

    price_lines = "\n".join(price_row(r) for r in rows)
    kw_lines = "\n".join(kw_row(r) for r in rows)
    hot_lines = "\n".join(hot_row(r) for r in rows)

    # 定位结论: 按中位数把品牌分档
    with_median = sorted((r for r in rows if r["median"] is not None), key=lambda r: r["median"])
    bands = []
    for r in with_median:
        m = r["median"]
        if m < 100:
            band = "低价走量 (<¥100)"
        elif m < 300:
            band = "中端 (¥100-300)"
        else:
            band = "中高端 (>¥300)"
        bands.append(f"- **{r['brand']}** 中位数 ¥{m} —— {band}")

    scanned_txt = "、".join(scanned) if scanned else "无"
    failed_txt = "、".join(failed) if failed else "无"

    return f"""# 拼多多蓝牙耳机竞品横向对比报告

> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 对比品牌: {scanned_txt}
> 失败品牌: {failed_txt}
> {source_note}

## 一、价格带横向对比

| 品牌 | 有效样本 | 价格区间 | 中位数 | P25~P75 | <¥100 | ¥100-300 | ¥300-800 | >¥800 |
|---|---|---|---|---|---|---|---|---|
{price_lines}

## 二、品牌价位定位

{chr(10).join(bands) if bands else "- 无有效价格数据"}

## 三、高频卖点横向对比（标题提取，取前 3）

| 品牌 | 卖点 TOP1 | TOP2 | TOP3 |
|---|---|---|---|
{kw_lines}

## 四、热度机型对比

| 品牌 | 热度 TOP 机型 | 价格 | 累计销量(店/牌) |
|---|---|---|---|
{hot_lines}

## 五、结论要点

- 以上销量字段为拼多多列表页 `salesTip` 的**店铺/品牌累计销量，非单品月销量**，仅作热度参考，不能跨品牌直接比谁单品卖得好。
- 评分 / 评论数 / 店铺名不在搜索列表页，需进详情页（本报告不含）。
- 定位结论：{with_median[0]['brand'] if with_median else '—'} 切入最低价位（中位数 ¥{with_median[0]['median']}），{with_median[-1]['brand'] if with_median else '—'} 价位最高（中位数 ¥{with_median[-1]['median']}）。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="多品牌拼多多蓝牙耳机竞品横向对比")
    parser.add_argument("--brands", nargs="+", default=list(DEFAULT_BRANDS), help="品牌列表")
    parser.add_argument("--suffix", default="蓝牙耳机", help="搜索关键词后缀 (默认: 蓝牙耳机)")
    parser.add_argument("--wait-ms", type=int, default=6000, help="每次导航后等待渲染毫秒数")
    parser.add_argument("--sleep-s", type=float, default=3.0, help="品牌间间隔秒数(防风控)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()

    cdp_url = os.getenv(CDP_URL_ENV, DEFAULT_CDP).rstrip("/")
    _check_cdp(cdp_url)

    from playwright.sync_api import sync_playwright

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    pref = AnalysisPreference(sort_by="sales", top_n=20)

    rows: list[dict] = []
    scanned: list[str] = []
    failed: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()

        for brand in args.brands:
            brand = brand.strip()
            if not brand:
                continue
            keyword = f"{brand}{args.suffix}"
            search_url = build_search_url(SearchParams(keyword=keyword))
            print(f"\n[scan] {brand} -> {search_url}")
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(args.wait_ms)
            except Exception as exc:  # noqa: BLE001
                print(f"[scan] {brand} 导航告警: {exc}")

            html = page.content()
            raw_path = out_dir / f"pdd_raw_{brand}_{timestamp}.html"
            raw_path.write_text(html, encoding="utf-8")
            print(f"[scan] {brand} raw html -> {raw_path.name} ({len(html)} chars)")

            competitors = extract_competitors(html)
            if not competitors:
                print(f"[scan] {brand} 解析出 0 个商品, 跳过")
                failed.append(brand)
                continue

            rep = analyze(competitors, pref)
            row = _brand_row(brand, rep, len(competitors))
            rows.append(row)
            scanned.append(brand)
            pd = rep.price_dist
            print(
                f"[scan] {brand}: 样本 {len(competitors)} | 中位数 ¥{pd.median if pd else '—'} | "
                f"区间 ¥{pd.min if pd else '—'}~¥{pd.max if pd else '—'}"
            )

            if brand is not args.brands[-1]:
                time.sleep(args.sleep_s)

        browser.close()

    if not rows:
        raise SystemExit("没有一个品牌解析出商品, 无法生成对比报告。")

    source_note = "数据来源: 拼多多搜索列表页真实数据 (本机登录 CDP 只读扫描), 每个品牌取 '品牌+蓝牙耳机' 首页结果"
    md = _render_markdown(rows, scanned, failed, source_note)
    md_path = out_dir / f"ecommerce_brand_compare_{timestamp}.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"\n[report] -> {md_path}")

    print("\n" + md)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
