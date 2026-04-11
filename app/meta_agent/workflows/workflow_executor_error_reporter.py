import json
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum, auto
from abc import ABC, abstractmethod

from app.meta_agent_tool.base import BaseTool, ToolResult

class WorkflowSuccessReporter(BaseTool):
    name: str = "workflow_success_reporter"
    description: str = "Reports that workflow execution succeeded."
    parameters: dict = {
        "type": "object",
        "properties": {
            "execution_summary": {
                "type": "string",
                "description": "A brief summary of the entire workflow execution process"
            },
            "output": {
                "type": "string",
                "description": "The final output produced by the workflow"
            },
            "guideline_reflection": {
                "type": "string",
                "description": "Reflection on flaws or improvements on the provided workflow guideline based on the execution experience"
            }
        },
        "required": ["execution_summary", "output", "guideline_reflection"]
    }

    async def execute(self, execution_summary: str, output: str, guideline_reflection: str):
        raise NotImplementedError("This tool is only a marker for workflow success reporting. Use specific success reporting tools instead.")

class WorkflowErrorReporter(BaseTool):
    name: str = "workflow_error_reporter"
    description: str = "Reports that workflow execution failed and specifies the high-level error type."
    parameters: dict = {
        "type": "object",
        "properties": {
            "failure_type": {
                "type": "string",
                "description": "High-level failure type of the workflow execution",
                "enum": [
                    "python_syntax_error",
                    "tool_interface_error",
                    "workflow_logic_error",
                    "tool_runtime_error",
                    "unexpected_tool_output",
                    "task_planning_error",
                    "system_error"
                ]
            }
        },
        "required": ["failure_type"]
    }

    async def execute(self, failure_type: str):
        # return ToolResult(output=json.dumps({
        #     "status": "failure",
        #     "failure_type": failure_type
        # }))
        raise NotImplementedError("This tool is only a marker for workflow error reporting. Use specific error reporting tools instead.")


class WorkflowPythonError(BaseTool):
    name: str = "workflow_python_error"
    description: str = "Reports a Python syntax or structural error in generated workflow code."
    parameters: dict = {
        "type": "object",
        "properties": {
            "error_message": {"type": "string"},
            "line": {"type": "integer"},
            "code_snippet": {"type": "string"},
            "root_cause": {
                "type": "string",
                "description": "Why this error occurred",
                "enum": ["invalid_syntax", "undefined_variable", "missing_import", "invalid_structure"]
            }
        },
        "required": ["error_message", "root_cause"]
    }

    async def execute(
        self,
        error_message: str,
        root_cause: str,
        line: int = None,
        code_snippet: str = None
    ):
        return ToolResult(output=json.dumps({
            "layer": "python",
            "failure_type": "python_syntax_error",
            "recoverable_by_builder": True,
            "error_message": error_message,
            "line": line,
            "code_snippet": code_snippet,
            "root_cause": root_cause,
            "suggested_builder_action": "rewrite_workflow_code"
        }))
    
class WorkflowToolInterfaceError(BaseTool):
    name: str = "workflow_tool_interface_error"
    description: str = "Reports incorrect usage of a tool interface."
    parameters: dict = {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "error_message": {"type": "string"},
            "expected_signature": {"type": "string"},
            "actual_call": {"type": "string"}
        },
        "required": ["tool_name", "error_message"]
    }

    async def execute(
        self,
        tool_name: str,
        error_message: str,
        expected_signature: str = None,
        actual_call: str = None
    ):
        return ToolResult(output=json.dumps({
            "layer": "tool",
            "failure_type": "tool_interface_error",
            "recoverable_by_builder": True,
            "tool_name": tool_name,
            "error_message": error_message,
            "expected_signature": expected_signature,
            "actual_call": actual_call,
            "suggested_builder_action": "fix_tool_call"
        }))
    
class WorkflowLogicError(BaseTool):
    name: str = "workflow_logic_error"
    description: str = "Reports logical or dependency errors in the workflow."
    parameters: dict = {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "node": {"type": "string"},
            "missing_dependency": {"type": "string"}
        },
        "required": ["description"]
    }

    async def execute(
        self,
        description: str,
        node: str = None,
        missing_dependency: str = None
    ):
        return ToolResult(output=json.dumps({
            "layer": "workflow",
            "failure_type": "workflow_logic_error",
            "recoverable_by_builder": True,
            "description": description,
            "node": node,
            "missing_dependency": missing_dependency,
            "suggested_builder_action": "rebuild_workflow_dag"
        }))

class WorkflowToolRuntimeError(BaseTool):
    name: str = "workflow_tool_runtime_error"
    description: str = "Reports runtime failure of an external tool."
    parameters: dict = {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "error_message": {"type": "string"},
            "retryable": {"type": "boolean"}
        },
        "required": ["tool_name", "error_message", "retryable"]
    }

    async def execute(
        self,
        tool_name: str,
        error_message: str,
        retryable: bool
    ):
        return ToolResult(output=json.dumps({
            "layer": "tool",
            "failure_type": "tool_runtime_error",
            "recoverable_by_builder": retryable,
            "tool_name": tool_name,
            "error_message": error_message,
            "retryable": retryable,
            "suggested_builder_action": (
                "add_retry_or_backoff" if retryable else "change_tool_or_strategy"
            )
        }))

class WorkflowUnexpectedOutputError(BaseTool):
    name: str = "workflow_unexpected_output_error"
    description: str = "Reports that a tool returned output that violates builder assumptions."
    parameters: dict = {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "observed_output": {"type": "string"},
            "expected_assumption": {"type": "string"}
        },
        "required": ["tool_name", "observed_output", "expected_assumption"]
    }

    async def execute(
        self,
        tool_name: str,
        observed_output: str,
        expected_assumption: str
    ):
        return ToolResult(output=json.dumps({
            "layer": "data",
            "failure_type": "unexpected_tool_output",
            "recoverable_by_builder": True,
            "tool_name": tool_name,
            "observed_output": observed_output,
            "expected_assumption": expected_assumption,
            "suggested_builder_action": "add_validation_or_fallback"
        }))

class WorkflowTaskPlanningError(BaseTool):
    name: str = "workflow_task_planning_error"
    description: str = "Reports that the workflow completed but failed to satisfy the user intent."
    parameters: dict = {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "missing_capabilities": {
                "type": "array",
                "items": {"type": "string"}
            }
        },
        "required": ["description"]
    }

    async def execute(
        self,
        description: str,
        missing_capabilities: list = None
    ):
        return ToolResult(output=json.dumps({
            "layer": "planning",
            "failure_type": "task_planning_error",
            "recoverable_by_builder": True,
            "description": description,
            "missing_capabilities": missing_capabilities,
            "suggested_builder_action": "replan_entire_workflow"
        }))


@dataclass
class ErrorEnvelope:
    failure_type: str
    layer: Optional[str]
    recoverable_by_builder: bool
    payload: Dict[str, Any]


class BuilderDecision(Enum):
    RETRY_EXECUTOR = auto()
    REWRITE_WORKFLOW = auto()
    REPLAN_WORKFLOW = auto()
    CHANGE_STRATEGY = auto()
    ABORT_TO_USER = auto()
    

class ErrorHandler(ABC):

    @abstractmethod
    def decide(self, error: ErrorEnvelope, context: Dict[str, Any]) -> BuilderDecision:
        pass

    @abstractmethod
    def apply(self, error: ErrorEnvelope, context: Dict[str, Any]) -> None:
        """
        Mutate builder state if needed (e.g., update workflow guideline)
        """
        pass

class PythonSyntaxErrorHandler(ErrorHandler):

    def decide(self, error: ErrorEnvelope, context: Dict[str, Any]) -> BuilderDecision:
        return BuilderDecision.REWRITE_WORKFLOW

    def apply(self, error: ErrorEnvelope, context: Dict[str, Any]) -> None:
        context['rewrite_mode'] = 'full'

class ToolInterfaceErrorHandler(ErrorHandler):

    def decide(self, error: ErrorEnvelope, context: Dict[str, Any]) -> BuilderDecision:
        return BuilderDecision.REWRITE_WORKFLOW

    def apply(self, error: ErrorEnvelope, context: Dict[str, Any]) -> None:
        context['rewrite_mode'] = 'tool_call_only'
        context['error_detail'] = error.payload

class WorkflowLogicErrorHandler(ErrorHandler):

    def decide(self, error: ErrorEnvelope, context: Dict[str, Any]) -> BuilderDecision:
        return BuilderDecision.REPLAN_WORKFLOW

    def apply(self, error: ErrorEnvelope, context: Dict[str, Any]) -> None:
        context['replan'] = True

class ToolRuntimeErrorHandler(ErrorHandler):

    def decide(self, error: ErrorEnvelope, context: Dict[str, Any]) -> BuilderDecision:
        if error.payload.get('retryable', False):
            return BuilderDecision.RETRY_EXECUTOR
        return BuilderDecision.CHANGE_STRATEGY

    def apply(self, error: ErrorEnvelope, context: Dict[str, Any]) -> None:
        context['tool_error'] = error.payload

class UnexpectedOutputErrorHandler(ErrorHandler):

    def decide(self, error: ErrorEnvelope, context: Dict[str, Any]) -> BuilderDecision:
        return BuilderDecision.REWRITE_WORKFLOW

    def apply(self, error: ErrorEnvelope, context: Dict[str, Any]) -> None:
        context['harden_workflow'] = True

class TaskPlanningErrorHandler(ErrorHandler):

    def decide(self, error: ErrorEnvelope, context: Dict[str, Any]) -> BuilderDecision:
        attempt = context.get('planning_failures', 0)
        if attempt >= 1:
            return BuilderDecision.ABORT_TO_USER
        return BuilderDecision.REPLAN_WORKFLOW

    def apply(self, error: ErrorEnvelope, context: Dict[str, Any]) -> None:
        context['planning_failures'] = context.get('planning_failures', 0) + 1
