from pathlib import Path

from app.tool.ctrip_query_tool import CtripQueryTool


class FakeElement:
    def __init__(self, *, tag_name="div", text="", attributes=None, xpath=""):
        self.tag_name = tag_name
        self.text = text
        self.attributes = attributes or {}
        self.xpath = xpath


def test_click_whitelist_allows_only_current_query_form_controls_and_requested_cities():
    tool = CtripQueryTool(allowed_city_names=("广州", "北京"))
    assert tool._is_safe_query_click(FakeElement(tag_name="input", attributes={"aria-label": "请输入出发地"}))[0]
    assert tool._is_safe_query_click(FakeElement(text="广州"))[0]
    assert tool._is_safe_query_click(FakeElement(text="搜索", xpath="html/body/form/div/button"))[0]


def test_click_whitelist_rejects_history_recommendation_and_booking_targets():
    tool = CtripQueryTool(allowed_city_names=("广州", "北京"))
    assert not tool._is_safe_query_click(FakeElement(text="北京 宁波 查最新价"))[0]
    assert not tool._is_safe_query_click(FakeElement(text="订票", xpath="html/body/form/div/button"))[0]
    assert not tool._is_safe_query_click(FakeElement(text="上海"))[0]


def test_click_validation_refreshes_selector_map_before_using_index():
    source = (Path(__file__).parents[1] / "app" / "tool" / "ctrip_query_tool.py").read_text(encoding="utf-8")
    assert "context.get_state(cache_clickable_elements_hashes={})" in source
    assert "stale or missing DOM index" in source


def test_input_and_date_actions_are_checked_against_current_form_fields():
    source = (Path(__file__).parents[1] / "app" / "tool" / "ctrip_query_tool.py").read_text(encoding="utf-8")
    assert 'action in {"click_element", "input_text", "select_date"}' in source
    assert "blocked text input outside the current origin/destination fields" in source
    assert "blocked date selection outside the current departure-date field" in source
