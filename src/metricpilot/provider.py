"""Bounded adapter for compatible Chat Completions function-calling providers."""

import asyncio
import json
import math
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx


class ProviderError(ValueError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Completion:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


def strict_json(value):
    def reject_constant(_):
        raise ValueError("Nonfinite JSON")

    return json.loads(value, parse_constant=reject_constant)


class Provider:
    def __init__(self, base_url, api_key, model, *, timeout=40, max_retries=2, transport=None):
        try:
            parsed = urlsplit(base_url)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or not parsed.port
                and parsed.netloc.endswith(":")
            ):
                raise ValueError
            if any(ord(char) < 33 for char in base_url):
                raise ValueError
            for value in (api_key, model):
                if not isinstance(value, str) or not value or len(value) > 500:
                    raise ValueError
                if any(ord(char) < 32 for char in value):
                    raise ValueError
            api_key.encode("ascii")
            if not math.isfinite(timeout) or not 0 < timeout <= 120:
                raise ValueError
            if type(max_retries) is not int or not 0 <= max_retries <= 2:
                raise ValueError
        except (ValueError, TypeError, AttributeError, UnicodeError):
            raise ProviderError("模型服务配置无效。") from None
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model = api_key, model
        self.timeout, self.max_retries, self.transport = timeout, max_retries, transport

    async def complete(self, messages, tools=None):
        payload = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            if len(json.dumps(payload, allow_nan=False).encode()) > 524_288:
                raise ProviderError("模型上下文超过本地预算，请缩小查询结果。")
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError("模型请求格式无效。") from None
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for attempt in range(self.max_retries + 1):
                retryable = False
                try:
                    # HTTPX timeout is inactivity-based; this also bounds slow-drip wall time.
                    async with asyncio.timeout(self.timeout):
                        async with client.stream(
                            "POST",
                            self.url,
                            headers={"Authorization": "Bearer " + self.api_key},
                            json=payload,
                        ) as response:
                            if response.status_code == 429 or response.status_code >= 500:
                                retryable = True
                            elif response.status_code != 200:
                                raise ProviderError("模型服务拒绝请求；请检查地址、模型和凭据。")
                            else:
                                body = bytearray()
                                async for chunk in response.aiter_bytes():
                                    body.extend(chunk)
                                    if len(body) > 262_144:
                                        raise ProviderError("模型响应超过本地大小上限。")
                                return self.parse(bytes(body))
                except (httpx.TransportError, TimeoutError):
                    retryable = True
                if retryable and attempt < self.max_retries:
                    await asyncio.sleep(0.1 * (2**attempt))
                    continue
                raise ProviderError("模型服务暂时不可用；请稍后创建新的分析任务。")
        raise ProviderError("模型请求未完成。")

    @staticmethod
    def parse(body):
        try:
            document = strict_json(body)
            choice = document["choices"][0]
            message = choice["message"]
            if choice.get("finish_reason") not in {None, "stop", "tool_calls"} or message.get(
                "refusal"
            ):
                raise ValueError
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list) or len(calls) > 4:
                raise ValueError
            result, identities = [], set()
            for call in calls:
                identity, name = call["id"], call["function"]["name"]
                arguments = call["function"]["arguments"]
                if (
                    call.get("type") != "function"
                    or not isinstance(identity, str)
                    or not 1 <= len(identity) <= 100
                    or identity in identities
                    or not isinstance(name, str)
                    or not 1 <= len(name) <= 80
                    or not isinstance(arguments, str)
                    or len(arguments) > 40_000
                ):
                    raise ValueError
                arguments = strict_json(arguments)
                if not isinstance(arguments, dict):
                    raise ValueError
                identities.add(identity)
                result.append(ToolCall(identity, name, arguments))
            return Completion(content, result)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise ProviderError("模型响应格式无效或工具参数超出预算。") from None
