"""workflow_manager.py — WorkflowManagerTool (ARCH-BUG-4).

Provides a toolcall interface so the Gardener agent (or any agent inside
the pipeline) can manipulate the WorkflowView associated with the current
pipeline session.

Supported actions
-----------------
edit   — write content into the active in-memory workflow
save   — persist the active workflow to disk (optionally rename)
open   — load a previously saved workflow by name (becomes active)
list   — list all saved workflows for this session
show   — return the current in-memory workflow content

The tool is initialised with a WorkflowView instance and registered in the
agent's tool collection before each pipeline run.
"""

from __future__ import annotations

from typing import Any, Optional

from app.meta_agent_tool.base import BaseTool, ToolResult
from app.meta_agent.skills.workflow_view import WorkflowView


class WorkflowManagerTool(BaseTool):
    """Agent-facing toolcall for WorkflowView operations."""

    name: str = "workflow_manager"
    description: str = (
        "管理当前会话的工作流视图（workflow view）。支持以下操作：\n"
        "- edit:  将提供的内容写入当前活跃工作流（仅在内存中，需调用 save 持久化）\n"
        "- save:  将当前活跃工作流持久化到本地文件系统（可指定名称和描述）\n"
        "- open:  按名称加载一个历史工作流并置为活跃状态\n"
        "- list:  列出本次会话中所有已保存的工作流\n"
        "- show:  显示当前活跃工作流的内容"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["edit", "save", "open", "list", "show"],
                "description": "要执行的操作",
            },
            "name": {
                "type": "string",
                "description": "工作流名称（edit / save / open 时使用）",
            },
            "content": {
                "type": "string",
                "description": "工作流内容（仅 edit 时使用）",
            },
            "description": {
                "type": "string",
                "description": "工作流的简短描述（仅 save 时使用）",
            },
        },
        "required": ["action"],
    }

    workflow_view: WorkflowView

    class Config:
        arbitrary_types_allowed = True

    async def execute(  # type: ignore[override]
        self,
        action: str,
        name: Optional[str] = None,
        content: Optional[str] = None,
        description: str = "",
        **_: Any,
    ) -> ToolResult:
        try:
            wv = self.workflow_view

            if action == "edit":
                if not name:
                    return ToolResult(error="'edit' 操作需要提供 'name' 参数")
                if content is None:
                    return ToolResult(error="'edit' 操作需要提供 'content' 参数")
                wv.edit(name, content)
                return ToolResult(output=f"已更新活跃工作流 '{name}'（共 {len(content)} 字符）")

            elif action == "save":
                path = wv.save(name=name, description=description)
                return ToolResult(output=f"已保存工作流到 {path}")

            elif action == "open":
                if not name:
                    return ToolResult(error="'open' 操作需要提供工作流名称")
                loaded = wv.open(name)
                return ToolResult(output=f"已打开工作流 '{name}':\n\n{loaded}")

            elif action == "list":
                workflows = wv.list_workflows()
                if not workflows:
                    return ToolResult(output="当前会话暂无已保存的工作流")
                lines = [f"- {w['name']} (保存于 {w['saved_at']}) {w.get('description', '')}".rstrip()
                         for w in workflows]
                return ToolResult(output="\n".join(lines))

            elif action == "show":
                active = wv.get_active()
                if active is None:
                    return ToolResult(output="当前无活跃工作流（使用 open 加载一个历史工作流，或由规划阶段自动设置）")
                return ToolResult(output=f"活跃工作流 '{wv.active_name}':\n\n{active}")

            else:
                return ToolResult(error=f"未知 action: {action!r}")

        except Exception as exc:
            return ToolResult(error=str(exc))
