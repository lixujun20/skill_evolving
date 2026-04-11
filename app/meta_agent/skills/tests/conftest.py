import pytest
import os
import time
from sqlalchemy import text
from sqlmodel import Session, SQLModel
from app.meta_agent.skills.database.models import (
    SkillGroup, Skill, TestCase, TestReport, SkillDependency, RefactorPlan,
)
from app.meta_agent.skills.database.manager import SkillDatabaseManager


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "llm: marks tests that exercise real LLM reasoning — run with: pytest -m llm"
    )

@pytest.fixture(autouse=True)
def llm_rate_limit_guard(request):
    """Add a cooldown after each LLM-marked test to avoid API rate limit (HTTP 429)."""
    yield
    if request.node.get_closest_marker("llm"):
        time.sleep(8)  # 8-second cooldown keeps requests/min well below limits

@pytest.fixture
def mock_db():
    import uuid
    base_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg2://edumanus_user:edumanus_password@localhost:15432/aicosmos_test",
    )
    # Parse the base URL to extract connection info for the admin connection.
    # Use a unique DB name per test to fully isolate from concurrent runs.
    test_db_name = f"aicosmos_test_{uuid.uuid4().hex[:12]}"
    # Admin connection to create/drop the per-test database.
    admin_url = base_url.rsplit("/", 1)[0] + "/postgres"
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin_engine.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {test_db_name}"))
    admin_engine.dispose()

    # Now connect to the fresh test database.
    test_url = base_url.rsplit("/", 1)[0] + f"/{test_db_name}"
    manager = SkillDatabaseManager(test_url)

    # pgvector is required by the Skill.embedding column.
    with manager.engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    SQLModel.metadata.create_all(manager.engine)
    try:
        yield manager
    finally:
        manager.engine.dispose()
        # Drop the unique test DB to avoid accumulating orphaned databases.
        admin_engine2 = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        with admin_engine2.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {test_db_name}"))
        admin_engine2.dispose()

# NOTE: LLM response caching is now handled directly inside LLM.ask() / LLM.ask_tool()
# via app/llm_cache.py.  Activate with env var LLM_CACHE_ENABLED=1.
# The old fixture-based patch mechanism below has been removed.


def create_mock_skill(manager, skill_name: str, version: str, code: str = "def mock(): pass",
                      group_name: str = None) -> Skill:
    """Create a versioned Skill in the given manager's DB.

    Args:
        skill_name: Human-readable skill identifier; used as SkillGroup.name when
                    group_name is not explicitly provided, so each unique skill_name
                    gets its own group.
        version:    Semantic version string, e.g. "1.0" or "2.3".
        code:       Python source code for the skill.
        group_name: Override the SkillGroup name.  Defaults to skill_name so that
                    tests can call `create_mock_skill(db, "foo", "1.0")` and later
                    `create_mock_skill(db, "foo", "1.1")` and both land in the same
                    group (enabling `len(versions) > 1` assertions after LLM extraction).
    """
    effective_group_name = group_name if group_name is not None else skill_name
    with Session(manager.engine) as session:
        from sqlmodel import select
        group = session.exec(select(SkillGroup).where(SkillGroup.name == effective_group_name)).first()
        if not group:
            group = SkillGroup(name=effective_group_name)
            session.add(group)
            session.commit()
            session.refresh(group)

        major, minor = 1, 0
        if isinstance(version, str) and "." in version:
            v_major, v_minor = version.split(".", 1)
            major, minor = int(v_major), int(v_minor)

        skill = Skill(
            group_id=group.id,
            major_version=major,
            minor_version=minor,
            code=code,
            docstring=f"Mock skill: {effective_group_name} v{version}",
            interface_schema={},
            embedding=[0.0] * 1024,
        )
        session.add(skill)
        session.commit()
        session.refresh(skill)
        return skill
