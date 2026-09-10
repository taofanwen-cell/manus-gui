"""``POST /api/scan`` + ``GET /api/scan/status`` (Day 9 Phase 2).

测试原则
--------
- **零 CDP / 零网络**: 一律注入 :class:`StubScanner`, CI 上不需要 Chrome。
- 只测**契约与状态机**: 503 (CDP 不通) / 409 (单飞锁) / 422 (脏 keyword) /
  done+failed 清单。真实扫描的正确性由脚本自己的用例和手工验收保证。
- 不碰真实 ``data/``。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ecommerce_api import create_app
from app.ecommerce_scan import (
    STATUS_DONE,
    STATUS_IDLE,
    STATUS_RUNNING,
    ScanState,
    StubScanner,
    SubprocessScanner,
    _parse_log,
)


def _client(scanner) -> TestClient:
    return TestClient(create_app(scanner=scanner))


# ---------------------------------------------------------------------------
# 日志反解 (脚本输出 → succeeded/failed/current)
# ---------------------------------------------------------------------------


def test_parse_log_extracts_succeeded_and_current():
    lines = [
        "[scan] OPPO -> https://mobile.yangkeduo.com/search_result.html?search_key=OPPO",
        "[scan] OPPO raw html -> pdd_raw_OPPO_20260910T120000.html (280 chars)",
        "[scan] OPPO: 样本 20 | 中位数 ¥128 | 区间 ¥39~¥599 | 销量=53000 (single)",
    ]
    ok, failed, current = _parse_log(lines, ["OPPO"])
    assert ok == ["OPPO"]
    assert failed == []
    assert current == "OPPO"


def test_parse_log_zero_goods_is_failure_with_login_hint():
    """0 商品 ≠ 真没数据 —— 大概率登录态失效, reason 必须把这点说出来。"""
    lines = [
        "[scan] OPPO -> https://...",
        "[scan] OPPO 解析出 0 个商品, 跳过",
    ]
    ok, failed, _ = _parse_log(lines, ["OPPO"])
    assert ok == []
    assert len(failed) == 1
    assert failed[0]["keyword"] == "OPPO"
    assert "登录" in failed[0]["reason"]


def test_parse_log_mixed_results():
    lines = [
        "[scan] 华为 -> https://...",
        "[scan] 华为: 样本 20 | 中位数 ¥257",
        "[scan] OPPO -> https://...",
        "[scan] OPPO 解析出 0 个商品, 跳过",
    ]
    ok, failed, current = _parse_log(lines, ["华为", "OPPO"])
    assert ok == ["华为"]
    assert [f["keyword"] for f in failed] == ["OPPO"]
    assert current == "OPPO"


# ---------------------------------------------------------------------------
# POST /api/scan 契约
# ---------------------------------------------------------------------------


def test_scan_ok_returns_job_id():
    c = _client(StubScanner())
    r = c.post("/api/scan", json={"keywords": ["OPPO"]})
    assert r.status_code == 200
    d = r.json()
    assert d["job_id"].startswith("stub-")
    assert d["keywords"] == ["OPPO"]
    assert d["status"] == STATUS_DONE


def test_scan_cdp_down_returns_503_with_how_to_fix():
    """CDP 没开 → 503 + 明确指引, 不能让用户干等或看 500。"""
    c = _client(StubScanner(probe_ok=False))
    r = c.post("/api/scan", json={"keywords": ["OPPO"]})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "CDP_UNAVAILABLE"
    assert "start_pdd_cdp_chrome.ps1" in detail["how_to_fix"]


def test_scan_busy_returns_409_with_current_job():
    """单飞锁: 并发扫描既触发风控又可能并发写坏 data/。"""
    c = _client(StubScanner(busy=True))
    r = c.post("/api/scan", json={"keywords": ["OPPO"]})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "SCAN_BUSY"
    assert "status" in detail


def test_scan_rejects_empty_keyword():
    c = _client(StubScanner())
    assert c.post("/api/scan", json={"keywords": ["  "]}) .status_code == 422


def test_scan_rejects_overlong_keyword():
    c = _client(StubScanner())
    assert c.post("/api/scan", json={"keywords": ["x" * 33]}).status_code == 422


def test_scan_rejects_path_separator():
    """keyword 会进 argv 和文件名 —— 路径分隔符必须挡在门口。"""
    c = _client(StubScanner())
    assert c.post("/api/scan", json={"keywords": ["../../etc/passwd"]}).status_code == 422


def test_scan_rejects_empty_list():
    c = _client(StubScanner())
    assert c.post("/api/scan", json={"keywords": []}).status_code == 422


def test_scan_passes_suffix_through():
    """suffix 允许显式传空串: keyword 已含品类时避免 '蓝牙音箱蓝牙耳机' 拼接。"""
    stub = StubScanner()
    c = _client(stub)
    assert c.post("/api/scan", json={"keywords": ["蓝牙音箱"], "suffix": ""}).status_code == 200
    assert stub.started[-1] == (["蓝牙音箱"], "")


# ---------------------------------------------------------------------------
# GET /api/scan/status
# ---------------------------------------------------------------------------


def test_status_idle_before_any_scan():
    d = _client(StubScanner()).get("/api/scan/status").json()
    assert d["status"] == STATUS_IDLE
    assert d["job_id"] is None


def test_status_reports_failed_with_reason():
    """失败品牌要能在状态里查到原因 (前端据此提示登录态失效)。"""
    c = _client(StubScanner(fail_keywords=("OPPO",)))
    c.post("/api/scan", json={"keywords": ["华为", "OPPO"]})
    d = c.get("/api/scan/status").json()
    assert d["succeeded"] == ["华为"]
    assert [f["keyword"] for f in d["failed"]] == ["OPPO"]
    assert "登录" in d["failed"][0]["reason"]
    assert d["returncode"] == 0  # 部分失败仍算脚本正常退出


def test_status_shape_is_stable():
    """前端按固定字段渲染, 缺字段会炸 —— 钉住契约。"""
    c = _client(StubScanner())
    c.post("/api/scan", json={"keywords": ["华为"]})
    d = c.get("/api/scan/status").json()
    for k in ("status", "job_id", "keywords", "current", "started_at",
              "finished_at", "log_tail", "succeeded", "failed", "returncode"):
        assert k in d, f"缺少字段 {k}"


# ---------------------------------------------------------------------------
# SubprocessScanner: 不连真 CDP 也能验的部分
# ---------------------------------------------------------------------------


def test_subprocess_scanner_rejects_non_loopback_cdp():
    """防误用: CDP 地址必须是回环, 不能指向任意主机。"""
    s = SubprocessScanner(cdp_url="http://evil.example.com:9223")
    ok, msg = s.probe()
    assert ok is False
    assert "回环" in msg


def test_subprocess_scanner_probe_down_port():
    """端口没人听 → 明确报错而不是抛异常。"""
    s = SubprocessScanner(cdp_url="http://127.0.0.1:9")  # discard port, 必连不上
    ok, msg = s.probe()
    assert ok is False
    assert "连不上 CDP Chrome" in msg


def test_subprocess_scanner_rejects_bad_keyword_before_popen():
    """脏 keyword 必须在 Popen 之前挡掉 (不能靠子进程报错才发现)。"""
    s = SubprocessScanner()
    with pytest.raises(ValueError):
        s.start(["../../evil"], None)
    with pytest.raises(ValueError):
        s.start(["x" * 33], None)
    with pytest.raises(ValueError):
        s.start([], None)


def test_subprocess_scanner_is_busy_false_initially():
    assert SubprocessScanner().is_busy() is False


def test_running_state_has_no_finished_at():
    """running 状态不该有 finished_at —— 前端据此判断"还在跑"。"""
    st = ScanState(status=STATUS_RUNNING, job_id="x")
    assert st.finished_at is None
    assert st.to_dict()["status"] == STATUS_RUNNING
