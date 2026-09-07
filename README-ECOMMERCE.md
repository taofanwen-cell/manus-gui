# 拼多多竞品调研 API

[![CI](https://github.com/ckenkuo/manus-gui/actions/workflows/ci.yml/badge.svg)](https://github.com/ckenkuo/manus-gui/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 基于 CDP 反爬 + FastAPI 服务化的多品牌拼多多蓝牙耳机竞品横向对比工具，含价格分档、卖点聚合、单品销量、详情页字段增强、可选 LLM 业务洞察、深色大屏 ECharts 前端。163 个单测零回归。

---

## ✨ 核心能力

| 能力 | 实现 |
|---|---|
| **真实数据** | 本机已登录 Chrome + Playwright CDP → 抓拼多多搜索列表真实数据 |
| **数据解析** | 列表页嵌 JSON (`window.rawData`) + 详情页 DOM/正文正则 |
| **多维度分析** | 6 品牌 × 20 商品 × 价格中位/分档/区间/卖点 TOP/热度机型 |
| **服务化** | FastAPI + Pydantic schema 收口 + CORS + 静态资源 mount |
| **可选 LLM** | DashScope (OpenAI 兼容) + 网络失败自动降级 StubInsightGenerator |
| **Web 前端** | 单文件深色大屏 ECharts (4 视图), 离线可用, 零构建链 |
| **测试守护** | 163 个 pytest 用例, sandbox/CI 离线可跑 (不需真实 CDP / LLM key) |

---

## 🚀 快速启动 (5 分钟看到效果)

### 0. 前置

- Python 3.11+ (项目在 `.venv/` 已装好)
- 一个能登录拼多多 PC 版的 Chrome

### 2. 启服务 (离线 HTML 已就绪则直接 0 步)

```bash
cd upstream/manus-gui
./.venv/Scripts/python.exe -m uvicorn app.ecommerce_api:app --host 127.0.0.1 --port 8001
# → 浏览器打开 http://127.0.0.1:8001
# → Swagger: http://127.0.0.1:8001/docs
```

如果你 `data/` 下有现成 `pdd_raw_<品牌>_*.html`，**无需任何网络** 直接出报告。

### 1. 离线准备 (可选, 5 分钟扫一次)

```powershell
# 终端 A: 启专用 Chrome (端口 9223, 不碰日常 Chrome)
.\scripts\start_pdd_cdp_chrome.ps1
# → 在弹出的 Chrome 里手动登录拼多多
```

```bash
# 终端 B: 扫 6 品牌的搜索列表页 (≈ 3 分钟)
$env:PDD_CDP_URL = 'http://127.0.0.1:9223'
./.venv/Scripts/python.exe scripts/ecommerce_brand_compare.py
# → 落到 data/pdd_raw_<品牌>_<时间戳>.html + 一份横向对比 Markdown 报告
```

### 2. (可选) 详情页字段增强

```bash
# 读各品牌 top1 热度商品的详情页, 补店铺名/单品销量/评论数
./.venv/Scripts/python.exe scripts/pdd_detail_enrich.py
# → data/pdd_detail_enrich_<时间戳>.json
```

### 3. 重启服务 → 看报告

服务重启后 Web UI 自动用最新 `data/pdd_raw_*.html`。

---

## 🖼️ 4 视图预览

| 视图 | 内容 |
|---|---|
| **总览大屏** | KPI 卡 + 价格中位数柱状 + 单品销量折线(面积) + 销量环形 + 业务洞察 |
| **价格分析** | 价格分档堆叠柱 + 价格区间 + 高频卖点横向柱 |
| **卖点词云** | 15 个关键词 (字号 = 出现频次) |
| **品牌对比** | 明细表: 价位分档条 / 高频卖点 / 销量冠军 / 单品销量 |

截图位于 `data/ui_overview2.png` / `ui_price2.png` / `ui_wordcloud2.png` / `ui_table2.png`。

---

## 📡 API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 单文件 Web 前端 |
| GET | `/docs` | Swagger UI (Try it out 已预填真实品牌示例) |
| GET | `/api/health` | 健康检查 |
| GET | `/api/brands` | 返回默认 6 品牌列表 |
| POST | `/api/competitor-report` | 主端点: 6 品牌横向对比 + 业务洞察 |

请求示例:
```bash
curl -X POST http://127.0.0.1:8001/api/competitor-report \
  -H 'Content-Type: application/json' \
  -d '{"brands":["华为","小米","倍思","QCY","万魔","漫步者"],"top_n":20,"include_insights":true}'
```

返回 (节选):
```json
{
  "brands": ["华为","小米","倍思","QCY","万魔","漫步者"],
  "rows": [
    {"brand":"华为","median":257,"min":82,"max":949,
     "bucket_under_100":0,"bucket_100_300":12,"bucket_300_800":8,"bucket_over_800":0,
     "top_keywords":[{"keyword":"降噪","count":11}, ...],
     "top_product":"Huawei FreeArc 蓝牙耳机","top_sales":53000}
  ],
  "insights": [
    {"kind":"tier","body":"倍思/漫步者/QCY 低价走量 (<¥100); 华为中端 ¥257, 差距 3.5 倍","evidence":[...]}
  ]
}
```

---

## 🧪 测试

```bash
cd upstream/manus-gui
./.venv/Scripts/python.exe -m pytest -q tests/test_ecommerce_*.py
# → 163 passed
```

涵盖 (CI 在 ubuntu + py3.11 上跑同一组命令):
- `test_ecommerce_analyzer.py` (29) — 过滤/排序/价格分布/聚合
- `test_ecommerce_url_query.py` (41) — 拼多多 URL 构造 + 自然语言解析
- `test_ecommerce_pdd_parser.py` (39) — `window.rawData` 提取 + 销量文案
- `test_ecommerce_pdd_detail.py` (10) — 详情页 3 种销量文案 + 店铺名/评论数
- `test_ecommerce_brand_compare.py` (5) — 6 品牌压行
- `test_ecommerce_insight.py` (21) — 4 段洞察 + LLM 兜底链路
- `test_ecommerce_api.py` (18) — FastAPI 端点 + CORS + schema

**不跑** `test_ctrip_cdp_setup.py` / `test_pdd_cdp_*.py` (要真实 Chrome + 桌面 CDP 通道)。

---

## 🏗️ 项目结构

```
upstream/manus-gui/
├── app/
│   ├── ecommerce_url_query.py     # 拼多多 URL 构造 + 自然语言解析
│   ├── ecommerce_policy.py        # 防写入 (13 写操作 + 21 交易词)
│   ├── ecommerce_analyzer.py      # 价格分档/排序/聚合 (480 行)
│   ├── ecommerce_pdd_parser.py    # window.rawData + 详情页解析
│   ├── ecommerce_insight.py       # LLM 总结层 + Stub 兜底
│   └── ecommerce_api.py            # FastAPI 服务 (3 端点)
├── scripts/
│   ├── start_pdd_cdp_chrome.ps1   # 启专用 Chrome 9223
│   ├── pdd_cdp_extract.py         # 只读扫描脚本
│   ├── ecommerce_competitor_report.py  # 单品牌报告
│   ├── ecommerce_brand_compare.py      # 6 品牌横向对比
│   ├── pdd_detail_enrich.py           # 详情页字段增强
│   └── ecommerce_brand_compare_html.py # 离线 HTML 报告
├── static/
│   ├── index.html                 # 深色大屏前端
│   └── vendor/                    # echarts.min.js (离线)
├── data/
│   ├── pdd_raw_<品牌>_<时间戳>.html
│   └── ui_overview2.png 等        # 前端截图
├── tests/test_ecommerce_*.py      # 160 测试
├── requirements-ecommerce.txt     # 最小依赖
└── docs/handoff/                  # 设计决策日志 (Day 1-7.5)
```

---

## 💡 关键设计决策 (为什么这么做)

### 1. 用 CDP 接管本机 Chrome 而不是下载 playwright chromium
- 国内网下载 `playwright install` 卡在 `storage.googleapis.com` 超时 (193MB 失败)
- 用用户已登录的 Chrome → 绕过拼多多登录墙 + 真实 cookie
- 用独立 profile `runtime/pdd-cdp-chrome-profile` 不污染日常 Chrome

### 2. 列表页读 `window.rawData` JSON 而不是 DOM 解析
- PDD 把搜索结果以 JSON 直接嵌在 `window.rawData = {...}` 里
- 比猜 selector 可靠 (DOM 渲染依赖前端框架, JSON 是契约)
- 字段: `goodsName` / `price` / `priceInfo`(显示价) / `salesTip`(销量文案) / `goodsID` / `tagList`

### 3. 销量语义辨析: 三种文案 + 不要把 salesTip 当单品月销量
- `salesTip` 在列表页是**店铺/品牌累计销量** (`本店已拼900万+` / `品牌热销4026.3万件`)
- **单品真实销量**要去详情页读, 文案有 3 种:
  - `热销N件` (漫步者 Zero Air)
  - `已抢N件` (漫步者 X1)
  - `总售N件` (华为 FreeArc)
- 教训: 拿到字段先读它旁边的真实文案语义, 不要看字段名想当然

### 4. Pydantic schema 收口, 不接原始 HTML / cookie
- 端点只接收结构化 JSON + 已渲染的 HTML 文本
- 不接收 raw cookies / 账号 / network traffic
- 政策红线: 防"agent 越权调用" (跨项目硬规则 #11)

### 5. 测试在 sandbox 离线可跑
- `FileHTMLSource` 默认从 `data/` 读 HTML, 不发起网络请求
- LLM 走 Stub, 不绑 API key
- 测试 163 个零依赖外部资源, 已接 GitHub Actions CI

---

## 📚 详细文档

- `docs/handoff/2026-09-05_OPENMANUS_CTRIP_PROJECT_HANDOVER.md` — 整体交接
- `docs/handoff/2026-09-06_CTRIP_FORM_EXECUTOR_IMPLEMENTATION.md` — 状态机模式可参考
- `.workbuddy/memory/2026-09-07.md` — Day 1-7.5 决策日志
- `.workbuddy/memory/MEMORY.md` — 项目骨架 + 跨项目硬规则

---

## 🤝 致谢与归属

模块内嵌于个人衍生项目 [manus-gui](README.md), 沿用 OpenManus (MIT) 许可证。
本模块的反爬思路部分参考上游 manus-gui 的 browser_use CDP 接管实现。