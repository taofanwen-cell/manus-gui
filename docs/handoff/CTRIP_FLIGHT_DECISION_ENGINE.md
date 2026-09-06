# 携程航班结果推导器：实现与面试说明

## 项目定位

本模块将**已经获得的航班结果 HTML 快照**标准化为结构化候选，并以确定性规则进行筛选、排序与推荐说明。它不抓取网页、不操作浏览器、不点击“订票”，也不进入订单或支付流程。

实时网站被 WhaleGuard 拦截时，系统必须停止，而不是伪造实时票价。离线解析、偏好推导与报告仍可被完整测试和演示。

## 实现架构

```text
用户登录的可见浏览器（下一阶段：只读 CDP 人工接管）
  -> CtripQueryTool：最小权限查询，遇保护页立即停止
  -> 页面 HTML/可见文本快照
  -> app/ctrip_flights.py：解析、标准化、校验、规则排序
  -> ctrip_flight_report.py：离线候选报告
  -> 用户自行决定是否进入携程订票流程
```

## 核心方法与理由

不让 LLM 直接“猜”推荐，而是使用可复现规则：

1. 先应用硬约束：预算、最早出发、最晚到达、是否排除共享航班。
2. 再按价格、时长、出发时间或平衡评分排序。
3. 平衡评分固定为 `价格 * 0.55 + 时长分钟 * 0.35 + 出发分钟 * 0.10`。
4. 页面没有的字段保持 `None`，绝不补造价格、库存或中转信息。

这比纯 LLM 推荐更适合作为面试项目：相同输入得到相同输出，评分依据可以审计，错误可定位，离线回归不依赖 API Key 或网站状态。

## 测试集

使用老师遗留的历史结果页作为 fixture：

- `tests/fixtures/ctrip/legacy_sha_bjs_result_20260121.html`

`tests/test_ctrip_flights.py` 覆盖：

- 航司、航班号、机场、时刻、价格和时长解析；
- 最低价排序；
- 联合硬约束过滤；
- 页面结构不完整时返回缺失字段而非猜测。

原有测试继续覆盖：最小权限 Schema、敏感动作拦截、订单/支付 URL 拦截、视觉识别保护页后的状态锁定。

## 运行

```powershell
cd D:\It\Test_Project\Openmanus-Project\upstream\manus-gui
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m pytest -q tests\test_ctrip_flights.py tests\test_ctrip_policy.py tests\test_ctrip_query_tool.py tests\test_ctrip_legacy_fixture.py
.\.venv\Scripts\python.exe .\ctrip_flight_report.py --html .\tests\fixtures\ctrip\legacy_sha_bjs_result_20260121.html --sort-by price --max-price 850 --limit 5
```

报告会明确标记输入为历史离线快照，不能视为实时航班或实时票价。

## 错误规避

| 风险 | 处理 |
|---|---|
| 网站风控/验证码 | 识别后立即停止，要求人工接管；不刷新、不换 URL、不规避。 |
| DOM 更新 | 使用航班结果的语义类名；字段级安全降级为 `None`。 |
| LLM 幻觉 | 排序和解释仅引用 `FlightOption` 的已解析字段。 |
| 历史票价误用 | CLI 固定标注“历史离线数据，不是实时票价”。 |
| 意外交易 | 查询工具不开放 GUI 坐标点击、订单、支付或任意 JS。 |

## 下一阶段

用户在已登录 Chrome 中自行打开航班结果页后，通过用户授权的 CDP 建立**只读连接**，抽取当前可见 DOM/HTML 再送入本解析器。任何订票、订单、支付和验证仍由用户完成。
