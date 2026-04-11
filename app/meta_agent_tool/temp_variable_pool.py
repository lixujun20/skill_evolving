import asyncio
from typing import Dict
from app.meta_agent_tool.base import BaseTool, ToolResult

COSMOS_VAR_PREFIX = "cosmos_var_"

class TempVariablePool:
    def __init__(self):
        self._variables: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._max_id = 0
        
    def is_handle(self, s: str) -> bool:
        return s.startswith(COSMOS_VAR_PREFIX) and (sid := s.split(COSMOS_VAR_PREFIX, 1)[1]).isdigit() and int(sid) < self._max_id
    
    def replace_reference(self, s: str) -> str:
        import re
        pattern = f'@{COSMOS_VAR_PREFIX}(\\d+)'
        def replacer(match):
            handle = f"{COSMOS_VAR_PREFIX}{match.group(1)}"
            return self._variables.get(handle, match.group(0))
        return re.sub(pattern, replacer, s)
    
    async def create(self, value: str):
        async with self._lock:
            handle = f"{COSMOS_VAR_PREFIX}{self._max_id}"
            self._max_id += 1
            self._variables[handle] = value
            return handle

    async def get(self, handle: str) -> str:
        async with self._lock:
            return self._variables.get(handle)
        
        
class TempVariableViewTool(BaseTool):
    name: str = "temp_variable_view"
    description: str = "View the content of a dumped tool execution result by its handle (@{}<id>). ".format(COSMOS_VAR_PREFIX)
    parameters: Dict = {
        "type": "object",
        "properties": {
            "handle": {
                "type": "string",
                "description": "The handle of the temporary variable to view, e.g., '{}3'.".format(COSMOS_VAR_PREFIX)
            }
        },
        "required": ["handle"]
    }
    
    async def execute(self, handle: str) -> ToolResult:
        if handle.startswith('@'):
            handle = handle[1:]
        if not TEMP_VARIABLE_POOL.is_handle(handle):
            return ToolResult(error=f"'{handle}' is not a valid temporary variable handle.")
        value = await TEMP_VARIABLE_POOL.get(handle)
        if value is None:
            return ToolResult(error=f"Error: No variable found for handle '{handle}'.")
        return ToolResult(output=value)
    

TEMP_VARIABLE_POOL = TempVariablePool()
# Example usage:
async def example_usage():
    handle = await TEMP_VARIABLE_POOL.create("some large text")
    value = await TEMP_VARIABLE_POOL.get(handle)
    print(TEMP_VARIABLE_POOL.is_handle(handle))
    print(f"Handle: {handle}, Value: {value}")
    text = ('This is a reference to @' + handle + ' in the text.') * 10
    replaced_text = TEMP_VARIABLE_POOL.replace_reference(text)
    print(f"Original text: {text}")
    print(f"Replaced text: {replaced_text}")
    
if __name__ == "__main__":
    asyncio.run(example_usage())