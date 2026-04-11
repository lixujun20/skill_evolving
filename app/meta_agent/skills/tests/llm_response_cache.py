"""Disk-based cache for LLM responses used in pytest tests."""

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, List, Optional

_lock = threading.Lock()

_stats = {"hits": 0, "misses": 0, "stored": 0}


def _cache_dir() -> Path:
    default = Path.home() / ".skill_llm_cache"
    return Path(os.environ.get("LLM_CACHE_DIR", str(default)))


def is_cache_enabled() -> bool:
    return os.environ.get("LLM_CACHE_ENABLED", "") == "1"


def get_cache_stats() -> dict:
    with _lock:
        return dict(_stats)


def _make_key(model: str, messages: Any, tools_or_tag: Any) -> str:
    messages_str = json.dumps(messages, sort_keys=True, default=str)
    if isinstance(tools_or_tag, str):
        tools_str = tools_or_tag
    else:
        tools_str = json.dumps(tools_or_tag or [], sort_keys=True, default=str)
    raw = f"{model}::{messages_str}::{tools_str}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_path(key: str) -> Path:
    return _cache_dir() / f"{key}.json"


# ---------------------------------------------------------------------------
# Mock objects so deserialized responses are duck-type compatible with
# ChatCompletionMessage / ToolCall / Function from openai SDK.
# ---------------------------------------------------------------------------

class MockFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class MockToolCall:
    def __init__(self, id: str, function: MockFunction, type: str = "function"):
        self.id = id
        self.function = function
        self.type = type


class MockChatCompletionMessage:
    def __init__(
        self,
        content: Optional[str],
        role: str = "assistant",
        tool_calls: Optional[List[MockToolCall]] = None,
        refusal: Optional[str] = None,
        function_call: Optional[Any] = None,
    ):
        self.content = content
        self.role = role
        self.tool_calls = tool_calls
        self.refusal = refusal
        self.function_call = function_call


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize_response_ask_tool(response: Any) -> dict:
    """Serialize a ChatCompletionMessage to a plain dict."""
    tool_calls_data = None
    if response.tool_calls:
        tool_calls_data = [
            {
                "id": tc.id,
                "type": getattr(tc, "type", "function"),
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in response.tool_calls
        ]
    return {
        "content": response.content,
        "role": getattr(response, "role", "assistant"),
        "tool_calls": tool_calls_data,
        "refusal": getattr(response, "refusal", None),
        "function_call": getattr(response, "function_call", None),
    }


def _deserialize_response_ask_tool(data: dict) -> MockChatCompletionMessage:
    """Deserialize a plain dict back into a MockChatCompletionMessage."""
    tool_calls = None
    if data.get("tool_calls"):
        tool_calls = [
            MockToolCall(
                id=tc["id"],
                function=MockFunction(
                    name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                ),
                type=tc.get("type", "function"),
            )
            for tc in data["tool_calls"]
        ]
    return MockChatCompletionMessage(
        content=data.get("content"),
        role=data.get("role", "assistant"),
        tool_calls=tool_calls,
        refusal=data.get("refusal"),
        function_call=data.get("function_call"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_cached_response(model: str, messages: Any, tools: Any) -> Optional[Any]:
    key = _make_key(model, messages, tools)
    path = _cache_path(key)
    with _lock:
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                _stats["hits"] += 1
                kind = payload.get("_kind", "ask_tool")
                if kind == "ask":
                    return payload["value"]
                return _deserialize_response_ask_tool(payload["value"])
            except Exception:
                # Corrupted cache entry — treat as miss
                pass
        _stats["misses"] += 1
        return None


def cache_response(model: str, messages: Any, tools: Any, response: Any) -> None:
    key = _make_key(model, messages, tools)
    path = _cache_path(key)
    with _lock:
        _cache_dir().mkdir(parents=True, exist_ok=True)
        if isinstance(tools, str) and tools == "ask":
            payload = {"_kind": "ask", "value": response}
        else:
            payload = {
                "_kind": "ask_tool",
                "value": _serialize_response_ask_tool(response),
            }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        _stats["stored"] += 1
