from typing import Dict, List
from pydantic import Field, PrivateAttr
import asyncio
from app.config import config
from app.meta_agent_tool.base import BaseTool, ToolResult
from app.sandbox.core.exceptions import SandboxTimeoutError
from app.sandbox.client import create_sandbox_client, LocalSandboxClient, SandboxSettings

MAX_CHAR = 10000
class SandboxTool(BaseTool):
    name: str = "sandbox_tool"
    description: str = "Execute linux command in docker sandbox or Python scripts in python interactive cli."
    parameters: dict = {
        "type": "object",
        "properties": {
            "terminal_id": {
                "type": "integer",
                "description": "The terminal id integer for your execution. If leave empty, will create a new terminal and return its id. Compulsory since the second call."
            },
            "command": {
                "type": "string",
                "description": "The command to be executed. At most {} chars. If too long, consider decomposing into several smaller commands.".format(MAX_CHAR)
            },
            "timeout": {
                "type": "number",
                "description": "Time to wait for command execution. Default is 10 seconds.",
                "default": 30
            }
        },
        "required": ['command']
    }
    env_vars: Dict[str, str] = None
    terminal_start_commands: List[str] = None
    session: LocalSandboxClient = None
    fill_empty_line: str = '#'
    terminal_ids: set = Field(default_factory=lambda: {0})

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.session = create_sandbox_client()

    async def initialize_session(self, **kwargs):
        image = kwargs.pop('image', config.library_config.tool_tester_image)
        kwargs.pop('network', None)
        sandbox_config = SandboxSettings(image=image, network_enabled=True, **kwargs)
        await self.session.create(sandbox_config, env_vars=self.env_vars, start_commands=self.terminal_start_commands)

    async def execute(self, **kwargs) -> ToolResult:
        terminal_id = kwargs.get('terminal_id', 0)

        command = kwargs['command']
        if self.fill_empty_line:
            command_lines = command.split('\n')
            end_flush = False
            if command[-1] == '\n':
                end_flush = True
            if end_flush:
                command_lines = [line if line.strip() != '' else self.fill_empty_line for line in command_lines[:-1]] + ['']
            else:
                command_lines = [line if line.strip() != '' else self.fill_empty_line for line in command_lines] + ['']
            command = '\n'.join(command_lines)
        timeout = kwargs.get('timeout', 30)
        timeout = min(timeout, 180) # Ban long waitting
        try:
            if len(command) > MAX_CHAR:
                raise ValueError("Command lengths larger than {}. Consider decomposing into several smaller commands.".format(MAX_CHAR))
            try:
                terminal_id = int(terminal_id)
            except:
                raise ValueError('terminal_id must be an integer')
            for i in range(2):
                try:
                    print("***************************************************************************")
                    print("Executing command in terminal {}: {}".format(terminal_id, command))
                    print("***************************************************************************")
                    output = await self.session.run_command(command=command, timeout=timeout, terminal_id=terminal_id)
                    # import pdb; pdb.set_trace()
                except RuntimeError as e:
                    if 'Terminal id invalid' in str(e):
                        assert i == 0, 'create error'
                        await self.session.create_terminal(terminal_id, self.env_vars, self.terminal_start_commands)
                        self.terminal_ids.add(terminal_id)
                    else:
                        raise e
                except SandboxTimeoutError as e:
                    raise ValueError(str(e) + '\nPlease check if your command is either **incomplete / not closing** or **too complicated**.')
                else:
                    return ToolResult(
                        output=output
                    )
        except Exception as e:
            return ToolResult(
                error=str(e),
            )

    def create_new_terminal_id(self):
        i = 0
        while True:
            if i in self.terminal_ids:
                i += 1
            else:
                return i

    async def create_terminal(self) -> int:
        new_terminal_id = self.create_new_terminal_id()
        print('creating terminal {}'.format(new_terminal_id))
        await self.session.create_terminal(new_terminal_id, env_vars=self.env_vars, start_commands=self.terminal_start_commands)
        self.terminal_ids.add(new_terminal_id)
        return new_terminal_id

    async def close_terminal(self, terminal_id):
        print('closing terminal {}'.format(terminal_id))
        await self.session.close_terminal(terminal_id)
        self.terminal_ids.remove(terminal_id)

    async def copy_from(self, container_path: str, local_path: str) -> None:
        """Copies file from container."""
        return await self.session.copy_from(container_path, local_path)

    async def copy_to(self, local_path: str, container_path: str) -> None:
        """Copies file to container."""
        return await self.session.copy_to(local_path, container_path)

    async def read_file(self, path: str) -> str:
        """Reads file."""
        return await self.session.read_file(path)

    async def write_file(self, path: str, content: str) -> None:
        """Writes file."""
        return await self.session.write_file(path, content)

    async def cleanup(self):
        if self.session:
            await self.session.cleanup()
        self.terminal_ids = {}

async def main():
    tool = SandboxTool(env_vars={'PYTHONPATH': '/'}, terminal_start_commands=["bash -c 'stty -echo; ipython'"])
    await tool.initialize_session()
    # result = await tool.execute(command="echo '1\n2' > 1.txt")
    # while True:
    #     if input() == 'Q':
    #         break
    command = '#\n%autoindent False\nimport importlib.util\nimport inspect\nimport asyncio\n#\n#\nspec = importlib.util.spec_from_file_location("WebSearch", "/app/tool_cosmos_ds/meta_tools/predefined/WebSearch.py")\nmodule = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n#\nfor obj in vars(module).values():\n    if inspect.isclass(obj) and any(c.__name__.split(".")[-1] == \'BaseTool\' for c in obj.__mro__[1:]):\n        globals()["web_search"] = obj()\n        break\n#\n#\n#\nspec = importlib.util.spec_from_file_location("LLMTool", "/app/tool_cosmos_ds/meta_tools/predefined/LLMTool.py")\nmodule = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n#\nfor obj in vars(module).values():\n    if inspect.isclass(obj) and any(c.__name__.split(".")[-1] == \'BaseTool\' for c in obj.__mro__[1:]):\n        globals()["llm_tool"] = obj()\n        break\n#\n#\n#\nspec = importlib.util.spec_from_file_location("DockerCodeExecutorTool", "/app/tool_cosmos_ds/meta_tools/predefined/DockerCodeExecutorTool.py")\nmodule = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n#\nfor obj in vars(module).values():\n    if inspect.isclass(obj) and any(c.__name__.split(".")[-1] == \'BaseTool\' for c in obj.__mro__[1:]):\n        globals()["docker_code_executor"] = obj()\n        break\n#\n#\n#\nspec = importlib.util.spec_from_file_location("StrReplaceEditor", "/app/tool_cosmos_ds/meta_tools/predefined/StrReplaceEditor.py")\nmodule = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n#\nfor obj in vars(module).values():\n    if inspect.isclass(obj) and any(c.__name__.split(".")[-1] == \'BaseTool\' for c in obj.__mro__[1:]):\n        globals()["str_replace_editor"] = obj()\n        break\n#\n'
    terminal_id = await tool.create_terminal()
    result = await tool.execute(terminal_id=terminal_id, command=command, timeout=10)
    print('result1:')
    print(result)
    result = await tool.execute(terminal_id=terminal_id, command="\n\n", timeout=10)
    print('result2:')
    print(result)
    await tool.close_terminal(terminal_id)
    terminal_id = await tool.create_terminal()

if __name__ == '__main__':
    asyncio.run(main())