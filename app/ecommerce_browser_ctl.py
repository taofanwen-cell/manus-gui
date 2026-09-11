"""浏览器控制层 — 探测 / 按需启动 CDP Chrome + 登录态检测.

为什么单独一层
--------------
``POST /api/scan`` 现在的行为是"CDP 不通就 503 让你自己去开 Chrome"。服务化之后
前端希望**一步到位**: 用户点「扫描并生成」→ 后端保证浏览器就绪 → 过登录闸门 →
再走原有扫描。这一层就是那个"保证浏览器就绪"的职责, 与 :mod:`ecommerce_scan`
(扫描任务管理) 分开 —— 前者管浏览器生命周期, 后者管扫描任务生命周期。

可注入 (与 :class:`app.ecommerce_scan.Scanner` 同一套路)
--------------------------------------------------------
``BrowserController`` 是 Protocol, 生产用 :class:`SubprocessBrowserController`
(真探 CDP + 真 Popen ``start_pdd_cdp_chrome.ps1`` + playwright 读 cookie),
测试用 :class:`StubBrowserController` (零依赖, CI 不需要 Chrome)。

复用的既有资产 (不另起炉灶)
---------------------------
- 启动脚本: ``scripts/start_pdd_cdp_chrome.ps1`` —— 它内部用 ``cmd /c start`` 让
  Chrome 脱离父进程常驻, 并自己轮询 ``/json/version`` 直到就绪才返回。
- 连接方式: ``playwright.chromium.connect_over_cdp`` —— 与 ``pdd_cdp_extract.py``
  / ``pdd_detail_enrich.py`` 完全一致, 零新依赖。

只读红线 (跨项目硬规则 #11)
---------------------------
本模块**只**做两件事: 启动浏览器、**读** cookie 名判断登录态。

- 绝不自动填账号 / 密码 / 验证码, 绝不注入或伪造 cookie, 绝不绕过验证。
- 登录凭证由人在 Chrome 窗口里填, 验证由人过 —— 代码只负责"告诉前端现在登没登"。
- ``check_login`` 只回传 cookie 的**名字**, 永不回传 cookie 的**值**。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]

CDP_URL_ENV = "PDD_CDP_URL"
DEFAULT_CDP = "http://127.0.0.1:9223"
DEFAULT_START_SCRIPT = ROOT / "scripts" / "start_pdd_cdp_chrome.ps1"

#: 回环 CDP 白名单 (与 ecommerce_scan / pdd_detail_enrich 里的约束一致)
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

#: 登录态 cookie 候选名。
#:
#: **2026-09-11 真机实测确认**(不再是猜测): 已登录的 CDP Chrome 里稳定出现
#: ``PDDAccessToken`` / ``pdd_user_id`` / ``pdd_user_uin`` 三个 —— 前两个 pdd_* 是
#: 登录身份, ``PDDAccessToken`` 是访问令牌。``pdd_uid`` 保留为兼容别名, 暂未在实测
#: 样本中出现。若日后 PDD 改名, ``check_login`` 会把**实际读到的** cookie 名回显到
#: ``GET /api/browser/login-status``, 照着实测结果改这份表即可。
LOGIN_COOKIE_CANDIDATES = (
    # —— 2026-09-11 真机实测命中的 (已登录的 CDP Chrome 里稳定出现) ——
    "PDDAccessToken",
    "pdd_user_id",
    "pdd_user_uin",
    # —— 兼容别名: 实测样本里未出现, 保留以防不同账号态/旧版命名 ——
    "pdd_uid",
    "PASS_ID_TOKEN",
)

#: 回显 cookie 名时, 只挑 PDD 域的 (避免把整包 cookie 名吐给前端)
_PDD_DOMAIN_HINTS = ("yangkeduo", "pinduoduo")

#: 回显上限 —— 只是给人看"实际叫什么", 不是做数据分析
MAX_COOKIE_NAMES_ECHOED = 50

#: 启动后等待 CDP 就绪的上限 (ps1 自己会轮询 30s, 这里给一点余量)
DEFAULT_LAUNCH_TIMEOUT_S = 30.0
DEFAULT_POLL_INTERVAL_S = 1.0

#: ``connect_over_cdp`` 的连接超时 (毫秒, 与 Playwright 其它 timeout 同单位).
#: 本端点被前端 3s 轮询一次, 若 CDP 端口活着但浏览器卡死, 无界的 attach 会一直
#: 占着 threadpool 线程 —— 用 5s 与 scripts 里的 ``urlopen(timeout=5)`` 口径一致.
CDP_CONNECT_TIMEOUT_MS = 5000

HOW_TO_FIX_MANUAL = (
    "确认已安装 Chrome; 或在**你自己的终端**里手动跑 "
    "scripts/start_pdd_cdp_chrome.ps1 后重试。"
)


@dataclass
class EnsureResult:
    """``ensure()`` 的结果 —— ``POST /api/browser/ensure`` 的响应体."""

    ready: bool
    #: 这次调用**是否真的启动了** Chrome (False = 本来就在跑, 幂等命中)
    launched: bool = False
    msg: str = ""
    how_to_fix: str = ""

    def to_dict(self) -> dict:
        return {
            "ready": self.ready,
            "launched": self.launched,
            "msg": self.msg,
            "how_to_fix": self.how_to_fix,
        }


@dataclass
class LoginStatus:
    """``check_login()`` 的结果 —— ``GET /api/browser/login-status`` 的响应体."""

    logged_in: bool
    #: ``"cookie"`` = 按 cookie 判的 / ``"none"`` = CDP 都没起 / ``"unknown"`` = 探测异常
    method: str = "none"
    cdp_ready: bool = False
    #: 实测读到的 PDD cookie **名**(不是值) —— 用来把上面的候选表钉成事实
    cookie_names: list[str] = field(default_factory=list)
    msg: str = ""

    def to_dict(self) -> dict:
        return {
            "logged_in": self.logged_in,
            "method": self.method,
            "cdp_ready": self.cdp_ready,
            "cookie_names": list(self.cookie_names),
            "msg": self.msg,
        }


class BrowserController(Protocol):
    """浏览器控制协议 —— 生产/测试两实现, 端点只依赖这个接口."""

    def probe(self) -> tuple[bool, str]:
        """探 CDP 是否已就绪 (不做任何启动动作)."""
        ...

    def ensure(self) -> EnsureResult:
        """保证浏览器就绪: 先 probe, 没起才启动, 然后等就绪. **必须幂等**. """
        ...

    def check_login(self) -> LoginStatus:
        """读 cookie 判断登录态 (只读, 不导航, 不触发风控)."""
        ...


def _check_loopback(cdp_url: str) -> str | None:
    """回环校验; 不合法返回错误信息, 合法返回 ``None``。"""
    parsed = urlparse(cdp_url)
    if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
        return f"CDP 地址不合法 (只允许回环地址): {cdp_url}"
    return None


def _probe_cdp(cdp_url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """探 ``/json/version``. 与 ``SubprocessScanner.probe`` 同一实现口径."""
    bad = _check_loopback(cdp_url)
    if bad:
        return False, bad
    opener = build_opener(ProxyHandler({}))  # 本机代理会把 127.0.0.1 拦成 502
    try:
        with opener.open(f"{cdp_url}/json/version", timeout=timeout) as resp:
            info = resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError) as exc:
        return False, f"连不上 CDP Chrome ({cdp_url}): {exc}"
    if "Browser" not in info:
        return False, f"CDP 返回内容异常: {info[:80]}"
    return True, "ok"


def _pick_login_cookie_names(cookies: list[dict], candidates: tuple[str, ...]) -> list[str]:
    """挑出"跟登录有关"或"属于 PDD 域"的 cookie **名**(去重 + 排序 + 截断)。

    只取 ``name``, 永不取 ``value`` —— 见模块顶部的只读红线。
    """
    picked: set[str] = set()
    for c in cookies:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        domain = (c.get("domain") or "").lower()
        if name in candidates or any(h in domain for h in _PDD_DOMAIN_HINTS):
            picked.add(name)
    return sorted(picked)[:MAX_COOKIE_NAMES_ECHOED]


class SubprocessBrowserController:
    """生产实现: 探 CDP → 按需 Popen 启动脚本 → 轮询就绪; 读 cookie 判登录态.

    ``__init__`` 里**不做任何 I/O** —— 只记参数。真正的探测/启动都在方法里,
    这样 ``create_app()`` 建默认实例时不会连网络。
    """

    def __init__(
        self,
        *,
        cdp_url: str = DEFAULT_CDP,
        start_script: Path | str = DEFAULT_START_SCRIPT,
        launch_timeout_s: float = DEFAULT_LAUNCH_TIMEOUT_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        login_cookie_names: tuple[str, ...] = LOGIN_COOKIE_CANDIDATES,
        cwd: Path | str | None = None,
    ):
        self.cdp_url = cdp_url.rstrip("/")
        self.start_script = Path(start_script)
        self.launch_timeout_s = launch_timeout_s
        self.poll_interval_s = poll_interval_s
        self.login_cookie_names = tuple(login_cookie_names)
        self.cwd = Path(cwd) if cwd else ROOT
        #: 最近一次由本实例拉起的启动进程 (仅用于排查, 不 wait)
        self._launch_proc: subprocess.Popen | None = None

    # -- 探活 ---------------------------------------------------------------
    def probe(self) -> tuple[bool, str]:
        return _probe_cdp(self.cdp_url)

    # -- 幂等保证浏览器就绪 -------------------------------------------------
    def ensure(self) -> EnsureResult:
        ok, msg = self.probe()
        if ok:
            # 幂等命中: 已经在跑就绝不再起第二个 (重复起不仅没意义, 同 profile
            # 二次启动还会触发 Chrome 的实例 handoff, 徒增不确定)
            return EnsureResult(ready=True, launched=False, msg="CDP Chrome 已在运行")

        if not self.start_script.is_file():
            return EnsureResult(
                ready=False,
                launched=False,
                msg=f"启动脚本不存在: {self.start_script}",
                how_to_fix=HOW_TO_FIX_MANUAL,
            )

        try:
            self._launch_proc = subprocess.Popen(  # noqa: S603 - 参数为固定脚本路径
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self.start_script),
                ],
                cwd=str(self.cwd),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
        except OSError as exc:
            return EnsureResult(
                ready=False,
                launched=False,
                msg=f"启动 Chrome 失败: {exc}",
                how_to_fix=HOW_TO_FIX_MANUAL,
            )

        # 启动脚本自己也会轮询端口, 但我们不能只信它 —— 直接自己探到就绪为止
        deadline = time.monotonic() + self.launch_timeout_s
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval_s)
            ok, _ = self.probe()
            if ok:
                return EnsureResult(
                    ready=True,
                    launched=True,
                    msg="已启动 CDP Chrome, 请在弹出的窗口里登录拼多多",
                )

        return EnsureResult(
            ready=False,
            launched=True,
            msg=f"已尝试启动 Chrome, 但 {self.launch_timeout_s:.0f}s 内 CDP 端口仍未就绪",
            how_to_fix=HOW_TO_FIX_MANUAL,
        )

    # -- 登录态 -------------------------------------------------------------
    def check_login(self) -> LoginStatus:
        ok, msg = self.probe()
        if not ok:
            return LoginStatus(logged_in=False, method="none", cdp_ready=False, msg=msg)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - 依赖缺失是环境问题
            return LoginStatus(
                logged_in=False, method="unknown", cdp_ready=True, msg=f"playwright 不可用: {exc}"
            )

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(
                    self.cdp_url, timeout=CDP_CONNECT_TIMEOUT_MS
                )
                # 没有 context 就说明这个 Chrome 里根本没登录态, 不必新建一个空的
                if not browser.contexts:
                    return LoginStatus(
                        logged_in=False, method="cookie", cdp_ready=True, msg="没有可用的浏览器上下文"
                    )
                context = browser.contexts[0]
                cookies = context.cookies()
                # 注意: 不要 browser.close() —— 这是 attach 到用户 Chrome 的连接,
                # close() 会把用户可见的窗口一起关掉 (连接随 sync_playwright 退出断开)
        except Exception as exc:  # noqa: BLE001 - 探测失败不该把端点打成 500
            return LoginStatus(
                logged_in=False, method="unknown", cdp_ready=True, msg=f"读取 cookie 失败: {exc}"
            )

        names = _pick_login_cookie_names(cookies, self.login_cookie_names)
        hit = [n for n in names if n in self.login_cookie_names]
        if hit:
            return LoginStatus(
                logged_in=True,
                method="cookie",
                cdp_ready=True,
                cookie_names=names,
                msg=f"检测到登录态 cookie: {', '.join(hit)}",
            )
        return LoginStatus(
            logged_in=False,
            method="cookie",
            cdp_ready=True,
            cookie_names=names,
            msg=(
                "未检测到 PDD 登录 cookie。"
                + (f" 当前 PDD 域 cookie: {', '.join(names[:12])}" if names else " 当前无 PDD 域 cookie")
            ),
        )


class StubBrowserController:
    """离线测试用实现: 不 Popen, 不连 CDP, 结果可脚本化。

    ``ensure_ok``  启动是否成功 (probe 不通时才会用到)
    ``relaunch``   强制 ``probe_ok=False`` 的情形下也报 ready (用于造幂等/失败两种分支)
    """

    def __init__(
        self,
        *,
        probe_ok: bool = True,
        ensure_ok: bool = True,
        logged_in: bool = False,
        cookie_names: tuple[str, ...] = (),
        msg: str = "stub",
    ):
        self.probe_ok = probe_ok
        self.ensure_ok = ensure_ok
        self.logged_in = logged_in
        self.cookie_names = tuple(cookie_names)
        self.msg = msg
        #: 调用计数 —— 用来断言幂等性 (已就绪时绝不再启动)
        self.probe_calls = 0
        self.ensure_calls = 0
        self.login_calls = 0

    def probe(self) -> tuple[bool, str]:
        self.probe_calls += 1
        if self.probe_ok:
            return True, "ok"
        return False, "连不上 CDP Chrome (http://127.0.0.1:9223): stub 模拟未启动"

    def ensure(self) -> EnsureResult:
        self.ensure_calls += 1
        if self.probe_ok:
            return EnsureResult(ready=True, launched=False, msg="CDP Chrome 已在运行")
        if self.ensure_ok:
            return EnsureResult(ready=True, launched=True, msg="stub 已启动 Chrome")
        return EnsureResult(
            ready=False,
            launched=True,
            msg="stub 启动失败",
            how_to_fix=HOW_TO_FIX_MANUAL,
        )

    def check_login(self) -> LoginStatus:
        self.login_calls += 1
        if not self.probe_ok:
            return LoginStatus(logged_in=False, method="none", cdp_ready=False, msg="CDP 未运行")
        return LoginStatus(
            logged_in=self.logged_in,
            method="cookie",
            cdp_ready=True,
            cookie_names=list(self.cookie_names),
            msg=self.msg,
        )


if __name__ == "__main__":  # 手动排查用: python -m app.ecommerce_browser_ctl
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ctl = SubprocessBrowserController()
    ready, message = ctl.probe()
    print(json.dumps({"probe_ok": ready, "msg": message}, ensure_ascii=False, indent=2))
    if ready:
        print(json.dumps(ctl.check_login().to_dict(), ensure_ascii=False, indent=2))
