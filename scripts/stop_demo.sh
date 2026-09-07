#!/usr/bin/env bash
# 关闭 demo.sh 启动的 FastAPI 服务
#
# 用法: bash scripts/stop_demo.sh
#
# 跨平台:
#   Mac/Linux: 用 pkill -f 'uvicorn app.ecommerce_api'  (POSIX)
#   Win Git Bash: pkill 不一定有, 改用 taskkill /IM python.exe /F + 过滤命令行
#
# 设计: 不依赖 PID 文件 (避免 demo 崩溃后 PID 残留)
#       用命令行模糊匹配, 一次杀干净, 兜底确认端口释放

set -e

echo "[stop] 查找 uvicorn 进程 ..."

case "$OSTYPE" in
  msys*|mingw*|cygwin*|win32*)
    # Win: wmic 在新版 Win 上默认禁用, Git Bash 上更可能没装. 用 PowerShell:
    #   Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%uvicorn%ecommerce_api%'"
    echo "[stop] Windows 平台, 用 PowerShell + taskkill"
    PIDS=$(powershell -NoProfile -Command "
      Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |
        Where-Object { \$_.CommandLine -like '*uvicorn*ecommerce_api*' } |
        Select-Object -ExpandProperty ProcessId
    " 2>/dev/null | tr -d '\r' | tr -d ' ')
    if [[ -z "$PIDS" ]]; then
      echo "[stop] 没找到 uvicorn 进程, 可能已经停了"
      exit 0
    fi
    echo "[stop] 找到 PID: $PIDS"
    for PID in $PIDS; do
      taskkill /PID "$PID" /F 2>/dev/null && echo "[stop] ✓ killed $PID" || echo "[stop] ✗ $PID 失败"
    done
    ;;
  *)
    # Mac/Linux: pkill
    echo "[stop] POSIX 平台, 用 pkill"
    if pkill -f "uvicorn app.ecommerce_api" 2>/dev/null; then
      echo "[stop] ✓ uvicorn 已杀"
    else
      # 兜底: 杀 demo.sh 启的所有 python -m uvicorn
      pkill -f "uvicorn.*ecommerce_api" 2>/dev/null && echo "[stop] ✓ 兜底杀成功" || echo "[stop] 没找到, 可能已经停了"
    fi
    ;;
esac

echo "[stop] 验证端口释放 ..."
sleep 1
# 端口 8001 是默认; 用户可能改 PORT, 这里只检测 LISTENING 状态
if netstat -ano 2>/dev/null | grep -E "LISTENING.*:8001 " | grep -q .; then
  echo "[stop] ⚠ 8001 端口还在 LISTENING (可能服务没退干净)"
else
  echo "[stop] ✓ 端口已释放"
fi