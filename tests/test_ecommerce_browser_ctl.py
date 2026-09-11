"""浏览器控制层 + 两端点 (``POST /api/browser/ensure`` / ``GET /api/browser/login-status``).

测试原则
--------
- **零 CDP / 零 Chrome / 零 Popen**: 一律注入 :class:`StubBrowserController`。
- 只测**契约与幂等**: ensure 已就绪时不重复启动 / 启动失败给 how_to_fix /
  login-status 形状稳定 / cookie 名只回显名字不回显值。
- 不碰真实 ``data/``, 不连真实网络。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.ecommerce_api import create_app
from app.ecommerce_browser_ctl import (
    DEFAULT_START_SCRIPT,
    LOGIN_COOKIE_CANDIDATES,
    MAX_COOKIE_NAMES_ECHOED,
    StubBrowserController,
    SubprocessBrowserController,
    _pick_login_cookie_names,
)


def _client(browser_ctl) -> TestClient:
    return TestClient(create_app(browser_ctl=browser_ctl))


# ---------------------------------------------------------------------------
# StubBrowserController: 契约
# ---------------------------------------------------------------------------


def test_ensure_when_already_running_is_idempotent():
    """已经在跑 → 绝不重复启动 (重复起会触发同 profile 的实例 handoff)。"""
    stub = StubBrowserController(probe_ok=True)
    res = stub.ensure()
    assert res.ready is True
    assert res.launched is False
    assert stub.ensure_calls == 1


def test_ensure_launches_when_cdp_down():
    stub = StubBrowserController(probe_ok=False, ensure_ok=True)
    res = stub.ensure()
    assert res.ready is True
    assert res.launched is True


def test_ensure_failure_carries_how_to_fix():
    stub = StubBrowserController(probe_ok=False, ensure_ok=False)
    res = stub.ensure()
    assert res.ready is False
    assert "start_pdd_cdp_chrome.ps1" in res.how_to_fix


def test_check_login_reports_cdp_down_without_cookie_method():
    stub = StubBrowserController(probe_ok=False)
    st = stub.check_login()
    assert st.cdp_ready is False
    assert st.logged_in is False
    assert st.method == "none"


def test_check_login_echoes_cookie_names_but_not_values():
    stub = StubBrowserController(logged_in=True, cookie_names=("PASS_ID_TOKEN",))
    st = stub.check_login()
    assert st.logged_in is True
    assert st.cookie_names == ["PASS_ID_TOKEN"]


# ---------------------------------------------------------------------------
# _pick_login_cookie_names: 只取名, 只挑 PDD 域
# ---------------------------------------------------------------------------


def test_pick_cookie_names_keeps_pdd_domain_and_candidates():
    cookies = [
        {"name": "PASS_ID_TOKEN", "domain": ".yangkeduo.com", "value": "SECRET"},
        {"name": "pdd_user_id", "domain": ".yangkeduo.com", "value": "SECRET"},
        {"name": "some_tracker", "domain": ".yangkeduo.com", "value": "SECRET"},
        {"name": "unrelated", "domain": ".example.com", "value": "SECRET"},
    ]
    names = _pick_login_cookie_names(cookies, LOGIN_COOKIE_CANDIDATES)
    assert "PASS_ID_TOKEN" in names
    assert "pdd_user_id" in names
    assert "some_tracker" in names          # PDD 域 → 回显, 方便把真实名字钉成事实
    assert "unrelated" not in names         # 非 PDD 域且不在候选 → 剔除


def test_pick_cookie_names_never_leaks_values():
    """红线: 只回传 cookie 名。值哪怕混进 name 字段也不能原样透出。"""
    cookies = [{"name": "pdd_user_id", "domain": ".yangkeduo.com", "value": "TOPSECRET"}]
    names = _pick_login_cookie_names(cookies, LOGIN_COOKIE_CANDIDATES)
    assert names == ["pdd_user_id"]
    assert all("TOPSECRET" not in n for n in names)


def test_pick_cookie_names_dedupes_and_caps():
    cookies = [
        {"name": f"ck_{i}", "domain": ".pinduoduo.com", "value": "v"}
        for i in range(MAX_COOKIE_NAMES_ECHOED + 20)
    ]
    cookies += [{"name": "ck_0", "domain": ".pinduoduo.com", "value": "v"}]  # 重复
    names = _pick_login_cookie_names(cookies, LOGIN_COOKIE_CANDIDATES)
    assert len(names) == MAX_COOKIE_NAMES_ECHOED
    assert len(set(names)) == len(names)


def test_pick_cookie_names_ignores_blank_names():
    cookies = [{"name": "", "domain": ".yangkeduo.com"}, {"name": "  ", "domain": ".yangkeduo.com"}]
    assert _pick_login_cookie_names(cookies, LOGIN_COOKIE_CANDIDATES) == []


# ---------------------------------------------------------------------------
# SubprocessBrowserController: 不连真 CDP 也能验的部分
# ---------------------------------------------------------------------------


def test_subprocess_ctl_rejects_non_loopback():
    """防误用: CDP 地址必须是回环, 不能指向任意主机。"""
    ctl = SubprocessBrowserController(cdp_url="http://evil.example.com:9223")
    ok, msg = ctl.probe()
    assert ok is False
    assert "回环" in msg


def test_subprocess_ctl_probe_down_port():
    ctl = SubprocessBrowserController(cdp_url="http://127.0.0.1:9")  # discard port
    ok, msg = ctl.probe()
    assert ok is False
    assert "连不上 CDP Chrome" in msg


def test_subprocess_ensure_missing_script_reports_without_popen(tmp_path):
    """启动脚本不在 → 直接报错, 不要傻等 30s 也不要 Popen。"""
    ctl = SubprocessBrowserController(
        cdp_url="http://127.0.0.1:9",
        start_script=tmp_path / "nope.ps1",
        launch_timeout_s=0.1,
        poll_interval_s=0.01,
    )
    res = ctl.ensure()
    assert res.ready is False
    assert res.launched is False
    assert "启动脚本不存在" in res.msg
    assert "start_pdd_cdp_chrome.ps1" in res.how_to_fix
    assert ctl._launch_proc is None


def test_subprocess_ctl_has_no_io_at_construction():
    """构造不能有副作用 —— create_app() 建默认实例时不该连网/起进程。"""
    ctl = SubprocessBrowserController()
    assert ctl.start_script == DEFAULT_START_SCRIPT
    assert ctl._launch_proc is None


# ---------------------------------------------------------------------------
# POST /api/browser/ensure 契约
# ---------------------------------------------------------------------------


def test_ensure_endpoint_ok_reports_not_relaunched():
    r = _client(StubBrowserController(probe_ok=True)).post("/api/browser/ensure")
    assert r.status_code == 200
    d = r.json()
    assert d["ready"] is True
    assert d["launched"] is False
    assert d["msg"]


def test_ensure_endpoint_reports_launched():
    r = _client(StubBrowserController(probe_ok=False, ensure_ok=True)).post("/api/browser/ensure")
    assert r.status_code == 200
    assert r.json()["launched"] is True


def test_ensure_endpoint_503_with_code_and_how_to_fix():
    """起不来 → 503 + 明确指引, 不能让前端只看到一个空失败。"""
    r = _client(StubBrowserController(probe_ok=False, ensure_ok=False)).post("/api/browser/ensure")
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "BROWSER_UNAVAILABLE"
    assert "start_pdd_cdp_chrome.ps1" in detail["how_to_fix"]


# ---------------------------------------------------------------------------
# GET /api/browser/login-status 契约
# ---------------------------------------------------------------------------


def test_login_status_logged_in_shape():
    c = _client(StubBrowserController(logged_in=True, cookie_names=("PASS_ID_TOKEN",)))
    r = c.get("/api/browser/login-status")
    assert r.status_code == 200
    d = r.json()
    assert d["logged_in"] is True
    assert d["cdp_ready"] is True
    assert d["method"] == "cookie"
    assert d["cookie_names"] == ["PASS_ID_TOKEN"]


def test_login_status_not_logged_in_is_still_200():
    """恒 200 —— 与 /api/scan/status 一致, 前端可高频轮询。"""
    r = _client(StubBrowserController(logged_in=False)).get("/api/browser/login-status")
    assert r.status_code == 200
    assert r.json()["logged_in"] is False


def test_login_status_cdp_down_is_200_and_says_so():
    r = _client(StubBrowserController(probe_ok=False)).get("/api/browser/login-status")
    assert r.status_code == 200
    d = r.json()
    assert d["cdp_ready"] is False
    assert d["logged_in"] is False


def test_login_status_shape_is_stable():
    """前端按固定字段渲染, 缺字段会炸 —— 钉住契约。"""
    d = _client(StubBrowserController()).get("/api/browser/login-status").json()
    for k in ("cdp_ready", "logged_in", "method", "cookie_names", "msg"):
        assert k in d, f"缺少字段 {k}"


# ---------------------------------------------------------------------------
# 与既有扫描链路的边界: 不能因为加了 ensure 就把原有兜底弄丢
# ---------------------------------------------------------------------------


def test_scan_503_path_unchanged_by_browser_ctl():
    """加了 browser 层之后, POST /api/scan 的 CDP_UNAVAILABLE 兜底仍须在。"""
    from app.ecommerce_scan import StubScanner

    c = TestClient(create_app(scanner=StubScanner(probe_ok=False), browser_ctl=StubBrowserController()))
    r = c.post("/api/scan", json={"keywords": ["OPPO"]})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "CDP_UNAVAILABLE"


def test_default_wiring_builds_without_io():
    """create_app() 默认装配 (真实现) 不应在构造期连网/起进程。"""
    c = TestClient(create_app())
    assert c.get("/api/health").status_code == 200
