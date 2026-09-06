import asyncio
from unittest.mock import AsyncMock, Mock

from app.tool.browser_use_tool import BrowserUseTool


def test_get_current_state_supports_new_browser_use_state_signature():
    async def scenario():
        tool = BrowserUseTool()
        page = Mock()
        page.bring_to_front = AsyncMock()
        page.wait_for_load_state = AsyncMock()
        page.screenshot = AsyncMock(return_value=b"png")
        page.url = "https://flights.ctrip.com/online/list/oneway"
        ctx = Mock()
        ctx.get_state = AsyncMock(side_effect=[
            TypeError("BrowserContext.get_state() missing 1 required positional argument: cache_clickable_elements_hashes"),
            Mock(
                viewport_info=Mock(height=720),
                element_tree=None,
                url=page.url,
                title="Ctrip",
                tabs=[],
                pixels_above=0,
                pixels_below=0,
            ),
        ])
        ctx.get_current_page = AsyncMock(return_value=page)
        result = await tool.get_current_state(ctx)
        assert result.error is None
        assert result.base64_image
        assert ctx.get_state.await_count == 2
        assert ctx.get_state.await_args_list[1].kwargs == {"cache_clickable_elements_hashes": {}}

    asyncio.run(scenario())
