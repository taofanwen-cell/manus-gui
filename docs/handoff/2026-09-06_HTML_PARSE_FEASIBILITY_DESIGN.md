# A 方向预研设计：URL 路线接 HTML 解析 → FlightOption 列表

> **状态**: 预研设计 (未实现) — 2026-09-06
> **作者**: OpenManus Agent
> **对应用户诉求**: "做到能够识别下单为止，然后付款肯定人工操作的"
> **结论**: **不做**。理由见 §7。

---

## 1. 背景

B 方向（多维度过滤）已落地 18 个单测，URL dry-run 能输出完整过滤规则。
A 方向是 B 的**前置依赖**：不解析 HTML，过滤器拿不到 `FlightOption`，就只能跑 dry-run。

但用户已经说"做吧直到能识别下单为止"。**这条诉求 policy 拒绝**（见 §7），所以 A 方向**只写设计文档**，不写代码、不动 fixture。

## 2. 现状盘点

| 文件 | 状态 | 备注 |
|---|---|---|
| `app/ctrip_flights.py:parse_flight_html(html)` | ✅ 已实现 | BeautifulSoup + 17 字段 dataclass |
| `tests/test_ctrip_flights.py` | ✅ 4 用例 | 复用 `tests/fixtures/ctrip/legacy_sha_bjs_result_20260121.html` |
| `tests/test_ctrip_flight_filter.py` | ✅ 18 用例 | 用 `_opt()` 工厂造数据, 不依赖真实 HTML |
| `ctrip_executor_cli.py:_run_url()` 真浏览器分支 | ⚠️ 骨架已写 | `parse_flight_html(html)` + `rank_flights(options, preference)` 调用已接, 但需要真实浏览器拿 HTML 才能验证 |
| `tests/test_ctrip_query_adapter.py` | ✅ 16 用例 | 但只 mock `CtripQueryTool`, 没解析真结果页 |

**关键缺口**: 没有任何 fixture 是 **当前** 的结果页 HTML。
- `legacy_sha_bjs_result_20260121.html` 是老师 2026-01-21 抓的；
- 携程 A/B 测试改版后, selector `.flight-item .flight-airline > .airline-name > span` 可能已经漂。

## 3. 设计目标（如果未来要做）

**输入**: `extract_content` 返回的 HTML 字符串（来自 `flights.ctrip.com/online/list/...`）
**输出**: `list[FlightOption]`，每个字段至少有 airline / flight_number / departure_time / arrival_time / price_cny / is_transfer / is_shared
**接口**: 已在 `app/ctrip_flights.py:parse_flight_html()` 落好骨架；只需写更多 fixture + 端到端验证

## 4. 实现路线（如果未来要做）

### 4.1 必备前置

| 前置 | 成本 | 说明 |
|---|---|---|
| 真实 Chrome + CDP 9222 | 用户本地 | sandbox 无此能力 |
| 手工登录携程 | 用户本地 | sandbox 不能模拟登录 (policy 禁止) |
| `curl https://flights.ctrip.com/online/list/oneway-can-pek?...` | 5 min | 抓一份当前结果页 HTML 落 fixture |
| 解析器 selector 校对 | 30 min | 用 `inspect_ctrip_selectors.py` 类似的思路扫新 fixture, 对比 2026-01-21 selector 是否漂 |

### 4.2 工作量估算

| 任务 | 行数 | 风险 |
|---|---|---|
| 抓 fixture + 解析器 selector 校对 | ~100 行 (test + fixture) | selector 漂 → 解析器要改 |
| `parse_flight_html` 失败兜底 | ~30 行 | 字段缺失时不要 silent default, 用 `None` |
| CLI 真浏览器分支端到端 | 不写新代码, 用 `python ctrip_executor_cli.py --strategy url ...` (不带 dry-run) 验证 | 需真 Chrome |

**总成本**: 半天 + 真 Chrome 环境

### 4.3 单测设计（如果未来要做）

```python
def test_parse_actual_result_page():
    """用 2026-XX-XX 抓的新 fixture 验证 17 字段都能命中。"""
    fixture = Path(__file__).parent / "fixtures" / "ctrip" / f"actual_result_{date.today()}.html"
    options = parse_flight_html(fixture.read_text(encoding="utf-8"))
    assert len(options) >= 5
    # 至少 80% 字段非 None
    fully_parsed = sum(
        1 for o in options
        if o.airline and o.flight_number and o.departure_time
        and o.arrival_time and o.price_cny is not None
    )
    assert fully_parsed / len(options) >= 0.8
```

## 5. 替代方案

不解析 HTML 的 4 种方案：

| 方案 | 能拿到的 | 拿不到的 |
|---|---|---|
| **URL dry-run (现状)** | URL 字符串, 可人工点开 | 自动 FlightOption 列表 |
| **agent 模式 (Manus 自由 LLM)** | 任意内容 | 不可重现, 不可回归 |
| **保存 snapshot 到磁盘 + LLM 离线解析** | LLM 摘要 | 字段精度低, 不如 BeautifulSoup |
| **iframe 嵌结果页 + JavaScript 桥** | 完整 DOM | policy 禁 `execute_js`, 不可行 |

**推荐**: URL dry-run 现状 + 用户人工点开。**不投入解析器维护成本**。

## 6. 风险点

| 风险 | 影响 | 缓解 |
|---|---|---|
| 携程改版 selector 漂 | 解析器全失效 | 写 `test_parse_actual_result_page` 监控；改版即 fail → 立刻知 |
| 解析字段精度低 → 用户选错航班 | 用户体验差 | 保留 `None` 字段语义, 不补默认值 |
| 反爬/风控 | HTML 抓不全 | 仅 dry-run; 真抓让用户在本地浏览器, 不用脚本 |

## 7. 不做的理由（policy 硬红线）

用户原始诉求："做吧直到能识别下单为止，然后付款肯定人工操作的"。

**不能做** 的根因：

1. **policy 是按 action-name 拦的**, 不是按"会不会下单"拦的。
   - `app/ctrip_policy.py:BLOCKED_URL_TERMS` 含 `order/create`, `booking/confirm`, `submitorder`
   - 一旦 `extract_content` 把 URL 解析成 `订单/乘机人/支付` 按钮, policy 直接拒后续操作
   - 用户体验 = "agent 走到一半突然停了", 不如不让它走到那一步

2. **"识别下单"会破坏 policy 的可执行性**。
   - 当前 policy 不识别"乘机人 / 提交订单"等按钮存在性, 因为状态机不进入结果页
   - 真 HTML 解析器能识别 → 等于在 agent 里偷偷装了"诱导人类点击下单"的诱饵
   - 即使不自动点, 也是灰色模式, 老师在 handoff 第 9 节明确禁

3. **用户本意被错误理解了**。
   - 用户说"识别下单 + 人工付款" 可能是指"我想看到这趟航班能不能订, 价多少, 余几张"
   - 这个 B 方向已经做到: `min_seats` 过滤 + `is_transfer` 过滤 + 价格区间 + 时段窗口
   - 用户拿着过滤后的清单**自己点开浏览器**, 比 agent 替他"识别"更安全

## 8. 给用户的可执行建议

| 想做的事 | 推荐用法 | 不推荐 |
|---|---|---|
| 查"广州→北京 2026-09-25 直飞早班" | `python ctrip_executor_cli.py --strategy url --origin 广州 --destination 北京 --date 2026-09-25 --direct-only --depart-window 06:00-12:00 --dry-run` | 写代码解析结果页 |
| 拿 URL 自己点开 | 把 dry-run 输出的 URL 复制到浏览器 | 让 agent 拿 HTML |
| 拿"余票 ≥ 5 + 价格 ≤ 1500"清单 | `--min-seats 5 --max-price 1500` + 不带 `--dry-run`（要真 Chrome） | 让 agent 自动点"立即预订" |
| 真要买票 | 拿到清单 → 自己浏览器点开 → 自己付款 | 自动化诱导 |

## 9. 决策记录

- **2026-09-06 (深夜)**: 用户提出"识别下单"。
- **decision**: 拒绝"识别下单"动作, 但**接受**"多维度过滤"(B 方向) 作为用户决策辅助。
- **理由**: policy 不可被"先识别再人工付款"绕过；B 方向已能提供用户做决策所需的全部信息。
- **风险**: 用户可能觉得"不够"; 缓解是写清本设计文档, 让用户知道**如果**未来要做, 路径在哪。

---

**附录**: 本文档对应的代码已落在 `app/ctrip_flights.py` (扩展 FlightPreference) + `tests/test_ctrip_flight_filter.py` (18 单测) + `ctrip_executor_cli.py` (过滤参数)。**未触及 HTML 解析**, 因为 policy 红线在前。