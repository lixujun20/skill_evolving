from typing import List, Dict, Optional, Any
from pydantic import Field, model_validator
import os
import asyncio
import json

from app.agent.toolcall_toolcosmos import ToolCallToolCosmosAgent
from app.meta_agent_tool import ToolCollection
from app.llm import LLM
# from app.meta_agent.skills.gardener import SkillGardener # Removed
from app.meta_agent.skills.schemas import AgentTrace, RefinedSkillResult
from app.meta_agent_tool.terminate import Terminate
import logging
from app.agent.state_machine import StatefulAgentMixin, StateMachine, TransitionRule

class SkillGardenerAgent(StatefulAgentMixin, ToolCallToolCosmosAgent):
    """
    SkillGardenerAgent is a specialized ReAct agent responsible for
    extracting and refining skills from execution traces.
    
    Instead of a fixed workflow, it uses tools to navigate the trace 'Map'
    and strategically 'Extract' code from specific 'Territories'.
    """
    name: str = "skill_gardener"
    description: str = """
    A specialized agent that analyzes execution traces to extract reusable Python skills.
    It can:
    1. Inspect the high-level Trace Map.
    2. Zoom in on specific steps to see detailed outputs.
    3. Generate Python code for new skills based on observed patterns.
    """
    
    # Internal state for the current session
    current_trace: Optional[AgentTrace] = Field(default=None, exclude=True)
    target_skill_id: Optional[int] = Field(default=None, exclude=True)
    db_manager: Any = Field(default=None, exclude=True)
    logger_instance: Any = Field(default_factory=lambda: logging.getLogger("skill_gardener"))
    
    def set_system_prompt():
        return """
        You are the Skill Gardener, an expert AI architect implementing `skill_evolving_v1`.
        Your goal is to extract new reusable Python skills or evolve existing ones based on execution traces.
        
        You have access to a tool called 'skill_gardener_tool' which allows you to:
        - `inspect_trace_map()`: Get a high-level summary of what happened. START HERE.
        - `zoom_in_step(step_index)`: Drill down into complex outputs.
        - `check_upstream_updates()`: ALWAYS USE THIS for evolving. It checks if underlying APIs have upgraded.
        - `generate_refactor_plan(...)`: Calls an internal LLM to draft the Active and Passive refactoring rules.
        - `execute_refactor(...)`: Physically generates the new Python code according to the plan and commits it to DB.
        - `test_skill(...)`: Sends the new compiled skill to ReviewerAgent for integration and unit testing.
        
        Strategy:
        1. Check the Map.
        2. If working on an existing skill, call `check_upstream_updates` to discover "Passive" changes needed.
        3. Call `generate_refactor_plan` (provide a trigger_reason).
        4. Call `execute_refactor` using the returned Plan ID. This will give you a New Skill Version ID.
        5. Call `test_skill(skill_version_id=new_id)`.
        6. Review the test report. If Failed, re-draft plan or execute again. If PASS, you are done.
        7. Before Terminating, ALWAYS output a summary of your actions and the final `skill_version_id` you successfully evolved.
        8. When finished, call the `terminate` tool to end the interaction.
        """

    system_prompt: str = Field(default_factory=set_system_prompt)
    
    llm: Optional[LLM] = Field(default_factory=lambda: LLM(config_name='tool_maker')) # Reuse tool_maker config
    max_steps: int = 15  # Gardener needs: inspect→plan→execute→test→terminate = ~8 steps; 15 is ample
    # Disable per-step "use terminate" reminder — terminate instruction is in the system prompt above.
    next_step_prompt: str = ""
    
    def initialize_tools(self) -> 'SkillGardenerAgent':
        # Import here to avoid circular dependencies if any
        from app.meta_agent.skills.gardener_tool import SkillGardenerTool
        
        # Create the specialized tool instance binding this agent's state
        # Now passing self.llm directly to the tool
        gardener_tool = SkillGardenerTool(
            llm=self.llm,
            db_manager=self.db_manager,
            current_trace=self.current_trace,
            target_skill_id=self.target_skill_id
        )
        
        self.available_tools = ToolCollection(
            gardener_tool,
            Terminate() # Standard termination tool
        )
        
        # --- State Machine Configuration ---
        if not self.state_machine:
            gardener_sm = StateMachine(
                initial_state="INIT",
                states={
                    "INIT": TransitionRule(
                        allowed_tools=["skill_gardener_tool:inspect_trace_map", "skill_gardener_tool:zoom_in_step", "skill_gardener_tool:check_upstream_updates"],
                        next_state=lambda tool, kwargs, res: "PLANNING" if kwargs.get("action") == "inspect_trace_map" else "INIT"
                    ),
                    "PLANNING": TransitionRule(
                        allowed_tools=["skill_gardener_tool:generate_refactor_plan", "skill_gardener_tool:check_upstream_updates"],
                        next_state=lambda tool, kwargs, res: "CODING" if kwargs.get("action") == "generate_refactor_plan" else "PLANNING"
                    ),
                    "CODING": TransitionRule(
                        allowed_tools=["skill_gardener_tool:execute_refactor", "skill_gardener_tool:check_upstream_updates"],
                        next_state=lambda tool, kwargs, res: "TESTING" if kwargs.get("action") == "execute_refactor" else "CODING"
                    ),
                    "TESTING": TransitionRule(
                        allowed_tools=["skill_gardener_tool:test_skill", "skill_gardener_tool:execute_refactor"], 
                        next_state=lambda tool, kwargs, res: "DONE" if ("Status: PASSED" in res or "is_passed=True" in res) else "PLANNING"
                    ),
                    "DONE": TransitionRule(
                        allowed_tools=["terminate"],
                        next_state="DONE"
                    )
                }
            )
            self.set_state_machine(gardener_sm)

        # Inject state instructions into Prompt
        sm_tip = self.state_machine.get_current_prompt_instruction()
        if sm_tip:
             # Just ensures the agent is aware if we restart
             pass
             
        return self

    async def run_extraction(
        self,
        trace: AgentTrace,
        db_manager: Any,
        target_skill_id: int = None,
        proposed_skills: list = None,  # v2.1: List[ProposedSkill] from planner
    ) -> Optional[str]:
        """
        Main entry point to start the gardening session.
        Returns the extracted python code if successful, or None.
        """
        import time
        _t0 = time.monotonic()
        print(f"\n[gardener] target_skill_id={target_skill_id} starting extraction")
        self.current_trace = trace
        self.target_skill_id = target_skill_id
        self.db_manager = db_manager

        # Clear per-run conversational history to prevent context contamination across calls.
        if hasattr(self, "memory") and getattr(self.memory, "messages", None) is not None:
            self.memory.messages.clear()
        # Reset state machine and agent state so each extraction starts from INIT.
        self.state_machine = None
        from app.schema import AgentState
        self.state = AgentState.IDLE
        self.current_step = 0

        # Snapshot the group's existing skill IDs before the run so we can detect
        # which new version was committed by execute_refactor.
        pre_run_skill_ids: set = set()
        if target_skill_id is not None:
            try:
                from sqlmodel import Session, select
                from app.meta_agent.skills.database.models import Skill as SkillModel
                with Session(db_manager.engine) as _s:
                    _base = _s.get(SkillModel, target_skill_id)
                    if _base:
                        _existing = _s.exec(
                            select(SkillModel).where(SkillModel.group_id == _base.group_id)
                        ).all()
                        pre_run_skill_ids = {sk.id for sk in _existing}
            except Exception:
                pass

        # Re-initialize tools to ensure they have the latest trace state
        self.initialize_tools()
        
        # Construct initial message
        user_msg = f"""
        Here is a new execution trace to analyze.
        Query: {trace.query}
        Total Steps: {len(trace.steps)}
        Target Skill ID to evolve: {self.target_skill_id if self.target_skill_id else "None (Create New)"}
        
        Please apply the `skill_evolving_v1` rules to formulate a plan and execute it.
        """

        # v2: inject workflow_plan if available — guides Gardener toward generalized abstraction
        if trace.workflow_plan:
            user_msg += f"""
        Additionally, a Workflow Plan (Python skeleton) was designed BEFORE execution as a guide
        for the INTENDED generalized solution pattern:

        ```python
        {trace.workflow_plan}
        ```

        When deciding skill boundary, abstraction level, and parameter interface, treat this plan
        as evidence of the intended reusable design. The actual execution trace may deviate from
        the plan — that is expected and acceptable.
        """

        # v2.1: inject proposed_skills from planner — candidate skills extracted from historical experience
        if proposed_skills:
            ps_lines = []
            for ps in proposed_skills:
                ps_lines.append(
                    f"  ### {ps.name}\n"
                    f"  描述: {ps.description}\n"
                    f"  来源查询: \"{ps.source_query}\"\n"
                    f"  代码骨架:\n"
                    f"  ```python\n  {ps.code_fragment}\n  ```"
                )
            user_msg += (
                "\n\n        ## Planner 候选技能提案 (v2.1)\n"
                "        以下候选技能是规划阶段从历史相似查询中提取的可复用片段。\n"
                "        请在分析执行 trace 时重点关注：\n"
                "        1. 这些候选技能是否在实际执行中被验证（直接/间接使用了类似逻辑）？\n"
                "        2. 如果被验证，根据实际执行细化其实现并提交为新技能版本。\n"
                "        3. 如果未使用，但仍有独立复用价值，也可酌情提交。\n"
                "        4. 额外从 trace 中发现 planner 未考虑到的新可复用技能并提交。\n\n"
                + "\n".join(ps_lines)
            )
        
        # Run execution
        await self.run(user_msg)

        # Primary: look for a newly created skill version in DB (most reliable).
        # execute_refactor commits code to DB; we find whichever skill ID is new.
        if target_skill_id is not None:
            try:
                from sqlmodel import Session, select
                from app.meta_agent.skills.database.models import Skill as SkillModel
                with Session(db_manager.engine) as _s:
                    _base = _s.get(SkillModel, target_skill_id)
                    if _base:
                        _all = _s.exec(
                            select(SkillModel).where(SkillModel.group_id == _base.group_id)
                        ).all()
                        new_skills = [sk for sk in _all if sk.id not in pre_run_skill_ids]
                        if new_skills:
                            # Return the code of the newest version
                            newest = max(new_skills, key=lambda sk: (sk.major_version, sk.minor_version))
                            if newest.code:
                                return newest.code
            except Exception:
                pass

        # Fallback: scan conversation history for a ```python block in assistant or tool messages
        messages = self.memory.messages
        for msg in reversed(messages):
            if msg.role == "assistant" and "```python" in str(msg.content):
                import re
                code_matches = re.findall(r"```python\s*(.*?)\s*```", str(msg.content), re.DOTALL)
                if code_matches:
                    return "\n\n".join(code_matches)
            if msg.role in ["tool", "function"] and "```python" in str(msg.content):
                import re
                code_matches = re.findall(r"```python\s*(.*?)\s*```", str(msg.content), re.DOTALL)
                if code_matches:
                    return "\n\n".join(code_matches)

        return None
