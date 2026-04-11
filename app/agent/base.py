from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from app.llm import LLM
from app.logger import logger
from app.sandbox.client import SANDBOX_CLIENT
from app.schema import ROLE_TYPE, AgentState, Memory, Message
import asyncio


class BaseAgent(BaseModel, ABC):
    """Abstract base class for managing agent state and execution.

    Provides foundational functionality for state transitions, memory management,
    and a step-based execution loop. Subclasses must implement the `step` method.
    """

    # Core attributes
    name: str = Field(..., description="Unique name of the agent")
    description: Optional[str] = Field(None, description="Optional agent description")

    # Prompts
    system_prompt: Optional[str] = Field(
        None, description="System-level instruction prompt"
    )
    next_step_prompt: Optional[str] = Field(
        None, description="Prompt for determining next action"
    )

    # Dependencies
    llm: LLM = Field(default_factory=LLM, description="Language model instance")
    memory: Memory = Field(default_factory=Memory, description="Agent's memory store")
    state: AgentState = Field(
        default=AgentState.IDLE, description="Current agent state"
    )

    # Execution control
    max_steps: int = Field(default=10, description="Maximum steps before termination")
    current_step: int = Field(default=0, description="Current step in execution")

    duplicate_threshold: int = 2
    # Track consecutive tool execution errors to determine stuck state
    consecutive_tool_error_count: int = Field(
        default=0, description="Number of consecutive tool execution errors"
    )
    tool_error_stuck_threshold: int = Field(
        default=10,
        description="Threshold of consecutive tool errors to consider the agent stuck",
    )

    class Config:
        arbitrary_types_allowed = True
        extra = "allow"  # Allow extra fields for flexibility in subclasses

    @model_validator(mode="after")
    def initialize_agent(self) -> "BaseAgent":
        """Initialize agent with default settings if not provided."""
        if self.llm is None or not isinstance(self.llm, LLM):
            self.llm = LLM(config_name=self.name.lower())
        if not isinstance(self.memory, Memory):
            self.memory = Memory()
        return self

    @asynccontextmanager
    async def state_context(self, new_state: AgentState):
        """Context manager for safe agent state transitions.

        Args:
            new_state: The state to transition to during the context.

        Yields:
            None: Allows execution within the new state.

        Raises:
            ValueError: If the new_state is invalid.
        """
        if not isinstance(new_state, AgentState):
            raise ValueError(f"Invalid state: {new_state}")

        previous_state = self.state
        self.state = new_state
        try:
            yield
        except Exception as e:
            self.state = AgentState.ERROR  # Transition to ERROR on failure
            raise e
        finally:
            # Only revert to previous state if current state hasn't been explicitly set to FINISHED
            if self.state != AgentState.FINISHED:
                self.state = previous_state  # Revert to previous state

    def update_memory(
        self,
        role: ROLE_TYPE,  # type: ignore
        content: str,
        base64_image: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Add a message to the agent's memory.

        Args:
            role: The role of the message sender (user, system, assistant, tool).
            content: The message content.
            base64_image: Optional base64 encoded image.
            **kwargs: Additional arguments (e.g., tool_call_id for tool messages).

        Raises:
            ValueError: If the role is unsupported.
        """
        message_map = {
            "user": Message.user_message,
            "system": Message.system_message,
            "assistant": Message.assistant_message,
            "tool": lambda content, **kw: Message.tool_message(content, **kw),
        }

        if role not in message_map:
            raise ValueError(f"Unsupported message role: {role}")

        # Create message with appropriate parameters based on role
        kwargs = {"base64_image": base64_image, **(kwargs if role == "tool" else {})}
        self.memory.add_message(message_map[role](content, **kwargs))

    async def run(self, request: Optional[str] = None) -> str:
        """Execute the agent's main loop asynchronously.

        Args:
            request: Optional initial user request to process.

        Returns:
            A string summarizing the execution results.

        Raises:
            RuntimeError: If the agent is not in IDLE state at start.
        """
        # if self.state != AgentState.IDLE:
        #     raise RuntimeError(f"Cannot run agent from state: {self.state}")
        import re

        if request:
            self.update_memory("user", request)
        # remove[...]from request
            request = re.sub(r'\[.*?\]', '', request)

        if hasattr(self, 'conversation_history'):
            if hasattr(self, 'file_upload') and self.file_upload:
                if not request:
                    request = ""
                self.conversation_history.append({"role": "user", "content": request.strip(), "file":self.file_upload})
                self.logger_instance.info(f"file_upload: {self.file_upload}")
                self.file_upload = []
            else:
                if not request:
                    request = ""
                self.conversation_history.append({"role": "user", "content": request.strip(), "file":[]})

        if hasattr(self, 'save_session_data'):
            self.save_session_data()

        results: List[str] = []
        async with self.state_context(AgentState.RUNNING):
            while (
                self.current_step < self.max_steps and self.state != AgentState.FINISHED
            ):
                await asyncio.sleep(0.1)
                self.current_step += 1
                logger.info(f"\n\n{'-'*50}\n\nExecuting step {self.current_step}/{self.max_steps}")
                step_result = await self.step()

                # Check for stuck state
                if self.is_stuck():
                    self.handle_stuck_state()

                results.append(f"Step {self.current_step}: {step_result}")


            if self.current_step >= self.max_steps:
                self.current_step = 0
                self.state = AgentState.IDLE
                results.append(f"Terminated: Reached max steps ({self.max_steps})")
        await SANDBOX_CLIENT.cleanup()
        # print("\n\nBaseAgent run finished\n\n")
        # print(results)
        # print("\n\n")
        return "\n".join(results) if results else "No steps executed"

    @abstractmethod
    async def step(self) -> str:
        """Execute a single step in the agent's workflow.

        Must be implemented by subclasses to define specific behavior.
        """

    def handle_stuck_state(self):
        # """Handle stuck state by adding a prompt to change strategy"""
        # stuck_prompt = "\
        # Observed duplicate responses. Consider new strategies and avoid repeating ineffective paths already attempted."
        # self.next_step_prompt = f"{stuck_prompt}\n{self.next_step_prompt}"
        logger.warning(f"Agent detected stuck state.")
        self.state = AgentState.FINISHED
        # if hasattr(self, 'conversation_history'):
        #     self.conversation_history.append({"role": "assistant", "content": "系统检测后台出现卡顿，请稍后重启会话再试。"})
        # self.state = AgentState.FINISHED
        # raise Exception("Agent detected stuck state.")


    def is_stuck(self) -> bool:
        """Determine stuck state based on consecutive tool execution errors."""
        return self.consecutive_tool_error_count >= self.tool_error_stuck_threshold

    @property
    def messages(self) -> List[Message]:
        """Retrieve a list of messages from the agent's memory."""
        return self.memory.messages

    @messages.setter
    def messages(self, value: List[Message]):
        """Set the list of messages in the agent's memory."""
        self.memory.messages = value
