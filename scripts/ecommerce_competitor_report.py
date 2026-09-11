"""端到端竞品调研报告生成器 (只读, 无任何写操作).

用法
----
先跑 ``scripts/pdd_cdp_extract.py`` 拿到 ``data/pdd_raw_*.html``,
再跑本脚本对它分析:

.. code-block:: text

    .\\.venv\\Scripts\\python.exe .\\scripts\\ecommerce_competitor_report.py --input data\\pdd_raw_20260907T164509.html

不传 ``--input`` 时自动找 ``data/`` 下最新的 ``pdd_raw_*.html``.

输出
----
- 终端打印可读报告
- 同时写一份 Markdown 到 ``data/ecommerce_competitor_report_<ts>.md`` (报告文件, 非验收文档)

这条链路 = 采集(CDP) -> 解析(pdd_parser) -> 分析(analyzer) -> 呈现(本脚本).
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ecommerce_analyzer import AnalysisPreference, analyze  # noqa: E402
from app.ecommerce_pdd_parser import extract_competitors  # noqa: E402

DEFAULT_DATA_DIR = ROOT / "data"

# 内嵌 rawData 里记录真实搜索词, 如 "searchKey":"蓝牙耳机"
_SEARCH_KEY_RE = re.compile(r'"searchKey"\s*:\s*"([^"]+)"')


def _detect_keyword(html: str) -> str | None:
    m = _SEARCH_KEY_RE.search(html)
    return m.group(1) if m else None


def _find_latest_raw(out_dir: Path) -> Path | None:
    files = sorted(out_dir.glob("pdd_raw_*.html"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return files[0] if files else None


def _fmt_price_dist(pd) -> str:
    if not pd:
        return "价格数据不全"
    return (
        f"**<¥100**: {pd.bucket_under_100} 个  |  **¥100-300**: {pd.bucket_100_300} 个  |  "
        f"**¥300-800**: {pd.bucket_300_800} 个  |  **>¥800**: {pd.bucket_over_800} 个\n\n"
        f"- 价格区间 **¥{pd.min} ~ ¥{pd.max}**, 中位数 **¥{pd.median}** "
        f"(P25 ¥{pd.p25} / P75 ¥{pd.p75})"
    )


def _fmt_keywords(report) -> str:
    if not report.top_keywords:
        return "没有提取到卖点关键词."
    lines = []
    for kw in report.top_keywords[:10]:
        lines.append(f"- **{kw.keyword}**: {kw.count} 个商品 ({kw.pct * 100:.0f}%)")
    return "\n".join(lines)


def _fmt_top_products(report) -> str:
    if not report.top_competitors:
        return "没有满足条件的商品."
    lines = []
    for i, c in enumerate(report.top_competitors, 1):
        sales = f"{c.monthly_sales:,}" if c.monthly_sales is not None else "?"
        price = f"¥{c.price_cny}" if c.price_cny is not None else "?"
        lines.append(f"{i}. **{price}** | 销量 **{sales}** | {c.title}")
    return "\n".join(lines)


def _fmt_warnings(report) -> str:
    if not report.warnings:
        return "无"
    return "\n".join(f"- {w}" for w in report.warnings)


def _render_markdown(report, keyword: str, source: str) -> str:
    return f"""# 拼多多「{keyword}」竞品调研报告

> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
> 关键词: {keyword}  
> 数据来源: {source}  

## 一句话结论

{report.summary}

## 价格分布

{_fmt_price_dist(report.price_dist)}

## 高频卖点

{_fmt_keywords(report)}

## 店铺/品牌累计销量 TOP 商品

> 说明：列表页 ``salesTip`` 字段的「本店已拼 / 全店总售 / 品牌热销」是**店铺或品牌累计销量**，
> 不是单品月销量。只能反映"这个店/品牌多热门"，不能精确到单个商品的月销量。

{_fmt_top_products(report)}

## 数据说明 / 局限

{_fmt_warnings(report)}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a plain-language competitor report from a PDD scan")
    parser.add_argument("--input", type=Path, default=None, help="Path to a pdd_raw_*.html file")
    parser.add_argument("--keyword", default=None, help="Search keyword label (auto-detected from HTML if omitted)")
    parser.add_argument("--sort", default="sales", choices=["sales", "price", "balanced", "rating", "newest"])
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()

    out_dir: Path = args.out_dir
    raw_path = args.input or _find_latest_raw(out_dir)
    if raw_path is None or not raw_path.exists():
        raise SystemExit(
            f"没有找到 pdd_raw_*.html。请先运行 scripts\\pdd_cdp_extract.py 拿到扫描结果。"
            f"可传 --input 指定文件路径。"
        )

    html = raw_path.read_text(encoding="utf-8", errors="replace")
    keyword = args.keyword or _detect_keyword(html) or "蓝牙耳机"

    competitors = extract_competitors(html)
    if not competitors:
        raise SystemExit("没能从 HTML 解析出任何商品（可能没抓到 window.rawData）。")

    pref = AnalysisPreference(sort_by=args.sort, top_n=args.top_n)
    report = analyze(competitors, pref)

    print(f"=== 拼多多「{keyword}」竞品调研报告 ===\n")
    print("【一句话结论】", report.summary, "\n")
    print("【价格分布】")
    print(_fmt_price_dist(report.price_dist), "\n")
    print("【高频卖点】")
    print(_fmt_keywords(report), "\n")
    print("【店铺/品牌累计销量 TOP 商品】(注: 是店铺/品牌累计, 非单品月销量)")
    print(_fmt_top_products(report), "\n")
    print("【数据说明】")
    print(_fmt_warnings(report))

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    md_path = out_dir / f"ecommerce_competitor_report_{ts}.md"
    md_path.write_text(
        _render_markdown(report, keyword=keyword, source=str(raw_path)),
        encoding="utf-8",
    )
    print(f"\n[report] markdown written -> {md_path}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
