"""Regression tests for scripts/inspect_ctrip_selectors.py + policy RECOMMENDED_FIELD_INDEX.

Teacher's debug_html/ contains ~150 elements.txt samples (2026-01-21 capture run).
These tests pin the empirical selector map extracted from those samples:

  * launch page (online/channel/domestic): origin 最常 = 37, dest 最常 in {38, 39},
                                            search 最常 in {43, 48}
  * list_result page (online/list/oneway-*): origin = 30, dest = 32, depart = 33

If teacher's capture re-run shows these shift (e.g. 携程 A/B 改版),
update RECOMMENDED_FIELD_INDEX in app/ctrip_policy.py + this test in the same commit.
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让脚本模块可 import（脚本目录不在 pytest 默认收集路径）
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.ctrip_policy import RECOMMENDED_FIELD_INDEX  # noqa: E402
import inspect_ctrip_selectors  # noqa: E402


DEBUG_HTML = Path("D:/It/Test_Project/case/OpenManus-gui/debug_html")


def _skip_if_no_fixtures():
    if not DEBUG_HTML.exists():
        return "debug_html fixtures not available; skip (CI sandbox without teacher's capture)."
    return None


def test_launch_origin_most_common_is_37():
    reason = _skip_if_no_fixtures()
    if reason:
        import pytest
        pytest.skip(reason)
    by_page = inspect_ctrip_selectors.scan(DEBUG_HTML)
    assert "launch" in by_page, "should find launch-page fixtures"
    counter = by_page["launch"]["origin"]
    assert counter, "should have at least one launch origin hit"
    top_idx, top_count = counter.most_common(1)[0]
    assert top_idx == 37, f"launch origin most-common index drifted: got {top_idx}×{top_count}, want 37"


def test_launch_destination_most_common_in_38_39():
    reason = _skip_if_no_fixtures()
    if reason:
        import pytest
        pytest.skip(reason)
    by_page = inspect_ctrip_selectors.scan(DEBUG_HTML)
    counter = by_page["launch"]["destination"]
    assert counter
    top_idx, _ = counter.most_common(1)[0]
    assert top_idx in (38, 39), f"launch dest drifted: got {top_idx}, want 38 or 39"


def test_list_result_origin_is_30():
    reason = _skip_if_no_fixtures()
    if reason:
        import pytest
        pytest.skip(reason)
    by_page = inspect_ctrip_selectors.scan(DEBUG_HTML)
    assert "list_result" in by_page
    counter = by_page["list_result"]["origin"]
    top_idx, _ = counter.most_common(1)[0]
    assert top_idx == 30, f"list_result origin drifted: got {top_idx}, want 30"


def test_list_result_destination_is_32():
    reason = _skip_if_no_fixtures()
    if reason:
        import pytest
        pytest.skip(reason)
    by_page = inspect_ctrip_selectors.scan(DEBUG_HTML)
    counter = by_page["list_result"]["destination"]
    top_idx, _ = counter.most_common(1)[0]
    assert top_idx == 32, f"list_result dest drifted: got {top_idx}, want 32"


def test_recommended_constants_match_observed_distribution():
    """RECOMMENDED_FIELD_INDEX 应该选直方图 top1（最常见值）。"""
    reason = _skip_if_no_fixtures()
    if reason:
        import pytest
        pytest.skip(reason)
    by_page = inspect_ctrip_selectors.scan(DEBUG_HTML)

    assert RECOMMENDED_FIELD_INDEX["launch"]["origin"] == by_page["launch"]["origin"].most_common(1)[0][0]
    assert RECOMMENDED_FIELD_INDEX["launch"]["destination"] == by_page["launch"]["destination"].most_common(1)[0][0]
    assert RECOMMENDED_FIELD_INDEX["list_result"]["origin"] == by_page["list_result"]["origin"].most_common(1)[0][0]
    assert RECOMMENDED_FIELD_INDEX["list_result"]["destination"] == by_page["list_result"]["destination"].most_common(1)[0][0]


def test_scan_handles_multiline_inner_text():
    """携程 elements.txt 中 [36]<div 仅看直飞/>\\n出发地\\n[37]<input /> 是常见跨行格式。

    用 find_field_indices 而不是 parse_elements_txt 验证：
    input 自身的 inner_text 永远是空，归属判断依赖前一个元素的续行文本。
    """
    sample = DEBUG_HTML / "20260121_220137_elements.txt"
    if not sample.exists():
        import pytest
        pytest.skip("fixture missing")
    rows = inspect_ctrip_selectors.parse_elements_txt(sample)
    # 校验 parse 把"出发地"作为 [36] div 的 inner text 续行捕获
    by_idx = {idx: (tag, text) for idx, tag, text in rows}
    assert 36 in by_idx, f"expected [36] div, got {sorted(by_idx.keys())[:5]}..."
    tag, text = by_idx[36]
    assert tag == "div"
    assert "出发地" in text, f"[36] div inner text should pick up '出发地' on next line, got {text!r}"
    # find_field_indices 应在 origin 字段里包含 37
    hits = inspect_ctrip_selectors.find_field_indices(rows)
    assert 37 in hits["origin"], f"origin should map to 37, got {hits['origin']}"