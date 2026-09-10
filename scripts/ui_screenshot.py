#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UI 验收截图: 起 headless Chromium 打开报告页, 逐视图截图.

用途
----
改完 ``static/index.html`` 后要肉眼验收 (比如"销量口径"改动是否真的把
店铺累计和单品销量分开展示了), 靠 curl 拿 JSON 看不出 Chart.js 渲染结果。
本脚本把每个视图截成 PNG, 落到 ``data/ui_<view>_<ts>.png``。

为什么不用 CDP Chrome
---------------------
CDP Chrome 是**带登录态**的浏览器, 用于真扫拼多多。截图只需要静态页面
(报告数据由 HTTP API 现给), 用 playwright 自带的 headless chromium 更干净,
不干扰用户的登录会话。

前置
----
1. API 已在本机跑: ``python -m uvicorn app.ecommerce_api:app --port 8012``
2. 报告已能出数据 (``POST /api/competitor-report`` 或页面上的"扫描并生成")

用法
----
    python scripts/ui_screenshot.py --base http://127.0.0.1:8012 \\
        --brands 华为 小米 倍思 QCY 万魔 漫步者 OPPO
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VIEWS = [
    ("overview", "#view-overview", "总览"),
    ("price", "#view-price", "价格"),
    ("wordcloud", "#view-wordcloud", "词云"),
    ("table", "#view-table", "明细表"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8012")
    ap.add_argument("--brands", nargs="+", default=["华为", "小米", "倍思", "QCY", "万魔", "漫步者", "OPPO"])
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("需要 playwright: ./.venv/Scripts/python.exe -m pip install playwright && playwright install chromium")
        return 2

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    shots: list[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        page.goto(args.base, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)

        # 在页面上下文里 fetch —— 走同源相对路径, 和前端按钮完全同一条链路,
        # 避免脚本自己拼 base URL / 漏 include_insights 造成"截出来的和用户看到的不一样"。
        report = page.evaluate(
            """async ({brands, topN}) => {
                const r = await fetch('/api/competitor-report', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({brands, top_n: topN, include_insights: true}),
                });
                return await r.json();
            }""",
            {"brands": args.brands, "topN": args.top_n},
        )
        rows = report.get("rows", [])
        print(f"[report] {len(rows)} 个品牌")
        for r in rows:
            print(f"  {r['brand']:<6} top_sales={r.get('top_sales')} source={r.get('top_sales_source')}")
        if not rows:
            print("没有数据, 截图会全是空态。先确认 data/ 里有扫描产物 + detail 文件。")

        # 注入报告 → 直接调 renderAll。
        # 必须先 clearEmpty(): 空态卡片会把所有 .view 设成 display:none, 不恢复的话
        # 截图全是空白。
        page.evaluate(
            """(data) => {
                if (typeof clearEmpty === 'function') clearEmpty();
                if (typeof renderAlert === 'function') renderAlert(data.warnings || []);
                if (typeof renderAll === 'function') renderAll(data);
            }""",
            report,
        )
        page.wait_for_timeout(1500)

        for key, sel, label in VIEWS:
            # 页面菜单是 <div class="menu-item" data-view="...">, 视图用 .show 控制显隐
            page.evaluate(
                """(key) => {
                    document.querySelectorAll('.view').forEach(v => v.classList.remove('show'));
                    const t = document.getElementById('view-' + key);
                    if (t) t.classList.add('show');
                    document.querySelectorAll('.menu-item').forEach(m => {
                        m.classList.toggle('active', m.dataset.view === key);
                    });
                }""",
                key,
            )
            page.wait_for_timeout(900)
            shot = out_dir / f"ui_{key}_{ts}.png"
            page.screenshot(path=str(shot), full_page=True)
            shots.append(shot)
            print(f"[shot] {label} → {shot.name}")

        browser.close()

    print("\n完成:")
    for s in shots:
        print(" ", s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
