import asyncio
from openai import AuthenticationError

from app.llm import _worth_retry, _worth_retry_text


def auth_error():
    request = __import__("httpx").Request("POST", "https://example.invalid")
    response = __import__("httpx").Response(401, request=request)
    return AuthenticationError("invalid API key", response=response, body=None)


def test_authentication_errors_are_not_retried():
    error = auth_error()
    assert _worth_retry_text(error) is False
    assert _worth_retry(error) is False


def test_no_async_plugin_is_required_for_this_retry_regression():
    async def scenario():
        assert _worth_retry_text(auth_error()) is False

    asyncio.run(scenario())
