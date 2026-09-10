"""``GET /api/cache-status`` + 空结果响应契约 (Day 9 · Phase 1).

为什么要有这个接口
------------------
查一个没扫过的关键词 (比如 "OPPO") 时, 报告端点只是把它塞进 ``warnings`` 然后
``rows: []`` —— 用户看到的是一片空白, 分不清"没数据"还是"系统坏了"。
cache-status 让前端能明确回答"缓存里有什么 / 缺什么 / 怎么补"。

测试约定
--------
- 一律用 ``tmp_path`` 造假 HTML, **不 commit 真实扫描产物** (data/ 在 .gitignore)。
- 不碰真实 CDP / 网络, sandbox 可跑。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.ecommerce_api import FileHTMLSource, build_cache_status, create_app


def _touch(d: Path, name: str, body: str = "<html></html>") -> Path:
    p = d / name
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# build_cache_status: 纯函数层
# ---------------------------------------------------------------------------


def test_empty_dir_returns_zero(tmp_path: Path):
    st = build_cache_status(tmp_path)
    assert st.total == 0
    assert st.keywords == []
    assert st.entries == []


def test_missing_dir_does_not_raise(tmp_path: Path):
    """目录不存在不能 500 —— 前端要能正常拿到"一个都没有"。"""
    st = build_cache_status(tmp_path / "does-not-exist")
    assert st.total == 0


def test_single_file_parsed(tmp_path: Path):
    _touch(tmp_path, "pdd_raw_OPPO_20260910T120000.html", "x" * 1234)
    st = build_cache_status(tmp_path)
    assert st.total == 1
    e = st.entries[0]
    assert e.keyword == "OPPO"
    assert e.file == "pdd_raw_OPPO_20260910T120000.html"
    assert e.size_bytes == 1234
    assert e.mtime and e.mtime.startswith("2026") is not None
    assert e.age_hours is not None and e.age_hours >= 0


def test_same_keyword_keeps_newest(tmp_path: Path):
    """同一关键词扫过多次 → 只留最新那份 (前端按 keyword 去重展示)。"""
    old = _touch(tmp_path, "pdd_raw_华为_20260907T172550.html", "old")
    new = _touch(tmp_path, "pdd_raw_华为_20260910T120000.html", "new!")
    st = build_cache_status(tmp_path)
    assert st.total == 1
    assert st.entries[0].file == new.name
    assert st.entries[0].size_bytes == len("new!")
    assert old.name != st.entries[0].file


def test_multiple_keywords_sorted(tmp_path: Path):
    for kw in ["小米", "OPPO", "华为"]:
        _touch(tmp_path, f"pdd_raw_{kw}_20260910T120000.html")
    st = build_cache_status(tmp_path)
    assert st.total == 3
    assert st.keywords == sorted(st.keywords)  # 输出稳定, 便于 diff/测试


def test_legacy_filename_without_keyword_still_listed(tmp_path: Path):
    """早期产物 ``pdd_raw_<ts>.html`` 没有 keyword 段 —— 也要列出来,
    否则会出现"data/ 里明明有文件, 接口却说没有"的困惑。"""
    _touch(tmp_path, "pdd_raw_20260907T164509.html")
    st = build_cache_status(tmp_path)
    assert st.total == 1
    assert st.entries[0].keyword == "20260907T164509"


def test_non_matching_files_ignored(tmp_path: Path):
    """其它产物 (pdd_detail_*.json / pdd_scan_*.json) 不该混进缓存清单。"""
    _touch(tmp_path, "pdd_raw_华为_20260910T120000.html")
    _touch(tmp_path, "pdd_detail_20260907T174522.json")
    _touch(tmp_path, "pdd_scan_20260907T164509.json")
    st = build_cache_status(tmp_path)
    assert st.keywords == ["华为"]


# ---------------------------------------------------------------------------
# 端点层 (注入 tmp_path, 与真实 data/ 解耦)
# ---------------------------------------------------------------------------


def test_endpoint_returns_200_with_injected_dir(tmp_path: Path):
    _touch(tmp_path, "pdd_raw_OPPO_20260910T120000.html")
    app = create_app(cache_dir=tmp_path)
    d = TestClient(app).get("/api/cache-status").json()
    assert d["total"] == 1
    assert d["keywords"] == ["OPPO"]
    assert str(tmp_path) == d["data_dir"]


# ---------------------------------------------------------------------------
# 空结果响应契约: 查没扫过的关键词时, API 该怎么表现
# ---------------------------------------------------------------------------


def test_report_missing_keyword_returns_200_with_warnings(tmp_path: Path):
    """向后兼容契约: 全都没数据也不能 500 —— 返回 200 + rows:[] + warnings。

    前端据此渲染空态卡片 (而不是抛错或空白)。

    注意: 必须显式注入 ``html_source``。只传 ``cache_dir`` 时 create_app 会用默认的
    ``FileHTMLSource(Path("data"))`` 去扫**真实项目 data/ 目录** —— 本地一旦真扫过
    OPPO (CDP 产物), 这条 "OPPO 应该没数据" 的断言就会挂。这是测试自身对"data/ 为空"
    的隐含依赖, 不是产品 bug (2026-09-10 真实踩到)。
    """
    # 只放一个真实存在的品牌, 但请求一个没扫过的
    empty_src = FileHTMLSource(tmp_path / "empty-data")
    app = create_app(html_source=empty_src, cache_dir=tmp_path)
    c = TestClient(app)
    resp = c.post("/api/competitor-report", json={"brands": ["OPPO"], "top_n": 5})
    assert resp.status_code == 200
    d = resp.json()
    assert d["rows"] == []
    assert d["brands"] == []
    assert d["warnings"], "没数据必须给出 warning, 不能静默空 rows"


def test_report_partial_miss_keeps_good_rows(tmp_path: Path):
    """部分失败: 有的品牌有数据、有的没有 → 好的照出, 缺的进 warnings。"""
    # 用真实 data/ 造一个只有"华为"的场景: 复制现有华为 HTML 到 tmp_path
    real = Path("data/pdd_raw_华为_20260907T172550.html")
    if not real.exists():
        # 没有真实产物就跳过 (CI 上 data/ 是空的)
        import pytest

        pytest.skip("需要 data/ 下的真实扫描产物")
    _touch(tmp_path, real.name, real.read_text(encoding="utf-8"))

    # 用真实 FileHTMLSource: 它会把 "华为蓝牙耳机" 反推成品牌 "华为" 再 glob
    app = create_app(html_source=FileHTMLSource(tmp_path), cache_dir=tmp_path)
    d = TestClient(app).post(
        "/api/competitor-report", json={"brands": ["华为", "OPPO"], "top_n": 5}
    ).json()
    assert [r["brand"] for r in d["rows"]] == ["华为"]
    assert d["brands"] == ["华为"]
    assert any("OPPO" in w for w in d["warnings"])
