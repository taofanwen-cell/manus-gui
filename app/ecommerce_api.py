"""拼多多竞品调研 — FastAPI 服务层.

设计原则
--------
* **可注入**: 业务依赖 (``insight_generator`` / ``html_source``) 都通过
  ``create_app(...)`` factory 注入, sandbox / 测试可以塞 stub 实现.
* **默认离线**: sandbox 跑不通真实浏览器, 默认端点接收 caller 提供的 HTML
  (or 文件 path), 不主动发起网络请求. 真生产模式可以通过 ``live_scan=True``
  启用 (需要在 caller 配好 CDP Chrome).
* **policy 红线**: 端点只接收结构化 JSON + 已渲染的 HTML 文本, 不接收 cookies /
  账号 / raw network traffic — 来自跨项目硬规则第 11 条.
* **错误契约**: 4xx 走 ``HTTPException(detail=...)`` + 业务 ``code`` 字段,
  不裸 500.

调用示例
--------
>>> from app.ecommerce_api import create_app
>>> app = create_app()
>>> # 生产: uvicorn app.ecommerce_api:app --host 0.0.0.0 --port 8000
>>> # 测试: TestClient(app).post('/api/competitor-report', json={...})
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ecommerce_analyzer import AnalysisPreference, analyze
from app.ecommerce_detail_store import load_latest_detail
from app.ecommerce_insight import (
    Insight,
    LLMInsightGenerator,
    StubInsightGenerator,
    render_insights_markdown,
)
from app.ecommerce_pdd_parser import extract_competitors


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_BRANDS = ("华为", "小米", "倍思", "QCY", "万魔", "漫步者")


# ---------------------------------------------------------------------------
# 协议: HTML 源 (默认离线 - 接受 path; 真实 CDP 单测外)
# ---------------------------------------------------------------------------


class HTMLSource(Protocol):
    """``fetch(keyword) -> html_text`` 的协议. 默认实现见下."""

    def fetch(self, keyword: str) -> str:
        ...


class FileHTMLSource:
    """从 ``data/pdd_raw_<brand>_<timestamp>.html`` 读 HTML. 默认 sandbox 模式.

    用于"预先离线扫好 HTML → API 分析"的场景.

    关键约定
    --------
    ``fetch(keyword)`` 收到形如 ``"华为蓝牙耳机"`` / ``"漫步者 蓝牙耳机"`` 的 keyword,
    反推出**品牌名**(剥后缀), 再在 ``data/`` 下找 ``pdd_raw_<brand>_*.html`` 最近一个.
    找不到返回 ``FileNotFoundError`` — 端点会把这条放到 warnings 里, 不应让整次分析 500.
    """

    DEFAULT_SUFFIX = ("蓝牙耳机", "耳机", "earphone")
    DEFAULT_BRAND_KEYWORDS = DEFAULT_BRANDS

    def __init__(
        self,
        data_dir: Path | None = None,
        brand_keywords: Iterable[str] | None = None,
    ):
        self.data_dir = data_dir or Path("data")
        self.brand_keywords = tuple(brand_keywords) if brand_keywords else self.DEFAULT_BRAND_KEYWORDS

    def fetch(self, keyword: str) -> str:
        brand = self._detect_brand(keyword)
        if brand is None:
            raise FileNotFoundError(f"无法从 keyword {keyword!r} 推出品牌名")
        candidates = sorted(
            self.data_dir.glob(f"pdd_raw_{brand}_*.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(
                f"data/ 下找不到品牌 {brand!r} 的 HTML 扫描文件 (用了 keyword={keyword!r})"
            )
        return candidates[0].read_text(encoding="utf-8", errors="replace")

    def _detect_brand(self, keyword: str) -> str | None:
        """已知品牌优先匹配 (避免品牌名是另一个品牌子串时的误判)."""
        for brand in self.brand_keywords:
            if brand in keyword:
                return brand
        # fallback: 剥已知后缀 (默认 "蓝牙耳机")
        for suf in self.DEFAULT_SUFFIX:
            if keyword.endswith(suf):
                return keyword[: -len(suf)].strip()
        return None


# ---------------------------------------------------------------------------
# 请求/响应 Schema
# ---------------------------------------------------------------------------

DEFAULT_BRANDS = ("华为", "小米", "倍思", "QCY", "万魔", "漫步者")


class CompetitorReportRequest(BaseModel):
    """POST /api/competitor-report 请求体."""

    # Swagger "Try it out" 默认预填真实品牌, 避免用户误用占位符 "string"
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "brands": list(DEFAULT_BRANDS),
                    "top_n": 20,
                    "include_insights": True,
                    "keyword_template": "{brand}蓝牙耳机",
                }
            ]
        }
    )

    brands: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BRANDS),
        min_length=1,
        max_length=20,
        description="要对比的品牌列表 (默认 6 主流蓝牙耳机品牌)",
    )
    top_n: int = Field(default=20, ge=1, le=100)
    include_insights: bool = Field(default=True)
    # keyword 模板 — 默认 "{brand}蓝牙耳机"
    keyword_template: str = Field(default="{brand}蓝牙耳机")

    @field_validator("brands")
    @classmethod
    def _no_empty(cls, v: list[str]) -> list[str]:
        if any(not (b and b.strip()) for b in v):
            raise ValueError("品牌名不能为空")
        return [b.strip() for b in v]


class CompetitorBrandRow(BaseModel):
    brand: str
    median: int | None
    min: int | None
    max: int | None
    p25: int | None
    p75: int | None
    bucket_under_100: int
    bucket_100_300: int
    bucket_300_800: int
    bucket_over_800: int
    sample_size: int
    filtered_size: int
    top_keywords: list[dict]
    top_product: str | None = None
    #: 销量数字 —— 语义由 :attr:`top_sales_source` 决定, **不要脱离来源单独解读**
    top_sales: int | None = None
    #: ``"single"`` 详情页单品销量 (可跨品牌比) / ``"shop_total"`` 列表页店铺或品牌
    #: 累计销量 (不可当单品比) / ``"unknown"`` 都没有
    top_sales_source: str = "unknown"
    #: 参考: 店铺/品牌累计销量 (列表页 salesTip), 与 top_sales 是不同指标
    shop_sales: int | None = None
    #: 详情页补充 (列表页没有)
    shop_name: str | None = None
    comment_count: int | None = None


class CompetitorReportResponse(BaseModel):
    brands: list[str]
    rows: list[CompetitorBrandRow]
    insights: list[dict] = Field(default_factory=list)  # [Insight.to_dict(), ...]
    insights_markdown: str = ""
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 业务函数
# ---------------------------------------------------------------------------


def _to_brand_row_dict(row: dict) -> CompetitorBrandRow:
    """``scripts/ecommerce_brand_compare._brand_row`` 输出 (``parsed/filtered/
    b_u100/...`` 等短 key) → pydantic model (``sample_size/bucket_under_100/...``).

    这里集中做字段映射, 端点/insight 调用方只用友好名.
    """
    # top_keywords: 直接复用 _brand_row 已聚合的 list (含真实 count/pct)
    # 不再从 kw1/kw2/kw3 拼空 list —— 那样词云/卖点横向柱会全是 0.
    top_keywords = list(row.get("top_keywords", []))
    return CompetitorBrandRow(
        brand=row["brand"],
        median=row.get("median"),
        min=row.get("min"),
        max=row.get("max"),
        p25=row.get("p25"),
        p75=row.get("p75"),
        bucket_under_100=row.get("b_u100", 0) or 0,
        bucket_100_300=row.get("b_100_300", 0) or 0,
        bucket_300_800=row.get("b_300_800", 0) or 0,
        bucket_over_800=row.get("b_o800", 0) or 0,
        sample_size=row.get("parsed", 0) or 0,
        filtered_size=row.get("filtered", 0) or 0,
        top_keywords=top_keywords,
        top_product=row.get("top_model"),
        top_sales=row.get("top_sales"),
        top_sales_source=row.get("top_sales_source", "unknown"),
        shop_sales=row.get("shop_sales"),
        shop_name=row.get("shop_name"),
        comment_count=row.get("comment_count"),
    )


def _insight_to_dict(ins: Insight) -> dict:
    return {"kind": ins.kind, "body": ins.body, "evidence": list(ins.evidence)}


def build_report(
    req: CompetitorReportRequest,
    *,
    html_source: HTMLSource,
    insight_gen: LLMInsightGenerator | None = None,
    pref: AnalysisPreference | None = None,
    detail_dir: Path | None = None,
) -> CompetitorReportResponse:
    """端点核心业务函数 — 便于测试直接调, 不必经过 HTTP.

    流程
    ----
    1. 拿每个品牌的 HTML (走 html_source, 默认离线读 data/)
    2. 解析 → analyze() → 一行 dict (详情字段从 detail_dir 注入, 见下)
    3. (可选) 跑 insight_gen.generate(rows) → list[Insight]
    4. 返回结构化响应

    ``detail_dir``
        详情页字段目录 (``data/pdd_detail_*.json`` 所在处)。给了就优先用**单品销量**,
        不给或没采集过则降级到列表页店铺/品牌累计, 并在 ``top_sales_source`` 标出。
        默认 ``None`` = 不加载 (纯列表页口径), 测试可用 tmp_path 注入。

    关键设计: **端点业务逻辑可在 sandbox 跑**, 因为默认 html_source 是离线读,
    不发起网络请求.
    """
    pref = pref or AnalysisPreference(top_n=req.top_n, sort_by="sales")
    insight_gen = insight_gen or StubInsightGenerator()

    rows: list[dict] = []
    row_models: list[CompetitorBrandRow] = []
    global_warnings: list[str] = []

    # 详情页增强数据: 单品销量 / 店铺名 / 评论数。缺了不报错 —— 走降级并且在
    # top_sales_source 里标 "shop_total", 让前端/insight 知道这数字不能当单品比。
    details: dict = load_latest_detail(detail_dir) if detail_dir is not None else {}
    if detail_dir is not None and not details:
        global_warnings.append(
            "未找到 data/pdd_detail_*.json (详情页单品销量), 销量降级为列表页店铺/品牌累计, 不可当单品比"
        )

    # 复用 _brand_row (从 ecommerce_brand_compare 引用, 避免重复实现)
    from scripts.ecommerce_brand_compare import _brand_row  # type: ignore

    for brand in req.brands:
        keyword = req.keyword_template.format(brand=brand)
        try:
            html = html_source.fetch(keyword)
        except FileNotFoundError as e:
            global_warnings.append(f"{brand}: {e}")
            continue
        except Exception as e:  # noqa: BLE001
            global_warnings.append(f"{brand}: HTML 获取失败 — {type(e).__name__}")
            continue

        competitors = extract_competitors(html)
        if not competitors:
            global_warnings.append(f"{brand}: HTML 解析出 0 个商品 (可能是 rawData 结构变更)")
            continue

        report = analyze(competitors, pref)
        row = _brand_row(brand, report, len(competitors), detail=details.get(brand))
        rows.append(row)
        row_models.append(_to_brand_row_dict(row))

    insights: list[Insight] = []
    if req.include_insights and rows:
        try:
            insights = insight_gen.generate(rows)
        except Exception as e:  # noqa: BLE001
            global_warnings.append(f"insight 生成失败: {type(e).__name__}")

    return CompetitorReportResponse(
        brands=[b for b in req.brands if any(r["brand"] == b for r in rows)],
        rows=row_models,
        insights=[_insight_to_dict(i) for i in insights],
        insights_markdown=render_insights_markdown(insights) if insights else "",
        warnings=global_warnings,
    )


# ---------------------------------------------------------------------------
# App Factory
# ---------------------------------------------------------------------------


def create_app(
    *,
    html_source: HTMLSource | None = None,
    insight_gen: LLMInsightGenerator | None = None,
    detail_dir: Path | None = None,
) -> FastAPI:
    """FastAPI 工厂. ``html_source`` / ``insight_gen`` 都可注入, 便于测试.

    ``detail_dir``: 详情页字段目录, 给了就让销量优先用**单品销量**。
    默认从 ``FileHTMLSource.data_dir`` 推断 (即 ``data/``)。
    """
    if html_source is None:
        # data/ 相对项目根; FastAPI 启动时 cwd 就是项目根
        html_source = FileHTMLSource(Path("data"))
    if insight_gen is None:
        insight_gen = StubInsightGenerator()
    if detail_dir is None and isinstance(html_source, FileHTMLSource):
        detail_dir = html_source.data_dir

    app = FastAPI(
        title="拼多多竞品调研 API",
        version="0.1.0",
        description=(
            "6 品牌蓝牙耳机横向对比 (价格带/卖点/单品销量) + 业务洞察。"
            "默认离线读 data/ 下的 HTML 扫描结果, 不发起网络请求。"
        ),
    )
    # CORS: 让 static/index.html 用 file:// 打开也能调 API
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 根路径返回单文件 Web 前端 (static/index.html)
    _STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
    # 静态资源 (bootstrap / marked / 自定义 css) 走 /static/...
    from fastapi.staticfiles import StaticFiles  # 局部 import, 避免无静态目录时 import 报错
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    # 用闭包捕获注入的依赖
    _html = html_source
    _gen = insight_gen
    _detail_dir = detail_dir

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": app.version}

    @app.get("/api/brands")
    def brands() -> dict:
        return {"default_brands": list(DEFAULT_BRANDS)}

    @app.post("/api/competitor-report", response_model=CompetitorReportResponse)
    def competitor_report(req: CompetitorReportRequest) -> CompetitorReportResponse:
        try:
            return build_report(
                req, html_source=_html, insight_gen=_gen, detail_dir=_detail_dir
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=500,
                detail={"code": "INTERNAL", "msg": f"{type(e).__name__}: {e}"},
            )

    return app


# 默认 app 实例, 给 ``uvicorn app.ecommerce_api:app`` 直接用
app = create_app()


__all__ = [
    "HTMLSource",
    "FileHTMLSource",
    "CompetitorReportRequest",
    "CompetitorReportResponse",
    "build_report",
    "create_app",
    "app",
]
