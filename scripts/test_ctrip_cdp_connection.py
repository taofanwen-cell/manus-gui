"""本机 Chrome CDP 预检：不读取 Cookie，不导出登录数据，不执行页面操作。"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse
from urllib.request import urlopen


def main() -> None:
    cdp_url = os.getenv("CTRIP_CDP_URL", "http://127.0.0.1:9222").rstrip("/")
    parsed = urlparse(cdp_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("拒绝连接：CTRIP_CDP_URL 必须是本机 HTTP CDP 地址，例如 http://127.0.0.1:9222")
    try:
        with urlopen(f"{cdp_url}/json/version", timeout=5) as response:
            version = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        raise SystemExit(f"CDP 未连接：{exc}\n请先运行 scripts\\start_ctrip_cdp_chrome.ps1，并在打开的 Chrome 中自行登录携程。") from exc
    print("CDP 预检通过")
    print(f"浏览器: {version.get('Browser', 'unknown')}")
    print(f"调试地址: {cdp_url}")
    print("本检查未读取 Cookie、密码或登录令牌，也没有执行页面点击。")


if __name__ == "__main__":
    main()
