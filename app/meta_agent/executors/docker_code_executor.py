import docker
import socket
import json
import asyncio
from functools import partial
import time
from app.tool.base import BaseTool, ToolResult
from typing import Any
import ast

class DockerExecutor:
    def __init__(self):
        try:
            self.client = docker.from_env()
            print("Docker client initialized successfully")
        except Exception as e:
            print(f"Docker connection failed: {e}")
        self.sessions = {}
        self.clean_interval = 3600
        self.clean_session_handle = None
        self.clean_session_flag = True

    async def start(self):
        if self.clean_session_handle is None:
            self.clean_session_handle = asyncio.create_task(
                self.clean_sessions()
            )

    async def clean_sessions(self):
        while self.clean_session_flag:
            print('cleaning...')
            clean_session = []
            for session_id, session in self.sessions.items():
                start_time = session['start_time']
                if time.time() - start_time >= self.clean_interval:
                    clean_session.append(session_id)
            for session_id in clean_session:
                await self.close_session(session_id)
            await asyncio.sleep(3600)

    async def _get_socket(self, exec_id):
        """Create a socket for a given exec session ID."""
        loop = asyncio.get_event_loop()
        sock = await loop.run_in_executor(
            None,
            partial(
                self.client.api.exec_start,
                exec_id,
                socket=True,
                tty=True
            )
        )
        
        read_sock = socket.socket(fileno=sock.fileno())
        read_sock.setblocking(0)
        write_sock = socket.socket(fileno=sock.fileno())

        return read_sock, write_sock, sock

    async def _read_output(self, read_sock, wait_for=1):
        """Read output from the read socket."""
        loop = asyncio.get_event_loop()
        chunks = []
        start_time = time.time()
        while time.time() - start_time < wait_for:
            try:
                chunk = await loop.run_in_executor(None, read_sock.recv, 4096)
                if not chunk:
                    break
                chunks.append(chunk.decode('utf-8'))
            except BlockingIOError:
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"Read error: {e}")
                break
            except asyncio.exceptions.CancelledError:
                break
        return ''.join(chunks)

    async def create_session(self, session_id, image_name="my-python-image", cmd="bash"):
        """Create a new session with a given ID."""
        if session_id in self.sessions:
            raise ValueError(f"Session '{session_id}' already exists.")

        # Create and start the container
        container = self.client.containers.run(
            image_name,
            detach=True,
            tty=True,
            stdin_open=True
        )

        # Create an exec instance for the container
        exec_id = self.client.api.exec_create(
            container.id,
            cmd,
            tty=True,
            stdin=True,
            stdout=True,
            stderr=True
        )

        # Retrieve the socket for interaction
        read_sock, write_sock, sock = await self._get_socket(exec_id)

        self.sessions[session_id] = {
            "container": container,
            "exec_id": exec_id,
            "read_sock": read_sock,
            "write_sock": write_sock,
            "raw_sock": sock,
            "start_time": time.time()
        }

    async def execute(self, session_id, input_command: str, wait_for: float=1):
        """Execute a command in an existing session and return the output."""
        if session_id not in self.sessions:
            # raise ValueError(f"Session '{session_id}' does not exist.")
            await self.create_session(session_id=session_id, cmd="python")

        session = self.sessions[session_id]
        read_sock = session["read_sock"]
        write_sock = session["write_sock"]

        try:
            # Write the user command to the container
            write_sock.send((input_command + "\n\n").encode('utf-8'))

            # Read the output from the socket
            output = await asyncio.wait_for(self._read_output(read_sock, wait_for=wait_for), timeout=wait_for + 1)
            return output.strip()

        except Exception as e:
            raise e

    async def close_session(self, session_id):
        """Close an existing session."""
        if session_id not in self.sessions:
            raise ValueError(f"Session '{session_id}' does not exist.")

        session = self.sessions.pop(session_id)
        # session["read_sock"].close()
        # session["write_sock"].close()
        session["container"].kill()
        session["raw_sock"].close()

    async def close_all_sessions(self):
        """Close all existing sessions."""
        for session_id in list(self.sessions.keys()):
            await self.close_session(session_id)

    async def __aexit__(self, exc_type, exc, tb):
        await self.close_all_sessions()
        self.clean_session_flag = False
        self.client.close()
        await self.clean_session_handle


class DockerCodeExecutorTool(BaseTool):

    name: str = "docker_code_executor_tool"
    description: str = "a contextual Python code execution tool"

    parameters: dict = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "A unique ID used to identify and reuse code execution sessions. For example, 'user_123_python_session'。"
            },
            "code": {
                "type": "string",
                "description": "Code string to be executed"
            },
            "variables": {
                "type": "array",
                "description": "Variables to be used in the code execution",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Variable name"
                        },
                        "type": {
                            "type": "string",
                            "description": "Variable type",
                        },
                        "value": {
                            "type": "string",
                            "description": "Variable value"
                        }
                    }
                }
            }
        },
        "required": ["session_id", "code"]
    }

    executor: DockerExecutor | None = None

    async def _ensure_executor(self):
        if self.executor is None:
            self.executor = DockerExecutor()
            await self.executor.start()

    async def execute(self, session_id: str, code: str, variables: list[dict[str, Any]]= None) -> ToolResult:
        try:
            await self._ensure_executor()
            if variables:
                print("variables:",variables)
                var_assignments = '\n'.join(f'{items["key"]} : {items["type"]} = {repr(items["value"])}' for items in variables)
                code = f"{var_assignments}\n{code}"

            output = await self.executor.execute(session_id,code)

            if output and ("Error" in output or "error" in output):
                output=output.split(">>>")
                all_mistakes=[]
                for each_sentence in output:
                    if "Error" in each_sentence or "error" in each_sentence:
                        all_mistakes.append(each_sentence)
                return ToolResult(error=str(all_mistakes))
            
            mark = "The result I want to output:"
            if mark in output:
                expected_output = output.split(mark)[-1]
                cleaned_output = expected_output.replace("\r\n>>>", "").strip()
                try:
                    json_output = json.loads(cleaned_output)
                    final_output = json_output

                except json.JSONDecodeError as e:
                    return ToolResult(error= f"Invalid JSON output: {str(e)}")
            else:
                final_output={}

            return ToolResult(output=final_output)

        except Exception as e:
            return ToolResult(error=f"An unexpected error occurred in the tool: {str(e)}")
