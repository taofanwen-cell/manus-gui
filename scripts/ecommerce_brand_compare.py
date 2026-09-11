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
from app.ecommerce_detail_store import load_detail_merged  # noqa: E402
from app.ecommerce_pdd_parser import extract_competitors  # noqa: E402
from app.ecommerce_url_query import SearchParams, build_search_url  # noqa: E402
from app.ecommerce_browser_ctl import CDP_CONNECT_TIMEOUT_MS  # noqa: E402

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


#: top_sales 的取值来源 —— 决定下游 (API/前端/insight) 怎么标这个数字
SALES_SRC_SINGLE = "single"        # 详情页单品销量 (真实, 可跨品牌比)
SALES_SRC_SHOP_TOTAL = "shop_total"  # 列表页 salesTip 店铺/品牌累计 (不可当单品比)
SALES_SRC_UNKNOWN = "unknown"

# ---------------------------------------------------------------------------
# 品类一致性检查 (问题①, B-79)
# ---------------------------------------------------------------------------
#: 本项目是蓝牙耳机竞品分析, 报告标题/主题据此判断预期品类
EXPECTED_CATEGORY_LABEL = "蓝牙耳机"
#: 标题含这些 → 视为「符合预期品类」(耳机类), 优先级最高
_EARPHONE_HINTS = ("耳机", "耳麦", "耳塞", "tws", "headphone", "earbuds", "蓝牙耳",
                   "真无线", "降噪")
#: 标题含这些 (且非耳机) → 视为「疑似非预期品类」(手机类)
_PHONE_HINTS = ("手机", "reno", "find ", "findx", "mate ", "iphone", "红米",
                "redmi note", "note ", "magic", "畅享", "galaxy", "小米1", "gt ")
#: 触发顶部警告的阈值: 疑似手机绝对数 >= 此值 且 占比 >= 此比例
_CATEGORY_DRIFT_MIN_COUNT = 3
_CATEGORY_DRIFT_MIN_RATIO = 0.5


def _classify_title_category(title: str) -> str:
    """把一个商品标题归类: ``earphone`` / ``phone`` / ``other``.

    ``earphone`` 优先判定 —— 含耳机词的即便也含手机词 (如 "红米 Buds 耳机") 也算耳机,
    不计入疑似手机, 避免把 "红米 Buds" 这类真耳机误判成手机。
    """
    t = (title or "").lower()
    if any(h in t for h in _EARPHONE_HINTS):
        return "earphone"
    if any(h in t for h in _PHONE_HINTS):
        return "phone"
    return "other"


def detect_category_drift(titles: list[str] | None) -> dict:
    """统计样本标题的品类分布, 识别「预期品类漂移」(如手机品牌搜出一堆手机).

    返回 dict: ``total / earphone / phone / other / phone_ratio / triggered /
    expected``. ``triggered=True`` 表示疑似手机占比过半且达最小绝对数, 应在报告顶部告警。
    """
    if not titles:
        return {"total": 0, "earphone": 0, "phone": 0, "other": 0,
                "phone_ratio": 0.0, "triggered": False, "expected": EXPECTED_CATEGORY_LABEL}
    cats = [_classify_title_category(t) for t in titles]
    total = len(titles)
    phone = cats.count("phone")
    earphone = cats.count("earphone")
    ratio = phone / total if total else 0.0
    triggered = phone >= _CATEGORY_DRIFT_MIN_COUNT and ratio >= _CATEGORY_DRIFT_MIN_RATIO
    return {
        "total": total,
        "earphone": earphone,
        "phone": phone,
        "other": total - phone - earphone,
        "phone_ratio": ratio,
        "triggered": triggered,
        "expected": EXPECTED_CATEGORY_LABEL,
    }


def _resolve_sales(top, detail: dict | None, *, same_goods: bool) -> tuple[int | None, str, int | None]:
    """决定 ``top_sales`` 取哪个值, 并说明来源.

    销量语义是这个项目最容易搞错的地方 (见 README「关键设计决策」):

    - 列表页 ``salesTip`` = **店铺/品牌累计** ("品牌热销4026.3万+件"), 品牌越大数字越虚,
      跨品牌比"谁单品卖得好"是错的。
    - 详情页正文 "热销/已抢/总售 N 件" = **单品销量**, 才是能比的那个。

    **同源约束 (B-78 补完)**: 热度机型行的 机型名/价格/销量 必须锚定**同一商品**
    (同一 ``goods_id``), 不允许各取一半。因此:

    - 仅当 ``same_goods`` (详情页 goods_id == 列表页 top1 goods_id) 时, 才允许用详情页
      单品销量 —— 它与展示的机型名/价格同属一件商品, 标 "单品销量" 才站得住;
    - ``same_goods`` 为 False 时, **整行锚定列表页 top1**: 销量取列表页月销
      (:data:`SALES_SRC_SHOP_TOTAL` 口径), 绝不把详情页另一件商品的单品销量拼进这一行;
    - 没有列表页 top1 (只有详情页快照) 时, 整行都来自详情页, 销量用详情页单品销量
      (此时同样同源, 但需注意快照日期可能跨天, 见报告标注)。

    返回 ``(top_sales, source, shop_sales)``:
      - ``top_sales``   最终展示的销量 (None = 都没有)
      - ``source``      :data:`SALES_SRC_SINGLE` / :data:`SALES_SRC_SHOP_TOTAL` / :data:`SALES_SRC_UNKNOWN`
      - ``shop_sales``  店铺/品牌累计销量 (参考列, 可能 None)
    """
    list_sales = top.monthly_sales if top is not None else None

    single = None
    if detail:
        v = detail.get("single_sales")
        if isinstance(v, int):  # 详情页可能没匹配到销量文案 → None
            single = v

    # 同源: 只有详情页与列表页 top1 是同一商品时, 详情页单品销量才对得上展示的机型
    if same_goods and single is not None:
        return single, SALES_SRC_SINGLE, (detail or {}).get("list_sales") or list_sales
    # 不一致 / 详情页没采到单品销量 → 整行锚定列表页 top1 月销 (店铺/品牌累计口径)
    if list_sales is not None:
        return list_sales, SALES_SRC_SHOP_TOTAL, list_sales
    # 无列表页 top1, 整行只能靠详情页快照 (同源兜底)
    if single is not None:
        return single, SALES_SRC_SINGLE, (detail or {}).get("list_sales") or None
    return None, SALES_SRC_UNKNOWN, None


def _brand_row(brand: str, rep, n_parsed: int, *, detail: dict | None = None,
               titles: list[str] | None = None) -> dict:
    """把单个品牌的 AnalysisReport 压成一行的对比数据.

    同时返回 ``kw1/kw2/kw3`` (兼容 markdown 报告) 和
    ``top_keywords`` (list[dict], 给 insight 层做卖点空缺分析)。

    ``detail`` 是 ``data/pdd_detail_*.json`` 里该品牌的详情字段 (可选)。给了就让
    **单品销量优先** 于列表页店铺累计销量, 并补上店铺名/评论数 —— 没有就降级,
    但会在 ``top_sales_source`` 里标清楚, 不放任下游误读。
    """
    pd = rep.price_dist
    # 词云要用所有特征词的频次聚合, 取 top10 给前端合并去重; markdown 报告仍只显示 TOP3
    kw = rep.top_keywords[:10]
    top = rep.top_competitors[0] if rep.top_competitors else None
    # 热度机型行的 机型名/价格/销量 必须锚定**同一商品** (同一 goods_id), 不允许各取一半 (B-78):
    #   - detail.goods_id == 列表页 top1.goods_id → 同一商品, 可混合 (详情页名 + 列表页当天价 + 详情页单品销量);
    #   - 不一致 → 整行只用列表页当天 top1 (名字+价格+销量都自洽, 不跨商品拼);
    #   - 都不在 → 名字可来自 detail, 价格留空, 销量退回详情页快照 (整行同源自详情页)。
    detail_goods_id = (detail or {}).get("goods_id")
    top_goods_id = getattr(top, "goods_id", None) if top is not None else None
    same_goods = (
        bool(detail_goods_id) and bool(top_goods_id)
        and str(detail_goods_id) == str(top_goods_id)
    )
    if top is not None and same_goods:
        # 同一商品: 机型名用详情页(更完整), 价格用列表页当天真实价
        top_model = (detail or {}).get("top_model")
        top_price = top.price_cny
        if not top_model:  # 防御: 详情页名缺失时回退列表页名
            top_model = top.title[:24] + "…" if len(top.title) > 24 else top.title
    elif top is not None:
        # 不同时: 整行只用列表页当天 top1 (名字+价格+销量都自洽, 不跨商品拼)
        top_model = top.title[:24] + "…" if len(top.title) > 24 else top.title
        top_price = top.price_cny
    else:
        # 无列表页 top (测试 stub / 解析退化兜底): 名字可来自 detail, 价格留空, 不强行 "—"
        top_model = (detail or {}).get("top_model")
        top_price = None
    # 销量跟随同源决策 (见上方 same_goods): 仅同商品才用详情页单品销量, 否则整行锚定列表页 top1 月销
    top_sales, sales_source, shop_sales = _resolve_sales(top, detail, same_goods=same_goods)
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
        "top_model": top_model or "—",
        # 别名: insight 层/前端按 `top_product` 读, 以前只有 `top_model` → 机型名一直是 "—"
        "top_product": top_model or "—",
        # 价格与 top_model 来自同一商品 (见上方 goods_id 对齐), 不再跨商品拼装 (B-78)
        "top_price": top_price,
        "top_sales": top_sales,
        # 语义标记: 下游按这个决定标签 (单品销量 / 店铺累计), 不要只看数字
        "top_sales_source": sales_source,
        # 参考列: 店铺/品牌累计销量 (列表页 salesTip), 明确不可当单品比
        "shop_sales": shop_sales,
        # 详情页补充字段 (列表页没有)
        "shop_name": (detail or {}).get("shop_name"),
        "comment_count": (detail or {}).get("comment_count"),
        # 详情页快照采集日期 (可能与列表页跨天, 报告里标出, 见 B-78 快照时效说明)
        "detail_snapshot_date": (detail or {}).get("snapshot_date"),
        # 给 insight 层消费的完整字段 (list[dict] 带 count/pct)
        "top_keywords": [
            {"keyword": k.keyword, "count": k.count, "pct": k.pct} for k in kw
        ],
        # 给 insight 层消费的数据质量警告
        "warnings": list(rep.warnings),
        # 品类一致性 (B-79): 本品牌样本标题与预期品类「蓝牙耳机」漂移的判定
        "category_drift": detect_category_drift(titles),
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
        if not sales:
            sales_txt = "—"
        elif sales >= 10000:
            sales_txt = f"{sales/10000:g}万"
        else:
            sales_txt = str(sales)
        # 语义标记: 单品销量可跨品牌比, 店铺/品牌累计不行 —— 表格里直接写清楚
        src = r.get("top_sales_source")
        snap = r.get("detail_snapshot_date")
        if not sales:
            src_txt = "—"
        elif src == SALES_SRC_SINGLE:
            # 单品销量来自详情页快照, 注明采集日期, 提醒读者与列表页价格可能跨天
            src_txt = f"单品销量 (快照 {snap})" if snap else "单品销量"
        elif src == SALES_SRC_SHOP_TOTAL:
            src_txt = "⚠ 店铺/品牌累计"
        else:
            src_txt = "未知"
        return (
            f"| {r['brand']} | {r['top_model']} | "
            f"¥{r['top_price'] if r['top_price'] is not None else '—'} | {sales_txt} | {src_txt} |"
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

    # 快照日期提示: 任一品牌用了详情页单品销量, 注明其采集日期 (可能与列表页跨天)
    snap_dates = sorted({
        r.get("detail_snapshot_date") for r in rows
        if r.get("top_sales_source") == SALES_SRC_SINGLE and r.get("detail_snapshot_date")
    })
    snap_note = (
        "\n>\n> **数据时效提示**: 上表「单品销量」来自商品详情页快照"
        f"（采集日期：{'、'.join(snap_dates)}），与列表页价格可能跨天采集，请留意时效性。"
    ) if snap_dates else ""

    # 品类一致性提醒 (问题①, B-79): 任一品牌疑似手机占比过半 → 报告顶部告警
    _drifts = [r.get("category_drift") or {} for r in rows]
    _triggered = [(r["brand"], d) for r, d in zip(rows, _drifts) if d.get("triggered")]
    if _triggered:
        _lines = "\n".join(
            f"- 品牌 **{brand}**：疑似手机 {d.get('phone')} 个 / 有效样本 {d.get('total')} 个"
            f"（占比 {d.get('phone_ratio', 0):.0%}），与预期品类「{d.get('expected')}」不符"
            for brand, d in _triggered
        )
        warning_md = (
            "\n> ⚠ **品类一致性提醒**：以下品牌搜索结果疑似手机占比偏高，"
            "与报告标题预期品类不符，请人工复核搜索词 / 筛选条件：\n" + _lines
        )
    else:
        warning_md = ""

    return f"""# 拼多多蓝牙耳机竞品横向对比报告

> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 对比品牌: {scanned_txt}
> 失败品牌: {failed_txt}
> {source_note}
{warning_md}

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

| 品牌 | 热度 TOP 机型 | 价格 | 销量 | 销量口径 |
|---|---|---|---|---|
{hot_lines}

> **销量口径说明**: 「单品销量」来自商品详情页正文 ("热销/已抢/总售 N 件"), 可跨品牌比;
> 「⚠ 店铺/品牌累计」来自列表页 `salesTip` ("品牌热销 N 件"), 是该店铺/品牌的历史累计数,
> **不能当作单品销量跨品牌比较**。详情页没采到时才降级用后者。{snap_note}

## 五、结论要点

- 销量优先取**详情页单品销量**（可跨品牌比），采不到才降级到列表页 `salesTip` 的**店铺/品牌累计**（不可比），并在上表「销量口径」列标出。
- 店铺名 / 评论数仅详情页有；未跑 `scripts/pdd_detail_enrich.py` 的品牌这两列为空。
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

    # 详情页增强数据 (单品销量/店铺名/评论数) —— 没跑过 pdd_detail_enrich.py 就是空 dict,
    # 走降级路径 (用列表页累计销量), 但会在 top_sales_source 里标 "shop_total"。
    # 用合并视图: 分批补采的品牌各自保留自己的最新记录, 不互相覆盖。
    details = load_detail_merged(out_dir)
    if details:
        print(f"[detail] 已加载 {len(details)} 个品牌的详情页字段 → 销量口径优先用单品销量")
    else:
        print("[detail] 无 pdd_detail_*.json → 销量降级为列表页店铺/品牌累计 (口径会标 ⚠)")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url, timeout=CDP_CONNECT_TIMEOUT_MS)
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
            row = _brand_row(
                brand, rep, len(competitors),
                detail=details.get(brand),
                titles=[c.title for c in competitors],
            )
            rows.append(row)
            scanned.append(brand)
            pd = rep.price_dist
            src = row["top_sales_source"]
            print(
                f"[scan] {brand}: 样本 {len(competitors)} | 中位数 ¥{pd.median if pd else '—'} | "
                f"区间 ¥{pd.min if pd else '—'}~¥{pd.max if pd else '—'} | "
                f"销量={row['top_sales']} ({src})"
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
