"""Run the related-task BFCL experiment with a temporary local Claude proxy config.

This wrapper lets us bind a run to a specific local proxy port without editing
the long-lived shared config files. It is intended for isolated parallel runs
where each experiment should talk to its own proxy instance.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    script_path = Path(__file__).resolve()
    script_dir = str(script_path.parent)
    project_root = str(script_path.parents[4])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    try:
        sys.path.remove(script_dir)
    except ValueError:
        pass

import asyncio

from app.config import LLMSettings, config
from academic.benchmarks.bfcl.related.experiment import main_async


def _build_llm_settings() -> tuple[str, LLMSettings]:
    port = int(os.environ.get("BFCL_PROXY_PORT", "4000"))
    config_name = os.environ.get("BFCL_PROXY_CONFIG_NAME", f"local_claude_proxy_{port}")
    model = os.environ.get("BFCL_PROXY_MODEL", "claude-sonnet-4-5")
    api_key = os.environ.get("BFCL_PROXY_API_KEY", "1234abcd")
    settings = LLMSettings(
        model=model,
        base_url=f"http://127.0.0.1:{port}/v1",
        api_key=api_key,
        max_tokens=32768,
        max_input_tokens=None,
        temperature=0.0,
        api_type="",
        api_version="",
    )
    return config_name, settings


def main() -> None:
    config_name, settings = _build_llm_settings()
    config.llm[config_name] = settings
    if "--llm-config" not in sys.argv:
        sys.argv.extend(["--llm-config", config_name])
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
