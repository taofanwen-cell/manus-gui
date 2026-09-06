"""扫描老师 debug_html 目录的 elements.txt，提取真实 selector 索引。

目的：
    让 .workbuddy/.../app/ctrip_policy.py 里的 RECOMMENDED_FIELD_INDEX_*
    不再凭想象，而是从老师真实跑过的 DOM 样本里抽出来。
    写本文件时 (2026-09-06)，debug_html 里约有 150+ 个 elements.txt 样本，
    覆盖 launch / list-result / date-picker 三种页面状态。

为什么单写脚本而不是直接在 policy 里硬编码：
    - DOM 每次访问 selector index 会漂 (见 launch 页 origin=[37] vs
      list-result 页 origin=[30])，硬绑一个 index 等于自找麻烦。
    - 让本脚本扫全部样本，给出"在 N 个文件里 origin 出现在这些 index"
      这种**分布**，由 maintainer 决定 RECOMMENDED_* 取哪个值。
    - 本脚本本身有单测 (tests/test_ctrip_real_selectors.py)。

输出：
    - 控制台打印：每个页面对应字段的 index 分布直方图
    - exit code：0 (成功) / 1 (debug_html 目录不存在)
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

DEBUG_HTML_DIR = Path(
    "D:/It/Test_Project/case/OpenManus-gui/debug_html"
)

# elements.txt 行格式： [37]<input />
# 提取 tag 和 tag 后面的中文文本
ELEMENT_RE = re.compile(
    r"^\[(\d+)\]<([a-zA-Z]+)\s*([^/>]*?)\s*/?>\s*$"
)

# 元素之前的"上下文"行：出发地 / 目的地 / 出发日期 / 搜索 / 返回日期
CONTEXT_TOKENS = {
    "origin": ("出发地",),
    "destination": ("目的地",),
    "depart_date": ("出发日期",),
    "return_date": ("返回日期",),
    "search": ("搜索",),
}


def parse_elements_txt(path: Path) -> list[tuple[int, str, str]]:
    """解析 elements.txt，返回 [(index, tag, inner_text)] 列表。

    携程 elements.txt 的真实格式：每个元素的 inner text 可能跨多行
    （例如 [36]<div 仅看直飞/>\\n出发地\\n[37]<input />）。
    因此非 [N]<tag 开头的非空行视为上一个元素的 inner_text 续行。
    """
    if not path.exists():
        return []
    rows: list[tuple[int, str, str]] = []
    current: tuple[int, str, str] | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = ELEMENT_RE.match(line)
        if m:
            if current is not None:
                rows.append(current)
            current = (int(m.group(1)), m.group(2).lower(), m.group(3).strip())
        else:
            # 续行：拼接到当前元素的 inner_text
            if current is not None:
                idx, tag, text = current
                current = (idx, tag, (text + " " + line).strip())
    if current is not None:
        rows.append(current)
    # 仅关心 input / button / a / div（携程表单的关键交互元素）
    return [(i, t, x) for (i, t, x) in rows if t in {"input", "button", "a", "div"}]


def find_field_indices(rows: list[tuple[int, str, str]]) -> dict[str, list[int]]:
    """在解析后的元素列表里，按"前一个非空元素的文本"作为上下文做字段归属。

    启发式规则（与携程 launch 页 DOM 形态一致）：
        若某个 input 之前的非空 div/a/span 含 "出发地"，
        则该 input 视为 origin_field。
        含 "目的地" -> destination_field。
        含 "出发日期" -> depart_date_field。
        含 "返回日期" -> return_date_field。
        含 "搜索" 的 button -> search_button。
    """
    hits: dict[str, list[int]] = {k: [] for k in CONTEXT_TOKENS}
    prev_text = ""
    for idx, tag, text in rows:
        if tag == "input":
            for field, tokens in CONTEXT_TOKENS.items():
                if any(tok in prev_text for tok in tokens):
                    hits[field].append(idx)
                    break
            prev_text = ""  # input 后清空，避免误连
        elif tag in {"button", "a", "div", "span"}:
            prev_text = (prev_text + " " + text).strip() if prev_text else text
    # 搜索按钮另算
    for idx, tag, text in rows:
        if tag == "button" and "搜索" in text:
            hits["search"].append(idx)
    return hits


def classify_page(path: Path) -> str:
    """根据 URL 行把 elements.txt 分到三类页面。"""
    try:
        first_line = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (IndexError, OSError):
        return "unknown"
    if "online/list/oneway" in first_line or "online/list/round" in first_line:
        return "list_result"
    if "online/channel/domestic" in first_line:
        return "launch"
    if "international/Schedule" in first_line or "international" in first_line:
        return "international"
    return "other"


def scan(debug_dir: Path = DEBUG_HTML_DIR) -> dict[str, dict[str, Counter]]:
    """扫整个目录，返回 {page_class: {field: Counter(index)}}。"""
    if not debug_dir.exists():
        print(f"[FAIL] debug_html 目录不存在: {debug_dir}", file=sys.stderr)
        sys.exit(1)

    by_page: dict[str, dict[str, Counter]] = {}
    elements_files = sorted(debug_dir.glob("*_elements.txt"))
    for path in elements_files:
        page = classify_page(path)
        rows = parse_elements_txt(path)
        hits = find_field_indices(rows)
        bucket = by_page.setdefault(page, {k: Counter() for k in CONTEXT_TOKENS})
        for field, idxs in hits.items():
            bucket[field].update(idxs)
    return by_page


def render(by_page: dict[str, dict[str, Counter]]) -> str:
    """把扫描结果渲染成人类可读表格。"""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("CTRIP REAL SELECTOR MAP — 来自老师 debug_html 真实 DOM 样本")
    lines.append("=" * 72)
    for page, fields in sorted(by_page.items()):
        lines.append(f"\n## {page} ({sum(sum(c.values()) for c in fields.values())} hits)")
        for field, counter in fields.items():
            if not counter:
                continue
            top = ", ".join(f"{idx}×{n}" for idx, n in sorted(counter.items()))
            lines.append(f"  - {field:<14} -> [{top}]")
    lines.append("")
    lines.append("字段索引分布直方图说明：")
    lines.append("  * launch 页     origin=[37]   dest=[39]   depart=[40]   return=[41]   search=[43]")
    lines.append("  * list_result 页 origin=[30]   dest=[32]   depart=[33]   return=无       search=无")
    lines.append("  * 启发式按'前一个元素的文本'做字段归属，DOM 改一点就漂 (印证 Bug #1)。")
    lines.append("  * 这是为什么 protocol 必须有 target_field_index，不能靠 attribute 启发式。")
    return "\n".join(lines)


def main() -> int:
    by_page = scan()
    print(render(by_page))
    return 0


if __name__ == "__main__":
    sys.exit(main())