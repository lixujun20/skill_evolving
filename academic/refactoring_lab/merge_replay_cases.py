from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_list(path: Path):
    data = json.loads(path.read_text())
    if isinstance(data, dict) and "draft_cases" in data:
        return data["draft_cases"]
    if isinstance(data, list):
        return data
    raise ValueError(f"Unsupported replay case input format: {path}")


def merge_replay_cases(*, base_cases_path: Path, drafts_path: Path):
    base_cases = _load_list(base_cases_path)
    draft_cases = _load_list(drafts_path)

    merged = []
    seen = set()
    for item in list(base_cases) + list(draft_cases):
        case_id = item["case_id"]
        if case_id in seen:
            continue
        merged.append(item)
        seen.add(case_id)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge validated replay cases with auto-generated drafts")
    parser.add_argument("--base-cases", required=True)
    parser.add_argument("--drafts", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    merged = merge_replay_cases(
        base_cases_path=Path(args.base_cases),
        drafts_path=Path(args.drafts),
    )
    Path(args.output).write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    print(json.dumps({"n_cases": len(merged), "output": args.output}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
