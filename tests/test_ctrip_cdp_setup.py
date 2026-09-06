from pathlib import Path


def test_ctrip_cdp_environment_has_priority_over_generic_browser_settings():
    source = (Path(__file__).parents[1] / "app" / "tool" / "browser_use_tool.py").read_text(encoding="utf-8")
    assert "CTRIP_CDP_URL" in source
    assert "attr in {\"headless\", \"disable_security\", \"cdp_url\"}" in source


def test_cdp_precheck_rejects_non_local_addresses():
    source = (Path(__file__).parents[1] / "scripts" / "test_ctrip_cdp_connection.py").read_text(encoding="utf-8")
    assert 'parsed.hostname not in {"127.0.0.1", "localhost", "::1"}' in source
