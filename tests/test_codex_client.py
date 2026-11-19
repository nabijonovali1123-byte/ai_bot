from __future__ import annotations

import json

import httpx
import pytest

from assistant.codex_client import CodexClient, CodexLimitError


class FakeCredentials:
    def __init__(self):
        self.refreshed = False

    async def access_token(self, force_refresh=False):
        self.refreshed = self.refreshed or force_refresh
        return "oauth-access"

    def metadata(self):
        return {"auth": "ChatGPT plan OAuth", "subject": "owner", "expires_at": 9999999999,
                "scopes": ["chatgpt.tokens.use.direct"]}


@pytest.mark.asyncio
async def test_streamed_reply_uses_plan_oauth_and_no_storage(tmp_path):
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"slug": "model-a", "display_name": "Model A", "visibility": "list"}]})
        body = json.loads(request.content)
        seen["body"] = body
        return httpx.Response(200, text='data: {"type":"response.output_text.delta","delta":"Hello"}\n\ndata: {"type":"response.completed"}\n\n')
    client = CodexClient(tmp_path / "unused", transport=httpx.MockTransport(handler))
    client.credentials = FakeCredentials()
    answer = await client.generate(instructions="reply briefly", prompt="hi")
    assert answer == "Hello"
    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["auth"] == "Bearer oauth-access"
    assert seen["body"]["store"] is False
    assert seen["body"]["stream"] is True
    assert "previous_response_id" not in seen["body"]


@pytest.mark.asyncio
async def test_usage_limit_event_is_not_retried(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"slug": "model-a", "visibility": "list"}]})
        return httpx.Response(200, text='data: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded","message":"usage limit"}}}\n\n')
    client = CodexClient(tmp_path / "unused", transport=httpx.MockTransport(handler))
    client.credentials = FakeCredentials()
    with pytest.raises(CodexLimitError):
        await client.generate(instructions="system", prompt="hello")
    assert calls.count("/v1/responses") == 1
