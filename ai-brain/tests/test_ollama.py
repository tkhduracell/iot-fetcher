import json

import httpx
import pytest
import respx

from ai_brain.llm import Message, ProviderError, ToolCall, ToolSpec
from ai_brain.llm.ollama import DEFAULT_NUM_CTX, OllamaProvider, build_request

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


def test_ollama_provider_declares_its_own_call_timeout():
    from ai_brain.llm.ollama import CALL_TIMEOUT_S

    assert provider().call_timeout_s == CALL_TIMEOUT_S
    assert provider().call_timeout_s > 60  # longer than the chain's own default


@respx.mock
async def test_text_reply():
    respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    reply = await provider().complete([Message(role="user", content="hi")], [], 256)

    assert reply.text == "hello"
    assert reply.tool_calls == ()
    assert reply.usage.prompt_tokens == 11
    assert reply.usage.completion_tokens == 3
    assert reply.model == "llama3.2:3b"


@respx.mock
async def test_a_thinking_model_keeps_its_reasoning_out_of_the_answer():
    body = text_response()
    body["message"]["thinking"] = "The spa is the only thing drawing power."
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    reply = await provider().complete([Message(role="user", content="hi")], [], 256)

    assert reply.text == "hello"
    assert reply.thinking == "The spa is the only thing drawing power."


@respx.mock
async def test_a_model_that_does_not_think_has_no_thinking():
    respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    reply = await provider().complete([Message(role="user", content="hi")], [], 256)

    assert reply.thinking == ""


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
    reply = await provider().complete(
        [Message(role="user", content="weather?")], [WEATHER], 256
    )

    assert reply.text == "checking"
    assert reply.tool_calls == (
        ToolCall(id="call_1", name="get_weather", args={"city": "Malmo"}),
        ToolCall(id="call_2", name="get_weather", args={"city": "Lund"}),
    )


@respx.mock
async def test_function_call_without_args_defaults_to_empty_dict():
    body = {
        "message": {"role": "assistant", "tool_calls": [{"function": {"name": "ping"}}]}
    }
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
            tool_calls=(
                ToolCall(id="call_1", name="get_weather", args={"city": "Malmo"}),
            ),
        ),
        Message(
            role="tool", name="get_weather", tool_call_id="call_1", content='{"c": 17}'
        ),
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
                    {
                        "function": {
                            "name": "get_weather",
                            "arguments": {"city": "Malmo"},
                        }
                    }
                ],
            },
            {"role": "tool", "content": '{"c": 17}'},
        ],
        "stream": False,
        "options": {"num_predict": 512, "temperature": 0.7, "num_ctx": DEFAULT_NUM_CTX},
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
        return_value=httpx.Response(404, json={"error": 'model "nope" not found'})
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
        await provider(client=client).complete(
            [Message(role="user", content="hi")], [], 64
        )
    assert excinfo.value.kind == "timeout"


def test_build_request_maps_tool_role_directly():
    messages = [Message(role="tool", name="a", tool_call_id="call_1", content="1")]
    payload = build_request("llama3.2:3b", messages, [], 64)
    assert payload["messages"] == [{"role": "tool", "content": "1"}]


# --- num_ctx ----------------------------------------------------------------


def test_build_request_sends_num_ctx_with_a_default():
    payload = build_request("llama3.2:3b", [Message(role="user", content="hi")], [], 64)
    assert payload["options"]["num_ctx"] == DEFAULT_NUM_CTX


def test_build_request_honours_an_explicit_num_ctx():
    payload = build_request(
        "llama3.2:3b", [Message(role="user", content="hi")], [], 64, num_ctx=32768
    )
    assert payload["options"]["num_ctx"] == 32768


@respx.mock
async def test_provider_sends_its_configured_num_ctx():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    await provider(num_ctx=12345).complete([Message(role="user", content="hi")], [], 64)

    sent = json.loads(route.calls.last.request.content)
    assert sent["options"]["num_ctx"] == 12345


def test_provider_defaults_num_ctx_to_a_pi5_sized_value():
    # Observed cycle prompts run ~9.7k tokens; the default must clear that.
    assert DEFAULT_NUM_CTX == 16384
    assert DEFAULT_NUM_CTX > 9700
    p = provider()
    assert p._num_ctx == DEFAULT_NUM_CTX


# --- fitting an oversized prompt into num_ctx --------------------------------


def _big_messages(n_tool_msgs=5, filler_chars=2000):
    filler = "x" * filler_chars
    messages = [Message(role="system", content="You are the brain.")]
    for i in range(n_tool_msgs):
        messages.append(
            Message(role="tool", name="probe", tool_call_id=f"call_{i}", content=filler)
        )
    messages.append(Message(role="user", content="what's the status?"))
    return messages


@respx.mock
async def test_an_oversized_prompt_is_trimmed_not_sent_verbatim(caplog):
    import logging

    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    messages = _big_messages()

    with caplog.at_level(logging.WARNING):
        await provider(num_ctx=1024).complete(messages, [], 64)

    assert any("exceeds num_ctx" in r.getMessage() for r in caplog.records)
    sent = json.loads(route.calls.last.request.content)
    sent_tool_bodies = [m["content"] for m in sent["messages"] if m["role"] == "tool"]
    assert any("[trimmed" in c for c in sent_tool_bodies)


@respx.mock
async def test_trimming_keeps_the_system_and_final_user_turn():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    messages = _big_messages()

    await provider(num_ctx=1024).complete(messages, [], 64)

    sent = json.loads(route.calls.last.request.content)
    assert sent["messages"][0] == {"role": "system", "content": "You are the brain."}
    assert sent["messages"][-1] == {"role": "user", "content": "what's the status?"}


@respx.mock
async def test_a_prompt_that_fits_is_sent_unmodified(caplog):
    import logging

    route = respx.post(URL).mock(return_value=httpx.Response(200, json=text_response()))
    messages = [
        Message(role="system", content="short"),
        Message(role="user", content="hi"),
    ]

    with caplog.at_level(logging.WARNING):
        await provider(num_ctx=DEFAULT_NUM_CTX).complete(messages, [], 64)

    assert not any("exceeds num_ctx" in r.getMessage() for r in caplog.records)
    sent = json.loads(route.calls.last.request.content)
    assert sent["messages"] == [
        {"role": "system", "content": "short"},
        {"role": "user", "content": "hi"},
    ]
