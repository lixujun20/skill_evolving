"""
config.py — LLM and path configuration for the academic framework.

Uses the existing app.llm.LLM wrapper so we share API keys / caching /
token-counting infrastructure with the engineering codebase.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # skill_evolving/
ACADEMIC_ROOT = Path(__file__).resolve().parent         # academic/
RESULTS_DIR = ACADEMIC_ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# ── LLM model slots ──────────────────────────────────────────────────────────
# All slots use config names from config/config.toml → [llm.<name>]
#
#   AGENT_MODEL  — the "main agent" that solves problems using skills
#   EXTRACT_MODEL — the "skill extractor" that analyses traces → skills
#
# Override via env vars if needed.
AGENT_MODEL: str = os.environ.get("TE_AGENT_MODEL", "bigmodel")
EXTRACT_MODEL: str = os.environ.get("TE_EXTRACT_MODEL", "bigmodel")

# ── Execution ─────────────────────────────────────────────────────────────────
CODE_EXEC_TIMEOUT: int = int(os.environ.get("TE_EXEC_TIMEOUT", "30"))
MAX_AGENT_STEPS: int = int(os.environ.get("TE_MAX_STEPS", "16"))
LLM_CALL_TIMEOUT: int = int(os.environ.get("TE_LLM_TIMEOUT", "600"))
LLM_TIMEOUT_RETRIES: int = int(os.environ.get("TE_TIMEOUT_RETRIES", "3"))
INTER_PROBLEM_DELAY: int = int(os.environ.get("TE_PROBLEM_DELAY", "10"))
RATE_LIMIT_RETRIES: int = int(os.environ.get("TE_RATE_RETRIES", "5"))
RATE_LIMIT_BASE_WAIT: int = int(os.environ.get("TE_RATE_WAIT", "60"))
