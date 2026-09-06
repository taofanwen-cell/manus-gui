import asyncio
from app.tool.ctrip_query_tool import CtripQueryTool


def test_reduced_schema_does_not_advertise_dangerous_actions():
    actions = CtripQueryTool().parameters["properties"]["action"]["enum"]
    assert "gui_action" not in actions
    assert "open_tab" not in actions
    assert "web_search" not in actions
    assert "upload_file" not in actions
    assert "execute_js" not in actions
    assert "vision_inspect" in actions
    assert "click_element" in actions
    assert "select_date" in actions


def test_reduced_tool_blocks_before_browser_initialization():
    async def scenario():
        tool = CtripQueryTool()
        result = await tool.execute(action="gui_action")
        assert result.error is not None
        assert "blocked" in result.error
        assert tool.browser_tool.browser is None

    asyncio.run(scenario())


def test_reduced_tool_rejects_payment_navigation_before_browser_initialization():
    async def scenario():
        tool = CtripQueryTool()
        result = await tool.execute(action="go_to_url", url="https://flights.ctrip.com/order/create")
        assert result.error is not None
        assert tool.browser_tool.browser is None

    asyncio.run(scenario())


def test_reduced_tool_blocks_sensitive_key_input_before_browser_initialization():
    async def scenario():
        tool = CtripQueryTool()
        result = await tool.execute(action="send_keys", keys="支付")
        assert result.error is not None
        assert tool.browser_tool.browser is None

    asyncio.run(scenario())

def test_vision_inspect_is_model_visible_but_not_a_coordinate_action():
    tool = CtripQueryTool()
    actions = tool.parameters["properties"]["action"]["enum"]
    assert "vision_inspect" in actions
    assert "gui_action" not in actions


def test_protection_detection_locks_future_actions():
    async def scenario():
        tool = CtripQueryTool()

        class FakeResult:
            output = "[vision_inspect / gui-plus] whaleguard block / site-protection"
            error = None
            base64_image = "present"

        tool._vision_inspect = lambda goal=None: __import__("asyncio").sleep(0, result=FakeResult())
        result = await tool.execute(action="vision_inspect", goal="classify page")
        assert result.error is None
        assert tool.protection_detected is True

        blocked = await tool.execute(action="refresh")
        assert blocked.error is not None
        assert "human takeover" in blocked.error

    asyncio.run(scenario())
