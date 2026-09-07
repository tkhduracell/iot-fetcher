import json

import httpx
import pytest
import respx

from ai_brain.llm import Message, ProviderError, ToolCall, ToolSpec
from ai_brain.llm.ollama import OllamaProvider, build_request

URL = "http://ollama:11434/api/chat"

WEATHER = ToolSpec(
    name="get_weather",
    description="Look up the weather",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)


def provider(**kw) -> OllamaProvider:
    return OllamaProvider("llama3.2:3b", "http://ollama:11434", **kw)


def text_response(text="hello", prompt=11, completion=3) -> dict:
    return {
        "message": {"role": "assistant", "content": text},
        "prompt_eval_count": prompt,
        "eval_count": completion,
    }


@respx.mock
async def test_text_reply():
    respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    reply = await provider().complete([Message(role="user", content="hi")], [], 256)

    assert reply.text == "hello"
    assert reply.tool_calls == ()
    assert reply.usage.prompt_tokens == 11
    assert reply.usage.completion_tokens == 3
    assert reply.model == "llama3.2:3b"


async def test_key_is_derived_from_model():
    assert provider().key == "ollama:llama3.2:3b"


@respx.mock
async def test_missing_usage_counts_zero():
    body = {"message": {"role": "assistant", "content": "hi"}}
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    reply = await provider().complete([Message(role="user", content="hi")], [], 256)
    assert reply.usage.prompt_tokens == 0
    assert reply.usage.completion_tokens == 0


@respx.mock
async def test_function_call_reply_with_two_calls():
    body = {
        "message": {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [
                {"function": {"name": "get_weather", "arguments": {"city": "Malmo"}}},
                {"function": {"name": "get_weather", "arguments": {"city": "Lund"}}},
            ],
        },
        "prompt_eval_count": 20,
        "eval_count": 7,
    }
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    reply = await provider().complete([Message(role="user", content="weather?")], [WEATHER], 256)

    assert reply.text == "checking"
    assert reply.tool_calls == (
        ToolCall(id="call_1", name="get_weather", args={"city": "Malmo"}),
        ToolCall(id="call_2", name="get_weather", args={"city": "Lund"}),
    )


@respx.mock
async def test_function_call_without_args_defaults_to_empty_dict():
    body = {"message": {"role": "assistant", "tool_calls": [{"function": {"name": "ping"}}]}}
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    reply = await provider().complete([Message(role="user", content="hi")], [], 256)
    assert reply.tool_calls == (ToolCall(id="call_1", name="ping", args={}),)


@respx.mock
async def test_request_body_mapping():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    messages = [
        Message(role="system", content="You are a helper."),
        Message(role="user", content="weather in Malmo?"),
        Message(
            role="assistant",
            content="checking",
            tool_calls=(ToolCall(id="call_1", name="get_weather", args={"city": "Malmo"}),),
        ),
        Message(role="tool", name="get_weather", tool_call_id="call_1", content='{"c": 17}'),
    ]
    await provider().complete(messages, [WEATHER], 512)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "model": "llama3.2:3b",
        "messages": [
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "weather in Malmo?"},
            {
                "role": "assistant",
                "content": "checking",
                "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "Malmo"}}}
                ],
            },
            {"role": "tool", "content": '{"c": 17}'},
        ],
        "stream": False,
        "options": {"num_predict": 512, "temperature": 0.7},
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Look up the weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                },
            }
        ],
    }


@respx.mock
async def test_request_body_omits_tools_when_none_given():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    await provider().complete([Message(role="user", content="hi")], [], 64)

    sent = json.loads(route.calls.last.request.content)
    assert "tools" not in sent


@respx.mock
async def test_missing_message_degrades_to_empty_text():
    respx.post(URL).mock(return_value=httpx.Response(200, json={}))
    reply = await provider().complete([Message(role="user", content="hi")], [], 64)
    assert reply.text == ""
    assert reply.tool_calls == ()


@respx.mock
async def test_model_not_found_is_a_not_found_error():
    respx.post(URL).mock(
        return_value=httpx.Response(404, json={"error": "model \"nope\" not found"})
    )
    with pytest.raises(ProviderError) as excinfo:
        await provider().complete([Message(role="user", content="hi")], [], 64)
    assert excinfo.value.kind == "not_found"


@respx.mock
async def test_server_error_is_retryable():
    respx.post(URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(ProviderError) as excinfo:
        await provider().complete([Message(role="user", content="hi")], [], 64)
    assert excinfo.value.kind == "server"


async def test_timeout_is_classified():
    def raise_timeout(_request):
        raise httpx.TimeoutException("timed out")

    transport = httpx.MockTransport(raise_timeout)
    client = httpx.AsyncClient(transport=transport)
    with pytest.raises(ProviderError) as excinfo:
        await provider(client=client).complete([Message(role="user", content="hi")], [], 64)
    assert excinfo.value.kind == "timeout"


def test_build_request_maps_tool_role_directly():
    messages = [Message(role="tool", name="a", tool_call_id="call_1", content="1")]
    payload = build_request("llama3.2:3b", messages, [], 64)
    assert payload["messages"] == [{"role": "tool", "content": "1"}]
