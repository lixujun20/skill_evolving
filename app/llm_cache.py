"""Disk-based response cache for the LLM class.

Activated via environment variable ``LLM_CACHE_ENABLED=1``.
Bypassed on a per-call basis via ``LLM_CACHE_BYPASS=1``.

Cache directory defaults to ``~/.skill_llm_cache/`` and can be overridden with
the ``LLM_CACHE_DIR`` environment variable.

Key = SHA-256( model :: json(messages) :: json(tools_or_tag) )

``ask()`` responses are cached as plain strings (tag ``"ask"``).
``ask_tool()`` responses are cached as serialized openai ChatCompletionMessage
objects (reconstructed via Pydantic ``model_validate`` on read).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("llm_cache")

_lock = threading.Lock()
_stats: dict[str, int] = {"hits": 0, "misses": 0, "stored": 0}


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def is_cache_enabled() -> bool:
    return os.environ.get("LLM_CACHE_ENABLED", "") == "1"


def is_cache_bypass() -> bool:
    return os.environ.get("LLM_CACHE_BYPASS", "") == "1"


def cache_dir() -> Path:
    default = Path.home() / ".skill_llm_cache"
    return Path(os.environ.get("LLM_CACHE_DIR", str(default)))


def get_cache_stats() -> dict[str, int]:
    with _lock:
        return dict(_stats)


def reset_cache_stats() -> None:
    with _lock:
        _stats["hits"] = 0
        _stats["misses"] = 0
        _stats["stored"] = 0


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------

def _make_key(model: str, messages: Any, tools_or_tag: Any) -> str:
    messages_str = json.dumps(messages, sort_keys=True, default=str)
    if isinstance(tools_or_tag, str):
        tools_str = tools_or_tag
    else:
        tools_str = json.dumps(tools_or_tag or [], sort_keys=True, default=str)
    raw = f"{model}::{messages_str}::{tools_str}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_path(key: str) -> Path:
    return cache_dir() / f"{key}.json"


# ---------------------------------------------------------------------------
# ask() — returns str
# ---------------------------------------------------------------------------

def get_cached_ask(model: str, messages: Any) -> Optional[str]:
    """Return cached string response for ``LLM.ask()``, or ``None`` on miss."""
    key = _make_key(model, messages, "ask")
    path = _cache_path(key)
    with _lock:
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("_kind") == "ask":
                    _stats["hits"] += 1
                    logger.debug(f"[llm_cache] HIT  model={model} key={key[:12]}")
                    return payload["value"]
            except Exception:
                pass  # corrupted entry → treat as miss
        _stats["misses"] += 1
        return None


def store_cached_ask(model: str, messages: Any, response: str) -> None:
    """Persist a string response for ``LLM.ask()``."""
    key = _make_key(model, messages, "ask")
    path = _cache_path(key)
    with _lock:
        cache_dir().mkdir(parents=True, exist_ok=True)
        payload = {"_kind": "ask", "value": response}
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        _stats["stored"] += 1
        logger.debug(f"[llm_cache] STORE model={model} key={key[:12]}")


# ---------------------------------------------------------------------------
# ask_tool() — returns ChatCompletionMessage | None
# ---------------------------------------------------------------------------

def get_cached_ask_tool(model: str, messages: Any, tools: Any) -> Any:
    """Return cached ChatCompletionMessage for ``LLM.ask_tool()``, or ``None`` on miss.

    Uses Pydantic ``model_validate`` so the returned object is a genuine
    ``openai.types.chat.ChatCompletionMessage`` — no duck-type mocks.
    """
    key = _make_key(model, messages, tools)
    path = _cache_path(key)
    with _lock:
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("_kind") == "ask_tool":
                    from openai.types.chat import ChatCompletionMessage
                    msg = ChatCompletionMessage.model_validate(payload["value"])
                    _stats["hits"] += 1
                    logger.debug(f"[llm_cache] HIT  model={model} key={key[:12]}")
                    return msg
            except Exception:
                pass
        _stats["misses"] += 1
        return None


def store_cached_ask_tool(model: str, messages: Any, tools: Any, response: Any) -> None:
    """Persist a ``ChatCompletionMessage`` for ``LLM.ask_tool()``."""
    key = _make_key(model, messages, tools)
    path = _cache_path(key)
    with _lock:
        try:
            cache_dir().mkdir(parents=True, exist_ok=True)
            # Pydantic v2: model_dump() produces a JSON-serialisable dict
            value = response.model_dump() if hasattr(response, "model_dump") else dict(response)
            payload = {"_kind": "ask_tool", "value": value}
            path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            _stats["stored"] += 1
            logger.debug(f"[llm_cache] STORE model={model} key={key[:12]}")
        except Exception as exc:
            logger.warning(f"[llm_cache] failed to store ask_tool response: {exc}")
