from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List


def _top_items(d: Dict[str, int], k: int = 15) -> List[tuple[str, int]]:
    return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:k]


def _fmt_top(title: str, d: Dict[str, int], k: int = 15) -> List[str]:
    lines = [f"{title} ({len(d)} unique):"]
    if not d:
        lines.append("- (none)")
        return lines
    for name, count in _top_items(d, k):
        lines.append(f"- `{name}`: {count}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize train/test skill retrieval and call stats")
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    obj = json.loads(args.summary.read_text())
    skill_stats = obj.get("skill_stats", {})
    train = skill_stats.get("train", {})
    test = skill_stats.get("test", {})

    report_lines: List[str] = []
    report_lines.append(f"Summary file: `{args.summary.name}`")
    report_lines.extend(_fmt_top("Train retrieved", train.get("retrieved_counts", {})))
    report_lines.extend(_fmt_top("Train called", train.get("called_counts", {})))
    report_lines.extend(_fmt_top("Train tool-called", train.get("tool_call_counts", {})))
    report_lines.extend(_fmt_top("Test retrieved", test.get("retrieved_counts", {})))
    report_lines.extend(_fmt_top("Test called", test.get("called_counts", {})))
    report_lines.extend(_fmt_top("Test tool-called", test.get("tool_call_counts", {})))

    text = "\n".join(report_lines)
    print(text)
    if args.out:
        args.out.write_text(text)


if __name__ == "__main__":
    main()
