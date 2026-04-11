import asyncio
import json
from typing import Any, List, Optional, Union

from pydantic import Field

from app.agent.react import ReActAgent
from app.exceptions import TokenLimitExceeded
from app.logger import logger
from app.prompt.toolcall import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import TOOL_CHOICE_TYPE, AgentState, Message, ToolCall, ToolChoice
from app.meta_agent_tool import CreateChatCompletion, Terminate, ToolCollection


TOOL_CALL_REQUIRED = "Tool calls required but none provided"


class ToolCallAgent(ReActAgent):
    """Base agent class for handling tool/function calls with enhanced abstraction"""

    name: str = "toolcall"
    description: str = "an agent that can execute tool calls."

    system_prompt: str = SYSTEM_PROMPT
    next_step_prompt: str = NEXT_STEP_PROMPT

    logger_instance: Any = Field(default=logger, exclude=True)

    available_tools: ToolCollection = ToolCollection(
        CreateChatCompletion(), Terminate()
    )
    tool_choices: TOOL_CHOICE_TYPE = ToolChoice.AUTO  # type: ignore
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])

    # Context for tool usage tracking
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    db: Optional[Any] = None

    tool_calls: List[ToolCall] = Field(default_factory=list)
    _current_base64_image: Optional[str] = None

    toolcall_history: List[dict] = Field(default_factory=list)

    tool_flow_step_count: int = 0
    tool_flow: List[dict] = Field(default_factory=list)

    max_steps: int = 30
    max_observe: Optional[Union[int, bool]] = None

    def set_context(self, user_id: str, session_id: Optional[str] = None, db: Optional[Any] = None):
        """Set context for tool usage tracking."""
        self.user_id = user_id
        self.session_id = session_id
        self.db = db

        # Set context for all tools
        if hasattr(self.available_tools, 'set_context_for_all_tools'):
            self.available_tools.set_context_for_all_tools(user_id, session_id, db)

    async def think(self) -> bool:
        """Process current state and decide next actions using tools"""
        print("\nToolCallAgent think called\n")
        if self.next_step_prompt:
            user_msg = Message.user_message(self.next_step_prompt)
            self.messages += [user_msg]

        try:
            # Get response with tool options
            # logger.info(f"🤖 {self.name}'s tools {self.available_tools.to_params()}")
            # logger.info(f"🤖 {self.name} messages: {self.messages}")
            response = await self.llm.ask_tool(
                messages=self.messages,
                system_msgs=(
                    [Message.system_message(self.system_prompt)]
                    if self.system_prompt
                    else None
                ),
                tools=self.available_tools.to_params(),
                tool_choice=self.tool_choices,
                timeout=30,
            )
        except ValueError:
            raise
        except Exception as e:
            # Check if this is a RetryError containing TokenLimitExceeded
            if hasattr(e, "__cause__") and isinstance(e.__cause__, TokenLimitExceeded):
                token_limit_error = e.__cause__
                self.logger_instance.error(
                    f"🚨 Token limit error (from RetryError): {token_limit_error}"
                )
                self.memory.add_message(
                    Message.assistant_message(
                        f"Maximum token limit reached, cannot continue execution: {str(token_limit_error)}"
                    )
                )
                self.state = AgentState.FINISHED
                return False
            raise

        # self.logger_instance.info(f"🤖 {self.name} received response: {response}")

        self.tool_calls = tool_calls = (
            response.tool_calls if response and response.tool_calls else []
        )
        content = response.content if response and response.content else ""

        # Log response info
        self.logger_instance.info(f"✨ {self.name}'s thoughts: {content}")
        self.logger_instance.info(
            f"🛠️ {self.name} selected {len(tool_calls) if tool_calls else 0} tools to use"
        )
        if tool_calls:
            self.logger_instance.info(
                f"🧰 Tools being prepared: {[call.function.name for call in tool_calls]}"
            )
            self.logger_instance.info(f"🔧 Tool arguments: {tool_calls[0].function.arguments}")

        try:
            if response is None:
                raise RuntimeError("No response received from the LLM")

            # Handle different tool_choices modes
            if self.tool_choices == ToolChoice.NONE:
                if tool_calls:
                    self.logger_instance.warning(
                        f"🤔 Hmm, {self.name} tried to use tools when they weren't available!"
                    )
                if content:
                    self.memory.add_message(Message.assistant_message(content))
                    return True
                return False

            # Create and add assistant message
            assistant_msg = (
                Message.from_tool_calls(content=content, tool_calls=self.tool_calls)
                if self.tool_calls
                else Message.assistant_message(content)
            )
            self.memory.add_message(assistant_msg)

            if self.tool_choices == ToolChoice.REQUIRED and not self.tool_calls:
                return True  # Will be handled in act()

            # For 'auto' mode, continue with content if no commands but content exists
            if self.tool_choices == ToolChoice.AUTO and not self.tool_calls:
                return bool(content)

            return bool(self.tool_calls)
        except Exception as e:
            self.logger_instance.error(f"🚨 Oops! The {self.name}'s thinking process hit a snag: {e}")
            self.memory.add_message(
                Message.assistant_message(
                    f"Error encountered while processing: {str(e)}"
                )
            )
            return False

    async def act(self) -> str:
        """Execute tool calls and handle their results"""
        if not self.tool_calls:
            if self.tool_choices == ToolChoice.REQUIRED:
                raise ValueError(TOOL_CALL_REQUIRED)

            # Return last message content if no tool calls
            return self.messages[-1].content or "No content or commands to execute"

        results = []
        for command in self.tool_calls:
            # Reset base64_image for each tool call
            self._current_base64_image = None

            self.toolcall_history.append({
                "role": "tool",
                "tool_name": command.function.name,
                "tool_call_id": command.id,
                "content": f"正在调用工具 {command.function.name}..."
            })

            result = await self.execute_tool(command)

            displayable_tools = [
                "deep_search", "learning_environment_creator", "lesson_environment_creator",
                "experiment_environment_creator", "research_environment_creator"
            ]
            tool_name = command.function.name
            display = tool_name in displayable_tools
            displayed_tool_info = None

            if display:
                try:
                    tool = self.available_tools.get_tool(tool_name)
                    prompt = f"""
You are an expert in explaining complex steps in a simple way.
Your task is to create a user-friendly "name" and "description" in Chinese for a tool that just ran. This will be shown to a user to help them understand a workflow.

**Rules:**
1. The name should be a short, intuitive, and action-oriented title.
2. The description should be a concise, one-to-two-sentence explanation of what this step accomplished and its outcome, based on the result.
3. The output MUST be a JSON object with two keys: "name" and "description". Do not output any other text.


**Tool Information:**
- Name: {tool.name}
- Description: {tool.description}

**Tool Execution Result:**
{result}

**Your JSON output:**
"""
                    llm_response = await self.llm.ask(messages=[Message.user_message(prompt)])
                    # Clean up the response to ensure it's valid JSON
                    cleaned_response = llm_response.strip().replace("```json", "").replace("```", "")
                    displayed_tool_info = json.loads(cleaned_response)

                except Exception as e:
                    self.logger_instance.error(f"Failed to generate displayed_tool_info for {tool_name}: {e}")
                    # Fallback to default info
                    displayed_tool_info = {"name": tool.name, "description": tool.description}

            if hasattr(self, 'db') and self.db:
                try:
                    tool = self.available_tools.get_tool(tool_name)
                    if tool:
                        tool_info = {
                            "name": tool.name,
                            "description": tool.description,
                            # "parameters": tool.model_dump_json(include={'args_schema'})
                        }

                        agent_state_to_save = self.model_dump(
                            mode='json',  # Use 'json' mode to properly serialize datetime and other non-JSON types
                            exclude={
                                "user_id", "session_id", "db", "user_input_queue", "log_list_lock", "logger_instance",
                                "llm", "memory", "available_tools",
                                "tool_categories", "special_tool_names",
                                "system_prompt", "next_step_prompt", "name", "description", "dify_client"
                            }
                        )

                        # Ensure the session exists in the database before adding tool flow step
                        # if hasattr(self, 'save_session_data'):
                        #     self.save_session_data()

                        # self.db.add_tool_flow_step(
                        #     session_id=self.session_id,
                        #     step_index=self.tool_flow_step_count,
                        #     tool_info=tool_info,
                        #     tool_result=result,
                        #     agent_state=agent_state_to_save
                        # )
                        self.tool_flow.append({
                            "step_index": self.tool_flow_step_count,
                            "tool_info": tool_info,
                            "tool_result": result,
                            "display": display,
                            "displayed_tool_info": displayed_tool_info,
                            # "agent_state": agent_state_to_save
                        })
                        self.tool_flow_step_count += 1
                        self.logger_instance.info(f"Saved tool call '{tool_name}' to flow history for session {self.session_id}.")
                except Exception as e:
                    self.logger_instance.error(f"Failed to save tool flow history for session {self.session_id}: {e}", exc_info=True)

            if self.max_observe:
                result = result[: self.max_observe]

            self.logger_instance.info(
                f"🎯 Tool '{command.function.name}' completed its mission! Result: {result}"
            )

            # Add tool response to memory
            tool_msg = Message.tool_message(
                content=result,
                tool_call_id=command.id,
                name=command.function.name,
                base64_image=self._current_base64_image,
            )
            self.memory.add_message(tool_msg)

            self.toolcall_history.append({
                "role": "tool",
                "tool_name": command.function.name,
                "tool_call_id": command.id,
                "content": f"工具 {command.function.name} 完成任务！结果: {result}"
            })

            results.append(result)

        return "\n\n".join(results)

    async def execute_tool(self, command: ToolCall) -> str:
        """Execute a single tool call with robust error handling"""
        if not command or not command.function or not command.function.name:
            # Count as a tool execution error
            try:
                if hasattr(self, "consecutive_tool_error_count"):
                    self.consecutive_tool_error_count += 1
            except Exception:
                pass
            return "Error: Invalid command format"

        name = command.function.name
        if name not in self.available_tools.tool_map:
            # Count as a tool execution error
            try:
                if hasattr(self, "consecutive_tool_error_count"):
                    self.consecutive_tool_error_count += 1
            except Exception:
                pass
            return f"Error: Unknown tool '{name}'"

        try:
            # Parse arguments
            args = json.loads(command.function.arguments or "{}")

            # Execute the tool
            self.logger_instance.info(f"🔧 Activating tool: '{name}'...")
            self.logger_instance.info(f"   Tool arguments: {args}")

            result = await self.available_tools.execute(name=name, tool_input=args)

            # Handle special tools
            await self._handle_special_tool(name=name, result=result)

            # Check if result is a ToolResult with base64_image
            if hasattr(result, "base64_image") and result.base64_image:
                # Store the base64_image for later use in tool_message
                self._current_base64_image = result.base64_image

            # Format result for display (standard case)
            # observation = (
            #     f"Observed output of cmd `{name}` executed:\n{str(result)}"
            #     if result
            #     else f"Cmd `{name}` completed with no output"
            # )
            # Update consecutive error counter based on tool result
            try:
                is_error = bool(getattr(result, "error", None))
                if is_error:
                    if hasattr(self, "consecutive_tool_error_count"):
                        self.consecutive_tool_error_count += 1
                else:
                    if hasattr(self, "consecutive_tool_error_count"):
                        self.consecutive_tool_error_count = 0
            except Exception:
                pass

            observation = str(result) if result else f"Cmd `{name}` completed with no output"

            return observation
        except json.JSONDecodeError:
            error_msg = f"Error parsing arguments for {name}: Invalid JSON format"
            self.logger_instance.error(
                f"📝 Oops! The arguments for '{name}' don't make sense - invalid JSON, arguments:{command.function.arguments}"
            )
            try:
                if hasattr(self, "consecutive_tool_error_count"):
                    self.consecutive_tool_error_count += 1
            except Exception:
                pass
            return f"Error: {error_msg}"
        except Exception as e:
            error_msg = f"⚠️ Tool '{name}' encountered a problem: {str(e)}"
            self.logger_instance.exception(error_msg)
            try:
                if hasattr(self, "consecutive_tool_error_count"):
                    self.consecutive_tool_error_count += 1
            except Exception:
                pass
            return f"Error: {error_msg}"

    async def _handle_special_tool(self, name: str, result: Any, **kwargs):
        """Handle special tool execution and state changes"""
        if not self._is_special_tool(name):
            return

        if self._should_finish_execution(name=name, result=result, **kwargs):
            # Set agent state to finished
            self.logger_instance.info(f"🏁 Special tool '{name}' has completed the task!")
            self.state = AgentState.FINISHED

    @staticmethod
    def _should_finish_execution(**kwargs) -> bool:
        """Determine if tool execution should finish the agent"""
        return True

    def _is_special_tool(self, name: str) -> bool:
        """Check if tool name is in special tools list"""
        return name.lower() in [n.lower() for n in self.special_tool_names]

    async def cleanup(self):
        """Clean up resources used by the agent's tools."""
        self.logger_instance.info(f"🧹 Cleaning up resources for agent '{self.name}'...")
        for tool_name, tool_instance in self.available_tools.tool_map.items():
            if hasattr(tool_instance, "cleanup") and asyncio.iscoroutinefunction(
                tool_instance.cleanup
            ):
                try:
                    self.logger_instance.debug(f"🧼 Cleaning up tool: {tool_name}")
                    await tool_instance.cleanup()
                except Exception as e:
                    self.logger_instance.error(
                        f"🚨 Error cleaning up tool '{tool_name}': {e}", exc_info=True
                    )
        self.logger_instance.info(f"✨ Cleanup complete for agent '{self.name}'.")

    async def run(self, request: Optional[str] = None) -> str:
        """Run the agent with cleanup when done."""
        try:
            return await super().run(request)
        finally:
            await self.cleanup()
