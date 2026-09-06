# OpenManus Ctrip Flight Assistant: Handover

**Handover date:** September 5, 2026  
**Project root:** `D:\It\Test_Project\Openmanus-Project\upstream\manus-gui`  
**Current scope:** Ctrip flight search and recommendation. The system must stop before booking, order creation, or payment.

> This document contains no API key, cookie, password, login token, passenger information, or payment information.

## 1. Product boundary

### In scope

- User supplies origin, destination, future departure date, budget, and time preferences.
- User logs in and handles verification in a visible Chrome window.
- Agent connects only to that user-authorized local Chrome through local CDP.
- Agent reads visible flight results, normalizes fields, filters/ranks options, and produces an explainable report.

Example itinerary used in testing:

```text
Guangzhou -> Beijing
September 25, 2026
One way
```

### Explicitly out of scope

The Agent must never:

- log in, register, solve CAPTCHA, bypass verification, or evade risk controls;
- copy/export cookies, passwords, or session tokens;
- enter passenger identity data;
- click booking / reserve / place-order controls;
- create or submit an order, enter a payment page, or pay;
- use GUI coordinate clicks, arbitrary page JavaScript, fingerprint changes, proxies, or anti-bot workarounds;
- describe historical fixture prices as live prices.

A user asked for automatic ordering. It was not performed. Refundability does not remove the risk of booking, inventory locking, or payment.

## 2. Architecture

```text
Visible Chrome owned by user (dedicated local profile)
  -> CDP bound to 127.0.0.1:9222 only
  -> BrowserUseTool (browser-use)
  -> CtripQueryTool (least-privilege policy + form click whitelist)
  -> current transitional runner: ctrip_query_assistant.py
  -> target replacement: deterministic CtripFlightFormExecutor state machine
  -> app/ctrip_flights.py (HTML parsing, normalization, rule ranking)
  -> ctrip_flight_report.py (offline report)
  -> user decides whether to continue manually before booking
```

### Required future change

Do **not** continue relying on Manus/free-form LLM browser clicking for the query form. Implement `CtripFlightFormExecutor` as a deterministic state machine:

1. refresh the current selector map;
2. locate origin field by semantic attributes;
3. fill/select the expected origin;
4. reread DOM and verify origin value;
5. repeat for destination and date;
6. verify all three values exactly match the requested itinerary;
7. locate only the search button inside the active form;
8. read results only; stop before booking controls.

LLM should be limited to lower-risk work: explaining page anomalies, mapping natural-language preferences to structured filters, and summarizing structured results.

## 3. Implemented components

### Model, credentials, and RAG

- `app/config.py`: resolves DashScope credentials in this order: `.env`, `config/.dashscope_api_key`, process environment.
- `.env`: user-local and ignored; contains `DASHSCOPE_API_KEY`. Never print or overwrite it.
- `config/config.toml`: main LLM is `qwen3.7-flash`; visual inspector is `gui-plus`.
- `scripts/test_dashscope_connection.py`: authentication was successfully tested earlier.
- Existing Chroma/RAG foundation:
  - `D:\It\Test_Project\Openmanus-Project\data\index\chroma`
  - `D:\It\Test_Project\Openmanus-Project\src\knowledge_base\service.py`
  - `app/tool/project_knowledge.py`

RAG may contain course notes, architecture decisions, and safe workflow experience. Never store live fares, inventory, credentials, or personal data.

### CDP and visible Chrome

- `scripts/start_ctrip_cdp_chrome.ps1`: launches a dedicated, visible Chrome with `--remote-debugging-address=127.0.0.1` and port `9222`.
- `scripts/test_ctrip_cdp_connection.py`: checks only `/json/version`; rejects non-local CDP endpoints.
- `app/tool/browser_use_tool.py`: reads `CTRIP_CDP_URL`; CDP cleanup must not close the user's Chrome.
- `.gitignore`: ignores `runtime/ctrip-cdp-chrome-profile/`.

Actual CDP verification on September 5, 2026:

```text
Chrome/152.0.7977.82
http://127.0.0.1:9222
```

### Least-privilege Ctrip tool

- `app/ctrip_policy.py`
- `app/tool/ctrip_query_tool.py`
- `ctrip_query_assistant.py`

Policy:

- domains limited to `ctrip.com` and `trip.com`;
- blocks GUI coordinates, arbitrary JS, extra tabs, uploads, image paste, web search;
- blocks login, CAPTCHA, account, order, payment, and related URLs;
- blocks text related to login, verification, passenger info, booking, reserve, place order, submit order, and payment;
- `vision_inspect` is read-only through `gui-plus`;
- WhaleGuard/site-protection/CAPTCHA detection locks future actions and requires human takeover.

Recent protection improvement:

- Before `click_element`, `input_text`, or `select_date`, the tool refreshes the current browser selector map with `context.get_state(cache_clickable_elements_hashes={})`.
- Only current form origin/destination/date fields, the active form search control, one-way selection, and exact requested city options are allowed.
- Recommendation cards, history cards, ads, unrelated cities, and all booking/payment targets are blocked.

### Offline parsing and ranking

- `app/ctrip_flights.py`: converts saved Ctrip HTML to `FlightOption`.
- `ctrip_flight_report.py`: offline CLI report.
- `docs/handoff/CTRIP_FLIGHT_DECISION_ENGINE.md`: detailed implementation notes.

Extracted fields include airline, flight number, aircraft, departure/arrival time, airports/terminals, price, cabin, discount, duration, shared-flight flag, seats, labels, and raw text.

Supported filters:

```text
max price
minimum departure time
maximum arrival time
exclude shared flights
```

Supported ranking:

```text
price / duration / departure / balanced
```

Missing fields remain `None`; no value is invented. Offline reports label the source as historical data, not live fares.

### Teacher-case reuse

Source case directory:

```text
D:\It\Test_Project\case\OpenManus-gui
```

Useful lessons reused:

- DOM first;
- dedicated date selection;
- retain HTML snapshots for diagnosis;
- visual model only for diagnostics;
- fixtures are not live fares.

Copied fixture:

```text
tests/fixtures/ctrip/legacy_sha_bjs_result_20260121.html
tests/fixtures/ctrip/manifest.json
```

Do not reuse fixed-coordinate or fixed-index clicking from the teacher case.

## 4. Real execution history

### Headless stage

Ctrip/Trip.com returned:

```text
whaleguard block
Interactive elements: 0
```

`gui-plus` confirmed a protection page. No bypass, proxy rotation, browser-fingerprint manipulation, or CAPTCHA handling was attempted.

Log:

```text
logs/ctrip_mvp/20260905_200039_can-bjs_visual_inspect.log
```

### CDP visible-Chrome stage

CDP connection succeeded. Ctrip flight search page was reachable:

```text
https://flights.ctrip.com/online/channel
68-69 interactive elements
```

#### First Guangzhou -> Beijing test

Command:

```powershell
$env:CTRIP_CDP_URL = 'http://127.0.0.1:9222'
.\.venv\Scripts\python.exe .\ctrip_query_assistant.py --origin Guangzhou --destination Beijing --date 2026-09-25 --max-steps 10
```

The Agent selected values, then reused a stale dynamic DOM index. Index `67` changed after a city panel rerender and pointed to a recommendation card. It briefly navigated to a wrong route:

```text
Beijing -> Ningbo
September 9, 2026
```

It returned to the query page. It did **not** book, create an order, or pay.

Logs:

```text
logs/ctrip_mvp/20260905_cdp_can-bjs_2026-09-25.log
logs/ctrip_mvp/20260905_cdp_current_state.txt
```

#### Guarded retry

The selector-refresh and form whitelist were added. The retry did not click recommendation cards. However, when the search control was blocked by the overly strict whitelist, the free-form Agent guessed list URLs and then a Trip.com URL. This is why the deterministic state-machine replacement is mandatory.

Log:

```text
logs/ctrip_mvp/20260905_cdp_can-bjs_guarded_retry.log
```

No booking, order creation, payment, verification handling, or protection bypass occurred.

## 5. Known bugs and next fixes

### A. Free-form Agent is unsafe for a dynamic form

**已架构解决：状态机落地。** 详见 §7.P0 与 `docs/handoff/2026-09-06_CTRIP_FORM_EXECUTOR_IMPLEMENTATION.md`。

`app/ctrip_form_executor.py` 已实现 8 状态有限状态机;每次动作前必 `refresh_state`、按 aria/name/placeholder/xpath 语义定位、逐字段 verify;城市面板缺匹配项直接 ABORT(杜绝 input_text 兜底偷填错城市);`BrowserInterface` Protocol 保证 LLM Agent 拿到的接口只有 `refresh_state/input_text/click/select_date/read_field_value/extract_results` 6 个,无 `gui_action`/`execute_js` 路径。

**仍待解 P1:** 把 `BrowserInterface` 通过 `CtripQueryBrowserAdapter` 接到真实 CDP,然后跑一次端到端验证。

### B. Search button whitelist needs current-page calibration

Observed state showed a search button, but the whitelist required a `/form/` XPath pattern and blocked it during the guarded retry. The actual DOM semantics must be captured read-only and converted to a robust selector/parent-form validation.

Do not weaken this to “any element containing search”. Add a fixture and tests first.

### C. CDP cleanup warnings

Symptoms:

```text
BrowserContext.close: 'NoneType' object has no attribute 'send'
RuntimeWarning: coroutine was never awaited
RuntimeError: Event loop is closed
```

Cause: `BrowserUseTool.__del__` tries async cleanup while Python is shutting down; browser-use remote CDP cleanup also differs by version.

Fix recommendation:

1. In `__del__`, immediately return when `CTRIP_CDP_URL` is set.
2. Keep CDP cleanup as detach/no-op.
3. Add a test.
4. Never close or kill the attached Chrome merely to suppress a warning.

### D. browser-use version differences

- Current page object does not provide `get_title()`; use title from `get_current_state()`.
- `get_state()` signatures differ across versions; compatibility code already exists in `app/tool/browser_use_tool.py`.

### E. Windows encoding risk

Use UTF-8 files and `Path.write_text(..., encoding="utf-8")` for Python-generated content. Set:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
```

Avoid complex multiline PowerShell replacement scripts for source code.

### F. Prompt separation

The generic BrowserUseTool documentation still mentions GUI and arbitrary-JS capabilities. The Ctrip tool schema blocks them, but a dedicated form executor should have its own minimal prompt and must not inherit generic browser-action guidance.

## 6. Test and run commands

### Full relevant regression

```powershell
cd D:\It\Test_Project\Openmanus-Project\upstream\manus-gui
$env:PYTHONIOENCODING = 'utf-8'

.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_browser_state_compat.py `
  tests\test_ctrip_policy.py `
  tests\test_ctrip_query_tool.py `
  tests\test_ctrip_click_whitelist.py `
  tests\test_ctrip_cdp_setup.py `
  tests\test_ctrip_flights.py `
  tests\test_ctrip_legacy_fixture.py `
  tests\test_llm_retry_policy.py `
  tests\test_dashscope_key_resolution.py
```

Recent checkpoints: 21 passed, then 16 passed, then 13 passed after the most recent form-whitelist changes. Expected non-blocking warnings: Pydantic configuration, `underscore_attrs_are_private`, faiss AVX2 fallback, and browser-use telemetry.

### Offline report demo

```powershell
.\.venv\Scripts\python.exe .\ctrip_flight_report.py `
  --html .\tests\fixtures\ctrip\legacy_sha_bjs_result_20260121.html `
  --sort-by price --max-price 850 --limit 5
```

### CDP preflight

```powershell
.\scripts\start_ctrip_cdp_chrome.ps1
# User logs in manually in the opened dedicated Chrome.
$env:CTRIP_CDP_URL = 'http://127.0.0.1:9222'
.\.venv\Scripts\python.exe .\scripts\test_ctrip_cdp_connection.py
```

Never set `CTRIP_CDP_URL` to a non-loopback address.

## 7. Next-Agent work order

### P0: stable query executor

**状态：已完成实现并落单测。** 详细交接见 `docs/handoff/2026-09-06_CTRIP_FORM_EXECUTOR_IMPLEMENTATION.md`。下面是精简版的"还差什么":

1. ~~Read this document plus~~ 已读 + 新增了 §执行器文档。
2. ~~Run the regression suite~~ 19+20=39 项通过（`tests/test_ctrip_form_executor.py` + 既有回归）。
3. ~~Implement `app/ctrip_form_executor.py` as deterministic state machine~~ ✅。
4. Collect the current search form element metadata via read-only CDP. —— **P1，未做**。
5. ~~Build mock browser-context fixtures for DOM rerender, stale index, wrong-city option, wrong date, and missing search button~~ ✅（`MockBrowserContext` + 19 单测覆盖所有这五类故障模式）。
6. ~~Only after all deterministic checks pass, run one short CDP query test~~ 待下一步执行（依赖 §4）。
7. Extract result data only and feed it into `FlightOption` parsing/ranking. —— `extract_results()` 已经把 `(visible_text, raw_html)` 落到 `FormExecutorReport`，下一步接到 `app/ctrip_flights.py` 即可。

### P1: result robustness

- Add de-identified fixtures for Guangzhou -> Beijing, cross-day arrival, transfer/stops, missing price, and multiple cabin types.
- Add `source_url`, `observed_at`, `is_live`, `missing_fields`, and `human_takeover_required` to result schema.
- Produce JSON and Markdown reports.

### P2: interview deliverables

- architecture diagram;
- demo scripts for offline success, CDP preflight, protection-page stop, and stale-index prevention;
- failure postmortem and test coverage narrative;
- clear distinction between search/recommendation automation and transaction automation;
- RAG policy: static knowledge allowed, live transactional data forbidden.

## 8. Git/workspace state

Current branch:

```text
main
```

There are many **uncommitted** changes from previous work: RAG, model config, browser compatibility, Ctrip MVP, fixtures, tests, scripts, and handoff docs.

The next Agent must:

- run `git status --short` first;
- never use `git reset --hard`;
- never overwrite/read/print `.env`;
- never put a real key into TOML/source;
- preferably create `codex/ctrip-form-state-machine` before implementing the executor;
- commit in small logical groups: policy, executor, fixtures/tests, result/report, docs.

## 9. Handover conclusion

This is no longer an empty prototype. It has working model configuration, safe key resolution, a Chroma/RAG base, browser-use compatibility work, CDP preflight, user-visible Chrome attach, a least-privilege Ctrip policy, protection-page stopping, offline parsing/ranking, historical fixtures, and regression tests.

The primary blocker is not API authentication or Ctrip login. It is this:

```text
A dynamic web form cannot safely be driven by a free-form LLM Agent reusing DOM indexes.
```

The correct next implementation is a deterministic, verifiable, replayable form state machine. Do not redirect the project toward automated ordering or bypassing Ctrip protection.

## 10. 老师的另一条路线 (vision / gui-plus) — 2026-09-06 补充

接手时一度以为 `D:\It\Test_Project\case\OpenManus-gui` 是"可一口气实现的源码"，差点把状态机路线推倒重做。后来发现那是**视觉路线**，根本不可直接搬：

| 维度 | 老师 (gui-plus 视觉) | 本项目 (CtripFlightFormExecutor 状态机) |
|---|---|---|
| 操作单位 | 屏幕像素坐标 + 键盘文本 | DOM selector_map + 显式 `target_field_index` |
| 决策主体 | 阿里云百炼 `gui-plus` 视觉大模型看截图 | 8 状态机：INITIAL → RESULTS_READY |
| DOM 角色 | 只看 `elements.txt` 描述 | 实际 click / read_field_value |
| 限速 | LLM 推理延迟 (~3-8s/步) | 浏览器原生速度 (~200ms/步) |
| 适配风控 / DOM rerender | LLM 自适应但容易幻觉 | 白名单硬约束 + 严格相等谓词 |
| 政策可执行性 | 无 (坐标点击无法 policy 拦) | `app/ctrip_policy.py` 三道白名单 |
| 复用现有 fixtures | 不能 (vision 看图) | 能 (mock + DOM 状态机) |
| 适用场景 | 一次性跨站点 GUI 任务 | 单一网站可回归的查询表单 |

**老师做了什么 (从 `debug_html/` 看)**：

- 真实跑过 `flights.ctrip.com/online/channel/domestic` (launch)、`online/list/oneway-sha-bjs` (结果页)、`date_picker_opened` 三种页面状态；
- 跨 qunar / fliggy / airpaz / skyscanner 多源通用；
- 城市输入策略：**先 TYPE 文本，不点 input**，等浮层出现，再 vision_click 第一条候选；
- 日期选择器：识别 `div.calendar-modal` 内的 `span.date-d` + `div.date-day[onclick]`。

**两条路线的取舍**：

- **本项目的状态机路线更适合**：可回归测试、可 policy 约束、可 mock 跑 CI、可 trace 调试。
- **老师的 vision 路线更适合**：跨站点一次性任务（japan-travel-plan 那种），不要求强 policy 约束。
- **不要混用**：vision 坐标点击 → 无法过 policy 拦截（policy 是按 action 名 + URL + text，不是按坐标）。

**老师成果在我们项目里的复用方式**：

1. `scripts/inspect_ctrip_selectors.py` 扫老师 `debug_html/*.elements.txt`，抽出真实 selector 分布直方图。
2. `app/ctrip_policy.py:RECOMMENDED_FIELD_INDEX` 是从 343 次 launch-page hits 算出的 top1（origin=37, dest=38, depart=40, return=41, search=48）。
3. `tests/test_ctrip_real_selectors.py` (6 用例) 把这些 top1 钉成回归基线 — 携程 A/B 改版让 top1 变了，本测试 + policy 常量要一起改。
4. `DOM 改一点 index 就漂` 这个事实，正式写入 Bug #1 的根因解释（已在 `MEMORY.md` 第 1 条硬规则说明）。

**未来若要吸收更多老师成果**：

- date_picker 的 `span.date-d` / `div.date-day` selector 可写进 `CtripQueryBrowserAdapter.refresh_state()` 的面板解析；
- `analyze_date_picker.py` 的 calendar-modal 切窗逻辑可抽成纯函数复用；
- vision 截图 `vision_click.png` / `vision_click_clicked.png` 系列可作"点击位置"的 ground truth 训练样本（需要新数据集 skill，本项目不做）。

## 11. 四条路线横向对比 (vision / URL / 状态机+adapter / 自由 LLM) — 2026-09-06 补充

把 `D:\It\Test_Project\case\` 下三个老师项目都看完后，整理出 4 条可走的路线。这不是历史回顾，是**选路决策依据**：

| 路线 | 实现 | 优势 | 劣势 | 适用场景 | sandbox 验证 |
|---|---|---|---|---|---|
| **A. Vision (gui-plus)** | 截图 → LLM 出坐标 → 鼠标点 | 跨站点、不规则页面通吃 | LLM 慢、坐标漂、policy 无法拦、需真浏览器 | 一次性跨站 GUI 任务 (japan-travel-plan) | 不行 (要真 Chrome) |
| **B. URL 路由 (rag)** | 拼 `online/list/oneway-can-pek?depdate=...` → goto | 1 次 goto、纯逻辑可单测、可 sandbox 验证 URL 构造 | 仅对路由友好的网站有效、URL schema 改了要重写 | **携程机票查询 (本 MVP 实际最优)** | **OK, URL 构造纯逻辑** |
| **C. 状态机+adapter (本项目 P0)** | 8 状态机模拟人填表 + 显式 target_field_index | DOM 精准、可 policy 拦截、可 verify、回归测试 | adapter 维护成本高、需真 Chrome 跑端到端 | 单一网站可回归查询表单 (A/B 改版后 adapter 要跟) | mock OK, 真机不行 |
| **D. 自由 LLM Agent (本项目原有)** | Manus 框架 + DashScope key + 自由 loop | 灵活、能处理意外 | 不可重现、不可回归、容易重试到炸、policy 形同虚设 | 探索性任务 (demo / 写代码) | 不行 (要 key + 真浏览器) |

**结论：本 MVP 的最优组合是 B + C**。

- **首选 B (URL 路线)**: 80% 任务（"广州到北京 2026-09-25"这种标准查询）一次 goto 拿结果。URL 构造逻辑 100% sandbox 验证，端到端在用户本地真 Chrome 跑只需 `goto_url` + `extract_content` 两步。
- **兜底 C (状态机路线)**: URL 失败（schema 改版 / 城市不在 IATA 表）时 fallback。原 65 单测 + CDP 脚本不变。
- **永不用 A 和 D**: A 推不动 policy；D 推不动回归。

**落地清单 (commit `123f0d2` 之后)**：

1. `app/ctrip_url_query.py` —— B 路线的核心，260 行。导出 `build_flight_url / build_flight_url_from_query / parse_date / get_city_code / URLOnlyPolicy / FlightSearchParams`。
2. `tests/test_ctrip_url_query.py` —— 26 个单测覆盖：单程 / 往返 / 中文 / IATA / 自然语言 / 未知城市报错 / 日期 wrap / policy 拦截 / 城市表完整性。
3. `ctrip_executor_cli.py` —— 加 `--strategy state|url` 双策略 + `--query "自然语言"` + `--dry-run`（sandbox 也能跑 URL 构造）。
4. `docs/handoff/...HANDOVER.md` —— 本节（第 11 节）作为选路决策依据。

**CLI 用法（用户在本地跑）**：

```powershell
# URL 路线 (首选) - sandbox 也能跑 dry-run 看到 URL
python ctrip_executor_cli.py --strategy url --origin 广州 --destination 北京 --date 2026-09-25 --dry-run
python ctrip_executor_cli.py --strategy url --query "明天从北京到广州的机票" --dry-run

# URL 路线 (真浏览器) - 拿到 URL 后浏览器打开即得结果
python ctrip_executor_cli.py --strategy url --origin 广州 --destination 北京 --date 2026-09-25

# 状态机路线 (兜底) - 不需真 Chrome 但完整 mock trace
python ctrip_executor_cli.py --strategy state --origin 广州 --destination 北京 --date 2026-09-25
```

**遗留（按优先级）**：

1. **真实 CDP 端到端 (URL 路线在用户本地 Chrome 跑一次)** — 验证 schema 没改、result page DOM 解析可用，是 P1 唯一剩下的活。
2. 抽取 `extract_flight_options_from_html` 落 `app/ctrip_flights.py` —— URL 路线拿到 HTML 后自动解析成 `FlightOption` 列表，跟状态机路线用同一份 `ctrip_flights.py` 数据结构。
3. UI 整合：自由 LLM 入口（`ctrip_query_assistant.py`）先 try URL → 失败 fallback 状态机 → 仍失败 HUMAN_TAKEOVER_REQUIRED。
