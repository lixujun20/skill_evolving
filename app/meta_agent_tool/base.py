from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from jsonschema import validate
from app.logger import logger

class BaseTool(ABC, BaseModel):
    name: str
    description: str
    parameters: Optional[dict] = None
    logger_instance: Any = logger
    dump_long_output: bool = False

    class Config:
        arbitrary_types_allowed = True

    async def __call__(self, **kwargs) -> Any:
        """Execute the tool with given parameters."""
        return await self.execute(**kwargs)

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Execute the tool with given parameters."""

    def to_param(self) -> Dict:
        """Convert tool to function call format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
    
class MadeTool(BaseTool):
    output_schema: dict

    async def execute(self, *args, **kwargs) -> Any:
        """Execute the tool with given parameters."""
        tool_result = await self._execute(*args, **kwargs)
        if not isinstance(tool_result, ToolResult):
            raise ValueError("Tool must return a ToolResult. Got {}".format(type(tool_result)))
        if tool_result.output:
            try:
                validate(instance=tool_result.output, schema=self.output_schema)
            except Exception as e:
                raise ValueError(f"Invalid output schema: {e}")
        return tool_result


class ToolResult(BaseModel):
    """Represents the result of a tool execution."""

    output: Any = Field(default=None)
    error: Optional[str] = Field(default=None)
    base64_image: Optional[str] = Field(default=None)
    system: Optional[str] = Field(default=None)

    class Config:
        arbitrary_types_allowed = True

    def __bool__(self):
        return any(getattr(self, field) for field in self.__fields__)

    def __add__(self, other: "ToolResult"):
        def combine_fields(
            field: Optional[str], other_field: Optional[str], concatenate: bool = True
        ):
            if field and other_field:
                if concatenate:
                    return field + other_field
                raise ValueError("Cannot combine tool results")
            return field or other_field

        return ToolResult(
            output=combine_fields(self.output, other.output),
            error=combine_fields(self.error, other.error),
            base64_image=combine_fields(self.base64_image, other.base64_image, False),
            system=combine_fields(self.system, other.system),
        )

    def __str__(self):
        return f"Error: {self.error}" if self.error else self.output

    def replace(self, **kwargs):
        """Returns a new ToolResult with the given fields replaced."""
        # return self.copy(update=kwargs)
        return type(self)(**{**self.dict(), **kwargs})


class CLIResult(ToolResult):
    """A ToolResult that can be rendered as a CLI output."""


class ToolFailure(ToolResult):
    """A ToolResult that represents a failure."""

        