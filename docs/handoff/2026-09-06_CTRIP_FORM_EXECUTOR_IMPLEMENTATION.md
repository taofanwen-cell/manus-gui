# CtripFlightFormExecutor 实现交接（确定性查询状态机）

**交付日期：** 2026 年 9 月 6 日
**模块：** `upstream/manus-gui/app/ctrip_form_executor.py`
**测试：** `upstream/manus-gui/tests/test_ctrip_form_executor.py`（19 个用例全绿）
**关联文档：** `2026-09-05_OPENMANUS_CTRIP_PROJECT_HANDOVER.md` §5.A、§7.P0

> 严格遵循"绝不下单 / 不登录 / 不验证码 / 不支付 / 不使用任意 JS / 不 GUI 坐标点击"的硬性边界，所有动作都必须通过 `BrowserInterface` 中显式列出的接口。

## 1. 设计目标

把原来由自由 LLM Agent 直接决策的查询表单填写，替换为可审计、可回放、纯确定性的八步有限状态机：

1. `INITIAL` —— 仅做 `FlightQuery.validate()`（含 `past date`、`origin==destination`、`YYYY-MM-DD` 格式），失败立即 `ABORTED`。
2. `FORM_READY` —— 调用 `refresh_state()` 抓最新选择器图；同时扫描 `PROTECTION_TOKENS`（whaleguard / 验证码 / 访问被拒 / 风控等）。命中则 `human_takeover=True`。
3. `ORIGIN_VERIFIED` —— 按语义属性定位出发输入（aria-label/name/placeholder/xpath 任一命中即匹配），**强制从城市面板里点击 option** 而非裸 `input_text`。`target_field_index` 显式传 field.index 避免来回路由失败。
4. `DESTINATION_VERIFIED` —— 与 ORIGIN 同语义，但独立重读选择器避免面板重渲染造成旧索引失效。
5. `DATE_VERIFIED` —— 优先找日期选项点击；找不到时回退 `input_text`，但回读 DOM 时必须满足 `target in normalize(value)`。
6. `ALL_FIELDS_VERIFIED` —— 三字段全部锁住，按 EXACT normalized equality 比对（"广州" != "广州北"）。
7. `SEARCH_DISPATCHED` —— 重新 `refresh_state` 抓最新搜索按钮（tag ∈ {button, a}，haystack 命中 `搜索/查询/search`），点击后写入 trace。
8. `RESULTS_READY` —— 再 refresh 一次确认页面已跳到结果页，调用 `extract_results()` 拿到可见文本 + HTML，**两者都必须非空**才算成功。

每一步都自带 `StepTrace`（state / action / target_index / target_text / observed_value / note），失败路径一律 `ABORTED` 并附 `human_takeover_required` / `protection_detected` 旗标。

## 2. 关键不变量

- **`matches required ⇒ click, not type`。** ORIGIN/DESTINATION 在 panel 里不存在匹配项时**直接 ABORT**，不允许用 `input_text` 兜底绕过——这是为了阻止自动补全悄悄填错城市（如"广州"被替换为"广州北"或"广州市"）。
- **每步刷新、绝不缓存索引。** 任何两步之间都做一次 `refresh_state`，避免重渲染后旧索引指向无关节点。
- **每次写动作都回读 DOM。** verify 阶段重新 `refresh_state` → `_find_field(kind)` → `field2.value`（非空 fallback 到 `field2.text`）。
- **比较走 normalized EXACT equality。** normalize = lowercase + 折叠空白/制表。对城市名、日期都执行相同比对；`bool(v)` 严格过滤空串。
- **审计不可篡改。** `StepTrace` 仅 append，列在 `FormExecutorReport.trace`。

## 3. 公共接口

### 数据结构（都暴露在 `app.ctrip_form_executor` 命名空间）

```python
class FlightQuery(origin: str, destination: str, departure_date: str)
class FormState(str, Enum)            # INITIAL / FORM_READY / ORIGIN_VERIFIED / ... / ABORTED
class FieldKind(str, Enum)             # ORIGIN / DESTINATION / DATE / SEARCH
class DOMElement(index, tag_name, text, attributes, xpath, value)
class ElementSnapshot(url, selector_map, protection_detected)
class StepTrace(state, action, target_index?, target_text?, observed_value?, note?, ts)
class FormExecutorReport(final_state, success, origin_observed, destination_observed,
                         date_observed, result_url, visible_text, raw_html,
                         protection_detected, human_takeover_required, trace)
```

### 执行器

```python
class CtripFlightFormExecutor:
    def __init__(self, browser: BrowserInterface, query: FlightQuery, *,
                 max_retries: int = 2) -> None: ...
    async def run(self) -> FormExecutorReport: ...
```

### 浏览器抽象（Protocol）

```python
@runtime_checkable
class BrowserInterface(Protocol):
    async def refresh_state(self) -> ElementSnapshot: ...
    async def input_text(self, *, index: int, text: str): ...
    async def click(self, *, index: int, target_field_index: Optional[int] = None): ...
    async def select_date(self, *, index: int, text: str): ...
    async def read_field_value(self, *, index: int) -> Optional[str]: ...
    async def extract_results(self) -> Tuple[Optional[str], Optional[str]]: ...
```

> `click` 第二个关键字参数 `target_field_index` 是关键——它让执行器把一次"点击城市选项"直接绑定到要填充的输入字段，避免基于 aria 启发式路由在中国城市面板重渲染后错位（"请输入出发地"和"请输入目的地"都包含"出发"，路由结果是随机的）。

### Mock（生产只读、测试可脚本化）

```python
@dataclass
class MockBrowserContext(BrowserInterface):
    selector_map: dict[int, DOMElement]
    states: list[ElementSnapshot]            # 可选脚本化序列，覆盖默认 stateful 行为
    url, visible_text, raw_html: str
    protection_detected: bool
    on_input, on_click, on_select_date: Callable  # 钩子
    # 暴露 _input_calls / _click_calls / _select_date_calls / _refresh_count 给测试
```

## 4. 测试矩阵（19 用例全绿）

| 场景 | 用例 |
|---|---|
| 输入校验 | `test_run_rejects_past_date_before_touching_browser` / `_same_origin_and_destination` / `_malformed_date` |
| Happy path | `test_happy_path_fills_origin_destination_date_and_reads_results` / `_trace_records_one_entry_per_step` |
| 索引失效 / DOM 重渲染 | `test_stale_index_is_recovered_when_city_panel_rerenders` |
| 错误选项 / 错误日期 | `test_wrong_city_option_aborts_when_only_unrelated_cities_visible` / `test_wrong_date_observation_aborts` |
| 缺失搜索按钮 | `test_missing_search_button_aborts_with_structured_error` |
| 风控 / 验证码 | `test_protection_page_aborts_immediately_with_human_takeover` / `test_explicit_protection_flag_is_treated_as_human_takeover` / `test_protection_token_in_selector_map_text_triggers_human_takeover` |
| 输入文本无法找到匹配项 | `test_input_text_fallback_used_when_no_matching_option_in_panel` |
| 重试恢复（暂态不匹配） | `test_executor_recovers_from_transient_verification_mismatch` |
| 历史搜索项 vs 真按钮 | `test_history_panel_with_search_word_is_ignored` |
| 提交危险关键字黑名单 | `test_executor_blocks_booking_keywords_in_input_text` |
| 协议合规 | `test_executor_depends_on_browser_interface_not_concrete_tool` / `test_executor_does_not_invoke_unsafe_actions` / `test_protection_tokens_constant_includes_required_categories` |

### 跑测命令

```bash
cd upstream/manus-gui
./.venv/Scripts/python.exe -m pytest -q \
    tests/test_ctrip_form_executor.py \
    tests/test_ctrip_policy.py \
    tests/test_ctrip_query_tool.py \
    tests/test_ctrip_click_whitelist.py \
    tests/test_ctrip_flights.py \
    tests/test_ctrip_legacy_fixture.py \
    tests/test_browser_state_compat.py
# 39 passed
```

## 5. 与已有工具的接缝

`BrowserInterface` 是抽象；生产路径需要新增 `CtripQueryBrowserAdapter`，把 `CtripQueryTool`（`app/tool/ctrip_query_tool.py`，已存在）包装成接口。规划：

| 接口方法 | 生产来源 |
|---|---|
| `refresh_state()` | `browser_use_tool.py` 的 `get_current_state()`、`extract_content()` 合并 |
| `input_text(index, text)` | `browser_use_tool` 的 `input(index, text)`（已白名单） |
| `click(index, target_field_index)` | `browser_use_tool` 的 `click(index)`（已白名单）；`target_field_index` 在生产端可以忽略 |
| `select_date(index, text)` | 同上，已被 `CtripQueryTool` 暴露 |
| `read_field_value(index)` | `browser_use_tool` 的 `get_element_value(index)` |
| `extract_results()` | `browser_use_tool` 的 `extract_content()` |

**待办（P1）**：在 `app/tool/ctrip_query_tool.py` 加 `target_field_index` 参数到 `click`（默认 `None`，向后兼容），并撰写一个 30 行不到的 `CtripQueryBrowserAdapter` 把工具方法直接委托给接口。

## 6. 后续工作

1. **P1 — 与 CDP 真实浏览器联通**：在 `scripts/` 写一个 60 行的脚本，仅跑一次 `广州 → 北京 2026-09-25` happy path，截 trace 表到 `data/ctrip_executor_trace_*.json`。
2. **P1 — `BrowserInterface` → 真实 adapter**：补 `app/ctrip_query_adapter.py`。
3. **P2 — 集成到运行器**：在 `ctrip_query_assistant.py` 里把"自由 LLM 决策点击查询表单"替换成"先把 `FlightQuery` 喂给执行器；执行器 ABORT 时再走 fallback 询问用户"。
4. **P2 — 审计报告**：把 `trace` 列表导出为 Markdown 表，方便案例复盘。
