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
import re
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


def _parse_filter_args(args: argparse.Namespace):
    """解析 CLI 过滤参数为 FlightPreference。失败抛 SystemExit 让 argparse 友好处理。"""
    from app.ctrip_flights import FlightPreference

    # 时间窗口: "HH:MM-HH:MM" 解析成 (start_min, end_min)
    def _parse_window(spec: str | None, *, label: str) -> tuple[int, int] | None:
        if not spec:
            return None
        m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", spec.strip())
        if not m:
            raise SystemExit(f"--{label} 必须是 HH:MM-HH:MM 格式, 如 06:00-12:00")
        start = int(m.group(1)) * 60 + int(m.group(2))
        end = int(m.group(3)) * 60 + int(m.group(4))
        if start == end:
            raise SystemExit(f"--{label} 起始和终止时间不能相同")
        return (start, end)

    def _parse_hhmm(value: str | None, *, label: str) -> str | None:
        if not value:
            return None
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
        if not m:
            raise SystemExit(f"--{label} 必须是 HH:MM 格式")
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    pref = FlightPreference(
        sort_by=getattr(args, "sort_by", "balanced"),
        max_price_cny=args.max_price,
        min_price_cny=args.min_price,
        earliest_departure=_parse_hhmm(args.earliest_departure, label="earliest-departure"),
        latest_arrival=_parse_hhmm(args.latest_arrival, label="latest-arrival"),
        depart_window=_parse_window(args.depart_window, label="depart-window"),
        arrive_window=_parse_window(args.arrive_window, label="arrive-window"),
        max_duration_minutes=args.max_duration,
        min_seats=args.min_seats,
        airlines_whitelist=tuple(a.strip() for a in (args.airlines or "").split(",") if a.strip()),
        airlines_blacklist=tuple(a.strip() for a in (args.block_airlines or "").split(",") if a.strip()),
        direct_flight_only=args.direct_only,
        exclude_shared=args.exclude_shared,
    )
    return pref


async def _run_url(
    *,
    origin: str | None,
    destination: str | None,
    departure_date: str | None,
    query: str | None,
    trace_path: Path | None,
    dry_run: bool,
    preference,
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
        "filter_active": any([
            preference.max_price_cny, preference.min_price_cny,
            preference.depart_window, preference.arrive_window,
            preference.max_duration_minutes, preference.min_seats,
            preference.airlines_whitelist, preference.airlines_blacklist,
            preference.direct_flight_only, preference.exclude_shared,
            preference.earliest_departure, preference.latest_arrival,
        ]),
        "filter_rule": _filter_rule_summary(preference),
    }

    if dry_run:
        # sandbox 没真 Chrome, 只构造 URL 不连浏览器
        return _emit(payload, trace_path) or 0

    # 真浏览器路线: goto_url → extract_content → parse_flight_html → rank_flights
    from app.ctrip_flights import parse_flight_html, rank_flights

    browser_tool = BrowserUseTool()
    try:
        result = await browser_tool.execute(action="go_to_url", url=url)
        if not result.success:
            payload["success"] = False
            payload["error"] = f"go_to_url failed: {result.error or 'unknown'}"
            return _emit(payload, trace_path) or 1
        extract_result = await browser_tool.execute(
            action="extract_content", goal="extract flight list HTML"
        )
        html = extract_result.output or ""
        options = parse_flight_html(html)
        ranked = rank_flights(options, preference)
        payload["page_url"] = browser_tool.current_url
        payload["options_total"] = len(options)
        payload["options_after_filter"] = len(ranked)
        payload["flights"] = [_flight_summary(o) for o in ranked[:20]]
    finally:
        await browser_tool.cleanup()
    return _emit(payload, trace_path) or 0


def _filter_rule_summary(preference) -> dict:
    """把 FlightPreference 翻译成可读的过滤规则说明。"""
    rules: dict = {}
    if preference.max_price_cny is not None:
        rules["max_price_cny"] = preference.max_price_cny
    if preference.min_price_cny is not None:
        rules["min_price_cny"] = preference.min_price_cny
    if preference.depart_window is not None:
        rules["depart_window"] = _window_to_str(preference.depart_window)
    if preference.arrive_window is not None:
        rules["arrive_window"] = _window_to_str(preference.arrive_window)
    if preference.max_duration_minutes is not None:
        rules["max_duration_minutes"] = preference.max_duration_minutes
    if preference.min_seats is not None:
        rules["min_seats"] = preference.min_seats
    if preference.airlines_whitelist:
        rules["airlines_whitelist"] = list(preference.airlines_whitelist)
    if preference.airlines_blacklist:
        rules["airlines_blacklist"] = list(preference.airlines_blacklist)
    if preference.direct_flight_only:
        rules["direct_flight_only"] = True
    if preference.exclude_shared:
        rules["exclude_shared"] = True
    if preference.earliest_departure:
        rules["earliest_departure"] = preference.earliest_departure
    if preference.latest_arrival:
        rules["latest_arrival"] = preference.latest_arrival
    rules["sort_by"] = preference.sort_by
    return rules


def _window_to_str(window: tuple[int, int]) -> str:
    s, e = window
    return f"{s // 60:02d}:{s % 60:02d}-{e // 60:02d}:{e % 60:02d}"


def _flight_summary(opt) -> dict:
    return {
        "flight_number": opt.flight_number,
        "airline": opt.airline,
        "departure_time": opt.departure_time,
        "arrival_time": opt.arrival_time,
        "price_cny": opt.price_cny,
        "duration_minutes": opt.duration_minutes,
        "is_transfer": opt.is_transfer,
        "is_shared": opt.is_shared,
        "remaining_seats": opt.remaining_seats,
    }


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

    # 过滤参数 (URL 路线专用, 不影响 state 路线)
    parser.add_argument("--max-price", type=int, help="最高价格 CNY")
    parser.add_argument("--min-price", type=int, help="最低价格 CNY")
    parser.add_argument("--earliest-departure", help="最早出发时间 HH:MM")
    parser.add_argument("--latest-arrival", help="最晚到达时间 HH:MM")
    parser.add_argument("--depart-window", help="出发时段窗口 HH:MM-HH:MM (跨天也可, 如 22:00-06:00)")
    parser.add_argument("--arrive-window", help="到达时段窗口 HH:MM-HH:MM")
    parser.add_argument("--max-duration", type=int, help="最大飞行时长 (分钟)")
    parser.add_argument("--min-seats", type=int, help="最低余票数")
    parser.add_argument("--airlines", help="航司白名单, 逗号分隔 (如 CZ,MU)")
    parser.add_argument("--block-airlines", help="航司黑名单, 逗号分隔")
    parser.add_argument("--direct-only", action="store_true", help="只要直飞")
    parser.add_argument("--exclude-shared", action="store_true", help="排除共享航班")
    parser.add_argument("--sort-by", default="balanced",
                        choices=["balanced", "price", "duration", "departure"])

    args = parser.parse_args()

    if args.strategy == "url" and args.dry_run and not args.query and not args.date:
        raise SystemExit("--dry-run 必须配合 --query 或 --origin/--destination/--date")

    origin, destination, departure_date_or_query = _parse_query(args)
    preference = _parse_filter_args(args)

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
                trace_path=args.trace_path, dry_run=args.dry_run, preference=preference,
            ))
        return asyncio.run(_run_url(
            origin=origin, destination=destination, departure_date=departure_date_or_query,
            query=None,
            trace_path=args.trace_path, dry_run=args.dry_run, preference=preference,
        ))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())