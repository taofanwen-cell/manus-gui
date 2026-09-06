"""CDP happy-path runner for the deterministic Ctrip executor.

This is the **only** script in the project that talks to a real Chrome
through CDP. Run it after ``scripts/start_ctrip_cdp_chrome.ps1`` opens a
visible Chrome on a debug port and you have manually logged into Ctrip.

The runner does three things:

1. Verify the debug port answers (no page reads).
2. Drive ``CtripFlightFormExecutor`` through
   :class:`app.ctrip_query_adapter.CtripQueryBrowserAdapter` using
   ``CTRIP_CDP_URL`` to attach to the existing Chrome.
3. Dump the structured trace JSON to ``data/ctrip_executor_trace_<timestamp>.json``
   so the next session can diff layout changes.

Pass ``--origin`` / ``--destination`` / ``--date`` on the command line; the
defaults below match the project's smoke-test itinerary.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ctrip_form_executor import (  # noqa: E402
    CtripFlightFormExecutor,
    FlightQuery,
)
from app.ctrip_query_adapter import CtripQueryBrowserAdapter  # noqa: E402
from app.tool.browser_use_tool import BrowserUseTool  # noqa: E402
from app.tool.ctrip_query_tool import CtripQueryTool  # noqa: E402


DEFAULT_DATA_DIR = ROOT / "data"
CDP_URL_ENV = "CTRIP_CDP_URL"


def _check_cdp(cdp_url: str) -> None:
    """Block until the CDP endpoint responds. Refuses to leave 127.0.0.1."""

    parsed = urlparse(cdp_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(
            f"Refusing to connect: {cdp_url} is not a loopback HTTP CDP address. "
            "Set CTRIP_CDP_URL to http://127.0.0.1:<port>."
        )
    try:
        with urlopen(f"{cdp_url}/json/version", timeout=5) as response:
            version = json.loads(response.read().decode("utf-8"))
    except (URLError, OSError) as exc:
        raise SystemExit(
            f"CDP not reachable at {cdp_url}: {exc}\n"
            "Run scripts\\start_ctrip_cdp_chrome.ps1 first and log in manually."
        ) from exc
    print(f"[cdp] attached to {version.get('Browser', 'unknown')}")


async def _run(
    *, origin: str, destination: str, departure_date: str, trace_path: Path,
) -> int:
    query = FlightQuery(origin=origin, destination=destination, departure_date=departure_date)

    browser_tool = BrowserUseTool()
    ctrip_tool = CtripQueryTool(
        browser_tool=browser_tool,
        allowed_city_names=(origin, destination),
    )
    adapter = CtripQueryBrowserAdapter(ctrip_tool)
    executor = CtripFlightFormExecutor(adapter, query)

    try:
        report = await executor.run()
    finally:
        await browser_tool.cleanup()

    payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "cdp_url": os.getenv(CDP_URL_ENV, ""),
        "query": {
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
        },
        "success": report.success,
        "final_state": report.final_state.name,
        "origin_observed": report.origin_observed,
        "destination_observed": report.destination_observed,
        "date_observed": report.date_observed,
        "result_url": report.result_url,
        "visible_text_excerpt": (report.visible_text or "")[:500],
        "human_takeover_required": report.human_takeover_required,
        "protection_detected": report.protection_detected,
        "error": report.error,
        "trace": [
            {
                "state": step.state.name,
                "action": step.action,
                "target_index": step.target_index,
                "target_text": step.target_text,
                "target_field_index": step.target_field_index,
                "observed_value": step.observed_value,
                "note": step.note,
            }
            for step in report.trace
        ],
    }
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[cdp] trace written to {trace_path}")
    print(json.dumps(
        {
            "success": report.success,
            "final_state": report.final_state.name,
            "origin_observed": report.origin_observed,
            "destination_observed": report.destination_observed,
            "date_observed": report.date_observed,
            "result_url": report.result_url,
            "error": report.error,
            "human_takeover_required": report.human_takeover_required,
            "protection_detected": report.protection_detected,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if report.success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Ctrip executor against a real Chrome via CDP")
    parser.add_argument("--origin", default="广州")
    parser.add_argument("--destination", default="北京")
    parser.add_argument(
        "--date", dest="departure_date",
        default=date.today().replace(year=date.today().year + 1).isoformat(),
        help="YYYY-MM-DD (default: one year from today)",
    )
    parser.add_argument(
        "--trace-dir", type=Path, default=DEFAULT_DATA_DIR,
        help="Where directory to write the trace JSON (default: ./data)",
    )
    args = parser.parse_args()

    cdp_url = os.getenv(CDP_URL_ENV, "http://127.0.0.1:9222").rstrip("/")
    _check_cdp(cdp_url)

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    trace_path = args.trace_dir / f"ctrip_executor_trace_{timestamp}.json"

    return asyncio.run(_run(
        origin=args.origin,
        destination=args.destination,
        departure_date=args.departure_date,
        trace_path=trace_path,
    ))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())