"""离线演示：解析已保存的携程结果快照并生成候选航班报告。"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.ctrip_flights import FlightPreference, explain_ranking, parse_flight_html, rank_flights


def main() -> None:
    parser = argparse.ArgumentParser(description="Ctrip flight snapshot report; no browser, booking, or payment")
    parser.add_argument("--html", required=True, help="Saved flight-result HTML snapshot")
    parser.add_argument("--sort-by", choices=("balanced", "price", "duration", "departure"), default="balanced")
    parser.add_argument("--max-price", type=int)
    parser.add_argument("--earliest-departure")
    parser.add_argument("--latest-arrival")
    parser.add_argument("--exclude-shared", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    options = parse_flight_html(Path(args.html).read_text(encoding="utf-8"))
    preference = FlightPreference(args.sort_by, args.max_price, args.earliest_departure, args.latest_arrival, args.exclude_shared)
    ranked = rank_flights(options, preference)
    print(f"来源快照: {Path(args.html).name}（历史离线数据，不是实时票价）")
    print(f"解析 {len(options)} 条，约束后 {len(ranked)} 条；排序={args.sort_by}")
    for index, item in enumerate(ranked[:max(0, args.limit)], start=1):
        route = f"{item.departure_airport or '未知机场'} {item.departure_time or '--:--'} -> {item.arrival_airport or '未知机场'} {item.arrival_time or '--:--'}"
        print(f"{index}. {item.airline or '未知航司'} {item.flight_number or '未知航班'} | {route}")
        print(f"   {explain_ranking(item)}")


if __name__ == "__main__":
    main()
