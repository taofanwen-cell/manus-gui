# BUG 知识库（踩坑手册）

> 用途：**遇到异常先查这里**。每条都是本项目真实踩过并修掉的坑，不是"应该如此"的想象。
> 覆盖 2026-09-06 ~ 2026-09-11 两个项目（携程机票查询 MVP / 拼多多竞品调研 API）共 74 条。
> 维护：修完一个真 bug，就按格式追加一条，并更新第 0 章速查索引。

---

## 0. 怎么用 + 速查索引

### 检索方式

1. **按症状关键词** → 查下面这张表拿到编号，**Ctrl+F 搜编号**跳过去（如搜 `B-17`）。
2. **按技术栈** → 直接跳对应章节。
3. 查不到 → 修完后**追加一条**（这是本文档唯一的价值来源）。

### 症状 → 编号速查表

| 你看到的现象 | 编号 |
|---|---|
| 数值 `5.3` 变 `53`、版本号被吃掉小数点 | B-09 |
| `不入耳` 被判成 `入耳`、`后天` 命中 `大后天` | B-10 |
| 空字符串 `""` 竟然通过了校验 | B-11 |
| 中文文案里出现字面量 `\n\n` | B-12 |
| 数字格式化出 `5000.0万` / `18.8894万` 这种怪值 | B-13 / B-14 |
| 报告里混进 markdown 符号 `- **倍思**` | B-15 |
| **销量数字大得离谱、各品牌都是同一个封顶值** | **B-17** |
| 详情页有的商品抓不到销量 | B-18 |
| **6 个品牌返回完全一样的数据** | **B-20** |
| 前端显示 `—`、冠军机型名为空 | B-34 / B-35 |
| 布尔参数语义反了（填 True 反而保留） | B-05 |
| 同秒写入的文件 `max(mtime)` 结果随机 | B-30 |
| `fetch`/取最新文件只按 mtime 排，时间打平取到旧文件 | B-74 (B-30 第三处) |
| `connect_over_cdp` 没传 `timeout`、无界卡住拖垮轮询端点 | B-75 (B-68) |
| `scripts/` 里"取最新 HTML"单判据排序 + scan 脚本 `connect_over_cdp` 无 timeout（同款复发） | B-76 (B-74/B-75 脚本侧) |
| 测试有时过有时挂（flaky） | B-30 / B-31 |
| 自己写的 fake 缺字段导致测试挂 | B-28 |
| 手搓的 mock 数据源行为跟真的不一样 | B-29 |
| 改 executor 一步，所有测试同时挂 | B-25 |
| 断言"应该含 X"结果不成立 | B-23 |
| `py_compile` 过了但运行时 NameError（scripts/ 无测试覆盖更危险） | B-03 / B-66 / B-77 |
| 同一文件并行改两处，一处被覆盖 | B-66 |
| 子进程卡死不返回 | B-39 |
| 后台脚本跑了很久最后没输出 | B-40 |
| 启的服务跨命令就死了 | B-41 |
| 访问 127.0.0.1 返回 502 | B-57 |
| curl 写文件 exit 23 / 显示 0 bytes | B-56 |
| 图表 x 轴只显示一半类目 | B-61 |
| 词云只有几个词 | B-62 |
| Swagger 试了返回空 | B-64 |
| 仓库里混入 chrome profile 大文件 | B-60 |
| 扫成功了却显示"扫描失败"、不生成报告 | B-71 |
| 服务端拉起的 Chrome 秒消失 / ensure 后一切超时 | B-72 |
| 无鉴权本机 API 放行任意来源 | B-73 |
| 热度机型行机型名是耳机、价格却是另一台手机（跨商品错配） | B-78 |
| 报告标题写蓝牙耳机、实际在比手机（品类漂移 / suffix 没拼上） | B-79 |

### 章节地图

| 章 | 主题 | 什么时候翻 |
|---|---|---|
| 1 | Python 语法/语义 | 写完代码报奇怪的错 |
| 2 | 字符串/正则/文本解析 | 清洗、抽取、格式化文本 |
| 3 | **数据语义（口径）** | **换数据源、加数值字段时必读** |
| 4 | 测试 | 写测试/测试挂/测试不稳定 |
| 5 | 字段与契约一致性 | 加字段、改接口 |
| 6 | 并发/子进程/服务 | 起服务、跑子进程、长任务 |
| 7 | 爬虫/CDP/反爬 | 抓数据、接新站点 |
| 8 | Windows/Git Bash/环境 | 本机跑命令出问题 |
| 9 | 前端/ECharts/Swagger | 改页面、调 API 文档 |
| 10 | 工程纪律 | 每次开工前 |
| 11 | 复用 Checklist | 动手改之前 |

---

## 1. Python 语法 / 语义

### B-01 docstring 里嵌 f-string 导致解析失败
- **症状**：文件 `SyntaxError`，位置指向一个 docstring。
- **根因**：`"""...f"""` 里再套 `f"""` 且带 `break`，Python 把引号配对搞乱。
- **修法**：docstring 里不写 f-string，普通文本即可。
- **判断规则**：docstring 只放静态说明；要展示变量就在函数体内 print/log。

### B-02 `tuple((a,b),(c,d))` 构造报错
- **症状**：`TypeError: tuple expected at most 1 argument, got 2`。
- **根因**：想造"元组的元组"，写成了 `tuple(t1, t2)`（两个参数）。
- **修法**：`evidence = (a, b), (c, d)` 直接逗号分隔，或 `tuple([(a,b),(c,d)])`。
- **判断规则**：`tuple()` / `dict()` / `set()` 只接受**一个可迭代对象**；造多个元素的元组用逗号，不用 `tuple()`。

### B-03 class body 里引用模块级常量作默认值 → NameError
- **症状**：`NameError: name 'DEFAULT_BRANDS' is not defined`，但常量明明在模块顶部定义了。
- **根因**：`def __init__(self, brands=DEFAULT_BRANDS)` 写在 **class body 内**，而 `DEFAULT_BRANDS` 定义在 class 之后（默认值在定义时求值，不延迟）。
- **修法**：用 `None` 哨兵：`def __init__(self, brands=None): self.brands = brands or DEFAULT_BRANDS`。
- **判断规则**：默认值只用来放**不可变字面量**（None / 数字 / 短字符串）。要引用模块级对象，一律 `None` 哨兵 + 函数体内解析。

### B-04 dataclass 加字段，三处不同步
- **症状**：`TypeError: __init__() got an unexpected keyword argument`。
- **根因**：给 dataclass 加了字段、`_trace(...)` 调用处传了参，但 dataclass 定义或测试断言没同步（曾一次挂 16 个测试）。
- **修法**：一次改完三处再跑测：**定义 + 所有构造点 + 断言**。
- **判断规则**：加字段前先 `grep` 该类名的所有构造点，列全了一起改。

### B-05 布尔参数语义写反
- **症状**：`exclude_missing_xxx=True` 传进去，结果是**保留**了缺失数据。
- **根因**：`return pref.exclude_missing_xxx` —— 语义是"剔除"，却直接返回成"通过"。
- **修法**：`return not pref.exclude_missing_xxx`，并在 docstring 显式写清 True/False 各自的**动词**。
- **判断规则**：布尔字段命名要能读出动词（`exclude_` / `only_` / `allow_`）。写完后**大声念一遍**："`exclude_missing=True` 时，这条数据会被排除" —— 念不通就是写反了。

### B-06 生成器表达式直接喂给 `argparse.parse_args`
- **症状**：`TypeError` 或 `SystemExit: 2`。
- **根因**：`parser.parse_args(f"--{k} {v}" for k, v in d.items())` —— argparse 收到一个 generator 对象当 argv。
- **修法**：`argv = [x for kv in d.items() for x in (f"--{kv[0]}", str(kv[1]))]` 展平成 list；**且 None 值要跳过**，不能 `str(None)` 变成字符串 `"None"`。
- **判断规则**：任何接受 `argv`/`*args` 的 API，传之前先确认是 list 不是 generator。

### B-07 `single_sales=0` 被 `or` 短路掉
- **症状**：销量为 0 的商品被当成"没数据"，跳过了单品销量逻辑。
- **根因**：`if detail.get("single_sales") or ...` —— 0 是 falsy。
- **修法**：`v = detail.get("single_sales"); if isinstance(v, int) and v >= 0:`（`isinstance` 同时挡掉 bool 和 None）。
- **判断规则**：**数值字段判断有无值，用 `is not None` / `isinstance`，永不用 `or`/truthy**。0 是合法业务值。

### B-08 ElementSnapshot / dataclass 凭印象加字段
- **症状**：`TypeError: unexpected keyword argument 'title'`。
- **根因**：`ElementSnapshot(title=...)` 但 dataclass 只有 `url`/`selector_map`/`protection_detected`。
- **修法**：加字段前先 Read 定义。
- **判断规则**：给任何已存在的 dataclass 塞新 kwarg 前，**先 Read 一遍定义**，别凭印象。

---

## 2. 字符串 / 正则 / 文本解析

### B-09 `re.sub(r"\D","")` 把 `蓝牙5.3` 剥成 `蓝牙53`
- **症状**：卖点标签出现 `蓝牙53`（应为 `蓝牙5.3`）。
- **根因**：`\D` = 所有非数字，**小数点也是非数字**，一起被删。
- **修法**：`re.sub(r"\s+", "", s)` —— 只去空白，别的都留。
- **判断规则**：清洗字符串时，先想清楚"要**保留**什么"，而不是"要删什么"。版本号 / 价格 / 小数 / 带 `-` 的编号永远要保留 `.` 和 `-`。

### B-10 子串包含匹配：`不入耳` 被判成 `入耳`
- **症状**：同一个商品同时标了 `入耳` 和 `不入耳` 两个互斥卖点。
- **根因**：`"入耳" in "不入耳"` = True。
- **修法**：互斥组按**优先级 + 长度倒序**匹配：`不入耳 > 半入耳 > 入耳`。
- **判断规则**：**凡是 `substring in text` 的匹配，先按子串长度倒序排，再逐条匹配**。
  - 同源坑：自然语言日期 `"后天" in "大后天"` = True → 得先匹配 3 天的"大后天"再匹配 2 天的"后天"。
  - 写法模板：`for kw in sorted(KEYWORDS, key=len, reverse=True): if kw in text: ...`

### B-11 谓词允许空字符串通过校验
- **症状**：字段还是空的，verify 却判定"填好了"。
- **根因**：`lambda v: v is not None and (target in normalize(v) or normalize(v) in target)` —— `v=""` 时 `"" in "广州"` = **True**。
- **修法**：三件套
  ```python
  match = lambda v: bool(v) and bool(target) and normalize(v) == target
  ```
  1. `bool(v)` 短路空值；2. `bool(target)` 短路空查询；3. **normalize 后严格 `==`**，不用 `in`。
- **判断规则**：任何字符串校验谓词都得满足"空输入必然返回 False"。写完拿 `""` 和 `None` 各测一次。

### B-12 转义写多，`\n\n` 变成字面量
- **症状**：报告正文里出现肉眼可见的 `\n\n` 两个字（不是换行）。
- **根因**：在已转义的上下文里又写了一次 `\\n\\n`。
- **修法**：改用真换行 / `textwrap.dedent` / `\n`.join。
- **判断规则**：生成文本后，**打印出来看一眼真实字符**，别只看变量内容。

### B-13 `5000.0万` 尾零丑
- **症状**：销量显示 `5000.0万`，应该是 `5000万`。
- **根因**：`f"{n:.1f}万"` 固定一位小数。
- **修法**：`f"{n:g}"` 去尾零（`5000万` / `61万` / `147.2万`）。

### B-14 `:g` 让 `18.8894万` 冒出来
- **症状**：评论数显示 `18.8894万`，应该是 `18.9万`。
- **根因**：`:g` 默认 6 位有效数字，不限制小数位。
- **修法**：`f"{n:.1f}万".rstrip("0").rstrip(".")` —— 先定 1 位，再去尾零。
- **判断规则**：**`:g` 和 `:.1f` 各有各的坑**：`:g` 不控小数位，`:.1f` 留尾零。人类可读数字 = `:.1f` + 去尾零。两个都要写测试钉住。

### B-15 markdown 前缀被带进结论文本
- **症状**：结论里出现 `- **倍思** 中位数 ¥73`。
- **根因**：从已渲染的 markdown 行 `bands[0].split(' —— ')[0]` 里截取，把 `- **` 装饰符一起带出来了。
- **修法**：直接用数据源 `with_median[0]['brand']`，不要从渲染后的字符串反解。
- **判断规则**：**永远从数据层取值，不要从渲染层的输出字符串里反解**。渲染字符串是给人看的，不是给程序读的。

### B-16 自然语言剥离 leading words 太短
- **症状**："调研一下蓝牙耳机的竞品" → 抽出关键词 `一下`。
- **根因**：只列了单字/短词 `["调研", "帮我"]`，剥完剩 `一下`。
- **修法**：leading words 按**完整短语**列：`帮我调研一下` / `调研一下` / `看一下`。
- **判断规则**：剥离词表按**长度倒序匹配**（同 B-10），且覆盖真实用户口癖（多写几个完整短语）。

---

## 3. 数据语义（口径）—— 最高危区

> 这一章的 bug 共同点：**代码没错，错在把 A 含义的数当 B 含义用**。测试测不出来，只有人看数据时才现形。

### B-17 列表页 `salesTip` 被当成单品销量 ★
- **症状**：华为/小米/倍思的"销量"全是 `5000万`（同一个封顶值），漫步者 `4026.3万` —— 明显不对。
- **根因**：拼多多列表页 `salesTip` 的**真实语义是"店铺/品牌累计销量"**（文案是"本店已拼 / 品牌热销"），不是单品销量。跨品牌比它 = 比谁开的店久。
- **修法**（三段，缺一不可）：
  1. 数据层：详情页正文"热销/已抢/总售 N 件"才是**单品销量**，优先用它；拿不到才降级 `shop_total`。
  2. 传输层：新增 `top_sales_source`（`single` / `shop_total` / `unknown`）**跟着数值一起走**。
  3. 消费层：前端表头/图标题/KPI/tooltip 全部按 source 变文案，`shop_total` 打 `⚠累计` 角标；insight 生成时**混口径不混比**。
- **判断规则**：**数值字段换数据源后，必须同时暴露"口径/来源"标记**。没有口径标记的数字不许进报告和图表。

### B-18 单品销量文案有 3 种，只看 1 个详情页就写正则必挂
- **症状**：有的商品抓得到销量，有的抓不到。
- **根因**：真实页面里有三种文案，只测了一种就定正则。

  | 文案 | 例子 | 出现品牌 |
  |---|---|---|
  | `热销N件` | 热销9.4万+件 | 漫步者 Zero Air |
  | `已抢N件` | 已抢11.3万+件 | 漫步者 X1 |
  | `总售N件` | 总售5.3万+件 | 华为 FreeArc |

- **修法**：正则兼容三种 `(?:热销|已抢|总售)\s*([\d.]+万?\+?)\s*件`，并**把三种文案都写成回归用例**。
- **判断规则**：**从网页抽字段，至少实测 3 个不同样本**（不同品牌/不同商品）再定正则。

### B-19 看到字段名就想当然（不看旁边真实文案）
- **症状**：把 `monthly_sales` 当"单品月销量"写进报告。
- **根因**：只看字段名 `salesTip` 猜语义。
- **修法**：**读字段旁边的真实展示文案**判断语义 —— "本店已拼"、"品牌热销"、"已抢 N 件" 是三种完全不同的含义。
- **判断规则**：拿到一个数值字段，先回答三个问题：**谁的**（单品/店铺/品牌）？**什么时间范围**（累计/月/日）？**页面上原文怎么写的**？

### B-20 fallback glob 导致 6 个品牌返回同一份数据 ★
- **症状**：6 个品牌横向对比，6 行数据**完全一样**（中位数都是 ¥83）。
- **根因**：`FileHTMLSource` 找不到 `pdd_raw_华为蓝牙耳机_*.html` 时，fallback 到 `pdd_raw_*.html` 的第一个 —— 于是 6 个品牌都读到同一份 HTML。
- **修法**：`_detect_brand` 从 keyword 反推品牌（优先匹配已知品牌名，否则剥后缀 `蓝牙耳机/耳机/earphone`）；**找不到就明确报错**。
- **判断规则**：**模糊 fallback 比"找不到"更危险** —— 它让错误数据看起来像正常数据。fallback 要么不做，要么在结果里打显眼标记。

### B-21 相同警告刷屏 6 遍
- **症状**：6 个品牌都报"20/20 缺评分"，洞察区刷出 6 条一模一样的警告。
- **修法**：去重 + 聚合标注"（覆盖 N 个品牌：华为, 小米, ...）"。
- **判断规则**：任何**会展示给人看的列表**，生成后先过一遍去重/聚合。

### B-22 店铺名只能从"进店逛逛"锚点取，官方自营页没有
- **症状**：华为/小米店铺名是 `None`。
- **根因**：这两个品牌是官方自营，详情页没有"进店逛逛"锚点。
- **处理**：这是**数据缺失，不是 bug** —— 报告里标注"官方自营"而不是留空或猜。
- **判断规则**：区分"抓不到（bug）"和"页面上本来就没有（数据缺失）"。后者要显式标注语义，不能静默留空。

---

## 4. 测试

### B-23 断言"应该含 X"结果不成立
- **症状**：`test_purchase_text_blocked` 期望 reason 含"交易"，实际是"写操作"。
- **根因**：policy 有双重保护，`click_element` 先在写操作黑名单被拦了，**根本走不到**文案拦截那一层。
- **修法**：断言改成 policy 的**真实行为** "写操作"，并加注释说明双重保护。
- **判断规则**：**测试期望写"系统真实会做什么"，不要写"我觉得它应该做什么"**。拿不准就先跑一次看实际输出。

### B-24 Protocol 签名不完整 → 真 bug 藏起来
- **症状**：`click(index=...)` 完全合规，但 mock 自己用启发式猜往哪写，字段错位 bug 一直没暴露。
- **根因**：`BrowserInterface.click(*, index)` 没声明 `target_field_index`。
- **修法**：Protocol 把**所有上下文参数**列全（哪怕调用方暂时传 None）。
- **判断规则**：**跨边界接口用 Protocol/Interface，签名必须完整**。Protocol 不说，调用方就不传，实现方就只能瞎猜。

### B-25 scriptable mock（按调用顺序 pop 状态）太脆弱
- **症状**：executor 内部调整一步，所有测试同时挂 —— 分不清是 mock 没跟上还是 executor 真坏了。
- **修法**：mock 默认 **stateful**：`click(index)` 直接改 `mock.fields[index]`，`read_field_value(index)` 直接读。只在确实要脚本化（DOM rerender / 缺按钮 / 风控页）时才用 `states[]` 队列。
- **判断规则**：**mock 的行为由"被施加了什么操作"决定，不由"第几次调用"决定**。状态切换靠行为触发（点搜索按钮才切结果页），不靠计数器。

### B-26 生产实现的能力 ≠ 协议的假设
- **症状**：所有 verify 步骤 ABORT。
- **根因**：executor 假定 `read_field_value` 能拿到 input.value；生产 browser-use 没有这个 action，返回 None。
- **修法**：协议层声明**能力查询** `can_strict_read() -> bool`；False 时退化为"信任 click + 字段仍在 DOM + 语义标签完整"。
- **判断规则**：协议不只声明"做什么"，还要声明"**能做什么**"。能力不足时要有明确的降级路径，不是硬失败。

### B-27 async 测试没装 pytest-asyncio
- **症状**：`async def functions are not natively supported`。
- **修法**：先看项目现有约定（本项目用 `asyncio.run` helper + 装饰器），跟着写，别另起炉灶。
- **判断规则**：写新测试前，**先读一个同目录已有测试**，抄它的异步/夹具写法。

### B-28 自己写的 fake 缺字段
- **症状**：`test_brand_row_exposes_top_product_alias` 挂，报缺 `filtered_size`。
- **根因**：手搓的 `_Rep` fake 少定义属性。
- **修法**：补上缺的字段。
- **判断规则**：**fake 对象照着真实 dataclass 的字段表抄一遍**，不要"用到哪个写哪个"。

### B-29 手搓 mock 数据源行为跟真的不一样
- **症状**：`test_report_partial_miss_keeps_good_rows` 挂 —— 手写的 `_Src` 没做品牌反推。
- **修法**：改用**真实的 `FileHTMLSource(tmp_path)`** 造数据（tmp_path 里放假 HTML），测的就是生产代码路径。
- **判断规则**：**能用真实类就用真实类 + tmp_path，别手搓行为等价物**。手搓的 fake 永远会漏掉真实类里的某个逻辑分支。

### B-30 同秒写入时 `max(mtime)` 结果随机（flaky）★
- **症状**：`test_load_latest_detail_picks_newest` 有时过有时挂。
- **根因**：同一秒内写入多个文件，`st_mtime` 相同，`max()` 返回哪个不确定。
- **修法**：双判据 `max(files, key=lambda p: (p.stat().st_mtime, p.name))` —— 文件名里的 `YYYYmmddTHHMMSS` 作稳定第二判据。
  - **同一个坑在两处同时出现**：`ecommerce_detail_store.find_latest_detail_file` 和 `build_cache_status`。修要一起修。
  - **第三处漏网之鱼**（2026-09-11 验收才抓到）：`app/ecommerce_api.py::FileHTMLSource.fetch` 也是单判据 `st_mtime` → 见 B-74。grep `key=lambda p: p.stat().st_mtime` 找漏网。
- **判断规则**：**任何"取最新文件/记录"的排序，单靠时间戳都不稳**。加一个单调的第二判据（文件名 / ID / 序号）。

### B-31 测试挂了先精确定位，别只看尾巴
- **症状**：`pytest -q` 失败但看不到是哪条。
- **修法**：`pytest --tb=long -v` 看具体断言；环境可疑时先 `pytest --co -q` 确认能收集（sandbox 曾因拒绝清理临时目录导致退出码 1、一条结果都拿不到）。
- **判断规则**：**跑测前先 `--co` 确认能收集**；挂了就 `--tb=long -v`，不要 `tail -5` 猜。

### B-32 CI 上 `ModuleNotFoundError: app`
- **症状**：本地 `pytest` 过，CI 上 import 失败。
- **根因**：裸 `pytest` 不把仓库根加进 `sys.path`（取决于 rootdir/conftest 位置）。
- **修法**：CI 和本地都统一用 `python -m pytest`。
- **判断规则**：**pytest 一律 `python -m pytest`**，别用裸命令。

### B-33 测试数据不进仓库
- **规则**：测试数据一律 `tmp_path` 造，**不 commit 真实 HTML/JSON 抓取产物**。
- **判断规则**：新增 fixtures 前问一句"这里有没有真实用户数据/抓取数据？"有就换 tmp_path 伪造。

---

## 5. 字段与契约一致性

### B-34 数据层字段名 ≠ 消费层读的字段名 ★
- **症状**：热度冠军机型名一直是 `—`。
- **根因**：`_brand_row` 输出 `top_model`，insight 层读 `top_product`。
- **修法**：`_brand_row` 同时给两个键（别名），并写测试钉死两个键都存在。
- **判断规则**：**改数据层的字段名/新增字段后，grep 所有消费方**（API / 前端 / insight / 报告模板）。至少 4 个地方会读同一个字段。

### B-35 前端显示 `—`（字段没传下去）
- **症状**：某列全是 `—`。
- **排查顺序**：
  1. 数据层有没有这个键？（B-34）
  2. Pydantic model 有没有声明这个字段？（没声明就被丢掉）
  3. 前端 JS 读的 key 拼对没有？（驼峰 vs 下划线）
- **判断规则**：**加一个端到端可见的字段，要同步改 4 处**：数据层 dict → Pydantic model → 前端渲染 → 测试。做成 checklist 逐项打勾。

### B-36 Pydantic model 漏字段 = 静默丢弃
- **规则**：API 响应模型是**白名单**，没声明的键不会出现在 JSON 里，且不报错。
- **判断规则**：新增返回值字段后，**实际调一次接口看 JSON**，别只看代码。

### B-37 消费方重拼数据，丢掉真实统计值
- **症状**：词云和卖点柱状图的 count 全是 0。
- **根因**：`_to_brand_row_dict` 没用 `_brand_row` 返回的 `top_keywords`（带真实 count/pct），反而用 `kw1/kw2/kw3` 重拼了一个**空 count 的 list**。
- **修法**：`list(row.get("top_keywords", []))` 直接透传。
- **判断规则**：**不要在中间层"重新组装"上游已经算好的数据** —— 重拼必丢字段。要透传。

### B-38 解析日志的正则锚点太松
- **症状**：扫描进度里 current 变成 `"OPPO raw html"`。
- **根因**：`_RE_SCANNING = r"^\[scan\] (.+?) -> "` 只锚了 `->`，把日志行 `[scan] OPPO raw html -> pdd_raw_xxx.html` 也当成了新关键词。
- **修法**：锚死 `-> https?://`（关键词行后面跟的是 URL，文件行后面跟的是文件名）。
- **判断规则**：**正则的右锚点要和左锚点一样严格**。写 `(.+?)` 捕获时，一定要问"它后面必须是什么才算匹配"。

---

## 6. 并发 / 子进程 / 服务

### B-39 `subprocess.PIPE` 写满导致子进程卡死 ★
- **症状**：子进程跑一半不返回，主进程也卡住。
- **根因**：stdout 用 PIPE，缓冲区（通常 64KB）写满后子进程阻塞在 write 上，而父进程在等它结束 —— 死锁。
- **修法**：stdout 重定向到**临时文件**（`tempfile.mkstemp`），父进程轮询读文件尾部（`_read_tail` 取最后 20 行）。
- **判断规则**：**长输出子进程一律用文件，不用 PIPE**。PIPE 只适合输出确定很小的命令。

### B-40 前台跑长任务，最后被截断/无输出
- **症状**：详情页脚本跑 2.5 分钟，前台命令超时被 SIGTERM，一点输出都没有。
- **修法**：`run_in_background` 跑（2.5 分钟正常完成）。
- **判断规则**：**超过 30 秒的任务一律后台跑** + 输出落文件。

### B-41 后台启的服务跨命令就死了
- **症状**：`demo.sh` 启的 uvicorn，下一条命令再访问就 502/连接拒绝。
- **根因**：服务随 shell 会话退出被回收。
- **修法**：用 `run_in_background` 直接起 `uvicorn.run(create_app(), ...)`，让进程脱离当前 shell。
- **判断规则**：需要跨命令存活的服务，用后台任务起，别放在一次性脚本里 `&`。

### B-42 handler 里不能 `.wait()`
- **症状**：`POST /api/scan` 发出后整个 API 不响应别请求。
- **根因**：请求处理器里阻塞等待子进程。
- **修法**：Popen 后**立即返回**，状态靠 `GET /api/scan/status` 轮询（前端 2s 一次）。
- **判断规则**：**HTTP handler 里永不阻塞等待**。长任务 = 提交即返回 + 状态端点 + 前端轮询。

### B-43 重复提交要挡（单飞锁）
- **症状**：连点两次"扫描"，起两个子进程打架。
- **修法**：`is_busy` 标志 + 重复提交返回 **409** 并带上当前任务状态。
- **判断规则**：**任何"一次只能跑一个"的操作，必须有单飞锁 + 明确的冲突响应码**。

### B-44 子进程隔离全局状态
- **决策**：扫描脚本含 playwright/CDP 全局状态，崩了会连累 API 进程 → 用**子进程隔离**。
- **判断规则**：第三方库有全局/进程级状态时，用子进程包一层，别直接 import 进服务进程。

### B-45 回环地址白名单 + 脏参数在 Popen 前挡
- **规则**：
  - CDP URL 只允许回环地址（`_LOOPBACK_HOSTS`），防 SSRF。
  - keyword 校验（非空 / ≤32 / 禁 `/ \ \x00`）**必须在 Popen 之前**完成，不能依赖子进程自己校验。
- **判断规则**：**外部输入先过白名单，再进系统调用**。

---

## 7. 爬虫 / CDP / 反爬

### B-46 先找内嵌 JSON，再写 DOM 提取 ★
- **发现**：拼多多搜索页把完整结果以 JSON 嵌在 `window.rawData` 里（字段名结构化：`goodsName`/`priceInfo`/`salesTip`/`goodsID`）。
- **症状（反例）**：一开始猜 DOM selector，`card_counts` 全 0，但 `visible_text_length` 1599（数据明明在页面上）。
- **修法**：提取前先 `view-source` 找 `window.xxx = {...}` 之类的内嵌数据，比解析 DOM 稳定得多。
- **判断规则**：**接新站点第一步不是写 selector，是先查有没有内嵌 JSON**。

### B-47 内嵌 JSON 要用括号配对提取，不能 split
- **症状**：`rawData` 里含嵌套 `{}` 和字符串里的花括号，`split("{",1)` 截断。
- **修法**：`extract_raw_data` 做**花括号配对 + 字符串感知**（跳过引号内的括号）扫描。
- **判断规则**：**从 HTML 里抠 JSON，必须做括号配对计数，不能用 split/index**。

### B-48 详情页是客户端渲染，要滚动后读 inner_text
- **症状**：详情页 `window.rawData = null`，直接读 HTML 什么都没有。
- **根因**：数据靠 XHR 异步加载后渲染到 DOM。
- **修法**：`connect_over_cdp` → goto → **滚动** → 读 `inner_text` → 正则提取。
- **判断规则**：**列表页读内嵌 JSON，详情页读渲染后的 inner_text** —— 两种页面两种打法，别混用。

### B-49 登录墙分页面：列表页免登录，详情页要登录
- **发现（反直觉）**：拼多多**搜索列表页免登录**能读，商品**详情页要登录**。
- **症状（反例）**：用户以为"每点一个商品都要登录，爬不了"。
- **修法**：**竞品报告只吃列表页数据**（标题/价格/销量/店铺都在列表页），不点进详情 → 绕开登录墙。
- **判断规则**：遇到登录墙，先测绘"**哪些页面不需要登录**"，用不需要的那些页面组出最小可用数据集。

### B-50 `agent-browser install` 下载 Chrome 超时
- **症状**：下载 Chrome 152（193MB）时 `storage.googleapis.com` 超时，重试 3 次都断。
- **根因**：国内网络 + Google 存储不可达。
- **修法**：`playwright.chromium.connect_over_cdp('http://127.0.0.1:9223')` **接管本机已装 Chrome**，不下载浏览器。
- **判断规则**：**国内环境下载浏览器基本不可行**。一律 connect_over_cdp 用本机 Chrome（顺便还能复用用户已登录的 cookie）。

### B-51 CDP 端口冲突 + 独立 profile
- **规则**：携程用 9222，**拼多多用 9223** 避开；profile 用独立目录 `runtime/pdd-cdp-chrome-profile`，**不碰用户日常 Chrome 的登录态**；只绑 `127.0.0.1`。
- **判断规则**：多个 CDP 项目共存 → **端口 + profile 目录都要隔离**。

### B-52 只读原则 + 限流
- **规则**：脚本只有 goto / screenshot / 读文本，**没有 click/input**；品牌之间 sleep；`RateLimiter` 滑动窗口 30次/60s。
- **判断规则**：**调研类脚本永远只读**。要写操作的地方先问 policy 层（本项目 policy 明令禁止登录/下单/支付/关注）。

### B-53 探测失败的提示要给"怎么修"
- **症状**：CDP 没开时 `POST /api/scan` 光报 `503` 一个码，用户不知道下一步。
- **修法**：响应体带 `code: CDP_UNAVAILABLE` + `how_to_fix: "先跑 scripts/start_pdd_cdp_chrome.ps1"`。
- **判断规则**：**错误响应必须包含"用户下一步该做什么"**，不能只给状态码。

---

## 8. Windows / Git Bash / 环境

### B-54 PowerShell 5.1 读 UTF-8 无 BOM 中文乱码
- **症状**：`.ps1` 脚本里的中文变成乱码。
- **修法**：**ps1 脚本全 ASCII**，中文只在注释用英文替代或写到 .md 里。
- **判断规则**：Windows 上的 `.ps1`/`.bat` 一律纯 ASCII；**不要写含非 ASCII 路径的脚本文件**。

### B-55 不要用裸 `python`，用 `sys.executable`
- **症状**：子进程起不来 / 用的是系统 python 而非 venv。
- **修法**：`argv = [sys.executable, script, ...]`。
- **判断规则**：**任何起子进程的场景，解释器路径用 `sys.executable`**。

### B-56 Git Bash 下 `curl` 写文件 exit 23 / size_download 显示 0 ★
- **症状**：`curl -w "%{size_download}"` 显示 0 bytes 且退出码 23，但 `-v` 明明显示 HTTP 200 + 10570 字节。
- **根因**：管道（`| head`）截断时 curl 写 stdout 报错。
- **修法**：用 python `urllib.request` 直读，不走 shell 管道。
- **判断规则**：**Windows/Git Bash 下验证 HTTP 一律用 python urllib，别用 curl**。

### B-57 本机代理把 127.0.0.1 拦成 502 ★
- **症状**：服务端日志显示 `POST 200 OK`，客户端却收到 `502 Bad Gateway`。
- **根因**：系统代理设置拦截了回环地址请求。
- **修法**：`build_opener(ProxyHandler({}))` 显式禁用代理。
- **判断规则**：**代码里访问 localhost 的服务，一律显式禁用代理**。

### B-58 `cmd.exe /c start "" "url"` 阻塞脚本
- **症状**：`demo.sh` 卡在打开浏览器那一步。
- **修法**：`&` 后台化 + `true` 兜底（防止 `set -e` 因 case 分支返回非 0 误退出）。
- **判断规则**：无头/后台环境里**不要用会打开窗口的阻塞命令**。

### B-59 用户在错误目录跑命令
- **症状**：用户在 `C:\Users\shadow>` 用相对路径跑 pytest，报找不到文件。
- **修法**：所有给用户的命令都写**绝对路径 + 先 cd 到项目根**。
- **判断规则**：给用户的命令模板 = `cd <绝对路径> && <命令>`，不要假设 cwd。

### B-60 `runtime/` 未 gitignore，chrome profile 被 commit
- **症状**：`git add -A` 后 commit 卡住（chrome profile 目录里有大文件）。
- **修法**：`.gitignore` 加 `runtime/`，再单独 add 源码文件。
- **判断规则**：**任何运行时产物目录（runtime/ data/ 浏览器 profile）先加 gitignore 再写代码**。

### B-60b venv / 依赖漂移
- **规则**：依赖写在 `requirements-ecommerce.txt`；CI 里 `pip install -r`。新增第三方 import 后**同步更新 requirements**（曾漏 loguru 导致 CI 挂）。
- **判断规则**：**加 import 就检查 requirements**。

---

## 9. 前端 / ECharts / Swagger

### B-61 ECharts 类目轴只显示一部分标签
- **症状**：6 个品牌，x 轴只显示 3 个。
- **根因**：`interval: auto`（默认）自动抽稀，怕中文标签重叠。
- **修法**：`axisLabel: { interval: 0, fontSize: 12 }` 强制全显。
- **判断规则**：**中文类目轴的 ECharts 图表，一律显式写 `interval: 0`**。

### B-62 词云只有 7 个词
- **症状**：词云稀稀拉拉。
- **根因**：`_brand_row` 里 `top_keywords[:3]` 只取 TOP3，6 品牌合并去重后只剩 7 词。
- **修法**：`[:3]` → `[:10]`（上游 `keyword_top_n` 默认 15 足够）→ 词云 15 词。
- **判断规则**：**聚合视图的上游截断要按"合并后总量"倒推**，不能按单条记录定。

### B-63 静态资源不要依赖 CDN
- **症状**：国内网络 CDN 加载 echarts 慢或失败。
- **修法**：`static/vendor/echarts.min.js`（1.03MB）+ wordcloud（16KB）**下载到本地**，离线可用。
- **判断规则**：**生产页面零外部 CDN 依赖**，vendor 目录随仓库走。

### B-64 Swagger 用户填了占位符 `"string"` 导致返回空
- **症状**：用户 Swagger "Try it out" 直接提交，返回 `brands: []` + "找不到品牌 'string' 的 HTML"。服务其实没坏。
- **修法**：`model_config = ConfigDict(json_schema_extra={"examples": [{"brands": ["华为","小米",...]}]})` —— Swagger 自动预填真实示例。
  - 注意：pydantic v2 把它放到 schema 的 `examples`（复数）键，`example`（单数）仍为 null，但 Swagger UI 读 `examples` 足够。
- **判断规则**：**对外 API 必须自带"能跑通的默认示例"**。占位符 `"string"` 是对非技术用户的陷阱。

### B-65 静态目录 mount 前判断存在性
- **规则**：`if _STATIC_DIR.is_dir(): app.mount(...)` —— 避免目录被删时服务起不来。
- **判断规则**：**挂载外部目录/文件前先判存在**，启动期失败比运行期失败难排查。

### B-66 并行对同一文件发多个 Edit = 竞态 ★
- **症状**：`py_compile` 通过（只查语法），运行时 `NameError: ConfigDict` —— import 行被另一次 Edit 的旧内容覆盖。
- **根因**：两个 Edit 并行，各自基于旧文件读-改-写，后写的覆盖先写的。
- **修法**：**对同一文件的多个 Edit 必须串行**（一条消息一个 Edit）。
- **判断规则**：
  1. 同一文件多处修改 → **串行发**。
  2. **`py_compile` 只查语法，不查未定义名** —— 要实际 import / 跑一次才算验证过。

### B-67 前端渲染：空数据不能只 throw
- **症状**：rows 为空时页面白屏/只有一个报错 toast。
- **修法**：空态走专用卡片 `renderEmpty(requested, warnings)` —— 说明原因 + 列出缓存里现有的关键词 + 给出扫描指引；部分失败走黄色警告条 `renderAlert`。
- **判断规则**：**空数据和失败是两种不同状态，都要有专门的 UI**，且都要说清楚"用户下一步能做什么"。

### B-67b ECharts 在隐藏视图里初始化 → 宽度塌陷 ★
- **症状**：切到某个视图，图表挤在左侧一条窄缝里，右侧大片空白（柱状/词云尤其明显）。
- **根因**：容器所在 `.view` 是 `display:none` 时调 `echarts.init()` —— `clientWidth=0`，ECharts 锁死成一个很小的默认宽度。之后切到该视图若不 resize，就一直是小尺寸。
- **隐蔽性**：真实用户点菜单时若恰好带 `resize()` 就看不见；但**首屏即渲染隐藏视图 + 截图脚本直接切 class** 两条路径都会暴露。
- **修法**（根治）：`initChart` 里挂 `ResizeObserver`，容器一拿到真实宽度就 `resize()`：
  ```js
  const ro = new ResizeObserver(() => { if (el.clientWidth > 0) charts[id].resize(); });
  ro.observe(el);
  ```
- **判断规则**：**任何在隐藏容器里初始化的 canvas/chart/地图，都要监容器尺寸补 resize**，别依赖调用方记得调。

### B-68 `connect_over_cdp` 180s 超时的真根因：Chrome 根本不在 ★★
- **症状**：`playwright.chromium.connect_over_cdp("http://127.0.0.1:9223")` 卡满 180s 后超时；`curl /json/version` 时好时坏。
- **误诊经历（代价巨大）**：先怀疑"target 太多握手慢"（实测只有 4 个 target，不是原因）、再怀疑"僵尸 ESTABLISHED 连接占用"（`netstat` 确有旧进程残留，但只是表象）。为绕开超时**手写了一个 websocket 直连 CDP 的脚本**（252 行），等于**架空项目工具**。
- **真根因**：**Chrome 实例不在。** 用 `Start-Process` 从**非交互式会话**（自动化 / agent / 工具宿主）启动的 Chrome 是会话的子进程；会话一结束，Chrome 被 Windows **Job Object 连带回收**。进程没了，playwright 就在对着死端口反复握手 → 180s。
- **验证方法**：进程内调 `IsProcessInJob(GetCurrentProcess(), NULL, &b)` → 输出 `in_job=True`；父进程是 `sandbox-cli.exe`。
- **正确修法**：
  1. 启动 Chrome 的脚本**必须由用户在自己的终端**跑（用户终端不在该 Job 内），或由服务宿主托管；
  2. 脚本内**启动后轮询 `/json/version` 直到 200 才算成功**，别假设 `Start-Process` 返回=就绪；
  3. 脚本加 `--no-first-run --no-default-browser-check`，跳过首启向导阻塞。
- **先量再猜**：连接健康时 `connect_over_cdp` 实测 **0.12s**（4 targets）。任何"连接慢/超时"的判断，先量一遍各阶段耗时，再谈优化。→ [D-11]

### B-69 attach 到用户 Chrome 时不能 `browser.close()` ★
- **症状**：采集脚本跑完，**用户可见的 Chrome 窗口被一起关掉**。
- **根因**：`connect_over_cdp` 拿到的是**用户现有浏览器**的句柄，`browser.close()` 会关闭整个浏览器而不只是断开连接。
- **修法**：attach 场景下**不要调 `browser.close()`**，让连接随 `sync_playwright()` 上下文退出自然断开。`scripts/pdd_cdp_extract.py` / `pdd_detail_enrich.py` 都已修正。
- **判断规则**：**区分 "我启动的浏览器" 和 "我 attach 的浏览器"** —— 前者该关，后者绝不能关。

### B-70 价格整数化不能截断；`priceInfo` ≠ `price` ★
- **症状**：前端价格显示"不明细"，如 ¥1.88 显示成 ¥1、¥126.99 显示成 ¥126。
- **根因 1**：`int(float(pinfo))` 对小数**单向截断**（不是四舍五入）。改为 `int(round(float(pinfo)))`。
- **根因 2**：详情/列表 JSON 里 `priceInfo`（券后价，字符串）与 `price`（整数**分**，原价口径）**可能不一致**（真实样本：`priceInfo="50.4"` vs `price=5960` 分 = ¥59.6）。**不可互换**，固定 `priceInfo` 优先，回退 `price` 时 `int(round(price/100))`。
- **根因 3**：**未登录采集会拿到假价格** —— 免登录访客页面把真实价格替换成 1~7 元的占位值（真实脏文件：40 个耳机商品里 12 个标价 ¥1.80~¥12.60）。这类**产物文件要隔离**（`data/_quarantine/`），不能进报告。
- **判断规则**：**价格的"分/元/字符串"三种表示必须显式归一**，且**必须用已登录会话采集**，否则数据源本身就不可信。→ [D-4]

### B-71 状态反解不能只喂"日志尾部"窗口 ★★
- **症状**：真机点「扫描并生成」→ 扫描**明明成功**(数据已落盘、`data/pdd_raw_*.html` 有了)，前端却显示 **"✗ 扫描失败"** 且**不生成报告**。`/api/scan/status` 里 `succeeded=[]`、`failed=[]`。
- **根因**：`SubprocessScanner._read_tail()` 把日志**截到尾部 20 行**再交给 `_parse_log`。而采集脚本的输出结构是「**成功行在前，几十行 Markdown 报告在后**」—— 实测 32 行日志里 `[scan] {kw}: 样本 N` 在 idx 2~4，**尾部 20 行里一条 `[scan]` 都没有**，于是状态反解拿到空集。
- **实测证据**（2026-09-11）：`pdd_scan_*.log` 共 32 行，`[scan]` 命中 idx 2/3/4；`lines[-20:]` 中 `[scan]` 计数 = **0**。
- **修法**：**判定与展示分开两条路径** —— 判定读全量（`_read_parse_lines()`，仅留 `LOG_PARSE_LINES=5000` 的极端防御上限），展示才截尾（`log_tail = lines[-LOG_TAIL_LINES:]`）。顺带补上"进程退出后，既没成功行也没失败行的 keyword 一律记 failed"，避免前端白等一个永不出现的品牌。
- **判断规则**：**"给人看的窗口" 和 "给程序判定的输入" 绝不能共用同一个截断**。凡是"反解日志/输出推断状态"的地方，先问：**关键信号会不会掉出这个窗口？**

### B-72 服务端"自动拉起 Chrome"的可用边界：宿主必须在 Job 之外
- **症状**：`POST /api/browser/ensure` 在**你自己的终端**里跑服务时能拉起 Chrome；一旦服务进程是被 agent 工具会话拉起的，Chrome 起完**立刻消失**，随后一切 CDP 操作 180s 超时。
- **根因**：agent 工具会话被包在 **Windows Job Object** 里（`IsProcessInJob=True`，kill-on-close）。`ensure()` 再 `Popen` 出的 Chrome 是 Job 的后代 → 宿主会话一结束就连坐回收。**这不是 `ensure` 的 bug，是执行环境约束**。
- **修法/口径**：把"自动起 Chrome"标注为**生产专属能力**（前提：服务由用户自己的终端/服务宿主拉起）；测试一律注入 `StubBrowserController`，CI 零 Chrome；首次真机 E2E 必须由用户在**自己的终端**起 uvicorn。
- **判断规则**：**"能拉起长命进程"这件事高度依赖宿主是否在被回收的 Job 内**。设计自动化前先确认宿主的生命周期，别把环境约束当成代码缺陷去"修"（会诱发出绕道脚本）。→ [D-13]

### B-73 同源前端 + `allow_origins=["*"]` = 无谓的暴露面
- **症状**：无鉴权的本机 API 对任意来源放行 CORS。
- **根因**：前端 `static/index.html` 用**相对路径** `fetch('/api/...')`，页面也由同一服务 `/` 提供 → **永远同源，正常流程根本不触发 CORS**。此时 `["*"]` 不带来任何功能收益，只扩大了暴露面。注释里写的"让 `file://` 打开也能调 API"是**过时理由**（该用法已不再支持）。
- **修法**：白名单收紧到本服务自己的回环地址（`http://127.0.0.1:8001` / `http://localhost:8001`），并在注释里写清"**不要加宽这个列表**"。补 6 条断言（非通配 / 全为回环 / 放行来源回显 / 外部来源不回显 / 外部预检拒绝 / 放行预检通过）。
- **判断规则**：**CORS 白名单是纵深防御，不是功能开关**。凡是"前后端同源"的服务，`*` 都应被视为可删的暴露面；收紧前先确认前端调用方式（相对路径 vs 绝对 URL）。

### B-74 `FileHTMLSource.fetch` 漏了 `(mtime, name)` 双判据 ★
- **症状**：2026-09-11 验收发现——`fetch()` 取最新 HTML 只按 `st_mtime` 排，时间打平时按 glob 文件夹顺序**随机取**，实测取到旧文件。
- **根因**：B-30 的坑在 `find_latest_detail_file` 和 `build_cache_status` 两处都修过，唯独 `app/ecommerce_api.py` 的 `FileHTMLSource.fetch()` 漏了，排序键还是 `key=lambda p: p.stat().st_mtime`（单判据）。
- **修法**：排序键改成 `key=lambda p: (p.stat().st_mtime, p.name)`（与另外两处一致）。
- **测试**：`test_ecommerce_api.py::TestFileHTMLSource::test_picks_latest_by_mtime` 改成**确定性写法**（`os.utime` 显式设不同 mtime，不依赖 `write_text` 的隐式 bump——CI 秒级文件系统下两次 write 可能同 tick）；新增 `test_mtime_tie_falls_back_to_filename` 直接复现"mtime 完全相同 → 按文件名取较新那份"。
- **判断规则**：**B-30 的坑每新增一处"取最新文件"的排序都要一起查**。grep `sorted(...)st_mtime` / `key=lambda p: p.stat()` 找单判据漏网之鱼。`scripts/pdd_detail_enrich.py` / `ecommerce_competitor_report.py` / `ecommerce_brand_compare_html.py` 的 3 处单判据排序也已在同笔提交改为双判据。

### B-75 `check_login` 的 `connect_over_cdp` 没传 `timeout` → 无界卡线程 ★
- **症状**：`GET /api/browser/login-status` 被前端每 3s 轮询一次；CDP 端口活着但浏览器卡死时，`check_login` 里的 attach 会一直占着 threadpool 线程。
- **根因**：`check_login` 调 `p.chromium.connect_over_cdp(self.cdp_url)` **没有传 `timeout`**（B-68 量过握手 0.12s，但那是健康态；卡死态无界）。`_probe_cdp` 有 3s 上限，真 attach 却没设。
- **修法**：新增常量 `CDP_CONNECT_TIMEOUT_MS = 5000`（与脚本里 `urlopen(timeout=5)` 口径一致），`connect_over_cdp(self.cdp_url, timeout=CDP_CONNECT_TIMEOUT_MS)`。Playwright 的 `timeout` 单位是**毫秒**。
- **测试**：`test_ecommerce_browser_ctl.py::test_check_login_passes_connect_timeout` 用 fake 替换 `playwright.sync_api.sync_playwright`（零 Chrome 启动），断言 capture 到 `timeout == CDP_CONNECT_TIMEOUT_MS`。
- **判断规则**：**任何 attach/connect 真实浏览器的调用都必须带显式 `timeout`**，否则单点卡死会拖垮整个轮询端点。`scripts/ecommerce_brand_compare.py`（`/api/scan` 真调用的脚本）的 `connect_over_cdp` 此前声称补了 `timeout=CDP_CONNECT_TIMEOUT_MS=10_000`，但**常量根本没定义**（edit 未落盘），实测 `NameError`；现已改从 `app.ecommerce_browser_ctl` import 该常量（值 **5000**，与 API 层一致），见 B-77。

### B-76 脚本侧复发：`scripts/` 里同样的单判据排序 + 无 timeout connect ★
- **症状**：验收点 B 收尾时只修了 `app/` 里的 `FileHTMLSource.fetch`（B-74）和 `check_login`（B-75），`scripts/` 里同款隐患没动——这是 B-74/B-75 的**第三处复发**。
- **根因**：B-74/B-75 的"判断规则"要求 grep 全仓单判据漏网之鱼，但当时按"非 Web UI 热路径"划掉了脚本，没一并修。
- **修法（同笔小提交）**：
  - 3 处单判据 `st_mtime` 排序 → 双判据 `(mtime, name)`：`scripts/pdd_detail_enrich.py:_find_latest_list_html`、`scripts/ecommerce_competitor_report.py:_find_latest_raw`、`scripts/ecommerce_brand_compare_html.py:_find_latest`。
  - `scripts/ecommerce_brand_compare.py` 的 `connect_over_cdp(cdp_url)` → `connect_over_cdp(cdp_url, timeout=CDP_CONNECT_TIMEOUT_MS)`（**从 `app.ecommerce_browser_ctl` import，值 5000**，与 API 层一致；此前声称"新增常量 10_000"但 edit 未落盘，实为 `NameError`，见 B-77）—— 这是 `/api/scan` 真正起子进程调用的脚本，卡死会让前端永远转圈。
- **判断规则**：**修 `app/` 的同款 bug 时，先 grep 全仓确认 `scripts/` 是否也有同样写法再定 scope**。本次只动了 `/api/scan` 热路径的 `ecommerce_brand_compare.py` 的 timeout（实际是补全 import，纠正之前的 `NameError`）；另两处脚本 connect（`pdd_cdp_extract.py:122` / `pdd_detail_enrich.py:142`）属手工一次性跑、风险低，按用户 scope 暂不动，留待后续。grep `key=lambda p: p.stat().st_mtime` 找所有单判据排序、grep `connect_over_cdp` 找所有 attach 点。

### B-77 `py_compile` 只查语法不查名字 → 无测试覆盖的 `scripts/` 漏出 NameError ★
- **症状**：CI 全绿，但 `/api/scan` 真机一跑就 `NameError: name 'CDP_CONNECT_TIMEOUT_MS' is not defined`，扫描功能整段坏掉。
- **根因**：`scripts/ecommerce_brand_compare.py:289` 的 `connect_over_cdp(cdp_url, timeout=CDP_CONNECT_TIMEOUT_MS)` 引用了一个**既没定义也没 import** 的常量（上一轮想加模块常量，但 edit 没落盘，与 B-66 的"假成功"同源）。而 CI **只跑 `tests/test_ecommerce_*.py`**，根本不 import `scripts/`；`py_compile` 又只查语法、不查未定义名 —— "语法通过 ≠ 代码能跑"，于是假绿。
- **修法**：
  1. 真 bug：`scripts/ecommerce_brand_compare.py` import 块补 `from app.ecommerce_browser_ctl import CDP_CONNECT_TIMEOUT_MS`（值 **5000**，与 API 层 `check_login` 一致），不在脚本里另搞 10_000。
  2. 护栏：CI 新增 `ruff check --select F821 --isolated app scripts` 步骤。`F821` = 未定义名（NameError 隐患），直接兜住这次的坑；`scripts/` 没有测试覆盖，静态检查就是它唯一的兜底。
- **验证**：`ruff check --select F821 --isolated app scripts` 在修之前报 **1 个错**（即本 bug），修之后 **0 错误**；`app/` 与 `scripts/` 其它地方无存量 F821。
- **判断规则（本次判据）**：**"只过了 `py_compile` / 只 import 成功" 不算验证过**。`scripts/` 这种没有测试覆盖的代码，每次改完必须补一次**真实执行**或**静态未定义名检查**（ruff F821 / pyflakes）。凡是"CI 不碰、又没单测"的目录，必须有静态检查兜底，别让 `py_compile` 的绿当安全信号。→ [D-18]
  - **验证必须执行到改动那一行**：`--help` 这种在 argparse 阶段就退出的冒烟**证明不了修复生效**——它走不到 `connect_over_cdp` 那一行。正确做法二选一：① `import` 后解析该名字（`assert mod.CDP_CONNECT_TIMEOUT_MS == 5000`）；② 直接调那个函数并断言参数真的传进去了（用 fake 替换 `playwright.sync_api`，零真浏览器依赖）。本次验收即用 ② 在 `main()` 内跑到第 290 行、断言 `timeout=5000` 真的传给 `connect_over_cdp`。

---

### B-78 热度机型行「机型名」和「价格」来自不同商品（跨商品错配）★

- **症状**：报告"热度机型对比"行里，机型名是**某耳机**（如 `OPPO Enco Free4`），价格却标着**另一台手机**的当天价（如 `¥2677`，实为列表页当天第一名 Reno15），而该耳机真实价才 `¥349`。名字和价格对不上，读者被带偏。
- **根因**：`_brand_row` 把 `detail["top_model"]`（详情页快照机型名，goods_id=A）和 `top.price_cny`（列表页当天第一名价格，goods_id=B）**无条件拼进同一行**。旧注释"详情页机型名跟列表页是同一个 top1"是 **09-10 成立、09-11 就破** 的假设：详情页快照的是 Enco Free4（A），当天列表页 top1 变成了 Reno15（B），根本不是一件商品。
- **修法（已补完）**：热度机型的「机型名 + 价格 + 销量」三者必须锚定**同一 goods_id**，不许各取一半。`_resolve_sales(top, detail, same_goods=...)` 只在 `detail.goods_id == 列表页 top.goods_id` 时才采用详情页单品销量（与展示机型同源，才标"单品销量"）；**不一致**整行锚定列表页 top1（名字+价格+销量全取自它，口径 `shop_total`，自洽）；都没有则名字降级取 detail、价格留空、销量退回详情页快照（整行同源自详情页）。另：详情页快照与列表页可能**跨天**（09-10 快照 / 09-11 列表），`ecommerce_detail_store` 从文件名反推 `snapshot_date`、`pdd_detail_enrich.py` 写入时显式标注；报告"热度机型对比"行对"单品销量"标 `(快照 YYYY-mm-dd)`，并在第四节加"数据时效提示"。
- **验证**：`tests/test_ecommerce_brand_compare.py` 加 6 用例钉死（同 goods_id 混合名价+销量取详情 / 异 goods_id 整行用 top 且**销量不得是详情单品销量** / 无 detail 用 top / 无 top 无 detail 全空 / 快照日期注入兜底）。现状 269 passed（B-78 6 条新增回归，全绿）。
- **判断规则**：详情页快照与列表页当天数据是不同时刻、可能不同商品、甚至跨天的两份数据——**同一行里凡是把两者字段拼在一起的，必须先按 `goods_id` 对齐，对齐失败就只用单一来源（名字/价格/销量三者同源）**。把跨来源字段无脑拼进同一格 = 静默失真，比不修更危险（名价自洽后读者反而失去怀疑销量的理由）。别相信"它俩是同一个 top1"这类会随时间破裂的假设。

### B-79 报告标题写「蓝牙耳机」、实际在比手机（品类一致性 / 搜索词没拼上后缀）★

- **症状**：报告标题是"拼多多蓝牙耳机竞品横向对比报告"，但 20 个样本里 16 个是手机（OPPO Reno15/Find X 等），仅 4 个是耳机；"中位 ¥2699"其实是手机价，整份价位/定位口径失真。根因："品牌+后缀"搜索不能保证品类——耳机品牌（漫步者/QCY）碰巧对，手机品牌（OPPO）一搜就崩。
- **根因**：扫描 `keyword = f"{brand}{args.suffix}"`，当调用方把 `suffix` 传空串（真机那次 `/api/scan {"keywords":["OPPO"],"suffix":""}`），实际搜"OPPO"而非"OPPO蓝牙耳机"；而报告标题**硬编码**"蓝牙耳机"。项目一贯"失败要响"，这次却是**静默失真**——不报错、读者被标题误导。
- **修法**：加**品类一致性检查**。`detect_category_drift(titles)` 把每个样本标题归类（耳机/手机/其他），统计"疑似手机"占比；当疑似手机绝对数 ≥ 3 且占比 ≥ 50% 时，在**报告顶部显式警告**（"N 个有效样本中 M 个标题疑似手机，与预期品类蓝牙耳机不符，口径可能失真"）并提示改用"品牌+蓝牙耳机"。`_brand_row` 算每品牌 `category_drift`，`_render_markdown` 顶部汇总；API 层 `build_report` 把触发品牌汇总进 `global_warnings`，前端 `renderAlert` 自动显示（文案已由"部分数据缺失"改通用"数据质量提醒"）。
- **验证**：`tests/test_ecommerce_brand_compare.py` 加用例钉死（16 手机+4 耳机→triggered / 全耳机→不触发 / 空或阈值不足→不触发 / `_brand_row.category_drift` 字段 / `_render_markdown` 含"品类一致性提醒"）。269 passed。
- **判断规则**：**搜索词带品类后缀时，报告标题/口径必须与"实际抓到的样本品类"对账**。凡是"主题写死 A、数据可能来自 B"的报告，必须加品类一致性校验并在顶部响——别让失真口径静默流进结论。→ 与 B-17/B-19 同属"口径失真要响"家族。

---

## 10. 工程纪律（从 bug 反推）

| # | 规则 | 反例代价 |
|---|---|---|
| D-1 | **每个可验证里程碑单独 commit**，不攒一起 | 接手人 pull 不到；出回归定位不到 commit（B-07 类） |
| D-2 | **P 级任务完成后停下给验收清单**，等确认再开下一段 | 用户错过审视点，方向错了要返工 |
| D-3 | **跑测前先 `--co` 确认能收集** | sandbox 拒绝清理 → 一条结果都拿不到 |
| D-4 | **改数据字段后 grep 所有消费方**（数据层/API/前端/insight/报告） | 前端显示 `—`（B-34） |
| D-5 | **换数据源必须带口径标记** | 拿店铺累计当单品销量（B-17） |
| D-6 | **加 import 就更新 requirements** | CI 挂 |
| D-7 | **长任务后台跑 + 输出落文件** | 超时 SIGTERM 零输出（B-40） |
| D-8 | **对外 API 带可运行示例 + 错误带 how_to_fix** | 用户卡在 Swagger/503 不知道下一步 |
| D-9 | **同一文件多次 Edit 串行** | 改动互相覆盖（B-66） |
| D-10 | **能从数据层取值就别从渲染字符串反解** | markdown 前缀混进结论（B-15） |
| D-11 | **性能/超时问题先量各阶段耗时再猜** | 误诊 CDP 超时 → 造 252 行绕道脚本架空项目（B-68） |
| D-12 | **修工具前先确认工具真的坏了** | Chrome 被 Job 回收当"playwright 超时"修（B-68） |
| D-13 | **绝不架空项目自研工具去写绕道** | 用户直接质问"为什么是你抓，不是我的工具抓"（B-68） |
| D-14 | **模糊 fallback 不如明确报错** | 6 品牌同一份数据（B-20） |
| D-15 | **修完真 bug 就往本文档追加一条** | 同一个坑踩第二次 |
| D-16 | **"给人看的窗口" ≠ "给程序判定的输入"** | 成功行掉出尾部 20 行 → 扫成功被判成失败（B-71） |
| D-17 | **同源服务的 CORS `*` 是可删的暴露面** | 无谓放行任意来源（B-73） |
| D-18 | **无测试覆盖的目录必须有静态检查兜底** | `py_compile` 绿 ≠ 能跑，scripts/ 漏出 NameError 假绿（B-77） |

---

## 11. 复用 Checklist（动手前自检）

### 11.1 新增/修改一个业务数值字段

- [ ] 它**真实语义**是什么？（谁的 / 什么时间范围 / 页面原文怎么写）→ [B-19]
- [ ] 有没有 `*_source` 口径标记一起传？→ [B-17]
- [ ] 数据层 dict → Pydantic model → 前端渲染 → 测试，**四处都改了吗**？→ [B-34][B-35][B-36]
- [ ] 0 值会不会被 `or` 短路掉？→ [B-07]
- [ ] 展示格式化有没有尾零 / 有效数字坑？→ [B-13][B-14]
- [ ] 混口径时有没有做"不混比"？→ [B-17]

### 11.2 接一个新抓取站点

- [ ] 先查有没有**内嵌 JSON**（`window.xxx = {...}`）→ [B-46]
- [ ] 抠 JSON 用**括号配对**，不用 split → [B-47]
- [ ] 客户端渲染？→ 滚动后读 inner_text → [B-48]
- [ ] 测绘**哪些页面免登录**，用免登录的组最小数据集 → [B-49]
- [ ] 用 `connect_over_cdp` 本机 Chrome，**不下载浏览器** → [B-50]
- [ ] 启动 Chrome 后**轮询 `/json/version` 确认 200** 再开始，别假设已就绪 → [B-68]
- [ ] Chrome 启动脚本**由用户终端跑**（工具会话内的子进程会被 Job 回收）→ [B-68]
- [ ] attach 到用户 Chrome 时**不要 `browser.close()`** → [B-69]
- [ ] **必须用已登录会话采集**，未登录的价格/销量是占位假值 → [B-70]
- [ ] 端口 + profile 目录**与其他项目隔离** → [B-51]
- [ ] 抽取正则**至少实测 3 个不同样本** → [B-18]
- [ ] **优先结构化字段**（内嵌 JSON）而非正文正则；正则只作回退 + 标 source → [B-17][B-46]
- [ ] 数值单位（分/元）显式归一，**不截断** → [B-70]
- [ ] 只读 + 限流，写操作交给 policy → [B-52]

### 11.3 新增一个 API 端点

- [ ] 入参校验（非空/长度/禁字符）在**任何系统调用之前** → [B-45]
- [ ] 长任务：提交即返回 + 状态端点，**handler 不阻塞** → [B-42]
- [ ] 单飞锁 + 409 → [B-43]
- [ ] 依赖可注入（`create_app(scanner=, html_source=, cache_dir=)`）→ 测试用 stub，CI 零外部依赖
- [ ] 错误码带 `how_to_fix` → [B-53]
- [ ] `json_schema_extra` 给可运行示例 → [B-64]

### 11.4 改前端视图

- [ ] ECharts 中文类目轴 `interval: 0` → [B-61]
- [ ] 聚合视图的上游截断值够不够 → [B-62]
- [ ] 空态 / 部分失败 / 全失败 三种状态各有 UI → [B-67]
- [ ] **隐藏容器里初始化的图表挂了 ResizeObserver 吗** → [B-67b]
- [ ] 静态资源走本地 vendor → [B-63]
- [ ] 同一文件多次修改**串行发** → [B-66]

### 11.5 写测试

- [ ] 期望写**系统真实行为**，先跑一次看输出 → [B-23]
- [ ] fake 照抄真实 dataclass 全部字段 → [B-28]
- [ ] 能用真实类 + `tmp_path` 就别手搓 → [B-29]
- [ ] "取最新"排序有**第二判据** → [B-30]
- [ ] 命令统一 `python -m pytest` → [B-32]
- [ ] 测试数据不 commit 真实抓取产物 → [B-33]

### 11.6 加"浏览器就绪 / 登录闸门"这类前置

- [ ] **幂等**：`ensure()` 先 `probe()` 再决定起不起，已在跑绝不重复起 → 用调用计数断言
- [ ] **只读红线**：只读 cookie **名**判登录，绝不回传值、绝不代填账号/验证码 → [D-13]
- [ ] **双保险**：自动探测是**加速器**，人工确认是**保证**（假阴性会卡死用户，假阳性会白扫） → [B-71]
- [ ] 放行原因**贯穿可见**（别只在中间态闪一下，会被下一次轮询覆盖）
- [ ] 探测端点**恒 200** 可高频轮询（与 `/api/scan/status` 一致），失败也别打成 500
- [ ] 依赖可注入（`create_app(browser_ctl=...)`），测试走 Stub，CI 零 Chrome
- [ ] **执行环境边界**：宿主要是不在被回收的 Job 内，才能拉起长命 Chrome → [B-72]
- [ ] 同源服务的 CORS 白名单收紧到回环，并写"不要加宽"注释 → [B-73]

---

## 附：本文档的来源

| 来源 | 内容 |
|---|---|
| `.workbuddy/memory/2026-09-06.md` | 携程状态机项目 13 个 bug（第 1/2/4/5 章主要来源） |
| `.workbuddy/memory/2026-09-07.md` | 电商项目 Day1–Day8 全部坑（第 2/3/7/8/9 章主要来源） |
| `.workbuddy/memory/2026-09-08.md` | 销量语义彻底修复 |
| `.workbuddy/memory/2026-09-10.md` | Day9 Phase1/2（缓存可见 + 扫描服务化） |
| `.workbuddy/memory/2026-09-10.md` | CDP 超时真根因（Job Object 回收 Chrome）+ 价格截断/口径（B-68~B-70） |
| `~/.workbuddy/MEMORY.md` | 跨项目硬规则（对应本文档第 10 章 D-1~D-13） |

**上次更新**：2026-09-11（B-78 补完：热度机型行「机型名+价格+销量」三者必须同源——`_resolve_sales` 改为仅在 `same_goods` 时取详情页单品销量，否则整行锚定列表页 top1 月销，杜绝把详情页另一件商品的单品销量拼进同一行；另加详情页快照日期（store 从文件名反推 + 采集脚本显式标注），报告标注"单品销量 (快照 YYYY-mm-dd)"与"数据时效提示"。B-79 品类一致性（detect_category_drift 顶部警告）维持"警告不过滤"）
