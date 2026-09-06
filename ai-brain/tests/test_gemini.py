import json

import httpx
import pytest
import respx

from ai_brain.llm import Message, ProviderError, ToolCall, ToolSpec
from ai_brain.llm.gemini import GeminiProvider

URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

WEATHER = ToolSpec(
    name="get_weather",
    description="Look up the weather",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)


def provider(**kw) -> GeminiProvider:
    return GeminiProvider("gemini-2.0-flash", "secret-key", **kw)


def text_response(text="hello", prompt=11, completion=3) -> dict:
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}],
        "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": completion},
    }


@respx.mock
async def test_text_reply():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    reply = await provider().complete([Message(role="user", content="hi")], [], 256)

    assert reply.text == "hello"
    assert reply.tool_calls == ()
    assert reply.usage.prompt_tokens == 11
    assert reply.usage.completion_tokens == 3
    assert reply.model == "gemini-2.0-flash"
    assert route.calls.last.request.headers["x-goog-api-key"] == "secret-key"


@respx.mock
async def test_key_is_derived_from_model():
    assert provider().key == "gemini:gemini-2.0-flash"


@respx.mock
async def test_multiple_text_parts_are_concatenated():
    body = {
        "candidates": [
            {"content": {"parts": [{"text": "one "}, {"text": "two"}]}},
        ],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2},
    }
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    reply = await provider().complete([Message(role="user", content="hi")], [], 256)
    assert reply.text == "one two"


@respx.mock
async def test_missing_usage_metadata_counts_zero():
    body = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    reply = await provider().complete([Message(role="user", content="hi")], [], 256)
    assert reply.usage.prompt_tokens == 0
    assert reply.usage.completion_tokens == 0


@respx.mock
async def test_function_call_reply_with_two_calls():
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "checking"},
                        {"functionCall": {"name": "get_weather", "args": {"city": "Malmo"}}},
                        {"functionCall": {"name": "get_weather", "args": {"city": "Lund"}}},
                    ]
                }
            }
        ],
        "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 7},
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
    body = {"candidates": [{"content": {"parts": [{"functionCall": {"name": "ping"}}]}}]}
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    reply = await provider().complete([Message(role="user", content="hi")], [], 256)
    assert reply.tool_calls == (ToolCall(id="call_1", name="ping", args={}),)


@respx.mock
async def test_request_body_mapping():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    messages = [
        Message(role="system", content="You are a helper."),
        Message(role="user", content="weather in Malmo and Lund?"),
        Message(
            role="assistant",
            content="checking",
            tool_calls=(
                ToolCall(id="call_1", name="get_weather", args={"city": "Malmo"}),
                ToolCall(id="call_2", name="get_weather", args={"city": "Lund"}),
            ),
        ),
        Message(role="tool", name="get_weather", tool_call_id="call_1", content='{"c": 17}'),
        Message(role="tool", name="get_weather", tool_call_id="call_2", content="not json"),
        Message(role="user", content="thanks"),
    ]
    await provider().complete(messages, [WEATHER], 512)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "systemInstruction": {"parts": [{"text": "You are a helper."}]},
        "contents": [
            {"role": "user", "parts": [{"text": "weather in Malmo and Lund?"}]},
            {
                "role": "model",
                "parts": [
                    {"text": "checking"},
                    {"functionCall": {"name": "get_weather", "args": {"city": "Malmo"}}},
                    {"functionCall": {"name": "get_weather", "args": {"city": "Lund"}}},
                ],
            },
            {
                "role": "user",
                "parts": [
                    {"functionResponse": {"name": "get_weather", "response": {"c": 17}}},
                    {
                        "functionResponse": {
                            "name": "get_weather",
                            "response": {"result": "not json"},
                        }
                    },
                ],
            },
            {"role": "user", "parts": [{"text": "thanks"}]},
        ],
        "tools": [
            {
                "functionDeclarations": [
                    {
                        "name": "get_weather",
                        "description": "Look up the weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                        },
                    }
                ]
            }
        ],
        "generationConfig": {"maxOutputTokens": 512, "temperature": 0.7},
    }


@respx.mock
async def test_request_body_omits_optional_sections():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    await provider().complete([Message(role="user", content="hi")], [], 64)

    sent = json.loads(route.calls.last.request.content)
    assert "systemInstruction" not in sent
    assert "tools" not in sent
    assert sent["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]


@respx.mock
async def test_assistant_without_tool_calls_is_a_plain_model_turn():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
        Message(role="user", content="bye"),
    ]
    await provider().complete(messages, [], 64)

    sent = json.loads(route.calls.last.request.content)
    assert sent["contents"] == [
        {"role": "user", "parts": [{"text": "hi"}]},
        {"role": "model", "parts": [{"text": "hello"}]},
        {"role": "user", "parts": [{"text": "bye"}]},
    ]


@respx.mock
async def test_json_tool_content_that_is_not_a_dict_is_wrapped():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    messages = [Message(role="tool", name="listing", content="[1, 2]")]
    await provider().complete(messages, [], 64)

    sent = json.loads(route.calls.last.request.content)
    assert sent["contents"] == [
        {
            "role": "user",
            "parts": [{"functionResponse": {"name": "listing", "response": {"result": "[1, 2]"}}}],
        }
    ]


@respx.mock
async def test_tool_result_does_not_merge_into_a_preceding_text_turn():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    messages = [
        Message(role="user", content="hi"),
        Message(role="tool", name="a", content="1"),
        Message(role="user", content="u"),
        Message(role="tool", name="b", content="2"),
    ]
    await provider().complete(messages, [], 64)

    sent = json.loads(route.calls.last.request.content)
    assert sent["contents"] == [
        {"role": "user", "parts": [{"text": "hi"}]},
        {
            "role": "user",
            "parts": [{"functionResponse": {"name": "a", "response": {"result": "1"}}}],
        },
        {"role": "user", "parts": [{"text": "u"}]},
        {
            "role": "user",
            "parts": [{"functionResponse": {"name": "b", "response": {"result": "2"}}}],
        },
    ]


@respx.mock
async def test_multiple_system_messages_are_joined():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    messages = [
        Message(role="system", content="Rule one."),
        Message(role="system", content="Rule two."),
        Message(role="user", content="hi"),
    ]
    await provider().complete(messages, [], 64)

    sent = json.loads(route.calls.last.request.content)
    assert sent["systemInstruction"] == {"parts": [{"text": "Rule one.\n\nRule two."}]}


@respx.mock
async def test_no_candidates_is_a_server_error():
    body = {"promptFeedback": {"blockReason": "SAFETY"}}
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(ProviderError) as err:
        await provider().complete([Message(role="user", content="hi")], [], 64)

    assert err.value.kind == "server"
    assert err.value.retry_after_s is None
    assert "SAFETY" in str(err.value)


@respx.mock
async def test_404_is_not_found():
    respx.post(URL).mock(
        return_value=httpx.Response(404, json={"error": {"message": "model not found"}})
    )
    with pytest.raises(ProviderError) as err:
        await provider().complete([Message(role="user", content="hi")], [], 64)

    assert err.value.kind == "not_found"
    assert err.value.retry_after_s is None


@respx.mock
async def test_429_parses_retry_delay_from_error_details():
    body = {
        "error": {
            "message": "quota exceeded",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure"},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"},
            ],
        }
    }
    respx.post(URL).mock(return_value=httpx.Response(429, json=body))
    with pytest.raises(ProviderError) as err:
        await provider().complete([Message(role="user", content="hi")], [], 64)

    assert err.value.kind == "rate_limited"
    assert err.value.retry_after_s == 7.0


@respx.mock
async def test_429_prefers_the_retry_after_header():
    body = {"error": {"details": [{"retryDelay": "7s"}]}}
    respx.post(URL).mock(
        return_value=httpx.Response(429, json=body, headers={"Retry-After": "30"})
    )
    with pytest.raises(ProviderError) as err:
        await provider().complete([Message(role="user", content="hi")], [], 64)

    assert err.value.kind == "rate_limited"
    assert err.value.retry_after_s == 30.0


@respx.mock
async def test_429_parses_fractional_retry_delay():
    body = {"error": {"details": [{"retryDelay": "12.5s"}]}}
    respx.post(URL).mock(return_value=httpx.Response(429, json=body))
    with pytest.raises(ProviderError) as err:
        await provider().complete([Message(role="user", content="hi")], [], 64)

    assert err.value.retry_after_s == 12.5


@respx.mock
async def test_429_without_any_hint_has_no_retry_after():
    respx.post(URL).mock(return_value=httpx.Response(429, text="slow down"))
    with pytest.raises(ProviderError) as err:
        await provider().complete([Message(role="user", content="hi")], [], 64)

    assert err.value.kind == "rate_limited"
    assert err.value.retry_after_s is None


@respx.mock
async def test_400_is_bad_request():
    respx.post(URL).mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad field"}})
    )
    with pytest.raises(ProviderError) as err:
        await provider().complete([Message(role="user", content="hi")], [], 64)

    assert err.value.kind == "bad_request"
    assert "bad field" in str(err.value)


@respx.mock
async def test_500_is_server():
    respx.post(URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(ProviderError) as err:
        await provider().complete([Message(role="user", content="hi")], [], 64)

    assert err.value.kind == "server"


@respx.mock
async def test_403_is_server():
    respx.post(URL).mock(return_value=httpx.Response(403, text="forbidden"))
    with pytest.raises(ProviderError) as err:
        await provider().complete([Message(role="user", content="hi")], [], 64)

    assert err.value.kind == "server"


@respx.mock
async def test_timeout_is_mapped():
    respx.post(URL).mock(side_effect=httpx.ConnectTimeout("too slow"))
    with pytest.raises(ProviderError) as err:
        await provider().complete([Message(role="user", content="hi")], [], 64)

    assert err.value.kind == "timeout"


@respx.mock
async def test_transport_error_is_a_server_error():
    respx.post(URL).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(ProviderError) as err:
        await provider().complete([Message(role="user", content="hi")], [], 64)

    assert err.value.kind == "server"


@respx.mock
async def test_api_key_never_reaches_the_url_or_body():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    await provider().complete([Message(role="user", content="hi")], [], 64)

    request = route.calls.last.request
    assert "secret-key" not in str(request.url)
    assert b"secret-key" not in request.content


@respx.mock
async def test_supplied_client_is_reused_and_not_closed():
    respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    async with httpx.AsyncClient() as client:
        p = provider(client=client)
        await p.complete([Message(role="user", content="hi")], [], 64)
        assert p._client is client
        await p.aclose()
        assert not client.is_closed


@respx.mock
async def test_lazy_client_is_created_once_and_closed():
    respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    p = provider()
    await p.complete([Message(role="user", content="hi")], [], 64)
    created = p._client
    await p.complete([Message(role="user", content="hi")], [], 64)
    assert p._client is created
    await p.aclose()
    assert created.is_closed


@respx.mock
async def test_thought_signatures_are_read_off_parts():
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "checking", "thoughtSignature": "sig-T"},
                        {
                            "functionCall": {"name": "get_weather", "args": {"city": "Malmo"}},
                            "thoughtSignature": "sig-A",
                        },
                        {"functionCall": {"name": "get_weather", "args": {"city": "Lund"}}},
                    ]
                }
            }
        ],
        "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 7},
    }
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    reply = await provider().complete([Message(role="user", content="weather?")], [WEATHER], 256)

    assert reply.thought_signature == "sig-T"
    assert reply.tool_calls[0].thought_signature == "sig-A"
    assert reply.tool_calls[1].thought_signature == ""


@respx.mock
async def test_reply_without_signatures_has_empty_ones():
    respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    reply = await provider().complete([Message(role="user", content="hi")], [], 64)
    assert reply.thought_signature == ""


@respx.mock
async def test_thought_signatures_are_echoed_back_on_the_matching_parts():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    messages = [
        Message(role="user", content="weather?"),
        Message(
            role="assistant",
            content="checking",
            tool_calls=(
                ToolCall(
                    id="call_1",
                    name="get_weather",
                    args={"city": "Malmo"},
                    thought_signature="sig-A",
                ),
                ToolCall(id="call_2", name="get_weather", args={"city": "Lund"}),
            ),
            thought_signature="sig-T",
        ),
    ]
    await provider().complete(messages, [WEATHER], 64)

    sent = json.loads(route.calls.last.request.content)
    assert sent["contents"][1] == {
        "role": "model",
        "parts": [
            {"text": "checking", "thoughtSignature": "sig-T"},
            {
                "functionCall": {"name": "get_weather", "args": {"city": "Malmo"}},
                "thoughtSignature": "sig-A",
            },
            {"functionCall": {"name": "get_weather", "args": {"city": "Lund"}}},
        ],
    }


@respx.mock
async def test_no_thought_signature_key_when_there_is_none():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    messages = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="checking",
            tool_calls=(ToolCall(id="call_1", name="get_weather", args={}),),
        ),
    ]
    await provider().complete(messages, [WEATHER], 64)

    assert b"thoughtSignature" not in route.calls.last.request.content
