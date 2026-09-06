"""Ctrip flight-query assistant MVP (no booking or payment, agent mode only).

The deterministic executor + production adapter live in
``ctrip_executor_cli.py``; this file intentionally keeps the historical
Manus free-form agent loop untouched so it can land in its own commit.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import date

from app.agent.manus import Manus
from app.tool import Terminate, ToolCollection
from app.tool.browser_use_tool import BrowserUseTool
from app.tool.ctrip_query_tool import CtripQueryTool


@dataclass(frozen=True)
class FlightQuery:
    origin: str
    destination: str
    departure_date: str

    def validate(self) -> None:
        try:
            requested = date.fromisoformat(self.departure_date)
        except ValueError as exc:
            raise ValueError("departure_date must be YYYY-MM-DD") from exc
        if requested < date.today():
            raise ValueError("departure_date cannot be in the past")
        if self.origin.strip() == self.destination.strip():
            raise ValueError("origin and destination must be different")


def prompt_for(query: FlightQuery) -> str:
    return f'''You are running the Ctrip flight-query assistant in strict query mode.

Task: Find one-way flights from {query.origin} to {query.destination} on {query.departure_date}.

Required behavior:
- Use only the browser_use tool and terminate. The tool itself exposes only query-safe actions.
- If the DOM is empty, says whaleguard block, or lacks reliable controls, call action="vision_inspect" once for diagnosis. If it identifies protection, verification, or blocking, stop immediately and call terminate; do not refresh, change URLs, or retry. Never use coordinate clicks.
- Navigate only to ctrip.com or trip.com flight pages. Fill only origin, destination, and departure date, then query and read results.
- Never log in, register, handle CAPTCHA/verification, enter passenger data, open an order page, submit an order, make a payment, or leave allowed domains.
- If the site requires login, verification, booking, or payment, stop and report that human takeover is required.
- Return up to five visible flight options with any available airline, departure/arrival time, duration, cabin, and price. Clearly label missing fields as unavailable.
- Cite that the result is a live webpage observation and may change.
- When finished, call terminate with status=success.
'''


async def main() -> None:
    parser = argparse.ArgumentParser(description="Ctrip flight-query assistant (query only, no booking/payment)")
    parser.add_argument("--origin", required=True, help="Origin city, e.g. 上海")
    parser.add_argument("--destination", required=True, help="Destination city, e.g. 北京")
    parser.add_argument("--date", dest="departure_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--max-steps", type=int, default=12)
    args = parser.parse_args()

    query = FlightQuery(args.origin, args.destination, args.departure_date)
    query.validate()
    agent = await Manus.create(max_steps=max(1, min(args.max_steps, 20)))
    generic_browser = agent.available_tools.get_tool(BrowserUseTool().name)
    if not isinstance(generic_browser, BrowserUseTool):
        raise RuntimeError("BrowserUseTool is unavailable")

    # Replace Manus's broad tool set with a schema that only advertises the
    # least-privilege Ctrip query browser and explicit termination.
    # Bind the click whitelist to this exact itinerary. The tool refreshes the
    # DOM before each click and will reject cards/history links with stale indices.
    agent.available_tools = ToolCollection(
        CtripQueryTool(browser_tool=generic_browser, allowed_city_names=(query.origin, query.destination)),
        Terminate(),
    )
    try:
        await agent.run(prompt_for(query))
    finally:
        await agent.cleanup()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())