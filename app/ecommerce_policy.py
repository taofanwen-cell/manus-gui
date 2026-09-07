"""电商竞品调研政策层 — "AI 只读不写"。

设计来源
--------
复用 ``app/ctrip_policy.py:decision()`` 的 hook 点设计，但把拦截面从
"防越权下单" 升级为 "防越权写入"。

为什么竞品调研的政策层比机票查询的更严
--------------------------------------
1. **写操作零容忍**：调研时如果误点了"加入购物车 / 收藏 / 关注"按钮，
   会被拼多多风控 / IP 标记，反而干扰后续采集。
2. **rate limit 必加**：高频访问会触发拼多多滑块 / 验证码。
   政策层把"每分钟最多 30 次"作为强约束，不让 agent 跑飞。
3. **域名白名单**：只允许 pinduoduo.com / yangkeduo.com / pdd.net，
   避免误跳到第三方推广站污染数据源。

为何这个模块对"AI 含金量"很重要
-------------------------------
"AI 只读不写"是 AI Infra 圈 2025-2026 年的核心议题
（Anthropic / OpenAI / browser-use 全部在做）。把"防写入"从一句
口头承诺变成可执行的代码拦截，是本项目从"应用"升"框架"的关键一步。

公开 API
--------
- :func:`decision` —— 单次 action 是否允许
- :class:`RateLimiter` —— 限流器（防止 agent 跑飞触发风控）
- :func:`host_is_allowed` —— 域名白名单判断
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlparse

# 与 app/ecommerce_url_query.py 保持一致。
ALLOWED_PDD_HOSTS = (
    "pinduoduo.com",
    "yangkeduo.com",
    "pdd.net",
)

# 允许的动作：纯读 + 必要的等待/滚动。
# 注意：scroll_down / scroll_up 看似无害，但高频触发也会被风控，
# 所以在 RateLimiter 里统一限速。
READ_ONLY_ACTIONS = frozenset({
    "go_to_url",
    "go_back",
    "refresh",
    "wait",
    "extract_content",
    "scroll_down",
    "scroll_up",
})

# 显式禁止的写操作（哪怕看起来无害）：
# - click_element: 误点 "加入购物车" / "关注" / "收藏" 等
# - input_text / focus_element / send_keys: 误填表单
# - upload_file / paste_image: 误上传
# - gui_action: 坐标点击，绕过 policy 拦截（参见跨项目硬规则第 7 条）
# - open_tab / web_search: 跳走，污染数据源
BLOCKED_WRITE_ACTIONS = frozenset({
    "click_element",
    "input_text",
    "focus_element",
    "select_date",
    "send_keys",
    "upload_file",
    "paste_image",
    "gui_action",
    "open_tab",
    "close_tab",
    "close_tabs",
    "switch_tab",
    "web_search",
    "execute_js",  # 即使看起来"只读"也禁，因为可能 DOM mutation
})

# 关键词黑名单：如果 action 文本里出现这些词（哪怕不在写操作白名单里），
# 也一律拒绝。防 LLM 看到 "立即购买" "加购" 这类按钮误识别为 "可点"。
# 这些词不是给人类用户看的，是给 LLM element classifier 看的。
BLOCKED_TEXT_TERMS = frozenset({
    "立即购买",
    "立即下单",
    "加入购物车",
    "加购",
    "去购买",
    "去支付",
    "去付款",
    "提交订单",
    "确认订单",
    "去结算",
    "关注",
    "收藏",
    "分享",
    "举报",
    "登录",
    "注册",
    "联系客服",
    "立即开团",
    "去拼单",
    "发起拼单",
})


def host_is_allowed(url: str) -> bool:
    """域名白名单：只允许拼多多主域 / 移动域 / 静态资源域。"""
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(host == h or host.endswith(f".{h}") for h in ALLOWED_PDD_HOSTS)


def decision(
    action: str,
    *,
    url: str | None = None,
    text: str | None = None,
    current_url: str | None = None,
) -> tuple[bool, str]:
    """判断单次 action 是否被允许。

    Parameters
    ----------
    action:
        浏览器操作名（"go_to_url" / "click_element" / ...）
    url:
        该 action 指向的 URL（若有），用于域名白名单校验。
    text:
        该 action 涉及的文本（按钮文字 / 输入内容），用于关键词黑名单。
    current_url:
        当前所在页面 URL（用于"导航后是否要校验"），可选。

    Returns
    -------
    (allowed, reason) 二元组。allowed=False 时 reason 写明原因，
    上层可直接展示给用户 / 写进 trace。
    """
    if action in BLOCKED_WRITE_ACTIONS:
        return False, f"竞品调研只读模式禁止写操作: {action}"

    if action not in READ_ONLY_ACTIONS:
        return False, f"竞品调研只读模式未识别动作: {action}"

    candidate = url or current_url or ""
    if candidate and not host_is_allowed(candidate):
        return False, f"竞品调研只允许访问拼多多域, 拒绝: {candidate}"

    lowered = (text or "").lower()
    if any(term in lowered for term in BLOCKED_TEXT_TERMS):
        return False, f"竞品调研禁止涉及交易/关注/登录文本: {text!r}"

    return True, "allowed"


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


@dataclass
class RateLimiter:
    """滑动窗口限流器。

    为何需要：拼多多移动端 H5 在 60 秒内连续请求 30+ 次会触发滑块。
    政策层通过 ``RateLimiter.acquire()`` 把"每分钟最多 N 次"做成可执行约束。

    为何不用 token bucket：
    - token bucket 突发友好，但竞品调研是匀速采集，不需要突发
    - 滑动窗口更直观："最近 60 秒请求数"，能直接讲清楚给用户听
    - 测试容易：mock ``time.monotonic`` 即可

    Attributes
    ----------
    max_per_window:
        窗口内最大允许次数。
    window_seconds:
        窗口大小（秒）。
    """

    max_per_window: int = 30
    window_seconds: float = 60.0
    _timestamps: deque[float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.max_per_window <= 0:
            raise ValueError("max_per_window 必须 > 0")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds 必须 > 0")
        object.__setattr__(self, "_timestamps", deque())

    def acquire(self, *, now: float | None = None) -> bool:
        """尝试获得一次配额；返回 True 表示允许，False 表示被限流。

        Parameters
        ----------
        now:
            当前时间戳（秒）。若为 None 使用 ``time.monotonic()``。
            传 ``now`` 是为了测试时可重现。
        """
        if now is None:
            now = time.monotonic()
        # 弹出窗口外的旧时间戳
        cutoff = now - self.window_seconds
        ts = self._timestamps
        while ts and ts[0] < cutoff:
            ts.popleft()
        if len(ts) >= self.max_per_window:
            return False
        ts.append(now)
        return True

    def remaining(self, *, now: float | None = None) -> int:
        """查看当前窗口还剩多少次配额（用于在 UI 提示用户）。"""
        if now is None:
            now = time.monotonic()
        cutoff = now - self.window_seconds
        ts = self._timestamps
        while ts and ts[0] < cutoff:
            ts.popleft()
        return max(0, self.max_per_window - len(ts))
