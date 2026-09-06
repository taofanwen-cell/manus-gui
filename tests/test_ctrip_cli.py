"""Tests for the Ctrip CLI entry points.

The free-form agent loop lives in :mod:`ctrip_query_assistant` (uses
Manus) and the deterministic executor lives in :mod:`ctrip_executor_cli`.
Both CLIs share a small validation helper that is unit-tested here.

The Manus-dependent path is only smoke-tested manually because it requires
the full Manus tool stack; the executor path is exercised end-to-end via
:mod:`tests.test_ctrip_query_adapter`.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ctrip_query_assistant as agent_cli  # noqa: E402
import ctrip_executor_cli as executor_cli  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Shared validation behaviour
# ---------------------------------------------------------------------------


def _executor_args(**overrides):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin")
    parser.add_argument("--destination")
    parser.add_argument("--date")
    parser.add_argument("--trace-path", type=Path, default=None)
    base = dict(origin="上海", destination="北京", date="2026-09-25",
                trace_path=None)
    base.update(overrides)
    argv: list = []
    for key, value in base.items():
        if value is None:
            continue
        argv.append(f"--{key}")
        argv.append(str(value))
    return parser.parse_args(argv)


def test_query_validation_iso_date_agent():
    agent_cli.FlightQuery("上海", "北京", "2026-09-25").validate()
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        agent_cli.FlightQuery("上海", "北京", "25-09-2026").validate()


def test_query_validation_past_date_agent():
    with pytest.raises(ValueError, match="past"):
        agent_cli.FlightQuery("上海", "北京", "2000-01-01").validate()


def test_query_validation_same_city_agent():
    with pytest.raises(ValueError, match="differ"):
        agent_cli.FlightQuery("上海", "上海", "2026-09-25").validate()


def test_executor_parse_query_rejects_past_date():
    args = _executor_args(date="2000-01-01")
    with pytest.raises(SystemExit, match="past"):
        executor_cli._parse_query(args)


def test_executor_parse_query_rejects_same_city():
    args = _executor_args(destination="上海")
    with pytest.raises(SystemExit, match="differ"):
        executor_cli._parse_query(args)


def test_executor_parse_query_rejects_bad_iso():
    args = _executor_args(date="25-09-2026")
    with pytest.raises(SystemExit, match="YYYY-MM-DD"):
        executor_cli._parse_query(args)


def test_executor_parse_query_ok():
    args = _executor_args()
    origin, destination, date_str = executor_cli._parse_query(args)
    assert (origin, destination, date_str) == ("上海", "北京", "2026-09-25")


# ---------------------------------------------------------------------------
# Agent-mode entry point routing
# ---------------------------------------------------------------------------


def test_agent_main_calls_run_agent(monkeypatch):
    called: Dict[str, Any] = {}

    async def fake_main_impl(query, max_steps):
        called["query"] = query
        called["max_steps"] = max_steps

    # Patch the body of ``agent_cli.main`` directly; monkeypatch.setattr on
    # the function replaces the coroutine wholesale.
    async def fake_main():
        return await fake_main_impl(
            agent_cli.FlightQuery("上海", "北京", "2026-09-25"),
            max_steps=8,
        )

    monkeypatch.setattr(agent_cli, "main", fake_main)
    monkeypatch.setattr(sys, "argv", [
        "ctrip_query_assistant.py",
        "--origin", "上海",
        "--destination", "北京",
        "--date", "2026-09-25",
        "--max-steps", "8",
    ])
    _run(agent_cli.main())
    assert called["query"].origin == "上海"
    assert called["max_steps"] == 8


def test_agent_main_validation_error(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "ctrip_query_assistant.py",
        "--origin", "上海",
        "--destination", "上海",
        "--date", "2026-09-25",
    ])
    with pytest.raises(ValueError, match="differ"):
        _run(agent_cli.main())


# ---------------------------------------------------------------------------
# Executor-mode entry point routing (no live browser)
# ---------------------------------------------------------------------------


def test_executor_main_calls_run(monkeypatch, tmp_path):
    captured: Dict[str, Any] = {}

    async def fake_run(*, origin, destination, departure_date, trace_path):
        captured["origin"] = origin
        captured["destination"] = destination
        captured["departure_date"] = departure_date
        captured["trace_path"] = trace_path
        return 0

    monkeypatch.setattr(executor_cli, "_run_state", fake_run)
    trace = tmp_path / "trace.json"
    monkeypatch.setattr(sys, "argv", [
        "ctrip_executor_cli.py",
        "--origin", "上海",
        "--destination", "北京",
        "--date", "2026-09-25",
        "--trace-path", str(trace),
    ])
    rc = executor_cli.main()
    assert rc == 0
    assert captured["origin"] == "上海"
    assert captured["trace_path"] == trace