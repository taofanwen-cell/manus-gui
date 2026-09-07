"""拼多多竞品调研 — 业务洞察生成层。

设计原则 (复用 ``app/ecommerce_analyzer`` 的数据契约)
--------------------------------------------------
* **InsightGenerator 协议**: 任何 ``generate(rows) -> list[Insight]`` 都可替换.
* **Stub vs DashScope 双实现**:
  - ``StubInsightGenerator`` = 纯模板, **不依赖 LLM/网络**, sandbox 可跑;
  - ``DashScopeInsightGenerator`` = 真 LLM 调用, 需要 ``DASHSCOPE_API_KEY`` 环境变量.
* **policy 红线**: LLM 输入**只接收 AnalysisReport 结构化字段** (中位数/区间/卖点/热度),
  不接 raw HTML/DOM/用户 cookie — 来自跨项目硬规则第 11 条 ("差最后一步仍是红线")
  和本项目 ``ecommerce_policy.BLOCKED_*`` 拦截策略.
* **3+2 洞察模板**: 3 条"数据可证明"洞察 (价位梯队/热度冠军/卖点空缺) +
  2 条"上下文"洞察 (数据限制/警告). 都是数字驱动, 不让 LLM 自由发挥.

Insight 数据类
-------------
``Insight(kind, body, evidence)``
* ``kind``  -- "tier"/"hot"/"gap"/"warning"
* ``body``  -- 一句话洞察 (用户可直接读)
* ``evidence`` -- 数据支撑 (字段-值列表, 用于回查)

调用方式
--------
>>> from app.ecommerce_brand_compare import _brand_row, analyze
>>> rows = [_brand_row(b, analyze(comps, pref), n) for ...]
>>> from app.ecommerce_insight import StubInsightGenerator
>>> gen = StubInsightGenerator()
>>> insights = gen.generate(rows)
>>> from app.ecommerce_insight import render_insights_markdown
>>> md = render_insights_markdown(insights)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable, Protocol

from app.ecommerce_analyzer import PriceDistribution, TopKeyword


@dataclass(frozen=True)
class Insight:
    """单条业务洞察.

    Attributes
    ----------
    kind:
        洞察类型. ``tier`` = 价位梯队, ``hot`` = 热度冠军,
        ``gap`` = 卖点空缺机会, ``warning`` = 数据限制警告.
    body:
        一句话洞察 (用户直接读).
    evidence:
        数据支撑 list[tuple[str, str]] — ``字段`` -> ``值`` 对照.
        用于回查/审核: 数字结论必须能追到 evidence.
    """

    kind: str
    body: str
    evidence: tuple[tuple[str, str], ...] = ()


class LLMInsightGenerator(Protocol):
    """洞察生成协议. 任何实现 ``generate`` 的对象都可替换."""

    def generate(self, rows: list[dict]) -> list[Insight]:
        ...


# ---------------------------------------------------------------------------
# Stub 实现 (模板驱动, 无 LLM, sandbox 跑得通)
# ---------------------------------------------------------------------------

# 价位梯队分桶: 中位数 < 100 元 → 低价走量; 100-300 → 中端; >300 → 高端
_TIER_LABELS = (
    (100, "低价走量 (¥100以内)"),
    (300, "中端 (¥100-300)"),
    (10_000, "高端 (¥300以上)"),
)


def _tier_of(median: int | None) -> str:
    if median is None:
        return "数据不足"
    for ceiling, label in _TIER_LABELS:
        if median < ceiling:
            return label
    return "高端 (¥300+)"


def _fmt_price(n: int | None) -> str:
    if n is None:
        return "—"
    return f"¥{n}"


def _fmt_top_sales(s: int | None) -> str:
    if s is None:
        return "—"
    if s >= 10_000:
        v = s / 10_000
        # 去掉多余小数 (5000.0万 → 5000万)
        s_v = f"{v:.1f}".rstrip("0").rstrip(".")
        return f"{s_v}万件"
    return f"{s}件"


class StubInsightGenerator:
    """模板驱动的洞察生成器.

    输出 3-5 条 Insight:
      1. 价位梯队 (基于中位数): 谁在低价走量, 谁在中端, 谁冲高端.
      2. 热度冠军 (基于 top_sales): 单品销量最高的品牌+机型.
      3. 卖点空缺机会: 跨品牌聚合卖点频次, 找出现次数 < N 的卖点.
      4. 中位数极差: 最高 vs 最低, 数字直接.
      5. 数据质量警告 (基于 rows 的 warnings 字段).
    """

    GAP_THRESHOLD = 0.3  # 卖点出现率低于 30% 视为"空缺机会"

    def generate(self, rows: list[dict]) -> list[Insight]:
        if not rows:
            return [Insight("warning", "没有可比品牌数据", ())]

        out: list[Insight] = []

        # ── 1. 价位梯队 ──
        out.extend(_insight_price_tiers(rows))

        # ── 2. 热度冠军 ──
        hot = _insight_hot_top(rows)
        if hot is not None:
            out.append(hot)

        # ── 3. 卖点空缺机会 ──
        gap = _insight_keyword_gap(rows)
        if gap is not None:
            out.append(gap)

        # ── 4. 中位数极差 ──
        out.append(_insight_price_spread(rows))

        # ── 5. 数据质量 ──
        warnings = _insight_warnings(rows)
        if warnings:
            out.extend(warnings)

        return out


def _insight_price_tiers(rows: list[dict]) -> list[Insight]:
    """按中位数把品牌分到低价/中端/高端桶, 各桶一段."""
    by_tier: dict[str, list[dict]] = {}
    for r in rows:
        tier = _tier_of(r.get("median"))
        by_tier.setdefault(tier, []).append(r)

    out: list[Insight] = []
    # 输出顺序固定: 低 → 中 → 高
    tier_order = [
        "低价走量 (¥100以内)",
        "中端 (¥100-300)",
        "高端 (¥300以上)",
        "高端 (¥300+)",
        "数据不足",
    ]
    seen_tiers: set[str] = set()
    for tier in tier_order:
        group = by_tier.get(tier)
        if not group or tier in seen_tiers:
            continue
        seen_tiers.add(tier)
        group_sorted = sorted(group, key=lambda r: r.get("median") or 0)
        brands = "、".join(
            f"{r['brand']}({_fmt_price(r.get('median'))})" for r in group_sorted
        )
        body = f"【{tier}】 {brands}"
        evidence = tuple(
            (f"{r['brand']} 中位数", _fmt_price(r.get("median"))) for r in group_sorted
        )
        out.append(Insight("tier", body, evidence))
    return out


def _insight_hot_top(rows: list[dict]) -> Insight | None:
    """单品销量最高的品牌+机型 (基于 top_sales, 通常来自己读详情页)."""
    best: tuple[str, int | None, str | None] | None = None
    for r in rows:
        s = r.get("top_sales")
        if s is None:
            continue
        if best is None or s > best[1]:
            best = (r["brand"], s, r.get("top_product"))
    if best is None:
        return None
    brand, sales, product = best
    body = (
        f"【热度冠军】 单品销量最高: {brand} 的 {product or '—'}, "
        f"卖出 {_fmt_top_sales(sales)}"
    )
    return Insight(
        "hot",
        body,
        (("品牌", brand), ("单品销量", _fmt_top_sales(sales)),
         ("热度机型", product or "—")),
    )


def _insight_keyword_gap(rows: list[dict]) -> Insight | None:
    """跨品牌聚合卖点频次, 找"空缺机会" (出现率低于阈值且非全无)."""
    if not rows:
        return None
    counter: dict[str, int] = {}
    n_brand = len(rows)
    for r in rows:
        for kw in r.get("top_keywords") or ():
            counter[kw["keyword"]] = counter.get(kw["keyword"], 0) + 1

    # 出现次数 >= ceil(N*0.5) 视为"主流", 严格小于该值且 > 0 视为空缺
    mainstream_threshold = max(2, (n_brand + 1) // 2)
    gap_items: list[tuple[str, int]] = sorted(
        ((k, v) for k, v in counter.items() if 0 < v < mainstream_threshold),
        key=lambda kv: kv[1],
    )
    if not gap_items:
        return None
    # 取最低 3 个空缺
    sample = gap_items[:3]
    body = (
        f"【卖点空缺机会】 仅 {sample[0][0]}({sample[0][1]}家)等少数品牌布局, "
        + "、".join(f"{k}({v}家)" for k, v in sample)
        + f" — 主流品牌({mainstream_threshold}+家布局)的空白点, "
        + "适合切入差异化卖点"
    )
    return Insight(
        "gap",
        body,
        (
            ("跨品牌总数", str(n_brand)),
            ("主流门槛", f"{mainstream_threshold}+家"),
        )
        + tuple((k, f"{v}/{n_brand}") for k, v in sample),
    )


def _insight_price_spread(rows: list[dict]) -> Insight:
    """中位数最高 vs 最低, 数字直接给."""
    priced = [r for r in rows if r.get("median") is not None]
    if not priced:
        return Insight("tier", "【价位极差】 数据不足", ())
    priced.sort(key=lambda r: r["median"])
    lo, hi = priced[0], priced[-1]
    spread = (hi["median"] - lo["median"]) if hi["median"] is not None and lo["median"] is not None else 0
    ratio = (hi["median"] / lo["median"]) if lo["median"] not in (None, 0) else None
    body = (
        f"【价位极差】 中位数最低: {lo['brand']} {_fmt_price(lo['median'])}; "
        f"最高: {hi['brand']} {_fmt_price(hi['median'])}"
        + (f" (差距 {ratio:.1f}倍)" if ratio and ratio > 1.5 else "")
    )
    return Insight(
        "tier",
        body,
        (("最低", f"{lo['brand']} {_fmt_price(lo['median'])}"),
         ("最高", f"{hi['brand']} {_fmt_price(hi['median'])}"),
         ("差距倍数", f"{ratio:.1f}倍" if ratio else "—")),
    )


def _insight_warnings(rows: list[dict]) -> list[Insight]:
    """数据质量警告 — 谁缺店铺/谁的 warning 里写了什么."""
    out: list[Insight] = []
    for r in rows:
        # 过滤 None/空串 (rows 序列化时可能出现)
        ws = [w for w in (r.get("warnings") or []) if w][:2]
        for w in ws:
            out.append(Insight(
                "warning",
                f"【数据限制】 {r['brand']}: {w}",
                (("品牌", r["brand"]), ("warning", w)),
            ))
    return out


# ---------------------------------------------------------------------------
# DashScope (可选) — 真 LLM 实现, 仅当 DASHSCOPE_API_KEY 在环境激活
# ---------------------------------------------------------------------------


class DashScopeInsightGenerator:
    """DashScope OpenAI 兼容接口调用.

    输入还是结构化 ``rows`` dict, 不是 raw HTML — 满足 policy 红线.
    **注意**: 网络请求失败/key 缺失应 fallback 到 ``StubInsightGenerator`` 而不是抛错.
    """

    MODEL = "qwen-plus"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.base_url = base_url or os.environ.get(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

    def generate(self, rows: list[dict]) -> list[Insight]:
        if not self.api_key:
            # 没 key → fallback stub, 不抛错
            return StubInsightGenerator().generate(rows)
        try:
            import urllib.request
            import json as _json

            prompt = _build_prompt(rows)
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=_json.dumps({
                    "model": self.MODEL,
                    "messages": [
                        {"role": "system", "content": "你是一名电商分析师。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                }).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
            text = payload["choices"][0]["message"]["content"]
            return [_parse_llm_insight(line) for line in text.strip().split("\n") if line.strip()]
        except Exception:
            # 网络/解析失败 → fallback stub
            return StubInsightGenerator().generate(rows)


def _build_prompt(rows: list[dict]) -> str:
    parts = ["以下是各品牌的竞品分析摘要, 请用 3-5 句中文给出关键业务洞察:\n"]
    for r in rows:
        kw = "、".join(f"{k['keyword']}({k['count']})" for k in (r.get("top_keywords") or [])[:3])
        parts.append(
            f"- {r['brand']}: 中位数 {_fmt_price(r.get('median'))}, "
            f"区间 {_fmt_price(r.get('min'))} ~ {_fmt_price(r.get('max'))}, "
            f"高频卖点 [{kw}], 热度机型 {r.get('top_product', '—')} "
            f"({_fmt_top_sales(r.get('top_sales'))})"
        )
    parts.append("\n请用简洁 bullet 输出, 不要解释, 不要客套。")
    return "\n".join(parts)


def _parse_llm_insight(line: str) -> Insight:
    """LLM 输出行解析 — 默认当成 hot 类型, 无法识别就归 tier."""
    stripped = line.lstrip("- •").strip()
    kind = "hot"
    if "空缺" in stripped or "机会" in stripped:
        kind = "gap"
    elif "警告" in stripped or "缺" in stripped:
        kind = "warning"
    elif "梯队" in stripped or "价位" in stripped or "中位数" in stripped:
        kind = "tier"
    return Insight(kind, stripped, (("source", "llm"),))


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

_KIND_HEADERS = {
    "tier": "### 💰 价位梯队",
    "hot": "### 🏆 热度冠军",
    "gap": "### 🎯 空缺机会",
    "warning": "### ⚠️ 数据限制",
}


def render_insights_markdown(insights: list[Insight]) -> str:
    """insights list -> Markdown 段 (按 kind 分组)."""
    if not insights:
        return ""

    by_kind: dict[str, list[Insight]] = {}
    for ins in insights:
        by_kind.setdefault(ins.kind, []).append(ins)

    parts: list[str] = ["## 🔍 业务洞察", ""]
    # 按 kind 输出顺序
    kind_order = ["tier", "hot", "gap", "warning"]
    for k in kind_order:
        if k not in by_kind:
            continue
        parts.append(_KIND_HEADERS.get(k, f"### {k}"))
        parts.append("")
        for ins in by_kind[k]:
            parts.append(f"- {ins.body}")
            if ins.evidence:
                evidences = " · ".join(f"{k}: {v}" for k, v in ins.evidence[:5])
                parts.append(f"  <sub>📎 {evidences}</sub>")
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 便捷工厂
# ---------------------------------------------------------------------------


def default_insight_generator() -> LLMInsightGenerator:
    """根据环境自动选择: 有 DASHSCOPE_API_KEY → DashScope, 否则 stub."""
    if os.environ.get("DASHSCOPE_API_KEY"):
        return DashScopeInsightGenerator()
    return StubInsightGenerator()


__all__ = [
    "Insight",
    "LLMInsightGenerator",
    "StubInsightGenerator",
    "DashScopeInsightGenerator",
    "default_insight_generator",
    "render_insights_markdown",
]
