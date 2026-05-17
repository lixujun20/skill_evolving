from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.meta_agent_tool.base import BaseTool, ToolResult


@dataclass
class EditResult:
    content: str
    replaced: int


class StrReplaceEditor(BaseTool):
    name: str = "str_replace_editor"
    description: str = "Edit text by replacing an exact substring or appending text."
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["show", "replace", "append"],
                "description": "Edit action.",
            },
            "content": {
                "type": "string",
                "description": "Current content for show/replace/append.",
            },
            "old": {
                "type": "string",
                "description": "Exact substring to replace.",
            },
            "new": {
                "type": "string",
                "description": "Replacement text.",
            },
            "append_text": {
                "type": "string",
                "description": "Text to append.",
            },
        },
        "required": ["action"],
    }

    async def execute(
        self,
        action: str,
        content: str | None = None,
        old: str | None = None,
        new: str | None = None,
        append_text: str | None = None,
        **_: Any,
    ) -> ToolResult:
        try:
            if action == "show":
                return ToolResult(output=content or "")
            if content is None:
                return ToolResult(error="'content' is required")
            if action == "replace":
                if old is None or new is None:
                    return ToolResult(error="'old' and 'new' are required for replace")
                replaced = content.count(old)
                if replaced == 0:
                    return ToolResult(error="substring not found")
                return ToolResult(output=EditResult(content=content.replace(old, new), replaced=replaced).content)
            if action == "append":
                suffix = append_text or ""
                return ToolResult(output=content + suffix)
            return ToolResult(error=f"unknown action: {action}")
        except Exception as exc:
            return ToolResult(error=str(exc))
