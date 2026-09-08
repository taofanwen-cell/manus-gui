#!/usr/bin/env bash
# 拼多多竞品调研 — 一键 demo 脚本 (跨 Win/Mac/Linux)
#
# 流程:
#   1. (可选) 检查本机 CDP Chrome; 在线则跑 brand_compare.py 离线扫描
#      → 落 data/pdd_raw_<品牌>_<时间戳>.html + 横向对比 Markdown
#   2. (后台) 启 FastAPI @ 127.0.0.1:8001
#   3. 自动打开浏览器到 Web UI (跨平台 start/open/xdg-open)
#   4. Ctrl+C 优雅关闭 uvicorn
#
# 用法:
#   bash scripts/demo.sh                       # 正常流程 (启动器模式)
#   PDD_SKIP_SCAN=1 bash scripts/demo.sh       # 跳过扫描 (data/ 已有 HTML)
#   PDD_CDP_URL=http://127.0.0.1:9333 bash scripts/demo.sh   # 自定义 CDP 端口
#   PORT=8765 bash scripts/demo.sh             # 自定义服务端口
#
# 停止服务:
#   pkill -f 'uvicorn app.ecommerce_api'       # Mac/Linux
#   taskkill /F /FI "IMAGENAME eq python.exe"  # Windows (强杀所有 python, 慎用)
#   taskkill /PID <PID> /F                     # Windows (指定 PID, 安全)
#
# 设计原则:
#   - 启动器模式 (不是常驻 wait): 启动 uvicorn 后立即 exit, 跨平台稳定
#   - 自动检测 .venv (Mac/Linux 用 bin/python, Win 用 Scripts/python.exe)
#   - 健康检查用 python urllib, 绕开 Git Bash 下 curl 写文件失败 (exit 23) 的已知 bug
#   - 打开浏览器用 OSTYPE 匹配 (darwin/linux/msys), 都后台化不阻塞
#   - 不引第三依赖 (没有 `jq` / `nc` 之类要求)

set -e

# 切到项目根 (脚本所在目录的上一级, 即 upstream/manus-gui/)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$ROOT"

# ---------------------------------------------------------------------------
# 配置 (env override)
# ---------------------------------------------------------------------------
CDP_URL="${PDD_CDP_URL:-http://127.0.0.1:9223}"
PORT="${PORT:-8001}"
HOST="${HOST:-127.0.0.1}"
SKIP_SCAN="${PDD_SKIP_SCAN:-0}"
BRANDS=(华为 小米 倍思 QCY 万魔 漫步者)

PY="${PYTHON:-python}"  # 用户可指定 python 路径 (CI / venv 都靠它)
# 自动检测 .venv (Mac/Linux 用 bin/python, Win 用 Scripts/python.exe)
if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif [[ -x ".venv/Scripts/python.exe" ]]; then
  PY=".venv/Scripts/python.exe"
fi

echo "============================================================"
echo " 拼多多竞品调研 · 一键 demo"
echo "  根目录:  $ROOT"
echo "  Python:  $PY"
echo "  CDP:     $CDP_URL  (SKIP_SCAN=$SKIP_SCAN)"
echo "  服务:    http://$HOST:$PORT/"
echo "============================================================"
echo

# ---------------------------------------------------------------------------
# 1. 离线扫描 (可选)
# ---------------------------------------------------------------------------
if [[ "$SKIP_SCAN" != "1" ]]; then
  echo "[1/4] 检查 CDP Chrome @ $CDP_URL ..."
  # 用 python 检测 CDP 端口, 绕开 Git Bash 下 curl 写文件失败 (exit 23) 的已知 bug
  if "$PY" -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen(sys.argv[1]+'/json/version', timeout=3).read() else 1)" "$CDP_URL" 2>/dev/null; then
    echo "    ✓ CDP 在线 → 启动 brand_compare.py 离线扫描"
    echo "    品牌: ${BRANDS[*]}"
    echo
    PDD_CDP_URL="$CDP_URL" "$PY" scripts/ecommerce_brand_compare.py \
      --brands "${BRANDS[@]}" \
      --sleep-s 2.0
    echo
    echo "    ✓ 扫描完成 → data/pdd_raw_<品牌>_<时间戳>.html"
  else
    echo "    ✗ CDP 不在线 → 跳过扫描, 使用 data/ 已有 HTML"
    echo "      提示: 想扫新数据先跑 scripts/start_pdd_cdp_chrome.ps1 并手动登录"
  fi
else
  echo "[1/4] PDD_SKIP_SCAN=1 → 跳过扫描"
fi
echo

# ---------------------------------------------------------------------------
# 2. 启 FastAPI (后台)
# ---------------------------------------------------------------------------
echo "[2/4] 启动 FastAPI @ http://$HOST:$PORT/"
"$PY" -m uvicorn app.ecommerce_api:app --host "$HOST" --port "$PORT" \
  > /tmp/ecommerce_demo_uvicorn.log 2>&1 &
UVICORN_PID=$!

# ---------------------------------------------------------------------------
# 3. 等服务起来 (最多 15 秒)
# ---------------------------------------------------------------------------
echo "[3/4] 等待服务健康 ..."
HEALTH_URL="http://$HOST:$PORT/api/health"
# 用 python 健康检查, 绕开 Git Bash 下 curl 写文件失败 (exit 23) 的已知 bug
HEALTH_PROBE='import urllib.request,sys; sys.exit(0 if urllib.request.urlopen(sys.argv[1], timeout=1).read() else 1)'
for i in $(seq 1 20); do
  if "$PY" -c "$HEALTH_PROBE" "$HEALTH_URL" 2>/dev/null; then
    echo "    ✓ 服务已启动 ($i 秒)"
    break
  fi
  sleep 1
  if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
    echo "    ✗ uvicorn 进程异常退出, 查看日志:"
    cat /tmp/ecommerce_demo_uvicorn.log
    exit 1
  fi
done
if ! "$PY" -c "$HEALTH_PROBE" "$HEALTH_URL" 2>/dev/null; then
  echo "    ✗ 20 秒未起来, 查看日志:"
  cat /tmp/ecommerce_demo_uvicorn.log
  kill "$UVICORN_PID" 2>/dev/null || true
  exit 1
fi
echo

# ---------------------------------------------------------------------------
# 4. 打开浏览器 (跨平台)
# ---------------------------------------------------------------------------
URL="http://$HOST:$PORT/"
echo "[4/4] 打开浏览器: $URL"
case "$OSTYPE" in
  darwin*)
    open "$URL" >/dev/null 2>&1 &
    ;;
  linux*)
    xdg-open "$URL" >/dev/null 2>&1 &
    ;;
  msys*|mingw*|cygwin*|win32*)
    cmd.exe /c start "" "$URL" >/dev/null 2>&1 &
    ;;
  *)
    ;;
esac
# 无 GUI 环境 (headless server / CI) 上 start/open 会失败但已后台化, 不阻塞
sleep 1
true  # 兜底, 即使上面某条 case 返回非 0, set -e 也不会让脚本挂掉
echo

# ---------------------------------------------------------------------------
# 设计说明 (为什么是"启动器"而不是常驻)
# ---------------------------------------------------------------------------
# Git Bash on Windows 上 Ctrl+C 信号在 nohup 后台进程组里传递不可靠,
# trap handler 不一定触发。改成启动器模式:
#   - demo.sh 启动 uvicorn 后立即 exit 0, 不在 bash 里 wait
#   - uvicorn 在 disown 后的独立进程组跑, 不依赖 demo.sh
#   - 用户用 pkill / taskkill 关闭 uvicorn
# 这样 demo 脚本既轻量又跨平台 (Win/Mac/Linux) 都稳定。
# ---------------------------------------------------------------------------
echo "============================================================"
echo " ✓ demo 已启动"
echo "   Web UI:    $URL"
echo "   Swagger:   ${URL}docs"
echo "   uvicorn PID: $UVICORN_PID"
echo "   日志:      tail -f /tmp/ecommerce_demo_uvicorn.log"
echo "   停止服务:  pkill -f 'uvicorn app.ecommerce_api'"
echo "             (或 Windows: taskkill /F /FI \"WINDOWTITLE eq uvicorn*\")"
echo "============================================================"

# disown: 把 uvicorn 从本 shell 的 job table 摘除, demo.sh exit 后不会因 SIGHUP 杀它
disown "$UVICORN_PID" 2>/dev/null || true

exit 0