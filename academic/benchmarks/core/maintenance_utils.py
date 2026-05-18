"""Small benchmark-neutral utilities for maintenance code."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any


def json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: Any, length: int = 10) -> str:
    raw = "\n".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except Exception:
        return default
