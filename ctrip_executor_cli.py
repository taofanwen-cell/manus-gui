"""Ctrip flight-query executor CLI (deterministic, no booking/payment).

Two strategies are supported:

* ``--strategy state`` (default): drive :class:`CtripFlightFormExecutor`
  through the launch form like a human would — fills origin/destination/date
  inputs, picks from the suggestion dropdown, clicks search. Verbose trace,
  8 states, recoverable.

* ``--strategy url``: build the result page URL directly via
  :mod:`app.ctrip_url_query` and ``browser.goto_url`` once. Skips the entire
  form + calendar interaction. Faster and easier to verify, but breaks if
  Ctrip changes the result page URL schema or the city is not in our IATA
  table.

Usage::

    # 状态机路线 (原有)
    python ctrip_executor_cli.py --strategy state --origin 广州 --destination 北京 --date 2026-09-25

    # URL 路线 (新)
    python ctrip_executor_cli.py --strategy url --origin 广州 --destination 北京 --date 2026-09-25

    # 自然语言查询 (URL 路线专用)
    python ctrip_executor_cli.py --strategy url --query "明天从北京到广州的机票"

    # URL 路线 dry-run: 只构造 URL 不连浏览器 (sandbox 可跑)
    python ctrip_executor_cli.py --strategy url --origin 广州 --destination 北京 --date 2026-09-25 --dry-run

Exit code is 0 on a successful trace and 1 on any failure.
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
from app.ctrip_url_query import build_flight_url_from_query
from app.tool.browser_use_tool import BrowserUseTool
from app.tool.ctrip_query_tool import CtripQueryTool


def _parse_query(args: argparse.Namespace):
    query = getattr(args, "query", None)
    if query and (args.origin or args.destination or args.date):
        raise SystemExit("--query 与 --origin/--destination/--date 互斥, 二选一")
    if query:
        return ("__query__", "__query__", query)
    if not args.date or not args.origin or not args.destination:
        raise SystemExit("必须传 --origin/--destination/--date, 或单传 --query")
    try:
        requested = date.fromisoformat(args.date)
    except ValueError as exc:
        raise SystemExit(f"--date must be YYYY-MM-DD: {exc}") from exc
    if requested < date.today():
        raise SystemExit("--date cannot be in the past")
    if args.origin.strip() == args.destination.strip():
        raise SystemExit("--origin and --destination must differ")
    return args.origin, args.destination, args.date


async def _run_state(
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
        "strategy": "state",
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
    return _emit(payload, trace_path)


async def _run_url(
    *,
    origin: str | None,
    destination: str | None,
    departure_date: str | None,
    query: str | None,
    trace_path: Path | None,
    dry_run: bool,
) -> int:
    """URL 路线: build URL → browser.goto_url → extract_content."""
    if query:
        url = build_flight_url_from_query(query)
        if url is None:
            payload = {
                "strategy": "url",
                "success": False,
                "error": "自然语言查询解析失败 (无日期或城市不在表里)",
                "query": query,
            }
            return _emit(payload, trace_path) or 1
    else:
        from app.ctrip_url_query import FlightSearchParams
        try:
            params = FlightSearchParams(origin, destination, departure_date)
        except Exception as exc:  # noqa: BLE001
            payload = {"strategy": "url", "success": False, "error": str(exc)}
            return _emit(payload, trace_path) or 1
        try:
            url = _build_with_check(params)
        except ValueError as exc:
            payload = {"strategy": "url", "success": False, "error": str(exc)}
            return _emit(payload, trace_path) or 1

    payload = {
        "strategy": "url",
        "success": True,
        "url": url,
        "dry_run": dry_run,
    }

    if dry_run:
        # sandbox 没真 Chrome, 只构造 URL 不连浏览器
        return _emit(payload, trace_path) or 0

    # 真浏览器路线: goto_url → extract_content → FlightOption 列表
    from app.ctrip_flights import extract_flight_options_from_html  # noqa: F401

    browser_tool = BrowserUseTool()
    try:
        result = await browser_tool.execute(action="go_to_url", url=url)
        if not result.success:
            payload["success"] = False
            payload["error"] = f"go_to_url failed: {result.error or 'unknown'}"
            return _emit(payload, trace_path) or 1
        extract_result = await browser_tool.execute(
            action="extract_content", goal="extract flight list as JSON"
        )
        payload["extracted_excerpt"] = (extract_result.output or "")[:2000]
        payload["page_url"] = browser_tool.current_url
    finally:
        await browser_tool.cleanup()
    return _emit(payload, trace_path) or 0


def _build_with_check(params) -> str:
    """build_flight_url + 错误处理 (导入放函数内避免 sandbox 启动时炸)。"""
    from app.ctrip_url_query import build_flight_url
    return build_flight_url(params)


def _emit(payload: dict, trace_path: Path | None) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if trace_path is not None:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if payload.get("success") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Ctrip flight-query executor (deterministic, no booking/payment)")
    parser.add_argument("--strategy", choices=["state", "url"], default="state",
                        help="state=走 8 状态表单; url=直接拼 URL 跳结果页")
    parser.add_argument("--origin")
    parser.add_argument("--destination")
    parser.add_argument("--date", help="YYYY-MM-DD")
    parser.add_argument("--query", help="自然语言查询 (URL 路线专用)")
    parser.add_argument("--dry-run", action="store_true",
                        help="URL 路线专用: 只构造 URL 不连浏览器 (sandbox 友好)")
    parser.add_argument("--trace-path", type=Path, default=None)
    args = parser.parse_args()

    if args.strategy == "url" and args.dry_run and not args.query and not args.date:
        raise SystemExit("--dry-run 必须配合 --query 或 --origin/--destination/--date")

    origin, destination, departure_date_or_query = _parse_query(args)

    if args.strategy == "state":
        if origin == "__query__":
            raise SystemExit("--strategy state 不支持 --query, 只接受 --origin/--destination/--date")
        return asyncio.run(_run_state(
            origin=origin, destination=destination, departure_date=departure_date_or_query,
            trace_path=args.trace_path,
        ))
    else:  # url
        if origin == "__query__":
            return asyncio.run(_run_url(
                origin=None, destination=None, departure_date=None,
                query=departure_date_or_query,
                trace_path=args.trace_path, dry_run=args.dry_run,
            ))
        return asyncio.run(_run_url(
            origin=origin, destination=destination, departure_date=departure_date_or_query,
            query=None,
            trace_path=args.trace_path, dry_run=args.dry_run,
        ))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())