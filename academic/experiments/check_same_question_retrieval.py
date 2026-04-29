from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from academic.skill_store import SkillStore


def _retrieve_tfidf_only(store: SkillStore, query: str, top_k: int):
    return store._retrieve_tfidf(query, top_k)  # diagnostic-only path


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether same-question retrieval hits evolved skills")
    parser.add_argument("--store_path", type=Path, required=True)
    parser.add_argument("--detail_path", type=Path, required=True)
    parser.add_argument("--top_k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--retrieval_mode", choices=["tfidf", "default"], default="tfidf")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    store = SkillStore.load(args.store_path)
    detail = json.loads(args.detail_path.read_text())
    evolve_details: List[dict] = detail.get("evolve_details", [])

    rows: List[Dict] = []
    total = 0
    matched = 0
    fully_matched = 0

    for entry in evolve_details:
        new_skills = entry.get("new_skills") or []
        question = entry.get("question") or ""
        if not new_skills or not question:
            continue
        if args.retrieval_mode == "tfidf":
            retrieved = _retrieve_tfidf_only(store, question, args.top_k)
        else:
            retrieved = store.retrieve_sync(question, top_k=args.top_k)
        retrieved_names = [sk.name for sk in retrieved]
        hit_skills = [name for name in new_skills if name in retrieved_names]
        total += 1
        if hit_skills:
            matched += 1
        if len(hit_skills) == len(new_skills):
            fully_matched += 1
        rows.append({
            "problem_id": entry.get("problem_id"),
            "question": question,
            "new_skills": new_skills,
            "retrieved_names": retrieved_names,
            "hit_skills": hit_skills,
            "n_new_skills": len(new_skills),
            "n_hit_skills": len(hit_skills),
        })
        if args.limit and len(rows) >= args.limit:
            break

    summary = {
        "store_path": str(args.store_path),
        "detail_path": str(args.detail_path),
        "top_k": args.top_k,
        "n_checked": total,
        "n_any_hit": matched,
        "n_full_hit": fully_matched,
        "any_hit_rate": (matched / total) if total else 0.0,
        "full_hit_rate": (fully_matched / total) if total else 0.0,
        "rows": rows,
    }

    if args.out:
        args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(json.dumps({
        "n_checked": summary["n_checked"],
        "n_any_hit": summary["n_any_hit"],
        "n_full_hit": summary["n_full_hit"],
        "any_hit_rate": summary["any_hit_rate"],
        "full_hit_rate": summary["full_hit_rate"],
    }, ensure_ascii=False, indent=2))
    for row in rows[:10]:
        print(json.dumps({
            "problem_id": row["problem_id"],
            "new_skills": row["new_skills"],
            "retrieved_names": row["retrieved_names"],
            "hit_skills": row["hit_skills"],
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
