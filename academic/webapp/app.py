"""
Skill Explorer — Web UI for browsing, searching, and running evolved skills.

Usage:
    cd ~/skill_evolving
    python -m academic.webapp.app [--port 5050] [--skills-dir PATH]
"""
from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, jsonify, render_template, request

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)

# library_id -> {name, path, skills: [...], by_name: {...}}
LIBRARIES: Dict[str, Dict[str, Any]] = {}
CURRENT_LIB: str = ""


def _make_lib_id(path: Path) -> str:
    """Derive a human-readable library id from filename."""
    stem = path.stem
    for suffix in ("_skills", "_skill"):
        stem = stem.replace(suffix, "")
    return stem


def load_libraries(skills_dir: str) -> None:
    global LIBRARIES, CURRENT_LIB
    LIBRARIES.clear()
    results_dir = Path(skills_dir)
    for p in sorted(results_dir.glob("*skills*.json")):
        try:
            with open(p) as f:
                skills = json.load(f)
            if not isinstance(skills, list) or not skills:
                continue
            lib_id = _make_lib_id(p)
            by_name = {s["name"]: s for s in skills}
            LIBRARIES[lib_id] = {
                "name": lib_id.replace("_", " ").title(),
                "path": str(p),
                "skills": skills,
                "by_name": by_name,
            }
        except Exception as e:
            print(f"Warning: skipping {p}: {e}")

    if LIBRARIES:
        CURRENT_LIB = next(iter(LIBRARIES))


def _get_lib(lib_id: str | None = None) -> Dict[str, Any] | None:
    """Get library by id, or current default."""
    lid = lib_id or request.args.get("lib") or CURRENT_LIB
    return LIBRARIES.get(lid)


# ── API Routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/libraries")
def api_libraries():
    """Return list of available skill libraries."""
    result = []
    for lid, lib in LIBRARIES.items():
        result.append({
            "id": lid,
            "name": lib["name"],
            "skill_count": len(lib["skills"]),
            "path": lib["path"],
        })
    return jsonify(result)


@app.route("/api/skills")
def api_skills():
    """Return all skills (summary view) for a library."""
    lib = _get_lib()
    if not lib:
        return jsonify([])
    q = request.args.get("q", "").lower()
    result = []
    for s in lib["skills"]:
        if q and q not in s["name"].lower() and q not in s.get("description", "").lower():
            continue
        result.append({
            "name": s["name"],
            "description": s.get("description", ""),
            "version": s.get("version", 1),
            "usage_count": s.get("usage_count", 0),
            "success_count": s.get("success_count", 0),
            "dependencies": s.get("dependencies", []),
            "has_test": bool(s.get("test_code")),
        })
    return jsonify(result)


@app.route("/api/skills/<name>")
def api_skill_detail(name: str):
    """Return full skill details."""
    lib = _get_lib()
    if not lib:
        return jsonify({"error": "No library selected"}), 404
    s = lib["by_name"].get(name)
    if not s:
        return jsonify({"error": f"Skill '{name}' not found"}), 404
    return jsonify(s)


@app.route("/api/skills/<name>/run", methods=["POST"])
def api_run_skill(name: str):
    """Run a skill's test code or custom code."""
    lib = _get_lib()
    if not lib:
        return jsonify({"error": "No library selected"}), 404
    s = lib["by_name"].get(name)
    if not s:
        return jsonify({"error": f"Skill '{name}' not found"}), 404

    body = request.get_json(silent=True) or {}
    custom_code = body.get("code", "")

    # Build namespace with dependencies
    namespace: Dict[str, Any] = {"__builtins__": __builtins__}
    dep_errors = []

    # Load dependencies first
    loaded = set()
    def _load_deps(skill_name: str):
        if skill_name in loaded:
            return
        loaded.add(skill_name)
        dep_skill = lib["by_name"].get(skill_name)
        if not dep_skill:
            dep_errors.append(f"Dependency '{skill_name}' not found")
            return
        for d in dep_skill.get("dependencies", []):
            _load_deps(d)
        try:
            exec(dep_skill["code"], namespace)
        except Exception as e:
            dep_errors.append(f"Error loading '{skill_name}': {e}")

    for dep in s.get("dependencies", []):
        _load_deps(dep)

    # Load the skill itself
    try:
        exec(s["code"], namespace)
    except Exception as e:
        return jsonify({"success": False, "output": f"Error loading skill: {e}"})

    # Run test or custom code
    code_to_run = custom_code or s.get("test_code", "")
    if not code_to_run:
        return jsonify({"success": True, "output": "No test code available."})

    import io
    import contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(code_to_run, namespace)
        output = buf.getvalue()
        if dep_errors:
            output = "Dependency warnings:\n" + "\n".join(dep_errors) + "\n\n" + output
        return jsonify({"success": True, "output": output or "✓ All assertions passed."})
    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({"success": False, "output": tb})


@app.route("/api/graph")
def api_graph():
    """Return dependency graph as nodes + edges for visualization."""
    lib = _get_lib()
    if not lib:
        return jsonify({"nodes": [], "edges": []})
    nodes = []
    edges = []
    for s in lib["skills"]:
        nodes.append({
            "id": s["name"],
            "usage": s.get("usage_count", 0),
            "success": s.get("success_count", 0),
        })
        for dep in s.get("dependencies", []):
            edges.append({"source": dep, "target": s["name"]})
    return jsonify({"nodes": nodes, "edges": edges})


@app.route("/api/stats")
def api_stats():
    """Return aggregate statistics."""
    lib = _get_lib()
    if not lib:
        return jsonify({"total_skills": 0, "used_skills": 0, "skills_with_deps": 0,
                         "total_usage": 0, "total_success": 0, "avg_success_rate": 0})
    skills = lib["skills"]
    total = len(skills)
    used = sum(1 for s in skills if s.get("usage_count", 0) > 0)
    with_deps = sum(1 for s in skills if s.get("dependencies"))
    total_usage = sum(s.get("usage_count", 0) for s in skills)
    total_success = sum(s.get("success_count", 0) for s in skills)
    return jsonify({
        "total_skills": total,
        "used_skills": used,
        "skills_with_deps": with_deps,
        "total_usage": total_usage,
        "total_success": total_success,
        "avg_success_rate": total_success / total_usage if total_usage else 0,
    })


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--skills-dir",
        default=str(Path(__file__).parent.parent / "results"),
        help="Directory containing *skills*.json files",
    )
    args = parser.parse_args()

    load_libraries(args.skills_dir)
    for lid, lib in LIBRARIES.items():
        print(f"  [{lid}] {lib['name']} — {len(lib['skills'])} skills ({lib['path']})")
    print(f"Loaded {len(LIBRARIES)} libraries. Server: http://{args.host}:{args.port}")

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
