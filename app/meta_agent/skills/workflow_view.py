"""workflow_view.py — ARCH-BUG-4 fix.

WorkflowView maintains a *named collection* of workflow plans for a session.
The active workflow can be edited in-memory, persisted to disk, and later
re-opened by name.  The agent drives all operations via WorkflowManagerTool
(toolcall interface).

Storage layout (per session)::

    ~/.skill_workflows/<session_id>/
        <name>.py          # persisted workflow plan files
        _index.json        # name → file mapping + metadata

Usage within the pipeline::

    session.workflow_view.edit("my_plan", content=workflow_plan_str)
    session.workflow_view.save("my_plan")
    old = session.workflow_view.open("my_plan")
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


_STORAGE_ROOT = Path.home() / ".skill_workflows"


def _safe_name(name: str) -> str:
    """Strip characters unsafe for file names."""
    return re.sub(r"[^\w\-]", "_", name)[:80]


@dataclass
class WorkflowView:
    """In-memory workflow view for a single pipeline session.

    One active workflow is editable at a time.  Others are persisted on disk
    and can be loaded by name.
    """

    session_id: str
    storage_dir: Path = field(init=False)

    # active (in-memory) workflow
    active_name: Optional[str] = field(default=None)
    active_content: Optional[str] = field(default=None)

    # metadata index: name → {path, saved_at, description}
    _index: Dict[str, dict] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.storage_dir = _STORAGE_ROOT / self.session_id
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._load_index()

    # ── Public API ────────────────────────────────────────────────────────

    def edit(self, name: str, content: str) -> None:
        """Set the active workflow (in-memory only; call save() to persist)."""
        self.active_name = name
        self.active_content = content

    def save(self, name: Optional[str] = None, description: str = "") -> str:
        """Persist the active workflow to disk.  Returns the file path."""
        target_name = name or self.active_name
        if not target_name:
            raise ValueError("No workflow name — provide one or set active_name first")
        if self.active_content is None:
            raise ValueError("No active content to save")

        safe = _safe_name(target_name)
        path = self.storage_dir / f"{safe}.py"
        path.write_text(self.active_content, encoding="utf-8")

        self._index[target_name] = {
            "path": str(path),
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "description": description,
        }
        self._save_index()
        return str(path)

    def save_and_close(self, name: Optional[str] = None, description: str = "") -> str:
        """Persist and clear the active workflow.  Returns the file path."""
        path = self.save(name=name, description=description)
        self.active_name = None
        self.active_content = None
        return path

    def open(self, name: str) -> str:
        """Load a saved workflow by name and set it as active.  Returns its content."""
        if name not in self._index:
            available = ", ".join(self._index) or "(none)"
            raise KeyError(f"Workflow '{name}' not found. Available: {available}")
        path = Path(self._index[name]["path"])
        content = path.read_text(encoding="utf-8")
        self.active_name = name
        self.active_content = content
        return content

    def list_workflows(self) -> List[dict]:
        """Return metadata list for all saved workflows (name, saved_at, description)."""
        return [
            {"name": n, **{k: v for k, v in meta.items() if k != "path"}}
            for n, meta in self._index.items()
        ]

    def get_active(self) -> Optional[str]:
        """Return the current in-memory workflow content, or None."""
        return self.active_content

    # ── Private helpers ───────────────────────────────────────────────────

    def _index_path(self) -> Path:
        return self.storage_dir / "_index.json"

    def _load_index(self) -> None:
        idx_path = self._index_path()
        if idx_path.exists():
            try:
                self._index = json.loads(idx_path.read_text(encoding="utf-8"))
            except Exception:
                self._index = {}

    def _save_index(self) -> None:
        self._index_path().write_text(
            json.dumps(self._index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
