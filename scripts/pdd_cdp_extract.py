"""Read-only PDD competitor extraction via CDP (local machine only).

This is the **only** e-commerce script that talks to a real Chrome through
CDP. Run it after ``scripts/start_pdd_cdp_chrome.ps1`` opens a visible Chrome
on a debug port and you have manually logged into PDD.

What it does (all read-only — no click, no input, no write to PDD):

1. Verify the debug port answers, refusing anything that is not 127.0.0.1.
2. Attach to the existing Chrome via ``playwright.chromium.connect_over_cdp``
   (uses *your* installed Chrome, no browser download needed).
3. Navigate to the mobile search page for ``--keyword`` and wait for render.
4. Capture a screenshot + raw HTML + visible text, and report a best-effort
   product-card count. This raw material is what the next step uses to pin
   down exact DOM selectors.

Why "scan first": we do not yet know PDD's exact DOM selectors. So this
script dumps evidence (screenshot / HTML / text) instead of pretending to
extract clean title/price/sales rows that would be wrong.

Outputs (under ``--out-dir``, default ``./data``):
- ``pdd_shot_<ts>.png``    screenshot
- ``pdd_raw_<ts>.html``    raw page HTML
- ``pdd_scan_<ts>.json``   structured scan summary
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ecommerce_url_query import SearchParams, build_search_url  # noqa: E402

CDP_URL_ENV = "PDD_CDP_URL"
DEFAULT_CDP = "http://127.0.0.1:9223"
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_KEYWORD = "蓝牙耳机"

# Candidate selectors tried to count product cards. PDD's DOM is not public,
# so we count hits and let the next step pick the real one from raw HTML.
_CARD_SELECTORS = (
    "[data-goods-id]",
    "li[data-goods-id]",
    "div[data-goods-id]",
    "[class*='goods-item']",
    "[class*='goods_item']",
    "[class*='goods-list'] > *",
    "[class*='search-result'] > *",
)

# If any of these appear in visible text, we are likely still behind a login
# or verification wall rather than seeing real product data.
_LOGIN_WALL_MARKERS = (
    "扫码登录",
    "验证码登录",
    "登录后查看",
    "立即登录",
    "手机验证码",
    "发送验证码",
)


def _check_cdp(cdp_url: str) -> None:
    """Block until the CDP endpoint responds. Refuses non-loopback addresses."""
    parsed = urlparse(cdp_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(
            f"Refusing to connect: {cdp_url} is not a loopback HTTP CDP address. "
            f"Set {CDP_URL_ENV} to http://127.0.0.1:<port>."
        )
    try:
        with urlopen(f"{cdp_url}/json/version", timeout=5) as response:
            version = json.loads(response.read().decode("utf-8"))
    except (URLError, OSError) as exc:
        raise SystemExit(
            f"CDP not reachable at {cdp_url}: {exc}\n"
            "Run scripts\\start_pdd_cdp_chrome.ps1 first and log in manually."
        ) from exc
    print(f"[cdp] attached to {version.get('Browser', 'unknown')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only PDD competitor scan via CDP")
    parser.add_argument("--keyword", default=DEFAULT_KEYWORD, help="Search keyword (default: 蓝牙耳机)")
    parser.add_argument(
        "--wait-ms", type=int, default=6000,
        help="Milliseconds to wait for cards to render after navigation (default: 6000)",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=DEFAULT_DATA_DIR,
        help="Directory for screenshot / raw HTML / scan JSON (default: ./data)",
    )
    args = parser.parse_args()

    cdp_url = os.getenv(CDP_URL_ENV, DEFAULT_CDP).rstrip("/")
    _check_cdp(cdp_url)

    keyword = args.keyword.strip()
    search_url = build_search_url(SearchParams(keyword=keyword))
    print(f"[scan] keyword={keyword!r} -> {search_url}")

    from playwright.sync_api import sync_playwright

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    shot_path = out_dir / f"pdd_shot_{timestamp}.png"
    html_path = out_dir / f"pdd_raw_{timestamp}.html"
    scan_path = out_dir / f"pdd_scan_{timestamp}.json"

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()

        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(args.wait_ms)
        except Exception as exc:  # noqa: BLE001 - report and keep going, we still dump state
            print(f"[scan] navigation warning: {exc}")

        final_url = page.url
        title = page.title()
        try:
            visible_text = page.inner_text("body")
        except Exception:  # noqa: BLE001
            visible_text = ""

        try:
            page.screenshot(path=str(shot_path), full_page=False)
            print(f"[scan] screenshot -> {shot_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"[scan] screenshot failed: {exc}")

        try:
            html_path.write_text(page.content(), encoding="utf-8")
            print(f"[scan] raw html -> {html_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"[scan] raw html failed: {exc}")

        card_counts: dict[str, int] = {}
        for sel in _CARD_SELECTORS:
            try:
                card_counts[sel] = page.locator(sel).count()
            except Exception:  # noqa: BLE001
                card_counts[sel] = -1

        login_wall_detected = any(m in visible_text for m in _LOGIN_WALL_MARKERS)

        payload = {
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "cdp_url": cdp_url,
            "keyword": keyword,
            "search_url": search_url,
            "final_url": final_url,
            "title": title,
            "visible_text_length": len(visible_text),
            "visible_text_excerpt": visible_text[:1500],
            "login_wall_detected": login_wall_detected,
            "card_counts": card_counts,
            "screenshot": str(shot_path),
            "raw_html": str(html_path),
        }
        scan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[scan] summary -> {scan_path}")

        # 注意: 不要 browser.close() —— 这是 attach 到用户 Chrome 的 CDP 连接,
        # close() 会把用户可见的 Chrome 一起关掉。连接随 sync_playwright 退出而断开。

    print(json.dumps(
        {
            "keyword": keyword,
            "final_url": final_url,
            "title": title,
            "visible_text_length": len(visible_text),
            "login_wall_detected": login_wall_detected,
            "card_counts": card_counts,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
