import docker
import socket
import asyncio
from functools import partial
import time

class DockerExecutor:
    def __init__(self):
        self.client = docker.from_env()
        self.sessions = {}
        self.clean_interval = 3600
        self.clean_session_handle = asyncio.create_task(self.clean_sessions())
        self.clean_session_flag = True

    async def clean_sessions(self):
        while self.clean_session_flag:
            # print('cleaning...')
            clean_session = []
            for session_id, session in self.sessions.items():
                start_time = session['start_time']
                if time.time() - start_time >= self.clean_interval:
                    clean_session.append(session_id)
            for session_id in clean_session:
                await self.close_session(session_id)
            await asyncio.sleep(1)

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
            self.create_session(session_id=session_id)

        session = self.sessions[session_id]
        read_sock = session["read_sock"]
        write_sock = session["write_sock"]

        try:
            # Write the user command to the container
            write_sock.send((input_command + "\n").encode('utf-8'))

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


# Example Usage
async def main():
    try:
        docker_exec = DockerExecutor()

        # Create a session
        await docker_exec.create_session("session1", cmd="sh")

        print('docker started!')
        while True:
            cmd = input()
            output = await docker_exec.execute("session1", cmd)
            print('output:\n', output, '\n----------------')
            if cmd == 'exit':
                break
    finally:
        # Close the session
        # await docker_exec.close_session("session1")
        pass
    while docker_exec.sessions:
        await asyncio.sleep(1)
    docker_exec.clean_session_flag = False
    docker_exec.clean_session_handle.cancel()
    print('all sessions cleaned!')

if __name__ == '__main__':
    asyncio.run(main())