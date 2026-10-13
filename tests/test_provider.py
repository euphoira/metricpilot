import asyncio
import json

import httpx
import pytest

from metricpilot.provider import Provider, ProviderError

MESSAGES = [{"role": "user", "content": "Inspect schema"}]


def response_payload(arguments="{}"):
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-a",
                            "type": "function",
                            "function": {"name": "inspect_schema", "arguments": arguments},
                        },
                    ],
                }
            }
        ]
    }


def provider(handler, **kwargs):
    return Provider(
        "https://example.test/v1",
        "test-only",
        "compatible-model",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def test_actual_function_protocol_and_authentication():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-only"
        payload = json.loads(request.content)
        assert payload["messages"] == MESSAGES
        assert payload["tool_choice"] == "auto"
        return httpx.Response(200, json=response_payload())

    result = await provider(handler).complete(MESSAGES, [{"type": "function"}])
    assert result.tool_calls[0].name == "inspect_schema"
    assert result.tool_calls[0].arguments == {}


async def test_transient_errors_retry_but_auth_errors_do_not():
    calls = []

    def transient(request):
        calls.append(request)
        return httpx.Response(429 if len(calls) < 3 else 200, json=response_payload())

    await provider(transient).complete(MESSAGES)
    assert len(calls) == 3
    calls.clear()

    def unauthorized(request):
        calls.append(request)
        return httpx.Response(401, text="PRIVATE RESPONSE MUST NOT LEAK")

    with pytest.raises(ProviderError) as error:
        await provider(unauthorized).complete(MESSAGES)
    assert "PRIVATE" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("arguments", ["[]", "null", "{broken", '{"value":NaN}', '"text"'])
async def test_malformed_tool_arguments_are_rejected(arguments):
    with pytest.raises(ProviderError):
        await provider(lambda _: httpx.Response(200, json=response_payload(arguments))).complete(
            MESSAGES
        )


async def test_oversized_responses_and_duplicate_calls_are_rejected():
    with pytest.raises(ProviderError):
        await provider(lambda _: httpx.Response(200, content=b"x" * 262_145)).complete(MESSAGES)
    payload = response_payload()
    payload["choices"][0]["message"]["tool_calls"] *= 2
    with pytest.raises(ProviderError):
        await provider(lambda _: httpx.Response(200, json=payload)).complete(MESSAGES)


async def test_transport_retries_are_bounded_and_safe():
    calls = []

    def failing(request):
        calls.append(request)
        raise httpx.ConnectError("sensitive endpoint detail")

    with pytest.raises(ProviderError) as error:
        await provider(failing, max_retries=1).complete(MESSAGES)
    assert "sensitive" not in str(error.value)
    assert len(calls) == 2


async def test_total_deadline_stops_slow_drip_responses():
    class SlowStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            for _ in range(6):
                await asyncio.sleep(0.02)
                yield b" "
            yield json.dumps(response_payload()).encode()

        async def aclose(self):
            self.closed = True

    stream = SlowStream()
    with pytest.raises(ProviderError):
        await provider(
            lambda _: httpx.Response(200, stream=stream), timeout=0.03, max_retries=0
        ).complete(MESSAGES)
    assert stream.closed


@pytest.mark.parametrize("reason", ["length", "content_filter", "error"])
async def test_incomplete_filtered_and_refused_responses_never_execute_tools(reason):
    payload = response_payload()
    payload["choices"][0]["finish_reason"] = reason
    with pytest.raises(ProviderError):
        await provider(lambda _: httpx.Response(200, json=payload)).complete(MESSAGES)
    payload["choices"][0]["finish_reason"] = "tool_calls"
    payload["choices"][0]["message"]["refusal"] = "Request refused"
    with pytest.raises(ProviderError):
        await provider(lambda _: httpx.Response(200, json=payload)).complete(MESSAGES)


@pytest.mark.parametrize(
    "options",
    [
        {"base_url": "file:///private"},
        {"base_url": "https://user:pass@example.test"},
        {"base_url": "https://example.test?token=secret"},
        {"base_url": "https://example.test:invalid"},
        {"api_key": "test\r\ninjected"},
        {"api_key": ""},
        {"timeout": float("nan")},
        {"max_retries": True},
        {"max_retries": 3},
    ],
)
def test_invalid_configuration_is_rejected(options):
    settings = {"base_url": "https://example.test", "api_key": "test-only", "model": "model"}
    with pytest.raises(ProviderError):
        Provider(**{**settings, **options})
