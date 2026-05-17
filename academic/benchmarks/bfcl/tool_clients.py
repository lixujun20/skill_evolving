"""Provider-specific BFCL native tool-call clients."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from academic.config import LLM_CALL_TIMEOUT, LLM_TIMEOUT_RETRIES, RATE_LIMIT_BASE_WAIT


@dataclass
class ToolModelResponse:
    content: str
    tool_calls: List[Dict[str, Any]]
    assistant_msg: Dict[str, Any]


class ToolApiClient:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    async def ask(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: str,
        tools: List[Dict[str, Any]],
        temperature: Optional[float],
        max_request_wall_s: Optional[float],
    ) -> ToolModelResponse | None:
        raise NotImplementedError

    def tool_result_message(self, tool_call_id: str, content: str) -> Dict[str, Any]:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": content}

    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class AnthropicDirectToolApiClient(ToolApiClient):
    def __init__(self, *, llm_config: str, model_name: Optional[str]) -> None:
        self.state = _make_anthropic_state(llm_config, model_name)
        self.prompt_tokens = 0
        self.completion_tokens = 0

    async def ask(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: str,
        tools: List[Dict[str, Any]],
        temperature: Optional[float],
        max_request_wall_s: Optional[float],
    ) -> ToolModelResponse | None:
        response = await _ask_anthropic_tool_with_retry(
            self.state,
            messages,
            system,
            tools,
            temperature=temperature,
            max_request_wall_s=max_request_wall_s,
        )
        if response is None:
            return None
        content, tool_calls, assistant_msg, usage = _normalize_anthropic_response(response)
        self.prompt_tokens += usage[0]
        self.completion_tokens += usage[1]
        return ToolModelResponse(content=content, tool_calls=tool_calls, assistant_msg=assistant_msg)

    def tool_result_message(self, tool_call_id: str, content: str) -> Dict[str, Any]:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": content,
                }
            ],
        }


class OpenAIDirectToolApiClient(ToolApiClient):
    def __init__(self, *, llm_config: str, model_name: Optional[str]) -> None:
        self.state = _make_openai_direct_state(llm_config, model_name)
        self.prompt_tokens = 0
        self.completion_tokens = 0

    async def ask(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: str,
        tools: List[Dict[str, Any]],
        temperature: Optional[float],
        max_request_wall_s: Optional[float],
    ) -> ToolModelResponse | None:
        response = await _ask_openai_direct_tool_with_retry(
            self.state,
            messages,
            system,
            tools,
            temperature=temperature,
            max_request_wall_s=max_request_wall_s,
        )
        if response is None:
            return None
        content, tool_calls, assistant_msg, usage = response
        self.prompt_tokens += usage[0]
        self.completion_tokens += usage[1]
        return ToolModelResponse(content=content, tool_calls=tool_calls, assistant_msg=assistant_msg)


class OpenAIStreamToolApiClient(ToolApiClient):
    def __init__(self, *, llm_config: str, model_name: Optional[str]) -> None:
        self.state = _make_openai_stream_state(llm_config, model_name)
        self.prompt_tokens = 0
        self.completion_tokens = 0

    async def ask(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: str,
        tools: List[Dict[str, Any]],
        temperature: Optional[float],
        max_request_wall_s: Optional[float],
    ) -> ToolModelResponse | None:
        response = await _ask_openai_stream_tool_with_retry(
            self.state,
            messages,
            system,
            tools,
            temperature=temperature,
            max_request_wall_s=max_request_wall_s,
        )
        if response is None:
            return None
        content, tool_calls, assistant_msg, usage = response
        self.prompt_tokens += usage[0]
        self.completion_tokens += usage[1]
        return ToolModelResponse(content=content, tool_calls=tool_calls, assistant_msg=assistant_msg)


class LegacyLLMToolApiClient(ToolApiClient):
    def __init__(self, *, llm_config: str) -> None:
        from app.llm import LLM

        self.llm = LLM(config_name=llm_config)
        self._tokens_before = self.llm.total_input_tokens + self.llm.total_completion_tokens
        self._completion_before = self.llm.total_completion_tokens

    @property
    def prompt_tokens(self) -> int:
        return self.total_tokens() - self.completion_tokens

    @property
    def completion_tokens(self) -> int:
        return self.llm.total_completion_tokens - self._completion_before

    async def ask(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: str,
        tools: List[Dict[str, Any]],
        temperature: Optional[float],
        max_request_wall_s: Optional[float],
    ) -> ToolModelResponse | None:
        response = await _ask_tool_with_retry(
            self.llm,
            messages,
            system,
            tools,
            model_name=None,
            temperature=temperature,
            max_request_wall_s=max_request_wall_s,
        )
        if response is None:
            return None
        content = response.content or ""
        tool_calls = [
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }
            for tc in (response.tool_calls or [])
        ]
        assistant_msg: Dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                }
                for tc in tool_calls
            ]
        return ToolModelResponse(content=content, tool_calls=tool_calls, assistant_msg=assistant_msg)

    def total_tokens(self) -> int:
        return (self.llm.total_input_tokens + self.llm.total_completion_tokens) - self._tokens_before


def make_tool_api_client(
    *,
    resolved_api_style: str,
    llm_config: str,
    model_name: Optional[str],
) -> ToolApiClient:
    factories = {
        "anthropic_direct": lambda: AnthropicDirectToolApiClient(llm_config=llm_config, model_name=model_name),
        "openai_direct": lambda: OpenAIDirectToolApiClient(llm_config=llm_config, model_name=model_name),
        "openai_stream": lambda: OpenAIStreamToolApiClient(llm_config=llm_config, model_name=model_name),
    }
    factory = factories.get(resolved_api_style)
    if factory is not None:
        return factory()
    return LegacyLLMToolApiClient(llm_config=llm_config)


def resolve_tool_api_style(
    requested: str,
    llm_config: str,
    model_name: Optional[str],
) -> str:
    """Pick the provider interaction style used for BFCL native tool calls."""
    if requested == "openai":
        return "openai_direct"
    if requested != "auto":
        return requested
    cfg = _llm_settings(llm_config)
    model = (model_name or cfg.model or "").lower()
    base_url = (cfg.base_url or "").lower()
    if model.startswith("claude-") or "anthropic.com" in base_url:
        return "anthropic_direct"
    if "bigmodel.cn" in base_url or cfg.api_type == "bigmodel":
        return "openai_direct"
    if "qwen" in model:
        return "openai_stream"
    return "openai_direct"


def _llm_settings(llm_config: str) -> Any:
    from app.config import config

    return config.llm.get(llm_config, config.llm["default"])


def _anthropic_tool(tool: Dict[str, Any]) -> Dict[str, Any]:
    function = tool.get("function", {})
    out = {
        "name": function.get("name", ""),
        "description": function.get("description", ""),
        "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
    }
    return {key: value for key, value in out.items() if value not in ("", None)}


def _make_anthropic_state(llm_config: str, model_name: Optional[str]) -> Dict[str, Any]:
    cfg = _llm_settings(llm_config)
    api_key = cfg.api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(f"Missing Anthropic API key for llm config '{llm_config}'")
    try:
        from anthropic import AsyncAnthropic
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "anthropic package is required for --bfcl-tool-api-style anthropic_direct. "
            "Install with `pip install anthropic`."
        ) from exc
    kwargs: Dict[str, Any] = {"api_key": api_key}
    if cfg.base_url:
        base_url = cfg.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        kwargs["base_url"] = base_url
    return {
        "client": AsyncAnthropic(**kwargs),
        "model": model_name or cfg.model,
        "max_tokens": int(cfg.max_tokens or 32768),
        "temperature": float(cfg.temperature if cfg.temperature is not None else 0.0),
    }


def _anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    converted: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        if role == "system":
            continue
        if role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": str(msg.get("content", "")),
                        }
                    ],
                }
            )
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        content = msg.get("content", "")
        if isinstance(content, list):
            normalized = copy.deepcopy(content)
        elif content is None:
            normalized = []
        else:
            normalized = [{"type": "text", "text": str(content)}]
        if not normalized and role == "assistant" and msg.get("tool_calls"):
            normalized = []
        converted.append({"role": role, "content": normalized})
    return _merge_consecutive_anthropic_messages(converted)


def _merge_consecutive_anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for msg in messages:
        if not msg.get("content"):
            continue
        if merged and merged[-1].get("role") == msg.get("role"):
            left = merged[-1].setdefault("content", [])
            right = msg.get("content", [])
            if isinstance(left, list) and isinstance(right, list):
                left.extend(right)
            else:
                merged.append(msg)
        else:
            merged.append(msg)
    return merged


async def _ask_anthropic_tool_with_retry(
    state: Dict[str, Any],
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = 0.001,
    max_request_wall_s: Optional[float] = None,
) -> Any:
    timeout_count = 0
    while True:
        try:
            effective_timeout = _effective_llm_timeout(max_request_wall_s)
            if effective_timeout <= 0:
                return None
            kwargs: Dict[str, Any] = {
                "model": state["model"],
                "max_tokens": state["max_tokens"],
                "temperature": temperature if temperature is not None else state["temperature"],
                "tools": [_anthropic_tool(tool) for tool in tools],
                "messages": _anthropic_messages(messages),
                "timeout": effective_timeout,
            }
            if system:
                kwargs["system"] = [{"type": "text", "text": system}]
            return await asyncio.wait_for(
                state["client"].messages.create(**kwargs),
                timeout=effective_timeout + 60,
            )
        except asyncio.TimeoutError:
            timeout_count += 1
            if timeout_count >= LLM_TIMEOUT_RETRIES:
                return None
            await asyncio.sleep(min(30 * timeout_count, 120))


def _normalize_anthropic_response(response: Any) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any], Tuple[int, int]]:
    text_parts: List[str] = []
    assistant_content: List[Dict[str, Any]] = []
    tool_calls: List[Dict[str, Any]] = []
    for block in getattr(response, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            text = getattr(block, "text", "")
            text_parts.append(text)
            assistant_content.append({"type": "text", "text": text})
        elif btype == "tool_use":
            tool_id = getattr(block, "id", "")
            name = getattr(block, "name", "")
            input_args = getattr(block, "input", {}) or {}
            assistant_content.append(
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": name,
                    "input": input_args,
                }
            )
            tool_calls.append(
                {
                    "id": tool_id,
                    "name": name,
                    "arguments": json.dumps(input_args, ensure_ascii=False),
                }
            )
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return (
        "\n".join(part for part in text_parts if part),
        tool_calls,
        {"role": "assistant", "content": assistant_content},
        (prompt_tokens, completion_tokens),
    )


def _make_openai_stream_state(llm_config: str, model_name: Optional[str]) -> Dict[str, Any]:
    from openai import AsyncOpenAI

    cfg = _llm_settings(llm_config)
    return {
        "client": AsyncOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            timeout=LLM_CALL_TIMEOUT,
        ),
        "model": model_name or cfg.model,
        "max_tokens": int(cfg.max_tokens or 32768),
        "temperature": float(cfg.temperature if cfg.temperature is not None else 0.0),
    }


def _make_openai_direct_state(llm_config: str, model_name: Optional[str]) -> Dict[str, Any]:
    from openai import AsyncOpenAI

    cfg = _llm_settings(llm_config)
    return {
        "client": AsyncOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            timeout=LLM_CALL_TIMEOUT,
        ),
        "model": model_name or cfg.model,
        "temperature": float(cfg.temperature if cfg.temperature is not None else 0.001),
    }


async def _ask_openai_direct_tool_with_retry(
    state: Dict[str, Any],
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = 0.001,
    max_request_wall_s: Optional[float] = None,
) -> Optional[Tuple[str, List[Dict[str, Any]], Dict[str, Any], Tuple[int, int]]]:
    attempt = 0
    timeout_count = 0
    while True:
        attempt += 1
        try:
            effective_timeout = _effective_llm_timeout(max_request_wall_s)
            if effective_timeout <= 0:
                return None
            return await asyncio.wait_for(
                _ask_openai_direct_tool_once(
                    state,
                    messages,
                    system,
                    tools,
                    temperature=temperature,
                    request_timeout_s=effective_timeout,
                ),
                timeout=effective_timeout + 60,
            )
        except asyncio.TimeoutError:
            timeout_count += 1
            if timeout_count >= LLM_TIMEOUT_RETRIES:
                return None
            await asyncio.sleep(min(30 * timeout_count, 120))
        except Exception as exc:
            err_str = str(exc)
            is_retryable_transient = (
                "429" in err_str
                or "rate" in err_str.lower()
                or "速率" in err_str
                or "connection error" in err_str.lower()
                or "apiconnectionerror" in type(exc).__name__.lower()
                or "apitimeouterror" in type(exc).__name__.lower()
                or type(exc).__name__ == "RateLimitError"
            )
            if is_retryable_transient:
                await asyncio.sleep(min(RATE_LIMIT_BASE_WAIT * attempt, 300))
                continue
            raise


async def _ask_openai_direct_tool_once(
    state: Dict[str, Any],
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = 0.001,
    request_timeout_s: Optional[float] = None,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any], Tuple[int, int]]:
    send_messages = [{"role": "system", "content": system}] + messages if system else list(messages)
    params: Dict[str, Any] = {
        "model": state["model"],
        "messages": send_messages,
        "temperature": temperature if temperature is not None else state["temperature"],
        "store": False,
    }
    if request_timeout_s is not None:
        params["timeout"] = request_timeout_s
    if tools:
        params["tools"] = tools
    response = await state["client"].chat.completions.create(**params)
    message = response.choices[0].message
    content = message.content or ""
    tool_calls = [
        {
            "id": tc.id,
            "name": tc.function.name,
            "arguments": tc.function.arguments,
        }
        for tc in (message.tool_calls or [])
    ]
    assistant_msg: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                },
            }
            for tc in tool_calls
        ]
    usage = response.usage
    return (
        content,
        tool_calls,
        assistant_msg,
        (
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
        ),
    )


async def _ask_openai_stream_tool_with_retry(
    state: Dict[str, Any],
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = 0.001,
    max_request_wall_s: Optional[float] = None,
) -> Optional[Tuple[str, List[Dict[str, Any]], Dict[str, Any], Tuple[int, int]]]:
    attempt = 0
    timeout_count = 0
    while True:
        attempt += 1
        try:
            effective_timeout = _effective_llm_timeout(max_request_wall_s)
            if effective_timeout <= 0:
                return None
            return await asyncio.wait_for(
                _ask_openai_stream_tool_once(
                    state,
                    messages,
                    system,
                    tools,
                    temperature=temperature,
                    request_timeout_s=effective_timeout,
                ),
                timeout=effective_timeout + 60,
            )
        except asyncio.TimeoutError:
            timeout_count += 1
            if timeout_count >= LLM_TIMEOUT_RETRIES:
                return None
            await asyncio.sleep(min(30 * timeout_count, 120))
        except Exception as exc:
            err_str = str(exc)
            is_retryable_transient = (
                "429" in err_str
                or "rate" in err_str.lower()
                or "速率" in err_str
                or "connection error" in err_str.lower()
                or "apiconnectionerror" in type(exc).__name__.lower()
                or "apitimeouterror" in type(exc).__name__.lower()
                or type(exc).__name__ == "RateLimitError"
            )
            if is_retryable_transient:
                await asyncio.sleep(min(RATE_LIMIT_BASE_WAIT * attempt, 300))
                continue
            raise


async def _ask_openai_stream_tool_once(
    state: Dict[str, Any],
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = 0.001,
    request_timeout_s: Optional[float] = None,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any], Tuple[int, int]]:
    send_messages = [{"role": "system", "content": system}] + messages if system else list(messages)
    params: Dict[str, Any] = {
        "model": state["model"],
        "messages": send_messages,
        "temperature": temperature if temperature is not None else state["temperature"],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if request_timeout_s is not None:
        params["timeout"] = request_timeout_s
    if tools:
        params["tools"] = tools
    stream = await state["client"].chat.completions.create(**params)
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_info: Dict[int, Dict[str, str]] = {}
    usage_pair = (0, 0)
    async for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            usage_pair = (
                int(getattr(usage, "prompt_tokens", 0) or 0),
                int(getattr(usage, "completion_tokens", 0) or 0),
            )
        if not getattr(chunk, "choices", None):
            continue
        delta = chunk.choices[0].delta
        if getattr(delta, "content", None):
            content_parts.append(delta.content)
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            reasoning_parts.append(reasoning)
        for tc in getattr(delta, "tool_calls", None) or []:
            idx = int(getattr(tc, "index", 0) or 0)
            info = tool_info.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if getattr(tc, "id", None):
                info["id"] += tc.id
            function = getattr(tc, "function", None)
            if function is not None:
                if getattr(function, "name", None):
                    info["name"] += function.name
                if getattr(function, "arguments", None):
                    info["arguments"] += function.arguments
    tool_calls = [
        {
            "id": info.get("id") or f"call_{idx}",
            "name": info.get("name", ""),
            "arguments": info.get("arguments", "{}"),
        }
        for idx, info in sorted(tool_info.items())
        if info.get("name")
    ]
    content = "".join(content_parts)
    assistant_msg: Dict[str, Any] = {"role": "assistant", "content": content or None}
    if tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": call["id"],
                "type": "function",
                "function": {"name": call["name"], "arguments": call["arguments"]},
            }
            for call in tool_calls
        ]
    if reasoning_parts:
        assistant_msg["reasoning_content"] = "".join(reasoning_parts)
    return content, tool_calls, assistant_msg, usage_pair


async def _ask_tool_with_retry(
    llm: Any,
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]],
    *,
    model_name: Optional[str] = None,
    temperature: Optional[float] = 0.001,
    max_request_wall_s: Optional[float] = None,
) -> Any:
    timeout_count = 0
    while True:
        try:
            effective_timeout = _effective_llm_timeout(max_request_wall_s)
            if effective_timeout <= 0:
                return None
            return await asyncio.wait_for(
                llm.ask_tool(
                    messages=messages,
                    system_msgs=[{"role": "system", "content": system}] if system else None,
                    tools=tools,
                    timeout=effective_timeout,
                    new_model=model_name,
                    temperature=temperature,
                ),
                timeout=effective_timeout + 60,
            )
        except asyncio.TimeoutError:
            timeout_count += 1
            if timeout_count >= LLM_TIMEOUT_RETRIES:
                return None
            await asyncio.sleep(min(30 * timeout_count, 120))


def _effective_llm_timeout(max_request_wall_s: Optional[float]) -> int:
    """Bound a single LLM request so it cannot consume an entire task budget."""
    if max_request_wall_s is None:
        return int(LLM_CALL_TIMEOUT)
    bounded = min(float(LLM_CALL_TIMEOUT), max(0.0, float(max_request_wall_s) - 15.0))
    return max(1, int(bounded))


__all__ = [
    "ToolModelResponse",
    "ToolApiClient",
    "AnthropicDirectToolApiClient",
    "OpenAIDirectToolApiClient",
    "OpenAIStreamToolApiClient",
    "LegacyLLMToolApiClient",
    "make_tool_api_client",
    "resolve_tool_api_style",
]
