from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def _load(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} did not decode to a JSON object")
    return payload


def _extract(payload: Dict[str, Any]) -> Dict[str, Any]:
    token_breakdown = dict(payload.get("token_breakdown") or {})
    summary = dict(token_breakdown.get("summary") or {})
    by_role = dict(token_breakdown.get("by_role") or {})
    by_phase = dict(token_breakdown.get("by_phase") or {})
    return {
        "summary": summary,
        "by_role": by_role,
        "by_phase": by_phase,
        "test_summary": dict(payload.get("test_summary") or {}),
        "train_summary": dict(payload.get("train_summary") or {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize BFCL token breakdown from result JSON")
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()

    payload = _load(args.result)
    data = _extract(payload)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
