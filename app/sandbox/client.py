from abc import ABC, abstractmethod
from typing import Dict, Optional, Protocol, List

from app.config import SandboxSettings
from app.sandbox.core.sandbox import DockerSandbox


class SandboxFileOperations(Protocol):
    """Protocol for sandbox file operations."""

    async def copy_from(self, container_path: str, local_path: str) -> None:
        """Copies file from container to local.

        Args:
            container_path: File path in container.
            local_path: Local destination path.
        """
        ...

    async def copy_to(self, local_path: str, container_path: str) -> None:
        """Copies file from local to container.

        Args:
            local_path: Local source file path.
            container_path: Destination path in container.
        """
        ...

    async def read_file(self, path: str) -> str:
        """Reads file content from container.

        Args:
            path: File path in container.

        Returns:
            str: File content.
        """
        ...

    async def write_file(self, path: str, content: str) -> None:
        """Writes content to file in container.

        Args:
            path: File path in container.
            content: Content to write.
        """
        ...


class BaseSandboxClient(ABC):
    """Base sandbox client interface."""

    @abstractmethod
    async def create(
        self,
        config: Optional[SandboxSettings] = None,
        volume_bindings: Optional[Dict[str, str]] = None,
    ) -> None:
        """Creates sandbox."""

    @abstractmethod
    async def run_command(self, command: str, timeout: Optional[int] = None) -> str:
        """Executes command."""

    @abstractmethod
    async def copy_from(self, container_path: str, local_path: str) -> None:
        """Copies file from container."""

    @abstractmethod
    async def copy_to(self, local_path: str, container_path: str) -> None:
        """Copies file to container."""

    @abstractmethod
    async def read_file(self, path: str) -> str:
        """Reads file."""

    @abstractmethod
    async def write_file(self, path: str, content: str) -> None:
        """Writes file."""

    @abstractmethod
    async def cleanup(self) -> None:
        """Cleans up resources."""


class LocalSandboxClient(BaseSandboxClient):
    """Local sandbox client implementation."""

    def __init__(self):
        """Initializes local sandbox client."""
        self.sandbox: Optional[DockerSandbox] = None

    async def create(
        self,
        config: Optional[SandboxSettings] = None,
        volume_bindings: Optional[Dict[str, str]] = None,
        env_vars: Optional[Dict[str, str]] = None,
        start_commands: Optional[List[str]] = None
    ) -> None:
        """Creates a sandbox.

        Args:
            config: Sandbox configuration.
            volume_bindings: Volume mappings.

        Raises:
            RuntimeError: If sandbox creation fails.
        """
        self.sandbox = DockerSandbox(config, volume_bindings)
        await self.sandbox.create(env_vars=env_vars)
        if start_commands:
            for command in start_commands:
                await self.sandbox.run_command(command)

    async def create_terminal(self, terminal_id, env_vars = None, start_commands=None) -> int:
        return await self.sandbox.create_terminal(terminal_id, env_vars=env_vars, start_commands=start_commands)

    async def run_command(self, command: str, timeout: Optional[int] = None, terminal_id: Optional[int] = None) -> str:
        """Runs command in sandbox.

        Args:
            command: Command to execute.
            timeout: Execution timeout in seconds.

        Returns:
            Command output.

        Raises:
            RuntimeError: If sandbox not initialized.
        """
        if not self.sandbox:
            raise RuntimeError("Sandbox not initialized")
        return await self.sandbox.run_command(command, timeout, terminal_id)

    async def copy_from(self, container_path: str, local_path: str) -> None:
        """Copies file from container to local.

        Args:
            container_path: File path in container.
            local_path: Local destination path.

        Raises:
            RuntimeError: If sandbox not initialized.
        """
        if not self.sandbox:
            raise RuntimeError("Sandbox not initialized")
        await self.sandbox.copy_from(container_path, local_path)

    async def copy_to(self, local_path: str, container_path: str) -> None:
        """Copies file from local to container.

        Args:
            local_path: Local source file path.
            container_path: Destination path in container.

        Raises:
            RuntimeError: If sandbox not initialized.
        """
        if not self.sandbox:
            raise RuntimeError("Sandbox not initialized")
        await self.sandbox.copy_to(local_path, container_path)

    async def read_file(self, path: str) -> str:
        """Reads file from container.

        Args:
            path: File path in container.

        Returns:
            File content.

        Raises:
            RuntimeError: If sandbox not initialized.
        """
        if not self.sandbox:
            raise RuntimeError("Sandbox not initialized")
        return await self.sandbox.read_file(path)

    async def write_file(self, path: str, content: str) -> None:
        """Writes file to container.

        Args:
            path: File path in container.
            content: File content.

        Raises:
            RuntimeError: If sandbox not initialized.
        """
        if not self.sandbox:
            raise RuntimeError("Sandbox not initialized")
        await self.sandbox.write_file(path, content)

    async def cleanup(self) -> None:
        """Cleans up resources."""
        if self.sandbox:
            await self.sandbox.cleanup()
            self.sandbox = None

    async def close_terminal(self, terminal_id):
        assert terminal_id != 0, "Cannot terminate main terminal"
        assert await self.sandbox.has_terminal(terminal_id), "Terminal {} not found".format(terminal_id)
        await self.sandbox.close_terminal(terminal_id)


def create_sandbox_client() -> LocalSandboxClient:
    """Creates a sandbox client.

    Returns:
        LocalSandboxClient: Sandbox client instance.
    """
    return LocalSandboxClient()


SANDBOX_CLIENT = create_sandbox_client()
async def main():
    config = SandboxSettings(image='my-python-image', network_enabled=True)
    await SANDBOX_CLIENT.create(config, {'/': '/app'})
    # await SANDBOX_CLIENT.copy_to('/data/meta-agent/edumanus-meta-agent/edumanus/tool/toolcosmos_v2/prompts/*.yml', '/app')
    while True:
        print(await SANDBOX_CLIENT.run_command(input()))

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())