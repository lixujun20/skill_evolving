"""Pytest fixtures that patch LLM.ask / LLM.ask_tool with the disk cache."""

import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.meta_agent.skills.tests.llm_response_cache import (
    cache_response,
    get_cache_stats,
    get_cached_response,
    is_cache_enabled,
)


def _is_bypass() -> bool:
    return os.environ.get("LLM_CACHE_BYPASS", "") == "1"


def _make_cached_ask_tool(original_method, llm_instance):
    async def _ask_tool_cached(
        messages,
        system_msgs=None,
        timeout=30,
        tools=None,
        tool_choice="auto",
        temperature=None,
        new_model=None,
        **kwargs,
    ):
        if not is_cache_enabled() or _is_bypass():
            return await original_method(
                messages,
                system_msgs=system_msgs,
                timeout=timeout,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                new_model=new_model,
                **kwargs,
            )
        model = new_model or llm_instance.model
        cached = get_cached_response(model, messages, tools)
        if cached is not None:
            return cached
        result = await original_method(
            messages,
            system_msgs=system_msgs,
            timeout=timeout,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            new_model=new_model,
            **kwargs,
        )
        if result is not None:
            cache_response(model, messages, tools, result)
        return result

    return _ask_tool_cached


def _make_cached_ask(original_method, llm_instance):
    async def _ask_cached(
        messages,
        system_msgs=None,
        timeout=30,
        stream=False,
        temperature=None,
        new_model=None,
    ):
        if not is_cache_enabled() or _is_bypass():
            return await original_method(
                messages,
                system_msgs=system_msgs,
                stream=stream,
                temperature=temperature,
                new_model=new_model,
            )
        model = new_model or llm_instance.model
        cached = get_cached_response(model, messages, "ask")
        if cached is not None:
            return cached
        result = await original_method(
            messages,
            system_msgs=system_msgs,
            stream=stream,
            temperature=temperature,
            new_model=new_model,
        )
        if result is not None:
            cache_response(model, messages, "ask", result)
        return result

    return _ask_cached


@pytest.fixture
def llm_cache(request):
    """Opt-in fixture: patches LLM.ask and LLM.ask_tool to use the disk cache."""
    from app.llm import LLM

    original_ask_tool = LLM.ask_tool
    original_ask = LLM.ask

    patched_instances: dict = {}

    _real_ask_tool_init = original_ask_tool
    _real_ask_init = original_ask

    def _patched_ask_tool(self, *args, **kwargs):
        if id(self) not in patched_instances:
            patched_instances[id(self)] = {
                "ask_tool": _make_cached_ask_tool(
                    lambda *a, **kw: original_ask_tool(self, *a, **kw), self
                ),
                "ask": _make_cached_ask(
                    lambda *a, **kw: original_ask(self, *a, **kw), self
                ),
            }
        return patched_instances[id(self)]["ask_tool"](*args, **kwargs)

    def _patched_ask(self, *args, **kwargs):
        if id(self) not in patched_instances:
            patched_instances[id(self)] = {
                "ask_tool": _make_cached_ask_tool(
                    lambda *a, **kw: original_ask_tool(self, *a, **kw), self
                ),
                "ask": _make_cached_ask(
                    lambda *a, **kw: original_ask(self, *a, **kw), self
                ),
            }
        return patched_instances[id(self)]["ask"](*args, **kwargs)

    with patch.object(LLM, "ask_tool", _patched_ask_tool), patch.object(
        LLM, "ask", _patched_ask
    ):
        yield

    stats = get_cache_stats()
    print(
        f"\n[llm_cache] test='{request.node.name}' "
        f"hits={stats['hits']} misses={stats['misses']} stored={stats['stored']}"
    )


@pytest.fixture(scope="session", autouse=False)
def llm_cache_summary():
    """Session-scoped fixture: prints total cache stats after the full session."""
    yield
    stats = get_cache_stats()
    print(
        f"\n[llm_cache] SESSION TOTALS — "
        f"hits={stats['hits']} misses={stats['misses']} stored={stats['stored']}"
    )
