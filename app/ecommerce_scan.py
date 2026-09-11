"""扫描任务管理 (Day 9 Phase 2) — 把 CDP 扫描脚本服务化, 但不阻塞事件循环.

设计要点
--------
1. **子进程, 不走库内 import**: 扫描脚本含 playwright/CDP 全局状态, 进程隔离后
   它崩了不会连累 API 进程; 同时保持"脚本可独立在命令行跑"的现有用法。
2. **单飞锁 (single-flight)**: 同一时刻只允许一个扫描任务。并发扫描既容易触发
   风控, 也会并发写坏 ``data/``。重复提交返回 409 + 当前任务状态。
3. **可注入**: ``Scanner`` 是 Protocol, 生产用 :class:`SubprocessScanner` (真 Popen),
   测试用 :class:`StubScanner` (秒完成, 零 CDP 依赖) —— CI 不需要 Chrome。
4. **不阻塞**: ``start()`` 只 Popen 就返回; 状态靠 ``snapshot()`` 轮询。
   输出重定向到临时文件 (而不是 PIPE), 避免管道写满把子进程卡死。

只读红线 (跨项目硬规则 #11)
---------------------------
本模块只 Popen 既有的只读扫描脚本, 不新增任何点击/下单/写操作/cookie 外传路径。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

#: 任务状态
STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

#: 状态接口回传的日志尾部行数 (**只影响展示**)
LOG_TAIL_LINES = 20

#: 状态判定要读多少行。**必须读全量**: 脚本先打 ``[scan] {kw}: 样本 N`` 成功行,
#: 再打几十行 Markdown 报告 —— 成功行会掉出尾部 20 行窗口, 只解析尾部就会把
#: "扫成功" 误判成 "什么都没发生"(前端据此显示"扫描失败"且不生成报告)。
#: 这个上限只是防御极端情况 (单品牌 ~32 行, 实际远远够)。
LOG_PARSE_LINES = 5000

#: 单个 keyword 最大长度 (防注入垃圾 / 超长 URL)
MAX_KEYWORD_LEN = 32

#: 回环 CDP 白名单 (与 pdd_detail_enrich / brand_compare 里的约束一致)
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

#: 脚本输出里标识"这个 keyword 扫完了/失败了"的行前缀
#: 注意必须锚到 ``-> http`` 上: 同一轮里还有 ``[scan] {kw} raw html -> pdd_raw_xxx.html``
#: 这行, 只写 ``->`` 会把 current 解析成 "OPPO raw html"
_RE_SCANNING = r"^\[scan\] (.+?) -> https?://"
_RE_OK = r"^\[scan\] (.+?): 样本 "
_RE_FAIL = r"^\[scan\] (.+?) 解析出 0 个商品"


@dataclass
class ScanState:
    """一次扫描任务的快照 (``GET /api/scan/status`` 的响应体)."""

    status: str = STATUS_IDLE
    job_id: str | None = None
    keywords: list[str] = field(default_factory=list)
    #: 当前正在扫的 keyword (从日志尾部推断)
    current: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    #: stdout 尾部 N 行
    log_tail: list[str] = field(default_factory=list)
    succeeded: list[str] = field(default_factory=list)
    #: [(keyword, 原因)] —— 0 商品大概率是登录态失效, 不是"真没数据"
    failed: list[dict] = field(default_factory=list)
    returncode: int | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "job_id": self.job_id,
            "keywords": list(self.keywords),
            "current": self.current,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log_tail": list(self.log_tail),
            "succeeded": list(self.succeeded),
            "failed": list(self.failed),
            "returncode": self.returncode,
        }


class Scanner(Protocol):
    """扫描器协议 — 生产/测试两实现, 端点只依赖这个接口."""

    def probe(self) -> tuple[bool, str]:
        """探活 (生产实现探 CDP; 失败信息直接给用户看怎么修)."""
        ...

    def is_busy(self) -> bool:
        """是否有任务在跑 (单飞锁)."""
        ...

    def start(self, keywords: list[str], suffix: str | None) -> str:
        """启动扫描, 立即返回 job_id."""
        ...

    def snapshot(self) -> ScanState:
        """当前状态快照 (幂等, 可高频轮询)."""
        ...


def _parse_log(lines: list[str], keywords: list[str]) -> tuple[list[str], list[dict], str | None]:
    """从脚本 stdout 推断 (succeeded, failed, current).

    脚本的输出格式是固定的人读日志, 没有结构化产物 —— 只能反解。三种关键行:
      ``[scan] {kw} -> {url}``      开始扫这个 keyword
      ``[scan] {kw}: 样本 N | ...`` 扫成功了
      ``[scan] {kw} 解析出 0 个商品`` 没解析出商品

    0 商品**不等于真没数据**: 列表页需要登录态, 登录过期时 PDD 返回空/验证页,
    所以失败原因要提示"登录态可能失效"(见实施文档决策 5)。
    """
    succeeded: list[str] = []
    failed: dict[str, str] = {}
    current: str | None = None

    for line in lines:
        m = re.match(_RE_SCANNING, line)
        if m:
            current = m.group(1).strip()
            continue
        m = re.match(_RE_OK, line)
        if m:
            kw = m.group(1).strip()
            if kw not in succeeded:
                succeeded.append(kw)
            failed.pop(kw, None)
            continue
        m = re.match(_RE_FAIL, line)
        if m:
            kw = m.group(1).strip()
            failed[kw] = "解析出 0 个商品 (列表页可能需要重新登录拼多多)"

    # 脚本可能"静默跳过"某个 keyword (既没成功行也没失败行, 比如 raw html 写失败),
    # 结束时把这些补成 failed, 避免前端一直等一个永远不会出现的品牌。
    return succeeded, [{"keyword": k, "reason": v} for k, v in failed.items()], current


class SubprocessScanner:
    """生产扫描器: Popen ``scripts/ecommerce_brand_compare.py``.

    ``suffix`` 语义: 拼到 keyword 后面的搜索词 (默认 "蓝牙耳机")。
    如果 keyword 本身已经含品类词 (如 "蓝牙音箱"), 调用方应传 ``suffix=""``,
    否则会拼出 "蓝牙音箱蓝牙耳机" 这种事故。
    """

    def __init__(
        self,
        *,
        script: Path | str = "scripts/ecommerce_brand_compare.py",
        cdp_url: str = "http://127.0.0.1:9223",
        cwd: Path | str | None = None,
        python_exe: str | None = None,
        max_keyword_len: int = MAX_KEYWORD_LEN,
    ):
        self.script = Path(script)
        self.cdp_url = cdp_url.rstrip("/")
        self.cwd = Path(cwd) if cwd else Path(self.script).resolve().parent.parent
        # Windows 上裸 "python" 会打到系统解释器, 必须用当前解释器的绝对路径
        self.python_exe = python_exe or sys.executable
        self.max_keyword_len = max_keyword_len

        self._proc: subprocess.Popen | None = None
        self._log_path: Path | None = None
        self._state = ScanState()
        self._keywords: list[str] = []

    # -- 探活 ---------------------------------------------------------------
    def probe(self) -> tuple[bool, str]:
        """探 CDP 端口。只接受回环地址 (防 SSRF 式误用)。"""
        parsed = urlparse(self.cdp_url)
        if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
            return False, f"CDP 地址不合法 (只允许回环地址): {self.cdp_url}"
        opener = build_opener(ProxyHandler({}))  # 本机代理会把 127.0.0.1 拦成 502
        try:
            with opener.open(f"{self.cdp_url}/json/version", timeout=3) as resp:
                info = resp.read().decode("utf-8", errors="replace")
        except (URLError, OSError) as exc:
            return False, f"连不上 CDP Chrome ({self.cdp_url}): {exc}"
        if "Browser" not in info:
            return False, f"CDP 返回内容异常: {info[:80]}"
        return True, "ok"

    # -- 单飞锁 -------------------------------------------------------------
    def is_busy(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # -- 启动 ---------------------------------------------------------------
    def start(self, keywords: list[str], suffix: str | None) -> str:
        if self.is_busy():
            raise RuntimeError("已有扫描任务在跑")
        if not keywords:
            raise ValueError("keywords 不能为空")

        for kw in keywords:
            if len(kw) > self.max_keyword_len:
                raise ValueError(f"keyword 过长 (> {self.max_keyword_len}): {kw!r}")
            if any(ch in kw for ch in "/\\\x00"):
                raise ValueError(f"keyword 含非法字符: {kw!r}")

        job_id = uuid.uuid4().hex[:12]
        cmd = [self.python_exe, str(self.script), "--brands", *keywords]
        if suffix is not None:
            cmd += ["--suffix", suffix]
        # 脚本从环境变量读 CDP 地址
        env = {**os.environ, "PDD_CDP_URL": self.cdp_url}

        # stdout → 临时文件 (PIPE 写满会卡死子进程)
        fd, path = tempfile.mkstemp(prefix="pdd_scan_", suffix=".log", text=True)
        os.close(fd)
        self._log_path = Path(path)
        self._log_file = open(self._log_path, "w", encoding="utf-8", errors="replace")

        self._proc = subprocess.Popen(
            cmd,
            cwd=str(self.cwd),
            env=env,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            # 禁 shell=True: keywords 是用户输入, 必须走 argv 列表传参
            shell=False,
        )
        self._keywords = list(keywords)
        self._state = ScanState(
            status=STATUS_RUNNING,
            job_id=job_id,
            keywords=list(keywords),
            started_at=datetime.now().replace(microsecond=0).isoformat(),
        )
        return job_id

    # -- 状态 ---------------------------------------------------------------
    def snapshot(self) -> ScanState:
        if self._proc is None:
            return ScanState()  # idle

        lines = self._read_parse_lines()      # 全量 (仅尾部上限), 用于状态判定
        succeeded, failed, current = _parse_log(lines, self._keywords)
        rc = self._proc.poll()

        st = self._state
        # 展示只给尾部; 判定用上面那批全量行 —— 两者**不能共用同一个截断**
        st.log_tail = lines[-LOG_TAIL_LINES:]
        st.succeeded = succeeded
        st.failed = failed
        st.current = current

        if rc is None:
            st.status = STATUS_RUNNING
        else:
            st.returncode = rc
            st.finished_at = datetime.now().replace(microsecond=0).isoformat()
            st.status = STATUS_DONE if rc == 0 else STATUS_FAILED
            st.current = None
            # 进程已退出: 既没成功行也没失败行的 keyword 是"被静默跳过"(比如 raw html
            # 写失败), 必须补成 failed —— 否则前端会一直等一个永远不会出现的品牌。
            done = set(succeeded) | {f["keyword"] for f in failed}
            for kw in self._keywords:
                if kw not in done:
                    failed.append({"keyword": kw, "reason": "脚本未报告该关键词 (静默跳过)"})
            self._close_log()
        return st

    def _read_parse_lines(self) -> list[str]:
        return self._read_lines()[-LOG_PARSE_LINES:]

    def _read_lines(self) -> list[str]:
        if not self._log_path or not self._log_path.exists():
            return []
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return [ln for ln in text.splitlines() if ln.strip()]

    def _close_log(self) -> None:
        try:
            if getattr(self, "_log_file", None):
                self._log_file.close()
        except OSError:
            pass


class StubScanner:
    """离线测试用扫描器: 不 Popen, 不连 CDP, 状态可脚本化。

    ``fail_keywords`` 里的 keyword 会被报成失败 (用于测 failed 清单与登录态提示)。
    """

    def __init__(
        self,
        *,
        probe_ok: bool = True,
        busy: bool = False,
        fail_keywords: tuple[str, ...] = (),
        status: str = STATUS_DONE,
    ):
        self.probe_ok = probe_ok
        self._busy = busy
        self.fail_keywords = tuple(fail_keywords)
        self.final_status = status
        self.state = ScanState()
        self.started: list[tuple[list[str], str | None]] = []

    def probe(self) -> tuple[bool, str]:
        if self.probe_ok:
            return True, "ok"
        return False, "连不上 CDP Chrome (http://127.0.0.1:9223): stub 模拟未启动"

    def is_busy(self) -> bool:
        return self._busy

    def start(self, keywords: list[str], suffix: str | None) -> str:
        if self._busy:
            raise RuntimeError("已有扫描任务在跑")
        self.started.append((list(keywords), suffix))
        job_id = "stub-" + uuid.uuid4().hex[:8]
        succeeded = [k for k in keywords if k not in self.fail_keywords]
        failed = [
            {"keyword": k, "reason": "解析出 0 个商品 (列表页可能需要重新登录拼多多)"}
            for k in keywords
            if k in self.fail_keywords
        ]
        self.state = ScanState(
            status=self.final_status,
            job_id=job_id,
            keywords=list(keywords),
            started_at=datetime.now().replace(microsecond=0).isoformat(),
            finished_at=datetime.now().replace(microsecond=0).isoformat(),
            log_tail=[f"[scan] {k} -> stub" for k in keywords],
            succeeded=succeeded,
            failed=failed,
            returncode=0,
        )
        return job_id

    def snapshot(self) -> ScanState:
        return self.state
