"""Benchmark-agnostic text LLM clients.

Benchmark adapters should not assume every model endpoint speaks OpenAI chat
completions. This module provides a small text-only facade for Anthropic-style,
OpenAI-compatible, and legacy app.llm backends.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from academic.config import LLM_CALL_TIMEOUT, LLM_TIMEOUT_RETRIES


@dataclass
class TextLLMResponse:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_input_tokens: int = 0
    model_name: str = ""
    api_style: str = ""


def resolve_text_api_style(
    requested: str,
    llm_config: str,
    model_name: Optional[str],
) -> str:
    if requested != "auto":
        return requested
    cfg = _llm_settings(llm_config)
    model = (model_name or cfg.model or "").lower()
    base_url = (cfg.base_url or "").lower()
    if model.startswith("claude-") or "anthropic.com" in base_url:
        return "anthropic_direct"
    if "bigmodel.cn" in base_url or cfg.api_type == "bigmodel":
        return "openai_direct"
    return "openai_direct"


async def ask_text_llm(
    *,
    llm_config: str,
    model_name: Optional[str],
    system: str,
    prompt: str,
    messages: Optional[List[Dict[str, str]]] = None,
    api_style: str = "auto",
    temperature: Optional[float] = None,
    max_request_wall_s: Optional[float] = None,
) -> TextLLMResponse:
    resolved = resolve_text_api_style(api_style, llm_config, model_name)
    if resolved == "anthropic_direct":
        return await _ask_anthropic_text(
            llm_config=llm_config,
            model_name=model_name,
            system=system,
            prompt=prompt,
            messages=messages,
            temperature=temperature,
            max_request_wall_s=max_request_wall_s,
            api_style=resolved,
        )
    if resolved == "openai_direct":
        return await _ask_openai_text(
            llm_config=llm_config,
            model_name=model_name,
            system=system,
            prompt=prompt,
            messages=messages,
            temperature=temperature,
            max_request_wall_s=max_request_wall_s,
            api_style=resolved,
        )
    if resolved == "legacy":
        return await _ask_legacy_text(
            llm_config=llm_config,
            model_name=model_name,
            system=system,
            prompt=prompt,
            messages=messages,
            temperature=temperature,
            max_request_wall_s=max_request_wall_s,
            api_style=resolved,
        )
    raise ValueError(f"Unknown text LLM api_style: {resolved}")


async def _ask_anthropic_text(
    *,
    llm_config: str,
    model_name: Optional[str],
    system: str,
    prompt: str,
    messages: Optional[List[Dict[str, str]]],
    temperature: Optional[float],
    max_request_wall_s: Optional[float],
    api_style: str,
) -> TextLLMResponse:
    cfg = _llm_settings(llm_config)
    try:
        from anthropic import AsyncAnthropic
    except ModuleNotFoundError as exc:
        raise RuntimeError("anthropic package is required for Anthropic-style text LLM calls") from exc
    kwargs: Dict[str, Any] = {"api_key": cfg.api_key}
    if cfg.base_url:
        base_url = cfg.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        kwargs["base_url"] = base_url
    client = AsyncAnthropic(**kwargs)
    model = model_name or cfg.model
    request: Dict[str, Any] = {
        "model": model,
        "max_tokens": int(cfg.max_tokens or 4096),
        "temperature": float(temperature if temperature is not None else (cfg.temperature if cfg.temperature is not None else 0.0)),
        "messages": _anthropic_text_messages(messages) if messages is not None else [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        "timeout": _effective_timeout(max_request_wall_s),
    }
    if system:
        request["system"] = [{"type": "text", "text": system}]
    debug_base = _llm_debug_payload(
        provider="anthropic",
        api_style=api_style,
        llm_config=llm_config,
        model=str(model or ""),
        base_url=str(cfg.base_url or ""),
        system=system,
        prompt=prompt,
        messages=messages,
        request=request,
    )
    if _llm_debug_enabled():
        _llm_debug_event("request_start", **debug_base)
    started = time.monotonic()
    try:
        if _llm_stream_debug_enabled():
            return await _with_retries(
                lambda: _ask_anthropic_text_stream(client, request, model=str(model or ""), api_style=api_style, debug_base=debug_base),
                max_request_wall_s=max_request_wall_s,
            )
        response = await _with_retries(
            lambda: client.messages.create(**request),
            max_request_wall_s=max_request_wall_s,
        )
    except Exception as exc:
        if _llm_debug_enabled():
            _llm_debug_event(
                "request_error",
                **debug_base,
                elapsed_s=round(time.monotonic() - started, 3),
                exception_type=type(exc).__name__,
                exception=str(exc)[:1000],
            )
        raise
    text_parts: List[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text_parts.append(str(getattr(block, "text", "") or ""))
    usage = getattr(response, "usage", None)
    content = "\n".join(part for part in text_parts if part)
    if _llm_debug_enabled():
        _llm_debug_event(
            "request_done",
            **debug_base,
            elapsed_s=round(time.monotonic() - started, 3),
            response_chars=len(content),
            prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )
    return TextLLMResponse(
        content=content,
        prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        model_name=str(model or ""),
        api_style=api_style,
    )


async def _ask_openai_text(
    *,
    llm_config: str,
    model_name: Optional[str],
    system: str,
    prompt: str,
    messages: Optional[List[Dict[str, str]]],
    temperature: Optional[float],
    max_request_wall_s: Optional[float],
    api_style: str,
) -> TextLLMResponse:
    from openai import AsyncOpenAI

    cfg = _llm_settings(llm_config)
    client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=LLM_CALL_TIMEOUT)
    model = model_name or cfg.model
    request_messages = []
    if system:
        request_messages.append({"role": "system", "content": system})
    request_messages.extend(_openai_text_messages(messages) if messages is not None else [{"role": "user", "content": prompt}])
    request = {
        "model": model,
        "messages": request_messages,
        "max_tokens": int(cfg.max_tokens or 4096),
        "temperature": float(temperature if temperature is not None else (cfg.temperature if cfg.temperature is not None else 0.0)),
        "timeout": _effective_timeout(max_request_wall_s),
    }
    debug_base = _llm_debug_payload(
        provider="openai",
        api_style=api_style,
        llm_config=llm_config,
        model=str(model or ""),
        base_url=str(cfg.base_url or ""),
        system=system,
        prompt=prompt,
        messages=messages,
        request=request,
    )
    if _llm_debug_enabled():
        _llm_debug_event("request_start", **debug_base)
    started = time.monotonic()
    try:
        if _llm_stream_debug_enabled():
            return await _with_retries(
                lambda: _ask_openai_text_stream(client, request, model=str(model or ""), api_style=api_style, debug_base=debug_base),
                max_request_wall_s=max_request_wall_s,
            )
        response = await _with_retries(
            lambda: client.chat.completions.create(**request),
            max_request_wall_s=max_request_wall_s,
        )
    except Exception as exc:
        if _llm_debug_enabled():
            _llm_debug_event(
                "request_error",
                **debug_base,
                elapsed_s=round(time.monotonic() - started, 3),
                exception_type=type(exc).__name__,
                exception=str(exc)[:1000],
            )
        raise
    usage = getattr(response, "usage", None)
    choice = response.choices[0] if getattr(response, "choices", None) else None
    message = getattr(choice, "message", None)
    content = str(getattr(message, "content", "") or "")
    if _llm_debug_enabled():
        _llm_debug_event(
            "request_done",
            **debug_base,
            elapsed_s=round(time.monotonic() - started, 3),
            response_chars=len(content),
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )
    return TextLLMResponse(
        content=content,
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        cache_input_tokens=int(getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0),
        model_name=str(model or ""),
        api_style=api_style,
    )


async def _ask_legacy_text(
    *,
    llm_config: str,
    model_name: Optional[str],
    system: str,
    prompt: str,
    messages: Optional[List[Dict[str, str]]],
    temperature: Optional[float],
    max_request_wall_s: Optional[float],
    api_style: str,
) -> TextLLMResponse:
    from app.llm import LLM

    llm = LLM(config_name=llm_config)
    before_prompt = int(getattr(llm, "total_input_tokens", 0) or 0)
    before_completion = int(getattr(llm, "total_completion_tokens", 0) or 0)
    request_messages = _openai_text_messages(messages) if messages is not None else [{"role": "user", "content": prompt}]
    debug_base = _llm_debug_payload(
        provider="legacy",
        api_style=api_style,
        llm_config=llm_config,
        model=str(model_name or getattr(llm, "model", "") or ""),
        base_url=str(getattr(llm, "base_url", "") or ""),
        system=system,
        prompt=prompt,
        messages=messages,
        request={
            "messages": request_messages,
            "max_tokens": int(getattr(llm, "max_tokens", 0) or 0),
            "timeout": _effective_timeout(max_request_wall_s),
        },
    )
    if _llm_debug_enabled():
        _llm_debug_event("request_start", **debug_base)
    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            llm.ask(
                messages=request_messages,
                system_msgs=[{"role": "system", "content": system}] if system else None,
                stream=False,
                new_model=model_name,
                temperature=temperature,
            ),
            timeout=_effective_timeout(max_request_wall_s),
        )
    except Exception as exc:
        if _llm_debug_enabled():
            _llm_debug_event(
                "request_error",
                **debug_base,
                elapsed_s=round(time.monotonic() - started, 3),
                exception_type=type(exc).__name__,
                exception=str(exc)[:1000],
            )
        raise
    content = str(response or "")
    if _llm_debug_enabled():
        _llm_debug_event(
            "request_done",
            **debug_base,
            elapsed_s=round(time.monotonic() - started, 3),
            response_chars=len(content),
            prompt_tokens=int(getattr(llm, "total_input_tokens", 0) or 0) - before_prompt,
            completion_tokens=int(getattr(llm, "total_completion_tokens", 0) or 0) - before_completion,
        )
    return TextLLMResponse(
        content=content,
        prompt_tokens=int(getattr(llm, "total_input_tokens", 0) or 0) - before_prompt,
        completion_tokens=int(getattr(llm, "total_completion_tokens", 0) or 0) - before_completion,
        model_name=str(model_name or getattr(llm, "model", "") or ""),
        api_style=api_style,
    )


async def _ask_anthropic_text_stream(
    client: Any,
    request: Dict[str, Any],
    *,
    model: str,
    api_style: str,
    debug_base: Dict[str, Any],
) -> TextLLMResponse:
    started = time.monotonic()
    first_event_s: Optional[float] = None
    text_parts: List[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    cache_input_tokens = 0
    stream_request = dict(request)
    stream_request["stream"] = True
    async for event in await client.messages.create(**stream_request):
        event_type = str(getattr(event, "type", "") or type(event).__name__)
        if first_event_s is None:
            first_event_s = round(time.monotonic() - started, 3)
            _llm_debug_event("stream_first_event", **debug_base, elapsed_s=first_event_s, event_type=event_type)
        if event_type == "message_start":
            usage = getattr(getattr(event, "message", None), "usage", None)
            prompt_tokens = int(getattr(usage, "input_tokens", 0) or prompt_tokens)
            cache_input_tokens = int(getattr(usage, "cache_read_input_tokens", 0) or cache_input_tokens)
        elif event_type == "message_delta":
            usage = getattr(event, "usage", None)
            completion_tokens = int(getattr(usage, "output_tokens", 0) or completion_tokens)
        elif event_type == "content_block_delta":
            delta = getattr(event, "delta", None)
            chunk = str(getattr(delta, "text", "") or "")
            if chunk:
                text_parts.append(chunk)
                _llm_stream_chunk_debug(debug_base, chunk)
        elif event_type in {"message_stop", "error"}:
            _llm_debug_event("stream_event", **debug_base, elapsed_s=round(time.monotonic() - started, 3), event_type=event_type)
    content = "".join(text_parts)
    _llm_debug_event(
        "request_done",
        **debug_base,
        elapsed_s=round(time.monotonic() - started, 3),
        first_event_s=first_event_s,
        response_chars=len(content),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    return TextLLMResponse(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_input_tokens=cache_input_tokens,
        model_name=model,
        api_style=api_style,
    )


async def _ask_openai_text_stream(
    client: Any,
    request: Dict[str, Any],
    *,
    model: str,
    api_style: str,
    debug_base: Dict[str, Any],
) -> TextLLMResponse:
    started = time.monotonic()
    first_event_s: Optional[float] = None
    text_parts: List[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    stream_request = dict(request)
    stream_request["stream"] = True
    async for event in await client.chat.completions.create(**stream_request):
        if first_event_s is None:
            first_event_s = round(time.monotonic() - started, 3)
            _llm_debug_event("stream_first_event", **debug_base, elapsed_s=first_event_s, event_type=type(event).__name__)
        usage = getattr(event, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or prompt_tokens)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or completion_tokens)
        choice = event.choices[0] if getattr(event, "choices", None) else None
        delta = getattr(choice, "delta", None)
        chunk = str(getattr(delta, "content", "") or "")
        if chunk:
            text_parts.append(chunk)
            _llm_stream_chunk_debug(debug_base, chunk)
    content = "".join(text_parts)
    _llm_debug_event(
        "request_done",
        **debug_base,
        elapsed_s=round(time.monotonic() - started, 3),
        first_event_s=first_event_s,
        response_chars=len(content),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    return TextLLMResponse(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model_name=model,
        api_style=api_style,
    )


def _openai_text_messages(messages: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for message in messages or []:
        role = str(message.get("role") or "user")
        if role not in {"user", "assistant", "system"}:
            role = "user"
        out.append({"role": role, "content": str(message.get("content") or "")})
    return out or [{"role": "user", "content": ""}]


def _anthropic_text_messages(messages: Optional[List[Dict[str, str]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for message in messages or []:
        role = str(message.get("role") or "user")
        if role == "system":
            role = "user"
        if role not in {"user", "assistant"}:
            role = "user"
        out.append({"role": role, "content": [{"type": "text", "text": str(message.get("content") or "")}]})
    return out or [{"role": "user", "content": [{"type": "text", "text": ""}]}]


def _llm_debug_enabled() -> bool:
    return str(os.environ.get("TE_LLM_DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}


def _llm_stream_debug_enabled() -> bool:
    return str(os.environ.get("TE_LLM_STREAM_DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}


def _llm_debug_payload(
    *,
    provider: str,
    api_style: str,
    llm_config: str,
    model: str,
    base_url: str,
    system: str,
    prompt: str,
    messages: Optional[List[Dict[str, str]]],
    request: Dict[str, Any],
) -> Dict[str, Any]:
    rendered_messages = messages if messages is not None else [{"role": "user", "content": prompt}]
    message_lengths = [_content_chars(message.get("content")) for message in rendered_messages]
    return {
        "provider": provider,
        "api_style": api_style,
        "llm_config": llm_config,
        "model": model,
        "base_url": base_url,
        "timeout_s": request.get("timeout"),
        "max_tokens": request.get("max_tokens"),
        "temperature": request.get("temperature"),
        "system_chars": len(system or ""),
        "prompt_chars": len(prompt or ""),
        "messages_count": len(rendered_messages),
        "messages_chars": sum(message_lengths),
        "message_chars_by_role": [
            {"role": str(message.get("role") or "user"), "chars": chars}
            for message, chars in zip(rendered_messages, message_lengths)
        ],
    }


def _content_chars(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_content_chars(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_content_chars(item) for item in value)
    return len(str(value))


def _llm_debug_event(event: str, **payload: Any) -> None:
    if not (_llm_debug_enabled() or _llm_stream_debug_enabled()):
        return
    record = {
        "event": event,
        "ts": round(time.time(), 3),
        **payload,
    }
    print("[TE_LLM_DEBUG] " + json.dumps(record, ensure_ascii=False, sort_keys=True), file=sys.stderr, flush=True)


def _llm_stream_chunk_debug(debug_base: Dict[str, Any], chunk: str) -> None:
    if not _llm_stream_debug_enabled():
        return
    payload: Dict[str, Any] = {
        **debug_base,
        "chunk_chars": len(chunk),
    }
    if str(os.environ.get("TE_LLM_STREAM_DEBUG_TEXT", "")).strip().lower() in {"1", "true", "yes", "on"}:
        payload["chunk_text"] = chunk
    _llm_debug_event("stream_chunk", **payload)


async def _with_retries(call_factory: Any, *, max_request_wall_s: Optional[float]) -> Any:
    timeout_count = 0
    while True:
        try:
            return await asyncio.wait_for(call_factory(), timeout=_effective_timeout(max_request_wall_s))
        except asyncio.TimeoutError:
            timeout_count += 1
            if timeout_count >= LLM_TIMEOUT_RETRIES:
                raise
            await asyncio.sleep(min(30 * timeout_count, 120))


def _effective_timeout(max_request_wall_s: Optional[float]) -> int:
    if max_request_wall_s is None:
        return int(LLM_CALL_TIMEOUT)
    bounded = min(float(LLM_CALL_TIMEOUT), max(1.0, float(max_request_wall_s)))
    return max(1, int(bounded))


def _llm_settings(llm_config: str) -> Any:
    from app.config import config

    return config.llm.get(llm_config, config.llm["default"])


__all__ = ["TextLLMResponse", "ask_text_llm", "resolve_text_api_style"]
