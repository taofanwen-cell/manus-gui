"""拼多多竞品调研 — FastAPI 端点单测.

测试策略
--------
* 使用 ``fastapi.testclient.TestClient`` (内置 httpx)
* ``html_source`` 用 stub (内存 dict) — 不读 data/ 不发网络
* ``insight_gen`` 用纯 dict-based stub, 不调 DashScope
* 断言响应结构 + 状态码 + 业务逻辑正确性
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from fastapi.testclient import TestClient

from app.ecommerce_api import (
    CompetitorReportRequest,
    FileHTMLSource,
    HTMLSource,
    build_report,
    create_app,
)
from app.ecommerce_insight import Insight, StubInsightGenerator


# ---------------------------------------------------------------------------
# Fixtures: HTML 源 stub + 简单的 rawData HTML fixture
# ---------------------------------------------------------------------------


def _make_html(brand: str, n: int = 5) -> str:
    """构造合法的内含 window.rawData 的最小 HTML."""
    goods = ",".join(
        f'{{"goodsID":"{brand}{i}","goodsName":"{brand}测试耳机{i}",'
        f'"price":{99 + i * 10},"priceInfo":"{99 + i * 10}",'
        f'"salesTip":"本店已拼{(i+1) * 1000}","tagList":[]}}'
        for i in range(n)
    )
    return (
        "<html><body>"
        f'<script>window.rawData = {{'
        f'"stores":{{"store":{{"data":{{"ssrListData":{{"list":[{goods}]}}}}}}}}'
        f'}}</script>'
        "</body></html>"
    )


class _MemHTMLSource(HTMLSource):
    """内存版 HTML source — 测试用. ``fetch(keyword) -> html_text``."""

    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping

    def fetch(self, keyword: str) -> str:
        if keyword not in self.mapping:
            raise FileNotFoundError(f"no html for keyword {keyword!r}")
        return self.mapping[keyword]


class _CountingInsightGen:
    """记录调用次数 + 强制返回固定洞察."""

    def __init__(self, fixed: list[Insight] | None = None):
        self.calls = 0
        self.fixed = fixed or [
            Insight("tier", "TEST_INSIGHT_TOP"),
            Insight("hot", "TEST_INSIGHT_HOT"),
        ]

    def generate(self, rows: list[dict]) -> list[Insight]:
        self.calls += 1
        return list(self.fixed)


@pytest.fixture()
def client() -> TestClient:
    html = {
        "华为蓝牙耳机": _make_html("华为", 10),
        "小米蓝牙耳机": _make_html("小米", 10),
        "漫步者蓝牙耳机": _make_html("漫步者", 10),
    }
    src = _MemHTMLSource(html)
    gen = StubInsightGenerator()
    app = create_app(html_source=src, insight_gen=gen)
    return TestClient(app)


@pytest.fixture()
def counting_client() -> tuple[TestClient, _CountingInsightGen]:
    gen = _CountingInsightGen()
    html = {"华为蓝牙耳机": _make_html("华为", 10)}
    app = create_app(html_source=_MemHTMLSource(html), insight_gen=gen)
    return TestClient(app), gen


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_ok(self, client: TestClient):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body


# ---------------------------------------------------------------------------
# /api/brands
# ---------------------------------------------------------------------------


class TestBrands:
    def test_default_brands_listed(self, client: TestClient):
        r = client.get("/api/brands")
        assert r.status_code == 200
        body = r.json()
        assert "default_brands" in body
        assert set(body["default_brands"]) >= {"华为", "小米", "漫步者"}


# ---------------------------------------------------------------------------
# /api/competitor-report 正常路径
# ---------------------------------------------------------------------------


class TestCompetitorReportSuccess:
    def test_default_brands_returns_rows(self, client: TestClient):
        r = client.post("/api/competitor-report", json={})
        assert r.status_code == 200
        body = r.json()
        # 默认 6 brands, 但 fixture 只准备了 3 个, 其它会进 warnings
        assert len(body["rows"]) == 3
        assert set(body["brands"]) == {"华为", "小米", "漫步者"}
        assert any("倍思" in w for w in body["warnings"])

    def test_each_row_has_required_fields(self, client: TestClient):
        r = client.post(
            "/api/competitor-report",
            json={"brands": ["华为"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["rows"]) == 1
        row = body["rows"][0]
        assert row["brand"] == "华为"
        assert row["sample_size"] == 10
        assert isinstance(row["median"], int)
        assert row["median"] >= 99

    def test_insights_markdown_present_when_enabled(self, client: TestClient):
        r = client.post(
            "/api/competitor-report",
            json={"brands": ["华为"], "include_insights": True},
        )
        body = r.json()
        assert len(body["insights"]) >= 1
        assert "## 🔍" in body["insights_markdown"]

    def test_insights_skipped_when_disabled(self, client: TestClient):
        r = client.post(
            "/api/competitor-report",
            json={"brands": ["华为"], "include_insights": False},
        )
        body = r.json()
        assert body["insights"] == []
        assert body["insights_markdown"] == ""

    def test_call_count_includes_insight_gen(self, counting_client):
        c, gen = counting_client
        r = c.post(
            "/api/competitor-report",
            json={"brands": ["华为"], "include_insights": True},
        )
        assert r.status_code == 200
        # insight_gen 真的跑了一次
        assert gen.calls == 1

    def test_no_insights_call_when_disabled(self, counting_client):
        c, gen = counting_client
        r = c.post(
            "/api/competitor-report",
            json={"brands": ["华为"], "include_insights": False},
        )
        assert r.status_code == 200
        assert gen.calls == 0


# ---------------------------------------------------------------------------
# /api/competitor-report 错误路径
# ---------------------------------------------------------------------------


class TestCompetitorReportErrors:
    def test_empty_brands_rejected(self, client: TestClient):
        r = client.post("/api/competitor-report", json={"brands": []})
        assert r.status_code == 422  # Pydantic min_length=1

    def test_blank_brand_rejected(self, client: TestClient):
        r = client.post("/api/competitor-report", json={"brands": ["华为", "  "]})
        assert r.status_code == 422

    def test_too_many_brands_rejected(self, client: TestClient):
        r = client.post(
            "/api/competitor-report",
            json={"brands": [f"B{i}" for i in range(21)]},
        )
        assert r.status_code == 422

    def test_top_n_out_of_range_rejected(self, client: TestClient):
        r = client.post(
            "/api/competitor-report",
            json={"brands": ["华为"], "top_n": 0},
        )
        assert r.status_code == 422

    def test_missing_html_lands_in_warnings_not_500(self, client: TestClient):
        # 给一个没有 HTML 的品牌, 不应 500
        r = client.post(
            "/api/competitor-report",
            json={"brands": ["未知品牌"], "include_insights": False},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["rows"] == []
        assert any("未知品牌" in w for w in body["warnings"])


# ---------------------------------------------------------------------------
# build_report 直接调用 (无 HTTP)
# ---------------------------------------------------------------------------


class TestBuildReportDirect:
    def test_direct_call_bypasses_http(self):
        """直接调 build_report, 不走 TestClient, 测纯业务函数."""
        req = CompetitorReportRequest(brands=["华为"], include_insights=True)
        src = _MemHTMLSource({"华为蓝牙耳机": _make_html("华为", 5)})
        gen = StubInsightGenerator()
        resp = build_report(req, html_source=src, insight_gen=gen)
        assert len(resp.rows) == 1
        assert resp.rows[0].brand == "华为"
        assert len(resp.insights) >= 1

    def test_keyword_template_substitutes(self):
        req = CompetitorReportRequest(
            brands=["华为"],
            keyword_template="DIY {brand} 定制",
            include_insights=False,
        )
        captured: list[str] = []

        class _CapturingSource(HTMLSource):
            def fetch(self, keyword: str) -> str:
                captured.append(keyword)
                return _make_html("华为", 5)

        resp = build_report(
            req, html_source=_CapturingSource(), insight_gen=StubInsightGenerator()
        )
        assert captured == ["DIY 华为 定制"]
        assert len(resp.rows) == 1


# ---------------------------------------------------------------------------
# FileHTMLSource 真文件路径 (用 tmp_path)
# ---------------------------------------------------------------------------


class TestFileHTMLSource:
    def test_picks_latest_by_mtime(self, tmp_path: Path):
        # 写两个华为 HTML 文件, 第二个 mtime 更新, fetch("华为蓝牙耳机") 取最新那个
        d = tmp_path / "data"
        d.mkdir()
        (d / "pdd_raw_华为_20260101T000000.html").write_text(_make_html("华为v1", 3), encoding="utf-8")
        (d / "pdd_raw_华为_20260201T000000.html").write_text(_make_html("华为v2", 5), encoding="utf-8")
        # touch 第二个让它 mtime 更新
        (d / "pdd_raw_华为_20260201T000000.html").write_text(
            _make_html("华为v2", 5), encoding="utf-8"
        )
        src = FileHTMLSource(d)
        html = src.fetch("华为蓝牙耳机")
        assert "华为v2" in html  # mtime 最新的那个
        assert "goodsID" in html

    def test_detect_brand_from_keyword_suffix(self, tmp_path: Path):
        """关键回归: keyword='华为蓝牙耳机' 必须推出 brand='华为', 不再返回所有 glob 第一份."""
        d = tmp_path / "data"
        d.mkdir()
        # 写两个品牌的 HTML
        (d / "pdd_raw_华为_20260101.html").write_text(
            _make_html("华为", 3), encoding="utf-8"
        )
        (d / "pdd_raw_小米_20260201.html").write_text(
            _make_html("小米", 4), encoding="utf-8"
        )
        # touch 小米 让它更新
        (d / "pdd_raw_小米_20260201.html").write_text(
            _make_html("小米", 4), encoding="utf-8"
        )
        src = FileHTMLSource(d)
        # 小米 keyword 应该拿到小米的 HTML, 不是华为的
        html_xiaomi = src.fetch("小米蓝牙耳机")
        assert "小米" in html_xiaomi
        assert "小米测试耳机" in html_xiaomi
        # 华为 keyword 应该拿到华为的 HTML
        html_huawei = src.fetch("华为蓝牙耳机")
        assert "华为" in html_huawei
        assert "华为测试耳机" in html_huawei
        # 两个 HTML 不一样 — 这是核心回归点
        assert html_xiaomi != html_huawei

    def test_unknown_brand_raises_filenotfound(self, tmp_path: Path):
        d = tmp_path / "data"
        d.mkdir()
        src = FileHTMLSource(d)
        with pytest.raises(FileNotFoundError):
            src.fetch("完全不存在的品牌蓝牙耳机")
