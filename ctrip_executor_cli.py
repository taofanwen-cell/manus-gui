"""Ctrip flight-query executor CLI (deterministic, no booking/payment).

This is the production path that wires the
:class:`app.ctrip_form_executor.CtripFlightFormExecutor` to
:class:`app.tool.ctrip_query_tool.CtripQueryTool` through
:class:`app.ctrip_query_adapter.CtripQueryBrowserAdapter`.

The free-form LLM loop lives in :mod:`ctrip_query_assistant`; this file
intentionally avoids Manus so the executor can be exercised without an
LLM token or browser automation stack running.

Usage::

    python ctrip_executor_cli.py --origin 广州 --destination 北京 --date 2026-09-25 \
        --trace-path data/ctrip_executor_trace_<timestamp>.json

Exit code is 0 on a successful trace and 1 on any ``FormState.ABORTED`` or
``HUMAN_TAKEOVER_REQUIRED`` outcome. The trace JSON is always written if a
path is provided so failures can still be inspected post-hoc.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from app.ctrip_form_executor import CtripFlightFormExecutor
from app.ctrip_query_adapter import CtripQueryBrowserAdapter
from app.tool.browser_use_tool import BrowserUseTool
from app.tool.ctrip_query_tool import CtripQueryTool


def _parse_query(args: argparse.Namespace):
    try:
        requested = date.fromisoformat(args.date)
    except ValueError as exc:
        raise SystemExit(f"--date must be YYYY-MM-DD: {exc}") from exc
    if requested < date.today():
        raise SystemExit("--date cannot be in the past")
    if args.origin.strip() == args.destination.strip():
        raise SystemExit("--origin and --destination must differ")
    return args.origin, args.destination, args.date


async def _run(
    *, origin: str, destination: str, departure_date: str,
    trace_path: Path | None,
) -> int:
    from app.ctrip_form_executor import FlightQuery

    browser_tool = BrowserUseTool()
    ctrip_tool = CtripQueryTool(
        browser_tool=browser_tool,
        allowed_city_names=(origin, destination),
    )
    adapter = CtripQueryBrowserAdapter(ctrip_tool)
    executor = CtripFlightFormExecutor(
        adapter,
        FlightQuery(origin=origin, destination=destination, departure_date=departure_date),
    )
    try:
        report = await executor.run()
    finally:
        await browser_tool.cleanup()

    payload = {
        "success": report.success,
        "final_state": report.final_state.name,
        "origin_observed": report.origin_observed,
        "destination_observed": report.destination_observed,
        "date_observed": report.date_observed,
        "result_url": report.result_url,
        "visible_text_excerpt": (report.visible_text or "")[:500],
        "error": report.error,
        "human_takeover_required": report.human_takeover_required,
        "protection_detected": report.protection_detected,
        "trace": [asdict(step) for step in report.trace],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if trace_path is not None:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if report.success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Ctrip flight-query executor (deterministic, no booking/payment)")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--trace-path", type=Path, default=None)
    args = parser.parse_args()

    origin, destination, departure_date = _parse_query(args)
    return asyncio.run(_run(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        trace_path=args.trace_path,
    ))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())