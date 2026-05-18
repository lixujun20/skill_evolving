"""SpreadsheetBench executable skill and Python runtime helpers."""
from __future__ import annotations

import ast
import json
import queue
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import openpyxl
from openpyxl.utils import column_index_from_string

from academic.benchmarks.core.types import SkillArtifact
from academic.config import CODE_EXEC_TIMEOUT


def extract_code(text: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", text or "", re.S | re.I)
    return match.group(1).strip() if match else ""


def write_spreadsheet_skill_library(skills: Sequence[SkillArtifact], work_dir: Path) -> Dict[str, Any]:
    callable_rows: List[Dict[str, Any]] = []
    chunks = [
        "from pathlib import Path\n",
        "import openpyxl\n",
        "from openpyxl import load_workbook\n",
        "from openpyxl.utils import column_index_from_string\n",
        "from datetime import datetime, date, time, timedelta\n",
        "import math\n",
        "import re\n\n",
        "def _spreadsheet_column_value(value):\n",
        "    if isinstance(value, str) and re.fullmatch(r\"[A-Za-z]+\", value.strip()):\n",
        "        return column_index_from_string(value.strip().upper())\n",
        "    return value\n\n",
        "def _spreadsheet_callable_kwargs(kwargs, ws):\n",
        "    env = dict(kwargs or {})\n",
        "    for key, value in list(env.items()):\n",
        "        if key.endswith(\"_column\"):\n",
        "            base = key[: -len(\"_column\")]\n",
        "            env.setdefault(f\"{base}_col\", _spreadsheet_column_value(value))\n",
        "            if base.endswith(\"ies\"):\n",
        "                env.setdefault(f\"{base[:-3]}y_col\", _spreadsheet_column_value(value))\n",
        "        elif key.endswith(\"_col\"):\n",
        "            env[key] = _spreadsheet_column_value(value)\n",
        "            env.setdefault(f\"{key[: -len('_col')]}_column\", value)\n",
        "    env.setdefault(\"value_start_row\", 1)\n",
        "    env.setdefault(\"value_end_row\", ws.max_row)\n",
        "    env.setdefault(\"result_start_row\", 1)\n",
        "    env.setdefault(\"result_end_row\", ws.max_row)\n",
        "    return env\n\n",
    ]
    for skill in skills:
        if not is_spreadsheet_callable_skill(skill):
            continue
        func_name = spreadsheet_skill_function_name(skill.name)
        code = spreadsheet_skill_code(skill)
        if not code:
            continue
        rendered = render_spreadsheet_skill_function(func_name, code)
        if not rendered:
            continue
        try:
            ast.parse(rendered)
        except SyntaxError:
            continue
        chunks.append(f"\n# Skill: {skill.name}\n")
        chunks.append(rendered)
        chunks.append("\n")
        callable_rows.append(
            {
                "skill_name": skill.name,
                "function_name": func_name,
                "description": spreadsheet_callable_description(skill),
            }
        )
    if callable_rows:
        (work_dir / "skill_library.py").write_text("".join(chunks))
    prompt = "\n".join(
        f"- `{row['function_name']}(INPUT_XLSX, OUTPUT_XLSX, **kwargs)` from skill `{row['skill_name']}`: {row['description'][:220]}"
        for row in callable_rows
    )
    if prompt:
        prompt = (
            "You may directly reuse executable spreadsheet skills by importing them in your returned code, e.g. "
            "`from skill_library import skill_name` then `skill_name(INPUT_XLSX, OUTPUT_XLSX)`. "
            "Prefer importing a callable over rewriting its implementation when its scope matches the workbook and requested answer range. "
            "Pass explicit keyword arguments for sheet names, ranges, columns, and row bounds when the description requires them.\n"
            + prompt
        )
    return {"prompt": prompt, "skills": callable_rows}


def spreadsheet_callable_description(skill: SkillArtifact, *, limit: int = 520) -> str:
    parts: List[str] = []
    if skill.description:
        parts.append(str(skill.description).strip())
    body = str(skill.body or "")
    applicability = re.search(
        r"(?is)(Applicability\s*:\s*.*?)(?:\n\n|Reusable openpyxl idiom|```|Non-applicability\s*:)",
        body,
    )
    if applicability:
        parts.append(applicability.group(1).strip())
    required = []
    for line in body.splitlines()[:12]:
        stripped = line.strip().lstrip("#").strip()
        if re.search(r"(?i)\b(required variables|assumes|parameters|kwargs)\b", stripped):
            required.append(stripped)
    if required:
        parts.append(" ".join(required[:3]))
    text = " ".join(part for part in parts if part).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text or str(skill.description or skill.name)


def is_spreadsheet_callable_skill(skill: SkillArtifact) -> bool:
    """Return whether a Spreadsheet skill should be exposed as importable code.

    Older evolved stores sometimes set ``metadata.injection_type`` to
    ``informational`` even for ``executable_tool`` artifacts.  Callable exposure
    should follow the artifact's semantic kind and code availability, not only
    the prompt injection label.  Workflow and knowledge/interface cards remain
    prompt-only.
    """
    kind = str(skill.kind or "").strip().lower()
    if kind not in {"executable_tool", "function_tool", "script_tool"}:
        return False
    return bool(spreadsheet_skill_code(skill))


def called_spreadsheet_skill_functions(
    code: str,
    callable_rows: Sequence[Dict[str, Any]],
) -> List[str]:
    function_to_skill = {
        str(row.get("function_name") or ""): str(row.get("skill_name") or "")
        for row in callable_rows
        if str(row.get("function_name") or "") and str(row.get("skill_name") or "")
    }
    if not function_to_skill or not str(code or "").strip():
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return called_spreadsheet_skill_functions_from_text(code, function_to_skill)
    imported_aliases: Dict[str, str] = {}
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "skill_library":
            for alias in node.names:
                imported = str(alias.name)
                if imported in function_to_skill:
                    imported_aliases[str(alias.asname or alias.name)] = imported
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "skill_library":
                    module_aliases.add(str(alias.asname or alias.name))
    called: List[str] = []
    for node in ast.walk(tree):
        func = getattr(node, "func", None)
        function_name = ""
        if isinstance(func, ast.Name):
            function_name = imported_aliases.get(func.id, func.id if func.id in function_to_skill else "")
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in module_aliases and func.attr in function_to_skill:
                function_name = func.attr
        if function_name:
            skill_name = function_to_skill.get(function_name)
            if skill_name and skill_name not in called:
                called.append(skill_name)
    return called


def called_spreadsheet_skill_functions_from_text(
    code: str,
    function_to_skill: Dict[str, str],
) -> List[str]:
    called: List[str] = []
    for function_name, skill_name in function_to_skill.items():
        if re.search(rf"\b{re.escape(function_name)}\s*\(", str(code or "")):
            if skill_name not in called:
                called.append(skill_name)
    return called


def spreadsheet_skill_function_name(name: str) -> str:
    value = re.sub(r"\W+", "_", str(name or "").strip()).strip("_").lower()
    if not value:
        value = "spreadsheet_skill"
    if value[0].isdigit():
        value = f"skill_{value}"
    return value


def spreadsheet_skill_code(skill: SkillArtifact) -> str:
    body = str(skill.body or "")
    match = re.search(r"```(?:python)?\s*(.*?)```", body, re.S | re.I)
    if match:
        code = match.group(1).strip()
    else:
        code = body.strip()
        if not looks_like_spreadsheet_python(code):
            return ""
    code = re.sub(r"load_workbook\((['\"])[^'\"]*?_input\\.xlsx\1\)", "load_workbook(INPUT_XLSX)", code)
    code = re.sub(r"\\.save\((['\"])[^'\"]*?_output\\.xlsx\1\)", ".save(OUTPUT_XLSX)", code)
    return code


def looks_like_spreadsheet_python(code: str) -> bool:
    if not code.strip():
        return False
    python_start = re.search(
        r"(?m)^\s*(?:from|import|def|class|for|while|if|try|with)\s+\w+|^\s*(?:wb|ws|worksheet|workbook)\b",
        code,
    )
    if python_start:
        return True
    spreadsheet_markers = [
        "INPUT_XLSX",
        "OUTPUT_XLSX",
        "load_workbook(",
        "openpyxl.",
        "wb.save(",
        ".cell(",
        "ws[",
    ]
    return any(marker in code for marker in spreadsheet_markers)


def render_spreadsheet_skill_function(func_name: str, code: str) -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return wrap_spreadsheet_skill_snippet(func_name, code)
    functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    if functions:
        first = functions[0]
        return (
            code
            + "\n\n"
            + f"def {func_name}(INPUT_XLSX, OUTPUT_XLSX, **kwargs):\n"
            + "    try:\n"
            + f"        return {first}(INPUT_XLSX, OUTPUT_XLSX, **kwargs)\n"
            + "    except TypeError:\n"
            + f"        return {first}(INPUT_XLSX, OUTPUT_XLSX)\n"
        )
    return wrap_spreadsheet_skill_snippet(func_name, code)


def wrap_spreadsheet_skill_snippet(func_name: str, code: str) -> str:
    code_literal = repr(code.strip() or "pass")
    return (
        f"def {func_name}(INPUT_XLSX, OUTPUT_XLSX, **kwargs):\n"
        "    INPUT_XLSX = Path(INPUT_XLSX)\n"
        "    OUTPUT_XLSX = Path(OUTPUT_XLSX)\n"
        "    wb = load_workbook(INPUT_XLSX)\n"
        "    ws = wb.active\n"
        "    env = dict(globals())\n"
        "    env.update({\n"
        "        'INPUT_XLSX': INPUT_XLSX,\n"
        "        'OUTPUT_XLSX': OUTPUT_XLSX,\n"
        "        'wb': wb,\n"
        "        'ws': ws,\n"
        "        'worksheet': ws,\n"
        "        'workbook': wb,\n"
        "    })\n"
        "    env.update(_spreadsheet_callable_kwargs(kwargs, ws))\n"
        f"    exec({code_literal}, env, env)\n"
        "    out_wb = env.get('wb', wb)\n"
        "    if hasattr(out_wb, 'save'):\n"
        "        out_wb.save(OUTPUT_XLSX)\n"
        "    return OUTPUT_XLSX\n"
    )


def spreadsheet_callable_kwargs(kwargs: Any, ws: Any) -> Dict[str, Any]:
    env = dict(kwargs or {})
    for key, value in list(env.items()):
        if key.endswith("_column"):
            base = key[: -len("_column")]
            env.setdefault(f"{base}_col", spreadsheet_column_value(value))
            if base.endswith("ies"):
                env.setdefault(f"{base[:-3]}y_col", spreadsheet_column_value(value))
        elif key.endswith("_col"):
            env[key] = spreadsheet_column_value(value)
            env.setdefault(f"{key[: -len('_col')]}_column", value)
    env.setdefault("value_start_row", 1)
    env.setdefault("value_end_row", ws.max_row)
    env.setdefault("result_start_row", 1)
    env.setdefault("result_end_row", ws.max_row)
    return env


def spreadsheet_column_value(value: Any) -> Any:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z]+", value.strip()):
        return column_index_from_string(value.strip().upper())
    return value


def run_code(code: str, input_xlsx: Path, output_xlsx: Path, work_dir: Path) -> Tuple[str, str, int]:
    script = work_dir / "run_spreadsheet_solution.py"
    script.write_text(
        "from pathlib import Path\n"
        f"INPUT_XLSX = Path({str(input_xlsx)!r})\n"
        f"OUTPUT_XLSX = Path({str(output_xlsx)!r})\n\n"
        + code
        + "\n"
    )
    proc = subprocess.run(
        ["python", str(script)],
        cwd=str(work_dir),
        text=True,
        capture_output=True,
        timeout=CODE_EXEC_TIMEOUT,
    )
    return proc.stdout[-4000:], proc.stderr[-4000:], proc.returncode


class NotebookPythonSession:
    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir.resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.driver_path = self.work_dir / "spreadsheet_notebook_driver.py"
        self.driver_path.write_text(NOTEBOOK_DRIVER_CODE)
        self.proc = subprocess.Popen(
            ["python", str(self.driver_path.resolve())],
            cwd=str(self.work_dir),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        self._stderr_tail: List[str] = []
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        if self.proc.stderr is None:
            return
        for line in self.proc.stderr:
            self._stderr_tail.append(line)
            if len(self._stderr_tail) > 40:
                self._stderr_tail = self._stderr_tail[-40:]

    def run_cell(self, code: str, *, timeout: float) -> Dict[str, Any]:
        if self.proc.poll() is not None:
            return {
                "stdout": "",
                "stderr": "Notebook Python session is not running.\n" + "".join(self._stderr_tail)[-2000:],
                "returncode": self.proc.returncode,
                "timed_out": False,
            }
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        payload = json.dumps({"code": code}, ensure_ascii=False)
        self.proc.stdin.write(payload + "\n")
        self.proc.stdin.flush()
        out_queue: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_one() -> None:
            try:
                out_queue.put(self.proc.stdout.readline())
            except Exception as exc:
                out_queue.put(json.dumps({"stdout": "", "stderr": str(exc), "returncode": 1}))

        reader = threading.Thread(target=read_one, daemon=True)
        reader.start()
        try:
            line = out_queue.get(timeout=max(1.0, float(timeout or CODE_EXEC_TIMEOUT)))
        except queue.Empty:
            self.close(kill=True)
            return {"stdout": "", "stderr": "Notebook cell timed out.", "returncode": 124, "timed_out": True}
        if not line:
            return {
                "stdout": "",
                "stderr": "Notebook Python session exited.\n" + "".join(self._stderr_tail)[-2000:],
                "returncode": self.proc.returncode,
                "timed_out": False,
            }
        try:
            result = json.loads(line)
        except Exception:
            result = {"stdout": "", "stderr": f"Malformed notebook driver response: {line[-1000:]}", "returncode": 1}
        result["stdout"] = str(result.get("stdout") or "")[-4000:]
        result["stderr"] = str(result.get("stderr") or "")[-4000:]
        result["returncode"] = int(result.get("returncode") or 0)
        result["timed_out"] = bool(result.get("timed_out", False))
        return result

    def close(self, *, kill: bool = False) -> None:
        if self.proc.poll() is not None:
            return
        if kill:
            self.proc.kill()
            return
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.write(json.dumps({"shutdown": True}) + "\n")
                self.proc.stdin.flush()
            self.proc.wait(timeout=2)
        except Exception:
            self.proc.kill()


NOTEBOOK_DRIVER_CODE = r'''
import contextlib
import io
import json
import sys
import traceback
from pathlib import Path

import openpyxl
from openpyxl import load_workbook

ns = {
    "__name__": "__spreadsheet_notebook__",
    "Path": Path,
    "openpyxl": openpyxl,
    "load_workbook": load_workbook,
}

for raw in sys.stdin:
    try:
        payload = json.loads(raw)
    except Exception as exc:
        print(json.dumps({"stdout": "", "stderr": f"bad json: {exc}", "returncode": 1}), flush=True)
        continue
    if payload.get("shutdown"):
        break
    stdout = io.StringIO()
    stderr = io.StringIO()
    rc = 0
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(str(payload.get("code") or ""), ns, ns)
    except Exception:
        rc = 1
        stderr.write(traceback.format_exc())
    print(
        json.dumps(
            {
                "stdout": stdout.getvalue()[-4000:],
                "stderr": stderr.getvalue()[-4000:],
                "returncode": rc,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
'''
