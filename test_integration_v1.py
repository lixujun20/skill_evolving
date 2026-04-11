import asyncio
from app.meta_agent.skills.database.models import SkillGroup, Skill, TestCase
from app.meta_agent.skills.database.manager import SkillDatabaseManager
from app.meta_agent.skills.gardener_agent import SkillGardenerAgent
from app.meta_agent.skills.schemas import AgentTrace, TraceStep
from sqlmodel import SQLModel, Session

async def run_integration():
    db_manager = SkillDatabaseManager("sqlite:///skills_test_v1.db")
    SQLModel.metadata.create_all(db_manager.engine)
    
    # Pre-seed some groups and skills
    with Session(db_manager.engine) as session:
        http_group = SkillGroup(name="HTTPClient", description="Sends HTTP", meta_tags=[])
        session.add(http_group)
        session.commit()
        session.refresh(http_group)
        
        http_skill = Skill(
            group_id=http_group.id,
            major_version=1,
            minor_version=0,
            code="def fetch(url): return 'data'",
            docstring="Initial HTTP fetch.",
            tags=[],
            python_dependencies=[],
            update_log="Init"
        )
        session.add(http_skill)
        session.commit()
        session.refresh(http_skill)
        
        test_case = TestCase(
            skill_version_id=http_skill.id,
            case_name="test_fetch_basic",
            executable_code="def test_basic(): assert fetch('http://ok.com') == 'data'"
        )
        session.add(test_case)
        session.commit()
        session.refresh(http_skill)
        
        api_group = SkillGroup(name="DataAPI", description="Gets API Date", meta_tags=[])
        session.add(api_group)
        session.commit()
        session.refresh(api_group)
        
        api_skill = Skill(
            group_id=api_group.id,
            major_version=1,
            minor_version=0,
            code="def get_user(uid):\n    return fetch(f'/user/{uid}')",
            docstring="Gets user",
            tags=[],
            python_dependencies=[],
            update_log="Init API"
        )
        session.add(api_skill)
        session.commit()
        session.refresh(api_skill)
        
        target_id = api_skill.id

    # Create dummy trace where there is a new feature (querying multiple users)
    trace = AgentTrace(final_answer="Done",
        query="Update DataAPI to support querying multiple users. The HTTPClient is still v1.0",
        steps=[TraceStep(
            step_id="step1", tool_name="User",
            tool_input={"args": []},
            status="success",
            tool_output="User said we need a get_users([uids]) function.",
            thought="I need to update DataAPI to support multiple ids using the existing HTTPClient v1."
        )]
    )
    
    # Initialize and run Gardener
    from app.llm import LLM
    llm = LLM(config_name="tool_maker")
    agent = SkillGardenerAgent(llm=llm)
    
    print("Running Gardener Agent...")
    res = await agent.run_extraction(trace=trace, db_manager=db_manager, target_skill_id=target_id)
    print("Gardener result:", res)

if __name__ == "__main__":
    asyncio.run(run_integration())
