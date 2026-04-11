from typing import List, Dict, Optional, Any
import re
from pydantic import Field
from app.meta_agent_tool.base import BaseTool, ToolResult
from app.meta_agent.skills.schemas import AgentTrace
from app.meta_agent.skills.prompts import SkillExtractorPrompts
from app.llm import LLM
from app.meta_agent.skills.database.manager import SkillDatabaseManager
from app.meta_agent.skills.database.models import Skill, RefactorPlan, SkillDependency
from sqlmodel import Session, select

class SkillGardenerTool(BaseTool):
    """
    Skill Evolving V1 Gardener Tool.
    Handles contextual trace inspection, DB-assisted upstream change detection,
    plan generation, code refactoring execution, and test handover.
    """
    name: str = "skill_gardener_tool"
    description: str = """
    A tool for exploring a trace, checking DB for upstream updates, drafting evolving plans, and executing skill refactoring.
    
    Actions:
    - inspect_trace_map: View high-level execution trace summaries.
    - zoom_in_step: View full step details.
    - check_upstream_updates: Inspect database for newly updated upstream APIs that our target skill depends on.
    - generate_refactor_plan: Uses LLM to draft Active/Passive Evolving rules based on the trace and updates. Stores plan in DB.
    - execute_refactor: Feeds the Plan to LLM Co-Pilot to write the exact Python Code + Update log. Upserts DB as new version.
    - test_skill: Send the new skill_id to Reviewer Agent to persist tests and determine function/compatibility pass.
    """
    
    llm: LLM = Field(exclude=True)
    db_manager: Any = Field(exclude=True)  # Instantiated in Agent initialization
    current_trace: Optional[AgentTrace] = Field(default=None, exclude=True)
    target_skill_id: Optional[int] = Field(default=None, exclude=True) # The specific old version we want to refactor

    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["inspect_trace_map", "zoom_in_step", "check_upstream_updates", "generate_refactor_plan", "execute_refactor", "test_skill"],
                "description": "The target action."
            },
            "step_index": {"type": "integer"},
            "start_index": {"type": "integer"},
            "end_index": {"type": "integer"},
            "trigger_reason": {
                "type": "string",
                "description": "Why are we generating a refactor plan? (e.g. 'traceback_error', 'opportunity_upgrade')"
            },
            "plan_id": {"type": "integer", "description": "The RefactorPlan DB ID to execute."},
            "skill_version_id": {"type": "integer", "description": "Used for 'test_skill'. New compiled version ID."},
        },
        "required": ["action"]
    }

    async def execute(self, action: str, **kwargs) -> ToolResult:
        if action == "inspect_trace_map":
            return ToolResult(output=self._format_trace_lines(self.current_trace, summary=True))
            
        elif action == "zoom_in_step":
            if kwargs.get('step_index') is None: return ToolResult(error="missing step_index")
            step_idx = kwargs['step_index']
            if step_idx < 0 or step_idx >= len(self.current_trace.steps):
                return ToolResult(error="invalid step index")
            s = self.current_trace.steps[step_idx]
            return ToolResult(output=f"=== Step Details ===\nThought:{s.thought}\nOut: {str(s.tool_output)[:5000]}")
            
        elif action == "check_upstream_updates":
            if not self.target_skill_id: 
                return ToolResult(output="No target_skill_id specified. It might be a purely new creation task.")
            
            # 判据：通过数据库查询 target_skill_id 所依赖的所有 callee_id，找它们同 group 下有没有更新的版本
            updates = self._check_upstream_updates_in_db(self.target_skill_id)
            if not updates:
                return ToolResult(output="No upstream dependencies have been updated. Passive Refactoring not required.")
                
            return ToolResult(output=f"=== Upstream Updates Detected ===\n{updates}")

        elif action == "generate_refactor_plan":
            trigger_reason = kwargs.get('trigger_reason', 'opportunity_upgrade')
            return await self._draft_plan_via_llm(trigger_reason)

        elif action == "execute_refactor":
            plan_id = kwargs.get('plan_id')
            if not plan_id:
                return ToolResult(error="Error: plan_id is required for execute_refactor. Call generate_refactor_plan first and use the returned Plan ID.")
            start_index = kwargs.get('start_index')
            end_index = kwargs.get('end_index')
            return await self._execute_code_generation(plan_id, start_index, end_index)

        elif action == "test_skill":
            skill_id = kwargs.get('skill_version_id')
            if not skill_id: return ToolResult(error="missing skill_version_id")
            
            from app.meta_agent.skills.reviewer_agent import SkillReviewerAgent
            
            # Start testing process
            reviewer = SkillReviewerAgent(db_manager=self.db_manager)
            reviewer_result = await reviewer.run_review_v1(target_skill_id=skill_id)
            
            # Get the report to verify
            reports = self.db_manager.get_test_reports(skill_id)
            report_str = "No reports generated."
            if reports:
                latest_report = reports[-1]
                report_str = f"Status: {'PASSED' if latest_report.is_passed else 'FAILED'}\nScore: {latest_report.functional_score}\nComp: {latest_report.compatibility_status}\nDetails: {latest_report.report_text}"
                
            return ToolResult(output=f"=== Reviewer Output ===\n{reviewer_result}\n=== Final Report ===\n{report_str}")
        
        return ToolResult(error=f"Unrecognized action {action}")

    def _check_upstream_updates_in_db(self, skill_id: int) -> str:
        """
        [被动重构判据] 取出当前版本最初绑定的上游版本，并在 DB 看看该上游 Group 是否有大/小更新。
        """
        output_lines = []
        with Session(self.db_manager.engine) as session:
            # 获取下游依赖的初始 callee
            deps = session.exec(select(SkillDependency).where(SkillDependency.caller_id == skill_id)).all()
            for dep in deps:
                used_callee = session.get(Skill, dep.callee_id)
                if not used_callee: continue
                
                # 去当前 Group 里查最大的主次版本
                latest_callee = self.db_manager.get_latest_skill_in_group(used_callee.group_id)
                
                if latest_callee and latest_callee.id != used_callee.id:
                    # 发现有更新！
                    is_major = latest_callee.major_version > used_callee.major_version
                    output_lines.append(
                        f"Group Name: {used_callee.group.name} | Used: v{used_callee.major_version}.{used_callee.minor_version} "
                        f"-> Latest Available: v{latest_callee.major_version}.{latest_callee.minor_version}\n"
                        f"Update Type: {'MAJOR (Interface Changed!)' if is_major else 'MINOR'}\n"
                        f"Update Log of Latest: {latest_callee.update_log}\n"
                    )
        return "\n".join(output_lines)
        
    async def _draft_plan_via_llm(self, trigger_reason: str) -> ToolResult:
        if not self.target_skill_id:
            # Creation behavior is slightly different, but assuming Evolving V1 here
            return ToolResult(error="Evolving requires a valid target_skill_id.")
            
        with Session(self.db_manager.engine) as session:
            target_skill = session.get(Skill, self.target_skill_id)
            if not target_skill: return ToolResult(error="Cannot find target skill")
            target_group_name = target_skill.group.name if target_skill.group else "Unknown Group"
            target_skill_context = f"Skill: {target_group_name}\nCode:\n```python\n{target_skill.code}\n```"
            
        trace_str = self._format_trace_lines(self.current_trace, summary=True)
        upstream_str = self._check_upstream_updates_in_db(self.target_skill_id) or "None"
        
        prompt = SkillExtractorPrompts.USER_Plan_TEMPLATE.format(
            query=self.current_trace.query,
            trace_str=trace_str,
            target_skill_context=target_skill_context,
            upstream_updates_str=upstream_str
        )
        
        messages = [
            {"role": "system", "content": "You are formulating an Evolving Plan. Return JSON only. schema: { 'active_plan': '', 'passive_plan': '', 'update_type': 'major|minor', 'hard_pinned_group_ids': [] }"},
            {"role": "user", "content": prompt}
        ]
        
        resp = await self.llm.ask(messages)
        
        import json
        try:
            match = re.search(r"\{.*\}", resp, re.DOTALL)
            plan_json = json.loads(match.group(0)) if match else json.loads(resp)
            
            with Session(self.db_manager.engine) as session:
                new_plan = RefactorPlan(
                    target_skill_id=self.target_skill_id,
                    trigger_reason=trigger_reason,
                    active_refactor_plan=plan_json.get('active_plan', ''),
                    passive_refactor_plan=plan_json.get('passive_plan', ''),
                    update_type=plan_json.get('update_type', 'minor'),
                    hard_pinned_group_ids=plan_json.get('hard_pinned_group_ids', []),
                    status="OPEN"
                )
                session.add(new_plan)
                session.commit()
                session.refresh(new_plan)
                return ToolResult(output=f"SUCCESS: Generated Refactor Plan ID {new_plan.id}.\nDecision Type: {plan_json.get('update_type')}\nPlan:\n{resp}")
        except Exception as e:
            return ToolResult(error=f"LLM did not return proper JSON plan: {resp} \nErr:{e}")

    async def _execute_code_generation(self, plan_id: int, start_idx: int, end_idx: int) -> ToolResult:
        with Session(self.db_manager.engine) as session:
            plan = session.get(RefactorPlan, plan_id)
            if not plan: return ToolResult(error=f"Invalid Plan ID: {plan_id}. Please call generate_refactor_plan first.")
            
            target_skill = session.get(Skill, plan.target_skill_id)
            if not target_skill:
                return ToolResult(error=f"Target skill (id={plan.target_skill_id}) not found in DB. The plan may reference a deleted skill.")
            group_id = target_skill.group_id
            original_code = target_skill.code
        
        # 截取相关 Trace
        segment_trace = AgentTrace(query=self.current_trace.query, steps=self.current_trace.steps[start_idx:end_idx], final_answer="", involved_skills=[])
        trace_segment = self._format_trace_lines(segment_trace, summary=False)
        upstream_apis = self._check_upstream_updates_in_db(self.target_skill_id)
        
        prompt = SkillExtractorPrompts.USER_CODE_TEMPLATE_V1.format(
            trace_segment=trace_segment,
            upstream_apis=upstream_apis,
            original_code=original_code,
            active_plan=plan.active_refactor_plan,
            passive_plan=plan.passive_refactor_plan,
            update_type=plan.update_type,
            hard_pins=", ".join(map(str, plan.hard_pinned_group_ids or [])) or "None"
        )
        
        resp = await self.llm.ask([{"role": "user", "content": prompt}])
        
        # Extract <update_log> and ```python ... ```
        ul_match = re.search(r"<update_log>\s*(.*?)\s*</update_log>", resp, re.DOTALL)
        update_log = ul_match.group(1) if ul_match else "No update log generated."
        
        code_match = re.search(r"```python\s*(.*?)\s*```", resp, re.DOTALL)
        new_code = code_match.group(1).strip() if code_match else resp.strip()
        
        update_type = plan.update_type if plan.update_type in ["major", "minor"] else "minor"

        # Convert hard-pinned group IDs to concrete callee version IDs (latest versions).
        callee_skill_ids = []
        for gid in (plan.hard_pinned_group_ids or []):
            latest = self.db_manager.get_latest_skill_in_group(gid)
            if latest:
                callee_skill_ids.append(latest.id)
        
        new_skill = self.db_manager.add_skill_version(
            group_id=group_id,
            code=new_code,
            update_type=update_type,
            docstring="",  
            callee_skill_ids=callee_skill_ids,
            hard_pinned_group_ids=plan.hard_pinned_group_ids or []
        )
        
        with Session(self.db_manager.engine) as session:
            db_skill = session.get(Skill, new_skill.id)
            db_skill.update_log = update_log
            session.add(db_skill)
            session.commit()
            
        return ToolResult(output=f"SUCCESS: Generated Code for Skill ID: {new_skill.id}\nUpdate Log: {update_log}\nTo verify, run `test_skill` action passing skill_version_id={new_skill.id}")

    def _format_trace_lines(self, trace: AgentTrace, summary: bool = False) -> str:
        lines = []
        is_codeact = any(step.code_block for step in trace.steps) or getattr(trace, 'trace_format', '') == "codeact"
        for i, step in enumerate(trace.steps):
            if summary:
                thought_preview = (step.thought or "").split('\n')[0][:100]
                lines.append(f"Step {i} [{step.status}]: {thought_preview}")
            else:
                lines.append(f"Step {i} [{step.status}]:")
                if step.thought: lines.append(f"  Thought: {step.thought}")
                output_str = str(step.tool_output)
                if len(output_str) > 4000: output_str = output_str[:4000] + "...(truncated)"
                lines.append(f"  Output: {output_str}")
        return "\n".join(lines)
