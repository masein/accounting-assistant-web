"""OpenAI-shape adapter tests — wire-format translation + httpx mocking.

Exercises the protocol layer in isolation:

* ``_chat_messages_to_openai_wire`` — normalized → OpenAI shape
* ``_parse_response`` — OpenAI shape → normalized
* ``_chat_completions_url`` — URL normalization across the common
  base-URL flavours we see in the wild
* End-to-end ``OpenAILLMClient.chat`` via a stubbed ``httpx`` transport,
  so we cover the happy path plus 401 / 429 / 500 error handling without
  ever touching the network.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from typing import Any

import httpx
import pytest

from app.services.ai_accountant import openai_client
from app.services.ai_accountant.llm_protocol import (
    ChatMessage, LLMClientError, ToolCall,
)
from app.services.ai_accountant.openai_client import (
    OpenAILLMClient,
    _chat_completions_url,
    _chat_messages_to_openai_wire,
    _parse_response,
)


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------


class TestChatCompletionsURL:
    @pytest.mark.parametrize("base,expected", [
        ("", "https://api.openai.com/v1/chat/completions"),
        ("https://api.openai.com", "https://api.openai.com/v1/chat/completions"),
        ("https://api.metisai.ir/openai/v1", "https://api.metisai.ir/openai/v1/chat/completions"),
        ("http://host.docker.internal:1234", "http://host.docker.internal:1234/v1/chat/completions"),
        ("http://localhost:1234/v1", "http://localhost:1234/v1/chat/completions"),
        ("https://x.example/chat/completions", "https://x.example/chat/completions"),
        ("https://api.openai.com/", "https://api.openai.com/v1/chat/completions"),  # trailing slash
    ])
    def test_resolves(self, base: str, expected: str) -> None:
        assert _chat_completions_url(base) == expected


# ---------------------------------------------------------------------------
# Message translation
# ---------------------------------------------------------------------------


class TestChatMessagesToOpenAI:
    def test_user_and_assistant_text(self) -> None:
        out = _chat_messages_to_openai_wire([
            ChatMessage(role="user", text="hi"),
            ChatMessage(role="assistant", text="hello"),
        ])
        assert out == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

    def test_assistant_with_tool_calls(self) -> None:
        out = _chat_messages_to_openai_wire([
            ChatMessage(
                role="assistant",
                text=None,
                tool_calls=[ToolCall(id="call_1", name="find_entity", input={"query": "Kim"})],
            ),
        ])
        assert out == [{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "find_entity", "arguments": '{"query": "Kim"}'},
            }],
        }]

    def test_tool_result_message(self) -> None:
        out = _chat_messages_to_openai_wire([
            ChatMessage(role="tool", tool_call_id="call_1", text='{"matches": []}'),
        ])
        assert out == [{
            "role": "tool",
            "tool_call_id": "call_1",
            "content": '{"matches": []}',
        }]

    def test_full_round_trip(self) -> None:
        """The orchestrator emits user → assistant(tool_call) → tool → assistant(text)
        sequences. Verify the full chain serializes cleanly."""
        out = _chat_messages_to_openai_wire([
            ChatMessage(role="user", text="what's my cash balance?"),
            ChatMessage(
                role="assistant", text=None,
                tool_calls=[ToolCall(id="c1", name="get_account_balance", input={"account_code": "1110"})],
            ),
            ChatMessage(role="tool", tool_call_id="c1", text='{"balance": 0}'),
            ChatMessage(role="assistant", text="Your cash balance is 0 IRR."),
        ])
        assert len(out) == 4
        assert out[0]["role"] == "user"
        assert out[1]["role"] == "assistant" and out[1]["tool_calls"]
        assert out[2]["role"] == "tool" and out[2]["tool_call_id"] == "c1"
        assert out[3]["role"] == "assistant" and out[3]["content"]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _stub_response(*, text: str | None = None, tool_calls: list[dict] | None = None,
                   finish_reason: str = "stop", usage: dict | None = None) -> dict:
    """Minimal OpenAI Chat Completions response body."""
    msg: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    body: dict[str, Any] = {
        "choices": [{"index": 0, "message": msg, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }
    return body


class TestParseResponse:
    def test_text_response(self) -> None:
        r = _parse_response(_stub_response(text="hello world"))
        assert r.message.role == "assistant"
        assert r.message.text == "hello world"
        assert r.message.tool_calls == []
        assert r.stop_reason == "end_turn"
        assert r.usage.input_tokens == 100
        assert r.usage.output_tokens == 20

    def test_tool_call_response(self) -> None:
        body = _stub_response(
            text=None,
            finish_reason="tool_calls",
            tool_calls=[{
                "id": "call_42",
                "type": "function",
                "function": {"name": "find_entity", "arguments": '{"query": "Kim"}'},
            }],
        )
        r = _parse_response(body)
        assert r.stop_reason == "tool_use"
        assert len(r.message.tool_calls) == 1
        tc = r.message.tool_calls[0]
        assert tc.id == "call_42"
        assert tc.name == "find_entity"
        assert tc.input == {"query": "Kim"}

    def test_malformed_tool_args_flagged(self) -> None:
        """Local models sometimes return invalid JSON. We tag the call so
        the orchestrator can surface a clean error."""
        body = _stub_response(
            text=None,
            finish_reason="tool_calls",
            tool_calls=[{
                "id": "call_x",
                "type": "function",
                "function": {"name": "find_entity", "arguments": "{not valid json"},
            }],
        )
        r = _parse_response(body)
        tc = r.message.tool_calls[0]
        assert tc.input.get("_parse_error") is True
        assert tc.input.get("_raw_arguments") == "{not valid json"

    def test_length_finish_reason_maps_to_max_tokens(self) -> None:
        r = _parse_response(_stub_response(text="…truncated", finish_reason="length"))
        assert r.stop_reason == "max_tokens"

    def test_cached_tokens_picked_up(self) -> None:
        r = _parse_response(_stub_response(
            text="hi",
            usage={
                "prompt_tokens": 200,
                "completion_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 180},
            },
        ))
        assert r.usage.cache_read_input_tokens == 180

    def test_empty_choices_raises(self) -> None:
        with pytest.raises(LLMClientError):
            _parse_response({"choices": []})


# ---------------------------------------------------------------------------
# Client.chat with httpx mocked
# ---------------------------------------------------------------------------


@contextmanager
def _patch_httpx(monkeypatch, handler):
    """Replace httpx.AsyncClient with one whose transport is a MockTransport
    routing all POSTs through ``handler(request) → httpx.Response``."""
    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def _patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)
    try:
        yield
    finally:
        monkeypatch.setattr(httpx.AsyncClient, "__init__", original_init)


def _patch_backend(monkeypatch, *, base_url="https://api.metisai.ir/openai/v1",
                   api_key="tpsg-test", model="gpt-4o-mini",
                   provider="metis") -> None:
    monkeypatch.setattr(
        openai_client, "resolve_active_ai_backend",
        lambda: {
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
            "api_key_header": "Authorization",
            "api_key_prefix": "Bearer",
        },
    )


class TestOpenAIClientChat:
    def test_happy_path(self, monkeypatch) -> None:
        _patch_backend(monkeypatch)
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=_stub_response(text="all good"))

        with _patch_httpx(monkeypatch, handler):
            client = OpenAILLMClient()
            r = asyncio.run(client.chat(
                system_prompt="you are a helpful assistant",
                tools=[],
                messages=[ChatMessage(role="user", text="hello")],
            ))

        assert r.message.text == "all good"
        assert r.stop_reason == "end_turn"
        assert captured["url"] == "https://api.metisai.ir/openai/v1/chat/completions"
        assert captured["auth"] == "Bearer tpsg-test"
        assert captured["body"]["model"] == "gpt-4o-mini"
        assert captured["body"]["messages"][0]["role"] == "system"

    def test_tool_def_translation_added_to_payload(self, monkeypatch) -> None:
        _patch_backend(monkeypatch)
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_stub_response(text="ok"))

        tool_def = {
            "name": "find_entity",
            "description": "look up",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        }
        with _patch_httpx(monkeypatch, handler):
            asyncio.run(OpenAILLMClient().chat(
                system_prompt="sp", tools=[tool_def],
                messages=[ChatMessage(role="user", text="hi")],
            ))

        sent_tools = captured["body"]["tools"]
        assert len(sent_tools) == 1
        assert sent_tools[0]["type"] == "function"
        assert sent_tools[0]["function"]["name"] == "find_entity"
        assert sent_tools[0]["function"]["parameters"]["required"] == ["query"]
        assert captured["body"]["tool_choice"] == "auto"

    def test_401_raises_clean_error(self, monkeypatch) -> None:
        _patch_backend(monkeypatch)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

        with _patch_httpx(monkeypatch, handler):
            with pytest.raises(LLMClientError) as ei:
                asyncio.run(OpenAILLMClient().chat(
                    system_prompt="sp", tools=[],
                    messages=[ChatMessage(role="user", text="hi")],
                ))
        assert "401" in str(ei.value)

    def test_500_retries_then_raises(self, monkeypatch) -> None:
        _patch_backend(monkeypatch)
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(503, json={"error": {"message": "server down"}})

        with _patch_httpx(monkeypatch, handler):
            with pytest.raises(LLMClientError):
                asyncio.run(OpenAILLMClient().chat(
                    system_prompt="sp", tools=[],
                    messages=[ChatMessage(role="user", text="hi")],
                ))
        # Retries MAX_RETRIES=3 times before giving up.
        assert attempts["n"] == openai_client.MAX_RETRIES

    def test_no_api_key_against_hosted_provider_errors(self, monkeypatch) -> None:
        _patch_backend(monkeypatch, api_key="", base_url="https://api.metisai.ir/openai/v1")
        with pytest.raises(LLMClientError) as ei:
            asyncio.run(OpenAILLMClient().chat(
                system_prompt="sp", tools=[],
                messages=[ChatMessage(role="user", text="hi")],
            ))
        assert "API key" in str(ei.value)

    def test_lmstudio_works_without_api_key(self, monkeypatch) -> None:
        """Local LM Studio doesn't require auth; we should NOT raise on missing key."""
        _patch_backend(monkeypatch, api_key="", base_url="http://host.docker.internal:1234",
                       provider="lmstudio")
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=_stub_response(text="local model says hi"))

        with _patch_httpx(monkeypatch, handler):
            r = asyncio.run(OpenAILLMClient().chat(
                system_prompt="sp", tools=[],
                messages=[ChatMessage(role="user", text="hi")],
            ))
        assert r.message.text == "local model says hi"
        # No Authorization header when no key is set.
        assert captured["auth"] is None

    def test_active_provider_anthropic_errors(self, monkeypatch) -> None:
        """If the user mis-configured the chat shape to OpenAI while the
        default provider is Anthropic, we fail with a clear message
        instead of sending a bogus request."""
        _patch_backend(monkeypatch, provider="anthropic")
        with pytest.raises(LLMClientError) as ei:
            asyncio.run(OpenAILLMClient().chat(
                system_prompt="sp", tools=[],
                messages=[ChatMessage(role="user", text="hi")],
            ))
        assert "anthropic" in str(ei.value).lower()
