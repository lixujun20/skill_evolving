import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from academic.refactoring_lab.build_replay_case_drafts import build_replay_case_drafts
from academic.refactoring_lab.merge_replay_cases import merge_replay_cases
from academic.refactoring_lab.mine_replay_candidates import mine_replay_candidates
from academic.refactoring_lab.planning_replay_benchmark import (
    DEFAULT_CASES_PATH,
    _load_replay_cases,
    run_planning_replay_benchmark,
)
from app.meta_agent.skills.database.manager import SkillDatabaseManager


@pytest.fixture
def mock_db():
    base_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg2://edumanus_user:edumanus_password@localhost:15432/aicosmos_test",
    )
    test_db_name = f"aicosmos_test_{uuid.uuid4().hex[:12]}"
    admin_url = base_url.rsplit("/", 1)[0] + "/postgres"

    from sqlalchemy import create_engine

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin_engine.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {test_db_name}"))
    admin_engine.dispose()

    test_url = base_url.rsplit("/", 1)[0] + f"/{test_db_name}"
    manager = SkillDatabaseManager(test_url)
    with manager.engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    SQLModel.metadata.create_all(manager.engine)
    try:
        yield manager
    finally:
        manager.engine.dispose()
        admin_engine2 = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        with admin_engine2.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {test_db_name}"))
        admin_engine2.dispose()


def test_replay_cases_load_from_json_file() -> None:
    cases = _load_replay_cases(DEFAULT_CASES_PATH)

    assert len(cases) == 5
    assert cases[0].case_id == "reuse_previous_plan_exact"
    assert cases[0].references.preferred_actions == ["reuse_plan"]
    assert cases[0].references.relevant_fragment_ids == ["frag_stats_summary"]
    assert cases[-1].references.possible_shared_skill_names == ["_shared_side_length_total"]
    assert cases[-1].mock_judge_response


@pytest.mark.asyncio
async def test_planning_replay_benchmark_prefers_joint_refactor(mock_db) -> None:
    result = await run_planning_replay_benchmark(
        cases_path=DEFAULT_CASES_PATH,
        db_manager=mock_db,
    )

    assert result["benchmark"] == "planning_replay_benchmark"
    assert result["benchmark_version"] == "workflow_reuse_v3_judge"
    assert result["n_cases"] == 5
    assert result["n_available_cases"] == 5
    assert result["n_judged_cases"] == 5
    assert result["joint_refactor_wins"] == 5
    first_case = result["cases"][0]
    assert first_case["winner"] == "joint_refactor"
    assert first_case["judge"]["available"] is True
    assert first_case["judge"]["winner"] == "joint_refactor"
    assert first_case["joint_refactor"]["diagnostics"]["history_reuse_action"] == "reuse_plan"
    assert first_case["joint_refactor"]["diagnostics"]["action_reference_match"] is True
    assert first_case["legacy_planner"]["diagnostics"]["discouraged_shared_skill_violations"] == [
        "_shared_sum_stats"
    ]
    last_case = result["cases"][-1]
    assert last_case["joint_refactor"]["diagnostics"]["possible_shared_skills_used"] == 1
    assert last_case["joint_refactor"]["diagnostics"]["relevant_fragment_mentions"] == 1
    assert last_case["heuristic_winner"] == "joint_refactor"


def test_mine_replay_candidates_extracts_regressions(tmp_path: Path) -> None:
    baseline = {
        "problems": [
            {
                "problem_idx": 0,
                "problem_id": "p0",
                "question": "Q0",
                "expected": "1",
                "n_runs": 4,
                "n_correct": 3,
                "accuracy": 0.75,
                "avg_total_tokens": 100.0,
                "avg_completion_tokens": 10.0,
                "has_timeout": False,
                "runs": [
                    {"n_steps": 20, "n_code_blocks": 4, "skills_retrieved": []},
                    {"n_steps": 20, "n_code_blocks": 4, "skills_retrieved": []},
                ],
            }
        ]
    }
    evolve = {
        "test_details": [
            {
                "problem_idx": 0,
                "problem_id": "p0",
                "question": "Q0",
                "expected": "1",
                "n_runs": 4,
                "n_correct": 1,
                "accuracy": 0.25,
                "avg_total_tokens": 180.0,
                "avg_completion_tokens": 15.0,
                "has_timeout": False,
                "runs": [
                    {
                        "n_steps": 6,
                        "n_code_blocks": 1,
                        "skills_retrieved": ["skill_a", "skill_b"],
                    },
                    {
                        "n_steps": 5,
                        "n_code_blocks": 0,
                        "skills_retrieved": ["skill_a"],
                    },
                ],
            }
        ]
    }

    baseline_path = tmp_path / "baseline.json"
    evolve_path = tmp_path / "evolve.json"
    baseline_path.write_text(json.dumps(baseline))
    evolve_path.write_text(json.dumps(evolve))

    result = mine_replay_candidates(
        baseline_detail_path=baseline_path,
        evolve_detail_path=evolve_path,
    )

    assert result["n_candidates"] == 1
    candidate = result["candidates"][0]
    assert candidate["problem_id"] == "p0"
    assert "regression" in candidate["candidate_reason_tags"]
    assert "shortcut_suspicion" in candidate["candidate_reason_tags"]
    assert "planner_or_retrieval_mismatch" in candidate["candidate_reason_tags"]


def test_build_replay_case_drafts_from_realistic_inputs(tmp_path: Path) -> None:
    candidates = {
        "candidates": [
            {
                "problem_idx": 0,
                "problem_id": "p0",
                "question": "Q0",
                "candidate_reason_tags": ["regression"],
                "evolve_skills_seen": ["skill_a"],
            }
        ]
    }
    evolve = {
        "test_details": [
            {
                "problem_idx": 0,
                "problem_id": "p0",
                "question": "Q0",
                "runs": [
                    {
                        "correct": False,
                        "steps": [{"content": "bad reasoning"}],
                    }
                ],
            }
        ]
    }
    baseline = {
        "problems": [
            {
                "problem_idx": 0,
                "problem_id": "p0",
                "question": "Q0 baseline",
                "runs": [],
            }
        ]
    }
    skills = [
        {"name": "skill_a", "description": "desc", "code": "def skill_a():\n    return 1\n"}
    ]

    c_path = tmp_path / "candidates.json"
    e_path = tmp_path / "evolve.json"
    b_path = tmp_path / "baseline.json"
    s_path = tmp_path / "skills.json"
    c_path.write_text(json.dumps(candidates))
    e_path.write_text(json.dumps(evolve))
    b_path.write_text(json.dumps(baseline))
    s_path.write_text(json.dumps(skills))

    result = build_replay_case_drafts(
        candidates_path=c_path,
        evolve_detail_path=e_path,
        baseline_detail_path=b_path,
        skills_path=s_path,
    )

    assert result["n_drafts"] == 1
    draft = result["draft_cases"][0]
    assert draft["case_id"] == "draft::0::p0"
    assert draft["status"] == "draft"
    assert draft["retrieved_skills"][0]["name"] == "skill_a"
    assert draft["references"]["possible_shared_skill_names"] == []
    assert draft["history_context"]["workflow_summary"] == "bad reasoning"


def test_merge_replay_cases_keeps_base_and_drafts(tmp_path: Path) -> None:
    base_path = tmp_path / "base.json"
    draft_path = tmp_path / "drafts.json"
    base_path.write_text(json.dumps([{"case_id": "base::1"}]))
    draft_path.write_text(json.dumps({"draft_cases": [{"case_id": "draft::1"}]}))

    merged = merge_replay_cases(
        base_cases_path=base_path,
        drafts_path=draft_path,
    )

    assert [item["case_id"] for item in merged] == ["base::1", "draft::1"]
