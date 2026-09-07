"""拼多多竞品调研 URL 构造（只读路线，无任何写操作）。

设计来源
--------
复用 ``app/ctrip_url_query.py`` 的 URL 路由思路：直接拼搜索页 / 商品详情页
URL，跳过"打开首页 → 点搜索框 → 输入关键词 → 回车"的完整表单状态机。

为什么竞品调研也走 URL 路线
----------------------------
1. **竞品调研是纯读任务** —— 只需要 go_to_url + extract_content，
   不需要任何 click_element / input_text。URL 路线天然只走 go_to_url，
   与 "只读不写" 政策层完全吻合。
2. **sandbox 可端到端验证** —— URL 构造是纯逻辑，不需要真实 Chrome / CDP。
   采集解析部分需要真浏览器，但 URL 层在 CI 就能全量跑。
3. **不碰用户自己的店铺** —— 只看竞品公开搜索页，零操作风险。
   这是本项目与"批量上架助手"的根本区别：上架要写，调研只读。

关于 URL 参数的诚实说明
------------------------
拼多多未公开 URL schema 文档，以下参数分两类：

* **已确认可用**（移动端 H5，长期稳定）：
  - 搜索页 ``https://mobile.yangkeduo.com/search_result.html?search_key=<kw>``
  - 商品页 ``https://mobile.yangkeduo.com/goods.html?goods_id=<id>``
* **未验证 / 可能失效**：分页 ``page``、排序 ``sort`` 等参数。
  本模块**默认不传**这些参数；如需使用请先在本机真浏览器验证，
  验证通过后把结论写回本文件的 docstring + 单测。

公开 API
--------
- :func:`build_search_url` —— 搜索页 URL
- :func:`build_goods_url` —— 商品详情页 URL
- :func:`parse_goods_id` —— 从各种 URL 形态抠出商品 ID
- :func:`extract_keyword` —— 从自然语言查询里抠出搜索关键词
- :class:`SearchParams` —— 搜索参数数据类
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

# 移动端 H5 入口：结构化比 PC 端好，反爬压力也小得多。
PDD_MOBILE_BASE = "https://mobile.yangkeduo.com"
PDD_SEARCH_PATH = f"{PDD_MOBILE_BASE}/search_result.html"
PDD_GOODS_PATH = f"{PDD_MOBILE_BASE}/goods.html"

# 允许访问的域名（与 app/ecommerce_policy.py 保持一致）。
ALLOWED_PDD_HOSTS = (
    "pinduoduo.com",
    "yangkeduo.com",
    "pdd.net",
)

# 从 URL 里抠 goods_id 的几种形态：
#   1. goods.html?goods_id=123456
#   2. goods.html?goods_id=123456&other=x
#   3. /goods/123456.html
#   4. goods2.html?goods_id=123456
_GOODS_ID_PATTERNS = (
    re.compile(r"[?&]goods_id=(\d+)"),
    re.compile(r"/goods\d*\.html\?.*?goods_id=(\d+)"),
    re.compile(r"/goods/(\d+)"),
)


@dataclass(frozen=True)
class SearchParams:
    """拼多多搜索参数。

    Attributes
    ----------
    keyword:
        搜索关键词，必填。中英文均可，会被 urlencode。
    page:
        页码，**未验证参数**，默认 1。拼多多移动端多为无限下拉而非翻页，
        传 page 未必生效；要用请先真机验证。
    sort:
        排序方式，**未验证参数**，默认 None（综合排序）。
        已知传闻值：``sales``(销量) / ``price_asc``(价升) / ``price_desc``(价降)——
        **均未实测，不要直接用于生产**。
    opt:
        额外查询参数，会合并进 query string。需要真机验证过才填。
    """

    keyword: str
    page: int = 1
    sort: Optional[str] = None
    opt: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        """校验参数；不合法直接抛错（不做静默兜底）。

        为何不静默兜底：搜索关键词写错 = 调研了一整个错误类目，
        报告全废。宁可启动即失败，也不要产出看起来正常的垃圾报告。
        """
        if not self.keyword or not self.keyword.strip():
            raise ValueError("keyword 不能为空")
        if self.page < 1:
            raise ValueError(f"page 必须 >= 1, 收到 {self.page}")


def build_search_url(params: SearchParams, *, extra: Optional[dict[str, str]] = None) -> str:
    """构造拼多多搜索页 URL。

    Examples
    --------
    >>> build_search_url(SearchParams("蓝牙耳机"))
    'https://mobile.yangkeduo.com/search_result.html?search_key=%E8%93%9D%E7%89%99%E8%80%B3%E6%9C%BA'
    """
    params.validate()
    query: dict[str, str] = {"search_key": params.keyword.strip()}
    if params.sort:
        query["sort"] = params.sort
    if params.page > 1:
        # 仅在非首页才带 page，避免传未验证参数污染最常见的调用路径。
        query["page"] = str(params.page)
    if params.opt:
        query.update(params.opt)
    if extra:
        query.update(extra)
    return f"{PDD_SEARCH_PATH}?{urlencode(query)}"


def build_goods_url(goods_id: str | int) -> str:
    """构造商品详情页 URL。

    Examples
    --------
    >>> build_goods_url(123456789)
    'https://mobile.yangkeduo.com/goods.html?goods_id=123456789'
    """
    raw = str(goods_id).strip()
    if not raw.isdigit():
        raise ValueError(f"goods_id 必须是纯数字, 收到 {goods_id!r}")
    return f"{PDD_GOODS_PATH}?{urlencode({'goods_id': raw})}"


def parse_goods_id(url: str) -> Optional[str]:
    """从 URL 里抠出商品 ID；抠不到返回 None。

    为何需要：搜索页解析出来的商品链接形态不统一（移动端 / PC 端 /
    带推广参数的短链），统一走这个函数能避免上层各写各的正则。
    """
    if not url:
        return None
    for pattern in _GOODS_ID_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


def host_is_allowed(url: str) -> bool:
    """判断 URL 是否属于拼多多域名。"""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith(f".{h}") for h in ALLOWED_PDD_HOSTS)


# 自然语言查询里表示"我要搜索"的引导词，抠关键词时要剥掉。
# 注意按**完整短语**列出（"帮我调研一下" 而非只"调研"），否则剥不干净
# 会留下"一下"/"帮我" 等残留词污染关键词（曾踩坑）。
_KEYWORD_LEADING_WORDS = (
    "帮我调研一下",
    "帮我看一下",
    "帮我搜一下",
    "帮我查一下",
    "帮我分析一下",
    "帮忙调研一下",
    "帮忙搜一下",
    "帮忙查一下",
    "帮我调研",
    "帮我分析",
    "帮我搜",
    "帮我查",
    "调研一下",
    "搜一下",
    "查一下",
    "搜索",
    "查询",
    "调研",
    "分析",
    "我想看",
    "看一下",
    "看看",
    "找",
    "搜",
    "查",
)
# 尾部的任务性废话，同样要剥掉。
_KEYWORD_TRAILING_WORDS = (
    "的竞品",
    "竞品分析",
    "竞品调研",
    "竞品",
    "怎么样",
    "有哪些",
    "有哪些品牌",
    "市场情况",
    "行情",
    "报告",
)


def extract_keyword(query: str) -> Optional[str]:
    """从自然语言查询里抠出搜索关键词。

    支持的样式
    ----------
    - ``调研一下蓝牙耳机的竞品``        → ``蓝牙耳机``
    - ``帮我搜 蓝牙耳机``               → ``蓝牙耳机``
    - ``蓝牙耳机                    ``   → ``蓝牙耳机``（本身就是关键词）
    - ``我想看看拼多多上蓝牙耳机有哪些`` → ``蓝牙耳机``

    设计原则
    --------
    * **keyword 按长度倒序剥离**（跨项目硬规则第 8 条）：
      ``"查" in "查一下"`` 为真，若按字典序会剥不干净。
    * **不做模糊兜底**：抠不出来返回 None，让上层决定是报错还是追问用户，
      绝不猜一个关键词去跑了整份报告。
    """
    if not query:
        return None
    text = query.strip()
    if not text:
        return None

    for word in sorted(_KEYWORD_LEADING_WORDS, key=len, reverse=True):
        if text.startswith(word):
            text = text[len(word):].strip()
            break

    for word in sorted(_KEYWORD_TRAILING_WORDS, key=len, reverse=True):
        if text.endswith(word) and len(text) > len(word):
            text = text[: -len(word)].strip()
            break

    # 去掉残留的助词 / 标点
    text = text.strip("的了啊呢吧吗，,。.、 ")
    return text or None


def build_search_url_from_query(query: str) -> Optional[str]:
    """从自然语言查询直接构造搜索 URL；抠不出关键词返回 None。"""
    keyword = extract_keyword(query)
    if not keyword:
        return None
    return build_search_url(SearchParams(keyword=keyword))
