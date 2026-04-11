from typing import Dict, List, Callable, Any, Optional
from pydantic import Field

class TransitionRule:
    def __init__(self, allowed_tools: List[str], next_state: str | Callable):
        """
        allowed_tools: List of tool specifications. 
                       Format: "tool_name" or "tool_name:action_name" (for routing tools).
                       Example: ["terminate", "skill_gardener_tool:inspect_trace_map"]
        next_state: The next state to transition to, or a callable dynamic evaluator
                    that returns the next state string given (tool_name, kwargs, result_str).
        """
        self.allowed_tools = allowed_tools
        self.next_state = next_state

class StateMachine:
    def __init__(self, initial_state: str, states: Dict[str, TransitionRule]):
        self.current_state = initial_state
        self.states = states
        self.history = [initial_state]

    def validate_action(self, tool_name: str, kwargs: dict) -> Optional[str]:
        """
        Checks if the chosen tool (and action) is allowed in the current state.
        Returns an error message string if denied, else None.
        """
        rule = self.states.get(self.current_state)
        if not rule:
            return f"State machine error: Unknown state '{self.current_state}'."
        
        action = kwargs.get("action")
        target_sig = f"{tool_name}:{action}" if action else tool_name
        
        # Check matching
        allowed = False
        for allowed_sig in rule.allowed_tools:
            if target_sig == allowed_sig or (action and allowed_sig == f"{tool_name}:*") or allowed_sig == "*":
                allowed = True
                break
            # Also allow direct tool match if not specifying action
            if not action and target_sig == allowed_sig:
                allowed = True
                break
                
        if not allowed:
             return (f"[State Machine Guard] You are in state '{self.current_state}'. "
                     f"The action '{target_sig}' is NOT allowed. "
                     f"Allowed actions: {rule.allowed_tools}. Check your protocol.")
        return None

    def advance(self, tool_name: str, kwargs: dict, result: str):
        """
        Updates the current state based on the rule.
        """
        rule = self.states.get(self.current_state)
        if not rule:
            return
            
        action = kwargs.get("action")
        
        # Only block state advance on *actual tool execution errors* — i.e. results that
        # start with "Error:" (returned by execute_tool on exception/unknown-tool) or
        # contain the "⚠️" marker.  Do NOT block on results that merely *contain* the
        # substring "Error" / "Exception" / "Failed" as part of normal output (e.g.
        # pytest output "Errors if any: None" or "AssertionError" in a traceback).
        #
        # NOTE: execute_tool wraps ToolResult output as:
        #   "Observed output of cmd `X` executed:\n{result_str}"
        # When a tool returns ToolResult(error=...), result_str = "Error: ...", so the
        # full observation string starts with "Observed output..." not "Error:".
        # We must also check if the payload (after the wrapper prefix) starts with "Error:".
        _obs_payload = result.split("executed:\n", 1)[-1] if result and "executed:\n" in result else ""
        is_tool_error = (
            result is None
            or result.startswith("Error:")
            or result.startswith("Error: ")
            or _obs_payload.startswith("Error:")
            or "⚠️" in result
        )
        if not is_tool_error:
            if callable(rule.next_state):
                next_val = rule.next_state(tool_name, kwargs, result)
                if next_val and next_val != self.current_state:
                    self.current_state = next_val
                    self.history.append(self.current_state)
            elif rule.next_state != self.current_state:
                self.current_state = rule.next_state
                self.history.append(self.current_state)
                
    def get_current_prompt_instruction(self) -> str:
        rule = self.states.get(self.current_state)
        if rule:
             return f"\n[STATE MACHINE] Current State: {self.current_state}. Allowed Actions: {rule.allowed_tools}"
        return ""

class StatefulAgentMixin:
    """
    A mixin intended for ToolCallToolCosmosAgent (or ReActAgent) to enforce state machine flows.
    """
    state_machine: StateMachine = Field(default=None, exclude=True)
    
    def set_state_machine(self, sm: StateMachine):
        self.state_machine = sm

    # Override execute_tool to wrap with state machine
    async def execute_tool(self, command) -> str:
        # Assuming command is a ToolCall object
        if not self.state_machine:
            return await super().execute_tool(command)
            
        import json
        name = command.function.name
        try:
            args = json.loads(command.function.arguments or "{}")
        except:
            args = {}
            
        # 1. Validate Target Action
        err = self.state_machine.validate_action(name, args)
        if err:
            return err
            
        # 2. Execute 
        result = await super().execute_tool(command)
        
        # 3. Advance state on success
        self.state_machine.advance(name, args, result)
        
        # Inject state context into output to keep ReAct aware
        state_tip = self.state_machine.get_current_prompt_instruction()
        if state_tip:
             result = result + "\n" + state_tip
             
        return result
