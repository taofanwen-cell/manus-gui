# 拼多多竞品调研 API

[![CI](https://github.com/taofanwen-cell/manus-gui/actions/workflows/ci.yml/badge.svg)](https://github.com/taofanwen-cell/manus-gui/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 基于 CDP 反爬 + FastAPI 服务化的多品牌拼多多蓝牙耳机竞品横向对比工具，含价格分档、卖点聚合、单品销量、详情页字段增强、可选 LLM 业务洞察、深色大屏 ECharts 前端，以及 **Web UI 输入任意关键词一键扫描出报告**。212 个单测零回归。

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
| **一键扫描** | 输入任意品牌 → `POST /api/scan` 起子进程扫拼多多 → 2s 轮询进度 → 自动出报告 |
| **失败要响** | CDP 不通 503 / 重复提交 409 / 脏关键词 422 / 无数据渲染空态卡片, 都带修复指引 |
| **测试守护** | 212 个 pytest 用例, sandbox/CI 离线可跑 (不需真实 CDP / LLM key) |

---

## 🚀 快速启动 (5 分钟看到效果)

### 0. 前置

- Python 3.11+ (项目在 `.venv/` 已装好)
- 一个能登录拼多多 PC 版的 Chrome

### 1. 一键 demo (推荐)

```bash
cd upstream/manus-gui
bash scripts/demo.sh                 # 正常流程 (有 data/ HTML 就直接出报告)
# 或:
PDD_SKIP_SCAN=1 bash scripts/demo.sh # data/ 已有 HTML → 跳过扫描直接出报告
PORT=8765 bash scripts/demo.sh       # 自定义端口

# 停止服务:
bash scripts/stop_demo.sh            # 跨平台 (Win/Mac/Linux 都行)
```

`demo.sh` 自动做 4 件事: ① 检测本机 CDP Chrome (在线则跑扫描) → ② 启 FastAPI 后台 → ③ 等健康检查 → ④ 打开浏览器。

> **任意关键词即查**: 打开 Web UI 后在输入框填品牌名 (如 `OPPO`), 点 **📡 扫描并生成** —— 后端起子进程扫拼多多 (30~60s), 前端 2s 轮询进度条 + 日志尾行, 扫完自动生成四视图报告。不用再手动跑脚本。

### 2. 手动步骤 (如果你想自己控每一步)

```bash
cd upstream/manus-gui
./.venv/Scripts/python.exe -m uvicorn app.ecommerce_api:app --host 127.0.0.1 --port 8001
# → 浏览器打开 http://127.0.0.1:8001
# → Swagger: http://127.0.0.1:8001/docs
```

> 🔒 **本服务只绑回环（`--host 127.0.0.1`）、仅供本机使用，不要对外暴露** —— 它没有鉴权，
> 且会驱动你真机上已登录的 Chrome 去抓数据。CORS 白名单也只放 `127.0.0.1:8001` /
> `localhost:8001` 两个来源（前端同源直发，正常流程不经 CORS）。不要改成 `0.0.0.0`，
> 也不要把白名单改回 `*`。

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
| GET | `/api/cache-status` | 列出 `data/` 里**已有缓存**的关键词 (按 keyword 去重取最新) + mtime/size/age |
| POST | `/api/scan` | **起子进程扫拼多多** (需要本机 CDP Chrome 已登录) |
| GET | `/api/scan/status` | 轮询扫描进度: `idle` / `running` / `done` / `failed` + 日志尾 20 行 |
| POST | `/api/competitor-report` | 主端点: 6 品牌横向对比 + 业务洞察 |

请求示例:
```bash
curl -X POST http://127.0.0.1:8001/api/competitor-report \
  -H 'Content-Type: application/json' \
  -d '{"brands":["华为","小米","倍思","QCY","万魔","漫步者"],"top_n":20,"include_insights":true}'
```

**扫描流程** (Web UI 的「📡 扫描并生成」就是调这两条):
```bash
# 1. 先看现有缓存, 避免白扫
curl http://127.0.0.1:8001/api/cache-status

# 2. 触发扫描 (suffix 默认拼 '蓝牙耳机'; 关键词已含品类就传 "")
curl -X POST http://127.0.0.1:8001/api/scan \
  -H 'Content-Type: application/json' \
  -d '{"keywords":["OPPO"],"suffix":"蓝牙耳机"}'

# 3. 轮询直到 status != running
curl http://127.0.0.1:8001/api/scan/status
```

扫描端点的错误码设计 (前端据此给不同提示):

| 状态码 | code | 含义 / 用户该做什么 |
|---|---|---|
| 503 | `CDP_UNAVAILABLE` | CDP Chrome 没起 → 跑 `scripts/start_pdd_cdp_chrome.ps1` 并登录拼多多 |
| 409 | `SCAN_BUSY` | 已有任务在跑 (单飞锁, 防风控/防并发写坏 `data/`) → 等它结束 |
| 422 | — | 关键词非法 (空 / >32 字符 / 含 `/ \ \x00`) |
| 200 | — | 已启动, 轮询 `scan/status` |

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
# → 212 passed
```

涵盖 (CI 在 ubuntu + py3.11 上跑同一组命令):
- `test_ecommerce_analyzer.py` (29) — 过滤/排序/价格分布/聚合
- `test_ecommerce_url_query.py` (41) — 拼多多 URL 构造 + 自然语言解析
- `test_ecommerce_pdd_parser.py` (39) — `window.rawData` 提取 + 销量文案
- `test_ecommerce_pdd_detail.py` (10) — 详情页 3 种销量文案 + 店铺名/评论数
- `test_ecommerce_sales_semantics.py` (20) — 单品销量 vs 店铺累计口径贯穿
- `test_ecommerce_brand_compare.py` (5) — 6 品牌压行
- `test_ecommerce_insight.py` (21) — 4 段洞察 + LLM 兜底链路
- `test_ecommerce_api.py` (18) — FastAPI 端点 + CORS + schema
- `test_ecommerce_cache_status.py` (10) — 缓存清单 (空目录/去重/旧式文件名)
- `test_ecommerce_scan_api.py` (19) — 日志反解 + 503/409/422 + 单飞锁

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
│   ├── ecommerce_detail_store.py  # 详情页产物读取 (单品销量)
│   ├── ecommerce_scan.py          # 扫描服务化: 子进程 + 单飞锁 + 状态轮询
│   └── ecommerce_api.py            # FastAPI 服务 (5 端点)
├── scripts/
│   ├── start_pdd_cdp_chrome.ps1   # 启专用 Chrome 9223
│   ├── pdd_cdp_extract.py         # 只读扫描脚本
│   ├── ecommerce_competitor_report.py  # 单品牌报告
│   ├── ecommerce_brand_compare.py      # 6 品牌横向对比
│   ├── pdd_detail_enrich.py           # 详情页字段增强
│   ├── ecommerce_brand_compare_html.py # 离线 HTML 报告
│   └── demo.sh / stop_demo.sh         # 一键 demo / 跨平台停服
├── static/
│   ├── index.html                 # 深色大屏前端 (含一键扫描)
│   └── vendor/                    # echarts.min.js (离线)
├── data/
│   ├── pdd_raw_<品牌>_<时间戳>.html
│   ├── pdd_detail_enrich_<时间戳>.json
│   └── ui_*.png                   # 前端截图
├── tests/test_ecommerce_*.py      # 212 测试
├── requirements-ecommerce.txt     # 最小依赖
├── docs/handoff/                  # 设计决策日志 (Day 1-9)
└── docs/BUG_PLAYBOOK.md           # 67 条真实踩坑知识库 (症状可检索)
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
- 扫描器走 Protocol 注入 (`create_app(scanner=StubScanner())`), CI 零 CDP 依赖
- 测试 212 个零依赖外部资源, 已接 GitHub Actions CI

### 6. 扫描跑子进程而不是 in-process
- 扫描脚本含 playwright/CDP 全局状态, 崩了会连累 API 进程 → `subprocess.Popen` 隔离
- stdout 重定向**临时文件**而非 PIPE (PIPE 写满会卡死子进程)
- handler 提交即返回, 状态靠 `GET /api/scan/status` 轮询 → 不阻塞事件循环
- 单飞锁: 重复提交 409, 防风控 + 防并发写坏 `data/`

### 7. 数值必须带口径
- 列表页 `salesTip` 是**店铺/品牌累计**, 详情页"热销/已抢/总售 N 件"才是**单品销量**
- 两个数**不能混着比** (比的是"谁开店久"而不是"谁卖得好")
- 所以 `top_sales` 一定配 `top_sales_source` 一起传, 前端按口径切标签 + 打 `⚠累计` 角标

---

## 📚 详细文档

- `docs/BUG_PLAYBOOK.md` — **67 条真实踩坑知识库** (症状可检索 + 动手前 Checklist)
- `docs/handoff/2026-09-10_任意关键词即查_实施提示词.md` — Day 9 扫描服务化设计
- `docs/handoff/2026-09-05_OPENMANUS_CTRIP_PROJECT_HANDOVER.md` — 整体交接
- `docs/handoff/2026-09-06_CTRIP_FORM_EXECUTOR_IMPLEMENTATION.md` — 状态机模式可参考
- `.workbuddy/memory/2026-09-07.md` — Day 1-7.5 决策日志
- `.workbuddy/memory/MEMORY.md` — 项目骨架 + 跨项目硬规则

---

## 🤝 致谢与归属

模块内嵌于个人衍生项目 [manus-gui](README.md), 沿用 OpenManus (MIT) 许可证。
本模块的反爬思路部分参考上游 manus-gui 的 browser_use CDP 接管实现。