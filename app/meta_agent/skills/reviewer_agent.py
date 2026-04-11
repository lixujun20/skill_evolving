import logging
from typing import List, Dict, Optional, Any
from pydantic import Field
import asyncio
import os
import json

from app.agent.toolcall_toolcosmos import ToolCallToolCosmosAgent
from app.meta_agent_tool.base import BaseTool, ToolResult
from app.meta_agent_tool import ToolCollection
from app.llm import LLM
from app.meta_agent.skills.schemas import AgentTrace
from app.meta_agent.skills.database.models import Skill, TestCase, TestReport
from app.meta_agent.skills.database.manager import SkillDatabaseManager
from app.meta_agent_tool.terminate import Terminate
from app.meta_agent.executors.code_executor import SandboxTool
from app.agent.state_machine import StatefulAgentMixin, StateMachine, TransitionRule

class SkillReviewerTool(BaseTool):
    """
    Tester 专用的执行与评估工具（支持 skill_evolving_v1 流程）。
    """
    name: str = "skill_reviewer_tool"
    description: str = """
    A tool for testing and writing pytest cases for extracted Python skills.
    
    Actions:
    - inspect_guidelines: Read the SKILL_REVIEW_GUIDELINES.
    - view_skill_code: Read the specific version of code you need to test.
    - list_test_cases: View existing test cases from previous versions (note if they are locked).
    - add_test_case: Add a new pytest case (or modify if it's not locked).
    - run_pytest: Execute all current test cases against the new skill code in the sandbox.
    - submit_report: [Final Step] Write the structural evaluation report to DB.
    """
    
    target_skill_id: int = Field(exclude=True)
    db_manager: SkillDatabaseManager = Field(exclude=True)
    sandbox: SandboxTool = Field(exclude=True)
    loaded_existing_cases: bool = Field(default=False, exclude=True)
    
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["inspect_guidelines", "view_skill_code", "list_test_cases", "add_test_case", "run_pytest", "submit_report"],
                "description": "The meta action to perform."
            },
            "case_name": {
                "type": "string",
                "description": "Required for 'add_test_case'. Name of the test."
            },
            "test_code": {
                "type": "string",
                "description": "Required for 'add_test_case'. Python pytest code snippet."
            },
            "functional_score": {
                "type": "integer",
                "description": "Required for 'submit_report'. 0-100."
            },
            "compatibility_status": {
                "type": "string",
                "description": "Required for 'submit_report'. e.g., 'Good', 'Broken'."
            },
            "failure_categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Required if is_passed is False. Array of enum values like 'syntax_error', 'import_error', 'minor_compatibility_broken', 'signature_mismatch', 'assertion_failed', 'timeout_infinite_loop', etc."
            },
            "report_text": {
                "type": "string",
                "description": "Required for 'submit_report'. Details of passed/failed tests."
            },
            "is_passed": {
                "type": "boolean",
                "description": "Required for 'submit_report'."
            }
        },
        "required": ["action"]
    }

    async def execute(self, action: str, **kwargs) -> ToolResult:
        print(f"[reviewer_tool] execute action={action} skill_id={self.target_skill_id} engine={self.db_manager.engine.url}")
        skill = self.db_manager.get_skill(self.target_skill_id)
        if not skill: return ToolResult(error="Skill not found in DB.")

        if action == "inspect_guidelines":
            return ToolResult(output="Guideline: 1. Mock dependencies. 2. No hardcoding. 3. Minor updates cannot delete old tests. 4. Major updates can recreate tests.")

        elif action == "view_skill_code":
            return ToolResult(output=f"=== Skill v{skill.major_version}.{skill.minor_version} ===\n```python\n{skill.code}\n```\nInterface: {json.dumps(skill.interface_schema)}")

        elif action == "list_test_cases":
            cases = self.db_manager.get_test_cases(self.target_skill_id)
            self.loaded_existing_cases = True
            if not cases: return ToolResult(output="No test cases yet.")
            output = []
            for c in cases:
                locked = "[LOCKED (Legacy)]" if c.is_legacy_locked else "[EDITABLE]"
                output.append(f"TestCase: {c.case_name} {locked}\n```python\n{c.executable_code}\n```")
            return ToolResult(output="\n---\n".join(output))

        elif action == "add_test_case":
            case_name = kwargs.get("case_name")
            test_code = kwargs.get("test_code")
            if not case_name or not test_code: return ToolResult(error="case_name and test_code required.")

            if not self.loaded_existing_cases:
                return ToolResult(
                    error=(
                        "Please call list_test_cases first, then add only missing/necessary tests "
                        "to follow incremental review policy."
                    )
                )

            existing = self.db_manager.get_test_cases(self.target_skill_id)
            if any(c.case_name == case_name for c in existing):
                return ToolResult(output=f"TestCase '{case_name}' already exists. Skipped (incremental mode).")
            
            tc = TestCase(skill_version_id=self.target_skill_id, case_name=case_name, executable_code=test_code)
            self.db_manager.save_test_case(tc)
            return ToolResult(output=f"TestCase '{case_name}' added.")

        elif action == "run_pytest":
            # Add skill to tmp
            await self.sandbox.write_file("/tmp/target_skill.py", skill.code)
            cases = self.db_manager.get_test_cases(self.target_skill_id)
            
            locked_names = [c.case_name for c in cases if c.is_legacy_locked]
            
            full_test_code = "import sys\nsys.path.append('/tmp')\nfrom target_skill import *\n\n"
            for c in cases:
                full_test_code += f"\n# {c.case_name}\n{c.executable_code}\n"
                
            await self.sandbox.write_file("/tmp/test_target_skill.py", full_test_code)
            
            res = await self.sandbox.execute(command="pytest /tmp/test_target_skill.py -v --tb=short")
            locked_note = (
                f"\n=== LOCKED TestCases (backward-compat guard): {locked_names} ===\n"
                f"If ALL functions inside these LOCKED cases PASS and only your own added tests fail "
                f"(e.g., due to sandbox IO/network issues), still report is_passed=True.\n"
            ) if locked_names else ""
            return ToolResult(output=f"{locked_note}=== Pytest Results ===\n{res.output}\nErrors if any:\n{res.error}")

        elif action == "submit_report":
            # Coerce is_passed: LLM sometimes passes string "True"/"False" instead of boolean.
            raw_is_passed = kwargs.get("is_passed", False)
            if isinstance(raw_is_passed, str):
                raw_is_passed = raw_is_passed.strip().lower() in ("true", "1", "yes")
            print(f"[submit_report] called skill_id={self.target_skill_id} is_passed={raw_is_passed} categories={kwargs.get('failure_categories')}")
            report = TestReport(
                skill_version_id=self.target_skill_id,
                is_passed=raw_is_passed,
                functional_score=kwargs.get("functional_score", 0),
                compatibility_status=kwargs.get("compatibility_status", "Unknown"),
                failure_categories=kwargs.get("failure_categories", []),
                report_text=kwargs.get("report_text", "")
            )
            try:
                saved = self.db_manager.save_test_report(self.target_skill_id, report)
                print(f"[submit_report] saved OK, report.id={saved.id if saved else '?'}")
            except Exception as e:
                print(f"[submit_report] EXCEPTION: {e}")
                return ToolResult(error=f"Error: save_test_report failed: {e}")
            return ToolResult(output="Report submitted successfully to DB. You can now call Terminate.")
            
        return ToolResult(error="Unknown action.")

class SkillReviewerAgent(StatefulAgentMixin, ToolCallToolCosmosAgent):
    name: str = "skill_reviewer_v1"
    description: str = "Tester that validates refactored code against DB test cases."
    
    db_manager: Optional[SkillDatabaseManager] = Field(default=None, exclude=True)
    sandbox: Optional[SandboxTool] = Field(default=None, exclude=True)
    logger_instance: Any = Field(default_factory=lambda: logging.getLogger("skill_reviewer"))
    
    def set_system_prompt():
        return """
        You are the Tester/Reviewer Agent operating under skill_evolving_v1.
        Your job is to test a specific version of a Skill from the database.
        
        Actions available via 'skill_reviewer_tool':
        1. view_skill_code: Read what the Extractor just committed.
        2. list_test_cases: View old test cases. If it's a Minor update, old tests are [LOCKED] and you must ensure they still pass (Backward Compatibility).
        3. add_test_case: Write your own Pytest code to test new features. Ensure you thoroughly test edge cases and mock correctly.
        4. run_pytest: Execute all tests in sandbox.
        5. submit_report: Write your final evaluation to the Database. If tests fail, you MUST classify failures using the predefined failure categories.
        
        Protocol:
          1. View code and list cases.
          2. You MUST call list_test_cases before add_test_case. Reuse existing cases first, then add only incremental missing tests.
          3. Add new test cases with mocks for networking/IO. Do not leave hardcoded data or external calls un-mocked.
          4. Run pytest ONCE. Do NOT keep re-running; one clean run is sufficient.
           CRITICAL: If any [LOCKED (Legacy)] test FAILS, immediately call submit_report with
           is_passed=False and the appropriate failure_categories. Do NOT attempt to add more
           tests or workarounds — locked test failures are definitive compatibility breaks.
           IMPORTANT: If all [LOCKED (Legacy)] tests PASS but some of your own added tests fail
           (e.g., due to IO/network errors in the sandbox), set is_passed=True — the skill is
           backward-compatible. Your added tests may have environment issues. Only report
           is_passed=False if LOCKED tests fail or there is a clear logic error in the skill code.
          5. Call submit_report with a detailed string, functional logic score, compatibility status, and boolean is_passed. 
           If is_passed is False, meticulously select failure_categories from: ['syntax_error', 'import_error', 'sandbox_error', 'assertion_failed', 'runtime_exception', 'minor_compatibility_broken', 'signature_mismatch', 'upstream_dependency_broken', 'timeout_infinite_loop', 'resource_exhaustion', 'hardcoded_restriction', 'extensibility_issue'].
          6. When finished, call the `terminate` tool to end the interaction.
        """

    system_prompt: str = Field(default_factory=set_system_prompt)
    llm: Optional[LLM] = Field(default_factory=lambda: LLM(config_name='tool_maker'))
    max_steps: int = 20  # Reviewer typically needs 6-15 steps (view→list→add×N→test→report→terminate)
    # Disable per-step "use terminate" reminder — it's already in the system prompt above,
    # and repeating it as a user message every step wastes tokens & grows context.
    next_step_prompt: str = ""

    async def run_review_v1(self, target_skill_id: int, trace: AgentTrace = None) -> str:
        import time
        _t0 = time.monotonic()
        print(f"\n[reviewer] skill_id={target_skill_id} starting review")
        # Reset per-run conversational history so retries do not pollute later runs.
        if hasattr(self, "memory") and getattr(self.memory, "messages", None) is not None:
            self.memory.messages.clear()
        # Reset state machine and agent state so each review starts from INIT.
        self.state_machine = None
        from app.schema import AgentState
        self.state = AgentState.IDLE
        self.current_step = 0

        if not self.sandbox: self.sandbox = SandboxTool()
        await self.sandbox.initialize_session()
        # Pre-install test dependencies once per session (not on every run_pytest call)
        _pkg = await self.sandbox.execute(command="pip install -q pytest pytest-mock responses", timeout=120)
        print(f"[reviewer]   pip install done ({time.monotonic()-_t0:.1f}s)")

        tool = SkillReviewerTool(
            target_skill_id=target_skill_id,
            db_manager=self.db_manager,
            sandbox=self.sandbox
        )
        self.available_tools = ToolCollection(tool, Terminate())

        reviewer_sm = StateMachine(
            initial_state="INIT",
            states={
                "INIT": TransitionRule(
                    allowed_tools=["skill_reviewer_tool:inspect_guidelines", "skill_reviewer_tool:view_skill_code", "skill_reviewer_tool:list_test_cases"],
                    next_state="CODING_TESTS"
                ),
                "CODING_TESTS": TransitionRule(
                    allowed_tools=["skill_reviewer_tool:add_test_case", "skill_reviewer_tool:list_test_cases", "skill_reviewer_tool:run_pytest"],
                    next_state=lambda tool, kwargs, res: "REPORTING" if tool == "skill_reviewer_tool" and kwargs.get('action') == "run_pytest" else "CODING_TESTS"
                ),
                "REPORTING": TransitionRule(
                    allowed_tools=["skill_reviewer_tool:submit_report", "skill_reviewer_tool:add_test_case", "skill_reviewer_tool:run_pytest"],
                    next_state=lambda tool, kwargs, res: "DONE" if tool == "skill_reviewer_tool" and kwargs.get('action') == "submit_report" else "REPORTING"
                ),
                "DONE": TransitionRule(
                    allowed_tools=["terminate"],
                    next_state="DONE"
                )
            }
        )
        self.set_state_machine(reviewer_sm)
        
        user_msg = f"Please review Skill ID: {target_skill_id} from DB."
        await self.run(user_msg)
        await self.sandbox.cleanup()
        elapsed = time.monotonic() - _t0
        sm_state = self.state_machine.current_state if self.state_machine else "?"
        # current_step is reset to 0 by base agent when max_steps is hit; use sm_state to infer outcome
        hit_max = self.current_step == 0 and elapsed > 5
        steps_info = f"max_steps({self.max_steps})" if hit_max else str(self.current_step)
        print(f"[reviewer] skill_id={target_skill_id} done in {elapsed:.1f}s, steps={steps_info}, sm={sm_state}")
        return "Review finished. See DB TestReports."
