"""Run BFCL maintenance through the local Anthropic-compatible proxy.

This wrapper avoids editing the long-lived config/config.toml while letting the
benchmark runner resolve a local llm_config name.
"""
from __future__ import annotations

import asyncio
import sys

from app.config import LLMSettings, config
from academic.benchmarks.core.runner import main_async


config.llm["local_claude_proxy"] = LLMSettings(
    model="claude-sonnet-4-5",
    base_url="http://127.0.0.1:4000",
    api_key="1234abcd",
    max_tokens=32768,
    max_input_tokens=None,
    temperature=0.0,
    api_type="",
    api_version="",
)

if "--llm-config" not in sys.argv:
    sys.argv.extend(["--llm-config", "local_claude_proxy"])

asyncio.run(main_async())
