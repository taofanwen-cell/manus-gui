"""拼多多蓝牙耳机竞品横向对比 -> 可视化 HTML 报告 (离线, 无 CDN 依赖).

合并两类数据源:
1. 列表页 (search_result.html) -> 价格带 / 价格分位数 / 高频卖点
2. 详情页 (goods.html)     -> 单品销量 / 店铺名 / 评论数 (pdd_detail_*.json)

渲染成一个独立的、无外部依赖的 HTML 文件 (内联 CSS + 内联 SVG 图表),
可直接在浏览器打开或由 ``present_files`` 在线预览。

用法::

    .\\.venv\\Scripts\\python.exe scripts\\ecommerce_brand_compare_html.py

输出 ``data/ecommerce_brand_compare_<ts>.html``.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ecommerce_analyzer import AnalysisPreference, analyze  # noqa: E402
from app.ecommerce_pdd_parser import extract_competitors  # noqa: E402

DEFAULT_DATA_DIR = ROOT / "data"
# 品牌 -> 图表用主色 (贯穿所有图表)
BRAND_COLORS = {
    "华为": "#c7000b",
    "小米": "#ff6900",
    "倍思": "#00a870",
    "QCY": "#00b4d8",
    "万魔": "#7c3aed",
    "漫步者": "#e11d48",
}
PRICE_BUCKET_LABELS = ["<¥100", "¥100-300", "¥300-800", ">¥800"]
PRICE_BUCKET_COLORS = ["#94a3b8", "#38bdf8", "#fb923c", "#f43f5e"]


def _find_latest(out_dir: Path, pattern: str) -> Path | None:
    files = sorted(out_dir.glob(pattern), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return files[0] if files else None


def _fmt_wan(n) -> str:
    if n is None:
        return "—"
    if n >= 10_000:
        v = n / 10_000
        s = f"{v:.1f}".rstrip("0").rstrip(".")
        return f"{s}万"
    return str(n)


def _build_rows(out_dir: Path, brands: list[str]) -> list[dict]:
    detail_json = _find_latest(out_dir, "pdd_detail_*.json")
    detail: dict[str, dict] = {}
    if detail_json:
        detail = json.loads(detail_json.read_text(encoding="utf-8"))

    rows = []
    for brand in brands:
        list_html = _find_latest(out_dir, f"pdd_raw_{brand}_*.html")
        if list_html is None:
            continue
        comps = extract_competitors(list_html.read_text(encoding="utf-8", errors="replace"))
        if not comps:
            continue
        rep = analyze(comps, AnalysisPreference(sort_by="sales", top_n=20))
        pd = rep.price_dist
        d = detail.get(brand, {})
        rows.append({
            "brand": brand,
            "color": BRAND_COLORS.get(brand, "#475569"),
            "sample": rep.filtered_size,
            "median": pd.median if pd else None,
            "min": pd.min if pd else None,
            "max": pd.max if pd else None,
            "buckets": [
                pd.bucket_under_100 if pd else 0,
                pd.bucket_100_300 if pd else 0,
                pd.bucket_300_800 if pd else 0,
                pd.bucket_over_800 if pd else 0,
            ],
            "keywords": [k.keyword for k in rep.top_keywords[:3]],
            "top_model": d.get("top_model") or (rep.top_competitors[0].title if rep.top_competitors else "—"),
            "top_price": d.get("top_price") or (rep.top_competitors[0].price_cny if rep.top_competitors else None),
            "single_sales": d.get("single_sales"),
            "shop_name": d.get("shop_name"),
            "comment_count": d.get("comment_count"),
        })
    return rows


# ---------------------------------------------------------------------------
# SVG 图表
# ---------------------------------------------------------------------------

def _bar_horiz(rows: list[dict], value_key: str, title: str, fmt, is_log=False) -> str:
    """横向条形图 (对数可选)."""
    values = [r[value_key] for r in rows]
    nums = [v for v in values if v]
    if not nums:
        return ""
    vmax = max(nums) if not is_log else max(nums)
    import math
    bars = []
    y = 0
    bar_h = 30
    for r in rows:
        v = r[value_key]
        label = r["brand"]
        color = r["color"]
        if v is None:
            w = 0
            val_txt = "—"
        else:
            if is_log and v > 0:
                w = max(8, int(math.log10(v + 1) / math.log10(vmax + 1) * 460))
            else:
                w = max(0, int(v / vmax * 460)) if vmax else 0
            val_txt = fmt(v)
        bars.append(
            f'<g transform="translate(0,{y})">'
            f'<text x="0" y="20" font-size="14" fill="#334155" font-weight="600">{label}</text>'
            f'<rect x="70" y="6" width="{w}" height="18" rx="4" fill="{color}"/>'
            f'<text x="{70 + w + 8}" y="20" font-size="13" fill="#475569">{val_txt}</text>'
            f'</g>'
        )
        y += bar_h
    h = y + 6
    return (
        f'<svg viewBox="0 0 620 {h}" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="{title}">'
        + "".join(bars)
        + "</svg>"
    )


def _bar_stacked(rows: list[dict]) -> str:
    """价格带堆叠条形 (6 品牌 x 4 档)."""
    bars = []
    y = 0
    bar_h = 30
    max_total = max(sum(r["buckets"]) for r in rows) or 1
    for r in rows:
        segs = []
        x = 0
        for i, b in enumerate(r["buckets"]):
            w = int(b / max_total * 460)
            segs.append(f'<rect x="{70 + x}" y="{6 + y}" width="{w}" height="18" fill="{PRICE_BUCKET_COLORS[i]}"/>')
            x += w
        bars.append(
            f'<text x="0" y="{20 + y}" font-size="14" fill="#334155" font-weight="600">{r["brand"]}</text>'
            + "".join(segs)
        )
        y += bar_h
    return (
        f'<svg viewBox="0 0 620 {y + 30}" width="100%" xmlns="http://www.w3.org/2000/svg">'
        + "".join(bars)
        # 图例
        + f'<g transform="translate(70,{y + 6})">'
        + "".join(
            f'<rect x="{i * 110}" y="0" width="12" height="12" rx="2" fill="{PRICE_BUCKET_COLORS[i]}"/>'
            f'<text x="{i * 110 + 16}" y="11" font-size="12" fill="#64748b">{PRICE_BUCKET_LABELS[i]}</text>'
            for i in range(4)
        )
        + "</g></svg>"
    )


def _keyword_table(rows: list[dict]) -> str:
    head = "<tr>" + "".join(f'<th style="text-align:left">{r["brand"]}</th>' for r in rows) + "</tr>"
    body = "<tr>"
    for r in rows:
        tags = "".join(
            f'<span style="display:inline-block;background:{r["color"]}18;color:{r["color"]};'
            f'border-radius:6px;padding:2px 8px;margin:2px;font-size:12px;font-weight:600">{k}</span>'
            for k in r["keywords"]
        )
        body += f'<td style="vertical-align:top">{tags}</td>'
    body += "</tr>"
    return f"<table style='width:100%;border-collapse:collapse'><thead>{head}</thead><tbody>{body}</tbody></table>"


def _heat_table(rows: list[dict]) -> str:
    trs = []
    for r in rows:
        trs.append(
            f'<tr>'
            f'<td style="font-weight:600;color:{r["color"]}">{r["brand"]}</td>'
            f'<td style="font-size:13px">{r["top_model"][:30]}</td>'
            f'<td style="font-weight:600">¥{r["top_price"] if r["top_price"] is not None else "—"}</td>'
            f'<td>{_fmt_wan(r["single_sales"])}</td>'
            f'<td>{r["shop_name"] or "官方自营"}</td>'
            f'<td>{_fmt_wan(r["comment_count"])}</td>'
            f'</tr>'
        )
    return (
        "<table style='width:100%;border-collapse:collapse'>"
        "<thead><tr>"
        "<th style='text-align:left'>品牌</th><th style='text-align:left'>热度机型</th>"
        "<th>价格</th><th>单品销量</th><th>店铺</th><th>评论数</th>"
        "</tr></thead><tbody>"
        + "".join(trs)
        + "</tbody></table>"
    )


def _band(r: dict) -> str:
    m = r["median"]
    if m is None:
        return "—"
    if m < 100:
        return "低价走量 <¥100"
    if m < 300:
        return "中端 ¥100-300"
    return "中高端 >¥300"


def _render_html(rows: list[dict], source_note: str) -> str:
    median_sorted = sorted(rows, key=lambda r: r["median"] or 0)
    conclusion_lines = "".join(
        f'<li><b style="color:{r["color"]}">{r["brand"]}</b> 中位数 ¥{r["median"]} — {_band(r)}</li>'
        for r in median_sorted
    )
    card = lambda title, body: f'<div class="card"><h2>{title}</h2>{body}</div>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>拼多多蓝牙耳机竞品横向对比</title>
<style>
  :root {{ --bg:#f5f6f8; --card:#fff; --ink:#0f172a; --sub:#64748b; --line:#e2e8f0; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:980px; margin:0 auto; padding:32px 20px 60px; }}
  header {{ margin-bottom:24px; }}
  header h1 {{ font-size:26px; margin:0 0 8px; }}
  header p {{ color:var(--sub); font-size:14px; margin:4px 0; line-height:1.6; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
           padding:22px 24px; margin-bottom:20px; box-shadow:0 1px 3px rgba(15,23,42,.04); }}
  .card h2 {{ font-size:17px; margin:0 0 16px; }}
  table {{ font-size:14px; }}
  th, td {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:center; }}
  th {{ color:var(--sub); font-weight:600; font-size:13px; }}
  ul {{ margin:0; padding-left:20px; }}
  ul li {{ margin:6px 0; }}
  .note {{ font-size:12.5px; color:var(--sub); line-height:1.7; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
  @media (max-width:760px) {{ .grid {{ grid-template-columns:1fr; }} }}
  .tag {{ display:inline-block; font-size:11px; background:#f1f5f9; color:#475569;
          border-radius:5px; padding:2px 7px; margin:2px; }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>拼多多蓝牙耳机竞品横向对比</h1>
  <p>生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")} · 6 个品牌 × 各 20 个真实商品</p>
  <p>{source_note}</p>
</header>

{card("一、价格中位数对比（元）", _bar_horiz(rows, "median", "价格中位数", lambda v: f"¥{v}"))}

<div class="grid">
{card("二、价格带分布", _bar_stacked(rows))}
</div>

{card("三、高频卖点对比（标题提取，各取前 3）", _keyword_table(rows))}

{card("四、热度机型与真实销量（详情页数据）", _heat_table(rows))}

<div class="grid">
{card("五、单品销量对比（详情页，对数刻度）", _bar_horiz(rows, "single_sales", "单品销量", _fmt_wan, is_log=True))}
{card("六、评论数对比（详情页，对数刻度）", _bar_horiz(rows, "comment_count", "评论数", _fmt_wan, is_log=True))}
</div>

{card("七、品牌价位定位", f'<ul>{conclusion_lines}</ul>')}

<footer class="note">
  <b>数据诚实声明：</b>
  <ul style="margin-top:6px">
    <li>单品销量/评论数/店铺名来自<b>详情页</b>（免登录可读）；价格带/中位数/卖点来自<b>搜索列表页</b>。</li>
    <li>「单品销量」是商品累计销量（如“已抢11.3万+件”），<b>非月销量</b>；列表页那个“5000万”是店铺/品牌累计，本报告已弃用。</li>
    <li>万魔为近期上架新品（评论数仅 48），暂无销量显示；华为/小米为官方自营，详情页不展示第三方店铺名。</li>
    <li>评分（星级数字）详情页不展示，故以评论数作为热度替代指标。</li>
  </ul>
</footer>
</div>
</body>
</html>"""
    return html


def main() -> int:
    out_dir = DEFAULT_DATA_DIR
    brands = list(BRAND_COLORS.keys())
    rows = _build_rows(out_dir, brands)
    if not rows:
        raise SystemExit("没有可用数据：先跑 pdd_cdp_extract / ecommerce_brand_compare / pdd_detail_enrich")

    source_note = "数据来源：拼多多搜索列表页 + 商品详情页真实数据（本机登录 CDP 只读扫描），关键词「品牌+蓝牙耳机」。"
    html = _render_html(rows, source_note)
    out_path = out_dir / f"ecommerce_brand_compare_{datetime.now().strftime('%Y%m%dT%H%M%S')}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[html] -> {out_path}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
