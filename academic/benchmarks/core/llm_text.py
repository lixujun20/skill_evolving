"""Benchmark-agnostic text LLM clients.

Benchmark adapters should not assume every model endpoint speaks OpenAI chat
completions. This module provides a small text-only facade for Anthropic-style,
OpenAI-compatible, and legacy app.llm backends.
"""
from __future__ import annotations

import asyncio
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
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        "timeout": _effective_timeout(max_request_wall_s),
    }
    if system:
        request["system"] = [{"type": "text", "text": system}]
    response = await _with_retries(
        lambda: client.messages.create(**request),
        max_request_wall_s=max_request_wall_s,
    )
    text_parts: List[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text_parts.append(str(getattr(block, "text", "") or ""))
    usage = getattr(response, "usage", None)
    return TextLLMResponse(
        content="\n".join(part for part in text_parts if part),
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
    temperature: Optional[float],
    max_request_wall_s: Optional[float],
    api_style: str,
) -> TextLLMResponse:
    from openai import AsyncOpenAI

    cfg = _llm_settings(llm_config)
    client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=LLM_CALL_TIMEOUT)
    model = model_name or cfg.model
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = await _with_retries(
        lambda: client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=int(cfg.max_tokens or 4096),
            temperature=float(temperature if temperature is not None else (cfg.temperature if cfg.temperature is not None else 0.0)),
            timeout=_effective_timeout(max_request_wall_s),
        ),
        max_request_wall_s=max_request_wall_s,
    )
    usage = getattr(response, "usage", None)
    choice = response.choices[0] if getattr(response, "choices", None) else None
    message = getattr(choice, "message", None)
    return TextLLMResponse(
        content=str(getattr(message, "content", "") or ""),
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
    temperature: Optional[float],
    max_request_wall_s: Optional[float],
    api_style: str,
) -> TextLLMResponse:
    from app.llm import LLM

    llm = LLM(config_name=llm_config)
    before_prompt = int(getattr(llm, "total_input_tokens", 0) or 0)
    before_completion = int(getattr(llm, "total_completion_tokens", 0) or 0)
    response = await asyncio.wait_for(
        llm.ask(
            messages=[{"role": "user", "content": prompt}],
            system_msgs=[{"role": "system", "content": system}] if system else None,
            stream=False,
            new_model=model_name,
            temperature=temperature,
        ),
        timeout=_effective_timeout(max_request_wall_s) + 60,
    )
    return TextLLMResponse(
        content=str(response or ""),
        prompt_tokens=int(getattr(llm, "total_input_tokens", 0) or 0) - before_prompt,
        completion_tokens=int(getattr(llm, "total_completion_tokens", 0) or 0) - before_completion,
        model_name=str(model_name or getattr(llm, "model", "") or ""),
        api_style=api_style,
    )


async def _with_retries(call_factory: Any, *, max_request_wall_s: Optional[float]) -> Any:
    timeout_count = 0
    while True:
        try:
            return await asyncio.wait_for(call_factory(), timeout=_effective_timeout(max_request_wall_s) + 60)
        except asyncio.TimeoutError:
            timeout_count += 1
            if timeout_count >= LLM_TIMEOUT_RETRIES:
                raise
            await asyncio.sleep(min(30 * timeout_count, 120))


def _effective_timeout(max_request_wall_s: Optional[float]) -> int:
    if max_request_wall_s is None:
        return int(LLM_CALL_TIMEOUT)
    bounded = min(float(LLM_CALL_TIMEOUT), max(0.0, float(max_request_wall_s) - 15.0))
    return max(1, int(bounded))


def _llm_settings(llm_config: str) -> Any:
    from app.config import config

    return config.llm.get(llm_config, config.llm["default"])


__all__ = ["TextLLMResponse", "ask_text_llm", "resolve_text_api_style"]
