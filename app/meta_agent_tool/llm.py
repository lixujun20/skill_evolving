from typing import Dict
from app.schema import Field
from app.llm import LLM
from app.meta_agent_tool.base import BaseTool, ToolResult

class LLMTool(BaseTool):
    name: str = "llm_tool"
    description: str = "Calling LLM API. Initialize with a specific LLM instance. e.g. LLM(config_name=\"vison\") (options: llm, free)"

    parameters: Dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["ask", "ask_tool"]
            },
            "prompt": {
                "type": "string"
            },
            "history": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {
                            "type": "string",
                            "enum": ["user", "assistant", "system", "tool"]
                        },
                        "content": {
                            "type": "string"
                        }
                    },
                    "required": ["role", "content"],
                    "additionalProperties": False
                }
            },
            "tools": {
                "type": "array",
                "description": "Tool calling protocol for LLM. Must be specified if `action` is `ask_tool`.",
                "items": {
                    "type": "object",
                    "properties": {
                        "function": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string"
                                },
                                "description": {
                                    "type": "string"
                                },
                                "strict": {
                                    "type": "boolean"
                                },
                                "parameters": {
                                    "type": "object",
                                    "additionalProperties": False
                                }
                            },
                            "required": ["name", "description", "strict"],
                            "additionalProperties": False
                        }
                    },
                    "required": ["function"],
                    "additionalProperties": False
                }
            },
            "temperature": {
                "type": "number"
            }
        },
        "required": ['action', 'prompt']
    }
    llm: LLM = Field(default_factory=lambda: LLM(config_name="free"))

    async def execute(self, **kwargs) -> ToolResult:
        action = kwargs.pop('action')
        prompt = kwargs.pop('prompt')
        history = kwargs.pop('history', [])
        tools = kwargs.pop('tools', None)
        if tools:
            tools = [{'type': 'function', 'function': tool} for tool in tools]
        temperature = kwargs.pop('temperature', None)
        messages = [{'role': 'user', 'content': prompt}]
        if history:
            history = [{'role': item['role'], 'content': item['content']} for item in history]
            messages = history + messages
        try:
            if action == 'ask':
                return ToolResult(output=await self.llm.ask(
                    messages=messages,
                    stream=False,
                    temperature=temperature
                ))
            elif action == 'ask_tool':
                return ToolResult(output=await self.llm.ask_tool(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    **kwargs
                ))
            else:
                raise ValueError(f"Invalid action: {action}")
        except Exception as e:
            return ToolResult(error=str(e))
