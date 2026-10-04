"""Provider adapters for the three supported model APIs.

All adapters share one internal conversation format:

    {"role": "user", "content": str}
    {"role": "assistant", "content": str | None, "tool_calls": [{"id", "name", "arguments"}]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

REQUEST_TIMEOUT_S = 240


class ProviderError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Completion:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


def _post_json(url: str, headers: dict, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            **headers,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:800]
        raise ProviderError(f"HTTP {error.code} from provider: {detail}") from error
    except urllib.error.URLError as error:
        raise ProviderError(f"could not reach provider: {error.reason}") from error
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise ProviderError(f"provider returned invalid JSON: {body[:300]}") from error


class AnthropicProvider:
    base_url = "https://api.anthropic.com"

    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Completion:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": _to_anthropic_messages(messages),
        }
        if tools:
            payload["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "input_schema": tool["parameters"],
                }
                for tool in tools
            ]
        data = _post_json(
            f"{self.base_url}/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            payload,
        )
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=str(block.get("id")),
                        name=str(block.get("name")),
                        arguments=block.get("input") or {},
                    )
                )
        return Completion(text="".join(text_parts).strip(), tool_calls=calls)


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for message in messages:
        role = message["role"]
        if role == "user":
            out.append({"role": "user", "content": message["content"]})
        elif role == "assistant":
            blocks: list[dict] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call.get("arguments") or {},
                    }
                )
            if not blocks:
                blocks.append({"type": "text", "text": ""})
            out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message["tool_call_id"],
                "content": message["content"],
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


class OpenAIProvider:
    base_url = "https://api.openai.com/v1"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Completion:
        chat_messages: list[dict] = [{"role": "system", "content": system}]
        for message in messages:
            role = message["role"]
            if role == "user":
                chat_messages.append({"role": "user", "content": message["content"]})
            elif role == "assistant":
                entry: dict = {"role": "assistant", "content": message.get("content")}
                calls = message.get("tool_calls") or []
                if calls:
                    entry["tool_calls"] = [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(
                                    call.get("arguments") or {}, ensure_ascii=False
                                ),
                            },
                        }
                        for call in calls
                    ]
                if entry["content"] is None and not calls:
                    entry["content"] = ""
                chat_messages.append(entry)
            elif role == "tool":
                chat_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": message["tool_call_id"],
                        "content": message["content"],
                    }
                )

        payload: dict = {"model": self.model, "messages": chat_messages}
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                    },
                }
                for tool in tools
            ]
        data = _post_json(
            f"{self.base_url}/chat/completions",
            {"authorization": f"Bearer {self.api_key}"},
            payload,
        )
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError(f"unexpected response shape: {json.dumps(data)[:400]}") from error
        calls = []
        for call in message.get("tool_calls") or []:
            raw_arguments = (call.get("function") or {}).get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = {"_raw": raw_arguments}
            calls.append(
                ToolCall(
                    id=str(call.get("id") or f"call_{len(calls)}"),
                    name=str((call.get("function") or {}).get("name") or ""),
                    arguments=arguments if isinstance(arguments, dict) else {"_raw": arguments},
                )
            )
        return Completion(
            text=(message.get("content") or "").strip(),
            tool_calls=calls,
        )


class GeminiProvider:
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    _stripped_keys = {
        "$schema",
        "$defs",
        "additionalProperties",
        "default",
        "examples",
        "title",
        "exclusiveMinimum",
        "exclusiveMaximum",
    }

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Completion:
        payload: dict = {
            "contents": _to_gemini_contents(messages),
            "systemInstruction": {"parts": [{"text": system}]},
        }
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool["name"],
                            "description": tool["description"],
                            "parameters": self._clean_schema(tool["parameters"]),
                        }
                        for tool in tools
                    ]
                }
            ]
        data = _post_json(
            f"{self.base_url}/models/{self.model}:generateContent",
            {"x-goog-api-key": self.api_key},
            payload,
        )
        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise ProviderError(f"prompt blocked: {feedback['blockReason']}")
        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError(f"no candidates returned: {json.dumps(data)[:400]}")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if "text" in part).strip()
        calls: list[ToolCall] = []
        for index, part in enumerate(parts):
            function_call = part.get("functionCall")
            if function_call:
                calls.append(
                    ToolCall(
                        id=f"call_{index}_{function_call.get('name', 'unknown')}",
                        name=str(function_call.get("name") or ""),
                        arguments=function_call.get("args") or {},
                    )
                )
        return Completion(text=text, tool_calls=calls)

    @classmethod
    def _clean_schema(cls, schema: object) -> object:
        if isinstance(schema, dict):
            cleaned = {
                key: cls._clean_schema(value)
                for key, value in schema.items()
                if key not in cls._stripped_keys
            }
            return cleaned
        if isinstance(schema, list):
            return [cls._clean_schema(item) for item in schema]
        return schema


def _to_gemini_contents(messages: list[dict]) -> list[dict]:
    contents: list[dict] = []
    for message in messages:
        role = message["role"]
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": message["content"]}]})
        elif role == "assistant":
            parts: list[dict] = []
            if message.get("content"):
                parts.append({"text": message["content"]})
            for call in message.get("tool_calls") or []:
                parts.append(
                    {
                        "functionCall": {
                            "name": call["name"],
                            "args": call.get("arguments") or {},
                        }
                    }
                )
            if not parts:
                parts.append({"text": ""})
            contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": message.get("name") or "tool",
                                "response": {"result": message["content"]},
                            }
                        }
                    ],
                }
            )
    return contents


def build_provider(provider: str, api_key: str, model: str, max_tokens: int):
    if provider == "anthropic":
        return AnthropicProvider(api_key, model, max_tokens)
    if provider == "openai":
        return OpenAIProvider(api_key, model)
    if provider == "gemini":
        return GeminiProvider(api_key, model)
    raise ProviderError(f"unknown provider \"{provider}\"")
