#!/usr/bin/env bash
# 电商模块 · 交付前自检 (preflight)
#
# 用途: 改完代码、提交/推送之前跑这一条。10 秒内回答两个问题:
#   1) 我有没有改坏东西?
#   2) 我以为落盘的改动, 真落盘了吗?   ← 专治"编辑假成功"
#
# 检查三层 (顺序: 便宜的先跑, 挂了立刻停):
#   ① ruff F821 静态检查     —— 未定义名字。CI 同款命令。
#      (为什么需要: py_compile 只查语法不查名字, 一个 NameError 曾因此上过 main)
#   ② import 冒烟            —— 每个模块真 import 一遍, 抓 import 期错误/缺依赖
#      (为什么需要: 测试只覆盖 tests/, 脚本坏了没人知道)
#   ③ 电商测试全套           —— 269+ 用例
#
# 用法:
#   bash scripts/preflight.sh          # 跑全部
#   bash scripts/preflight.sh --quick  # 跳过大全套, 只跑 ① ②
#
# 退出码: 0 = 全过; 非 0 = 有失败 (可直接挂 git pre-push 钩子)

set -u
set -o pipefail   # 防御: 管道中任一段失败即为失败 (避免"检查永远不会红")

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$ROOT"

QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

PY="${PYTHON:-python}"
if [[ -x ".venv/Scripts/python.exe" ]]; then
  PY=".venv/Scripts/python.exe"
elif [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
fi

FAILED=0
step() { echo; echo "── $1"; }
ok()   { echo "   ✓ $1"; }
bad()  { echo "   ✗ $1"; FAILED=1; }

echo "============================================================"
echo " preflight · 交付前自检"
echo "   repo:   $ROOT"
echo "   python: $PY"
echo "   mode:   $([ $QUICK -eq 1 ] && echo 'quick (跳过测试全套)' || echo 'full')"
echo "============================================================"

# ① 静态检查: 未定义名字 (CI 同款)
# 注意: 必须显式接住 ruff 的退出码。写成 `if ruff ... | tail; then` 是错的 ——
# 管道退出码取最后一条命令 (tail 永远成功), 这个检查就永远不会红。
step "① ruff F821 静态检查 (未定义名字)"
RUFF_OUT=$("$PY" -m ruff check --select F821 --isolated --no-cache app scripts 2>&1)
RUFF_RC=$?
echo "$RUFF_OUT" | tail -3 | sed 's/^/   /'
if [ $RUFF_RC -eq 0 ]; then
  ok "无未定义名字"
else
  bad "存在未定义名字 —— 运行时会 NameError"
fi

# ② import 冒烟: 模块能不能真的 import
step "② import 冒烟 (模块真实加载)"
SMOKE='import sys
sys.path[:0] = [".", "scripts"]
mods = [
    "app.ecommerce_api",
    "app.ecommerce_analyzer",
    "app.ecommerce_pdd_parser",
    "app.ecommerce_insight",
    "app.ecommerce_browser_ctl",
    "app.ecommerce_scan",
    "ecommerce_brand_compare",
    "ecommerce_competitor_report",
    "ecommerce_brand_compare_html",
    "pdd_detail_enrich",
]
bad = []
for m in mods:
    try:
        __import__(m)
    except Exception as e:
        bad.append("%s -> %s: %s" % (m, type(e).__name__, e))
if bad:
    print("\n".join(bad)); sys.exit(1)
print("   %d 个模块全部 import 成功" % len(mods))'
if "$PY" -c "$SMOKE"; then
  ok "无 import 期错误"
else
  bad "有模块 import 失败 (见上)"
fi

# ③ 测试全套
if [ $QUICK -eq 0 ]; then
  step "③ 电商测试全套"
  # 同样显式接退出码, 不靠 grep 文本判成败
  TEST_OUT=$("$PY" -m pytest -q -p no:cacheprovider tests/test_ecommerce_*.py 2>&1)
  TEST_RC=$?
  echo "$TEST_OUT" | tail -2 | sed 's/^/   /'
  if [ $TEST_RC -eq 0 ]; then
    ok "全部通过"
  else
    echo "$TEST_OUT" | grep -E "^FAILED|^ERROR" | head -8 | sed 's/^/   /'
    bad "有用例失败 (见上)"
  fi
fi

echo
echo "============================================================"
if [ $FAILED -eq 0 ]; then
  echo " ✓ preflight 全过 — 可以 commit / push"
else
  echo " ✗ preflight 有失败 — 别提交, 先修"
fi
echo "============================================================"
exit $FAILED
