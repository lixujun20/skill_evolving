from pathlib import Path

from academic.benchmarks.bfcl import BFCLToolCall, load_bfcl_tasks, load_bfcl_tools, score_bfcl_calls
from academic.benchmarks.artifacts import ArtifactStore
from academic.benchmarks.run import (
    _build_bfcl_skill_bundles,
    _build_maintenance_test_results,
    _build_skill_test_result,
    _load_saved_details,
    _result_from_dict,
    _refine_bfcl_skill_store,
    _validate_refine_output_consistency,
)
from academic.benchmarks.bfcl_skills import extract_bfcl_skills_from_results
from academic.benchmarks.types import SkillArtifact, SkillBundleCase, SkillTestResult
from academic.benchmarks.registry import BENCHMARK_REGISTRY
from academic.benchmarks.spreadsheet import load_spreadsheet_tasks, verify_spreadsheet_output


def test_registry_has_selected_benchmarks() -> None:
    assert {"bfcl_v3", "appworld", "officeqa", "spreadsheet", "tir_bench"} <= set(BENCHMARK_REGISTRY)


def test_bfcl_loader_and_scorer_contract() -> None:
    train, test = load_bfcl_tasks(cache_dir=Path("data/benchmarks/bfcl_v3"), n_train=2, n_test=2)
    tools = load_bfcl_tools(Path("data/benchmarks/bfcl_v3"))
    assert len(train) == 2
    assert len(test) == 2
    assert any(t["function"]["name"] == "cd" for t in tools)

    from academic.benchmarks.bfcl import _parse_call

    task = train[0]
    calls = []
    for turn_index, turn in enumerate(task.expected):
        for raw in turn:
            name, args = _parse_call(raw)
            calls.append(BFCLToolCall(name=name, arguments=args, turn_index=turn_index))
    score = score_bfcl_calls(calls, task.expected)
    assert score["task_success"] is True
    assert score["call_f1"] == 1.0


def test_turn_watchdog_breaks_on_repeated_call() -> None:
    from academic.benchmarks.bfcl import _TurnWatchdog

    watchdog = _TurnWatchdog(expected_names=["a", "b"])
    assert watchdog.observe([BFCLToolCall(name="a", arguments={"x": 1}, turn_index=0)]) is None
    # exact duplicate of a previously seen call → break
    assert (
        watchdog.observe([BFCLToolCall(name="a", arguments={"x": 1}, turn_index=0)])
        == "repeated_call"
    )


def test_turn_watchdog_breaks_after_expected_coverage_then_extras() -> None:
    from academic.benchmarks.bfcl import _TurnWatchdog

    watchdog = _TurnWatchdog(expected_names=["a", "b"])
    # cover both expected names
    assert (
        watchdog.observe(
            [
                BFCLToolCall(name="a", arguments={"x": 1}, turn_index=0),
                BFCLToolCall(name="b", arguments={"y": 1}, turn_index=0),
            ]
        )
        is None
    )
    # one pure-extra step is still tolerated
    assert (
        watchdog.observe([BFCLToolCall(name="c", arguments={"z": 1}, turn_index=0)])
        is None
    )
    # second consecutive pure-extra step → break
    assert (
        watchdog.observe([BFCLToolCall(name="d", arguments={"z": 2}, turn_index=0)])
        == "all_expected_covered_and_extra"
    )


def test_turn_watchdog_resets_pure_extra_when_expected_call_returns() -> None:
    from academic.benchmarks.bfcl import _TurnWatchdog

    watchdog = _TurnWatchdog(expected_names=["a"])
    assert watchdog.observe([BFCLToolCall(name="a", arguments={}, turn_index=0)]) is None
    assert watchdog.observe([BFCLToolCall(name="c", arguments={"z": 1}, turn_index=0)]) is None
    # mixing in another expected-name call resets the consecutive counter
    assert watchdog.observe([BFCLToolCall(name="a", arguments={"k": 9}, turn_index=0)]) is None
    assert watchdog.observe([BFCLToolCall(name="d", arguments={"z": 2}, turn_index=0)]) is None
    # only one pure-extra after the reset, so should not break yet
    assert watchdog.early_stop_reason is None


def test_turn_watchdog_no_break_without_expected() -> None:
    from academic.benchmarks.bfcl import _TurnWatchdog

    watchdog = _TurnWatchdog(expected_names=[])
    # Without expected names, only repetition can trigger a break
    assert watchdog.observe([BFCLToolCall(name="a", arguments={"x": 1}, turn_index=0)]) is None
    assert watchdog.observe([BFCLToolCall(name="b", arguments={"y": 2}, turn_index=0)]) is None
    assert watchdog.observe([BFCLToolCall(name="c", arguments={"z": 3}, turn_index=0)]) is None
    assert watchdog.early_stop_reason is None


def test_spreadsheet_loader_and_verifier_contract() -> None:
    train, test = load_spreadsheet_tasks(cache_dir=Path("data/benchmarks/spreadsheet"), n_train=2, n_test=1)
    assert len(train) == 2
    assert len(test) == 1
    task = train[0]
    result = verify_spreadsheet_output(
        predicted_xlsx=Path(task.expected["golden_xlsx"]),
        golden_xlsx=Path(task.expected["golden_xlsx"]),
        sheet_name=task.expected["answer_sheet"],
        answer_range=task.expected["answer_position"],
    )
    assert result["pass"] is True
    assert result["cell_accuracy"] == 1.0


def test_bfcl_refine_disables_harmful_auto_skill() -> None:
    harmful = SkillArtifact(
        name="bfcl_avoid_extra_startEngine",
        kind="negative_rule_card",
        description="Avoid unnecessary startEngine calls unless required.",
        body="Do not use startEngine as a speculative lookup.",
        metadata={"source": "evolve_rollouts", "injection_type": "informational", "source_task_ids": ["t_bad"]},
    )
    helpful = SkillArtifact(
        name="bfcl_params_cp",
        kind="atomic_tool_rule_card",
        description="Observed parameter names for cp.",
        body="For `cp`, use source and destination.",
        metadata={"source": "evolve_rollouts", "injection_type": "informational", "source_task_ids": ["t_good"]},
    )
    store = ArtifactStore([harmful, helpful])
    train_details = [
        {
            "task_id": "t_bad",
            "runs": [
                {
                    "metrics": {
                        "official_valid": True,
                    }
                }
            ],
        },
        {
            "task_id": "t_good",
            "runs": [
                {
                    "metrics": {
                        "official_valid": False,
                    }
                }
            ],
        },
    ]
    replay_details = [
        {
            "task_id": "t_bad",
            "runs": [
                {
                    "metrics": {
                        "official_valid": False,
                        "retrieved_skills": ["bfcl_avoid_extra_startEngine"],
                        "prompt_injected_skills": ["bfcl_avoid_extra_startEngine"],
                        "tool_injected_skills": [],
                        "called_skill_tools": [],
                        "used_skills": [],
                    }
                }
            ],
        },
        {
            "task_id": "t_good",
            "runs": [
                {
                    "metrics": {
                        "official_valid": True,
                        "retrieved_skills": ["bfcl_params_cp"],
                        "prompt_injected_skills": ["bfcl_params_cp"],
                        "tool_injected_skills": [],
                        "called_skill_tools": [],
                        "used_skills": [],
                    }
                }
            ],
        },
    ]
    _build_bfcl_skill_bundles(store, train_details=train_details, replay_details=replay_details)
    maintenance = _build_maintenance_test_results(
        store,
        train_details=train_details,
        replay_details=replay_details,
        run_label="unit",
    )
    decisions = _refine_bfcl_skill_store(
        store,
        train_details=train_details,
        replay_details=replay_details,
        maintenance_test_results=maintenance,
        min_fail_count=2,
    )
    harmful_after = next(skill for skill in store.all() if skill.name == "bfcl_avoid_extra_startEngine")
    helpful_after = next(skill for skill in store.all() if skill.name == "bfcl_params_cp")
    assert harmful_after.is_disabled() is True
    assert helpful_after.is_disabled() is False
    harmful_decision = next(row for row in decisions if row["skill_name"] == "bfcl_avoid_extra_startEngine")
    assert harmful_decision["action"] == "disable_on_regression"


def test_bfcl_refine_rolls_back_regressed_skill_version_when_history_exists() -> None:
    store = ArtifactStore()
    original = SkillArtifact(
        name="bfcl_schema_parameter_names",
        kind="atomic_tool_rule_card",
        description="Use exact schema names.",
        body="Use booking_id exactly when the schema requires booking_id.",
        metadata={"source": "evolve_rollouts", "injection_type": "informational"},
    )
    store.add(original)
    broken = SkillArtifact(
        name="bfcl_schema_parameter_names",
        kind="atomic_tool_rule_card",
        description="Broken alias rule.",
        body="For invoice and support calls, use reservation_id instead of booking_id.",
        metadata={"source": "evolve_rollouts", "injection_type": "informational"},
    )
    store.add(broken)
    train_details = [
        {
            "task_id": "task-1",
            "runs": [{"metrics": {"official_valid": True}}],
        }
    ]
    replay_details = [
        {
            "task_id": "task-1",
            "runs": [
                {
                    "metrics": {
                        "official_valid": False,
                        "retrieved_skills": ["bfcl_schema_parameter_names"],
                        "prompt_injected_skills": ["bfcl_schema_parameter_names"],
                        "tool_injected_skills": [],
                        "called_skill_tools": [],
                        "used_skills": ["bfcl_schema_parameter_names"],
                    }
                }
            ],
        }
    ]
    _build_bfcl_skill_bundles(store, train_details=train_details, replay_details=replay_details)
    maintenance = _build_maintenance_test_results(
        store,
        train_details=train_details,
        replay_details=replay_details,
        run_label="unit",
    )
    decisions = _refine_bfcl_skill_store(
        store,
        train_details=train_details,
        replay_details=replay_details,
        maintenance_test_results=maintenance,
        min_fail_count=1,
    )
    repaired = next(skill for skill in store.all() if skill.name == "bfcl_schema_parameter_names")
    assert repaired.version == 1
    assert repaired.is_disabled() is False
    row = next(item for item in decisions if item["skill_name"] == "bfcl_schema_parameter_names")
    assert row["action"] == "rollback_on_regression"


def test_bfcl_refine_rolls_back_repeatedly_harmful_skill_without_help() -> None:
    store = ArtifactStore()
    original = SkillArtifact(
        name="bfcl_multi_action_turn_completion",
        kind="planning_card",
        description="Complete all required tool calls before ending a turn.",
        body="Keep calling tools until all requested actions are complete.",
        metadata={"source": "evolve_rollouts", "injection_type": "workflow"},
    )
    store.add(original)
    broken = SkillArtifact(
        name="bfcl_multi_action_turn_completion",
        kind="planning_card",
        description="Broken stop-early rule.",
        body="End the turn after the first relevant tool call.",
        metadata={"source": "evolve_rollouts", "injection_type": "workflow"},
    )
    store.add(broken)
    train_details = [
        {"task_id": "task-a", "runs": [{"metrics": {"official_valid": False}}]},
        {"task_id": "task-b", "runs": [{"metrics": {"official_valid": False}}]},
    ]
    replay_details = [
        {
            "task_id": "task-a",
            "runs": [
                {
                    "metrics": {
                        "official_valid": False,
                        "retrieved_skills": ["bfcl_multi_action_turn_completion"],
                        "prompt_injected_skills": ["bfcl_multi_action_turn_completion"],
                        "tool_injected_skills": [],
                        "called_skill_tools": [],
                        "used_skills": ["bfcl_multi_action_turn_completion"],
                    }
                }
            ],
        },
        {
            "task_id": "task-b",
            "runs": [
                {
                    "metrics": {
                        "official_valid": False,
                        "retrieved_skills": ["bfcl_multi_action_turn_completion"],
                        "prompt_injected_skills": ["bfcl_multi_action_turn_completion"],
                        "tool_injected_skills": [],
                        "called_skill_tools": [],
                        "used_skills": ["bfcl_multi_action_turn_completion"],
                    }
                }
            ],
        },
    ]
    _build_bfcl_skill_bundles(store, train_details=train_details, replay_details=replay_details)
    maintenance = _build_maintenance_test_results(
        store,
        train_details=train_details,
        replay_details=replay_details,
        run_label="unit",
    )
    decisions = _refine_bfcl_skill_store(
        store,
        train_details=train_details,
        replay_details=replay_details,
        maintenance_test_results=maintenance,
        min_fail_count=1,
    )
    repaired = next(skill for skill in store.all() if skill.name == "bfcl_multi_action_turn_completion")
    assert repaired.version == 1
    row = next(item for item in decisions if item["skill_name"] == "bfcl_multi_action_turn_completion")
    assert row["action"] == "rollback_on_no_help"


def test_bfcl_refine_disables_manual_fault_without_harming_unchanged_copresent_skills() -> None:
    good = SkillArtifact(
        name="bfcl_state_id_reuse",
        kind="functional_workflow_card",
        description="Reuse ids from previous turns.",
        body="Reuse exact ids from tool outputs and prior turns.",
        metadata={"injection_type": "workflow"},
    )
    bad = SkillArtifact(
        name="bad_cancel_order_143",
        kind="workflow_guardrail_card",
        description="Broken task-specific cancel rule.",
        body="Do not call cancel_order for the reviewed order.",
        metadata={
            "manual_fault_injected": True,
            "injection_type": "workflow",
            "intent_keywords": ["cancel", "order", "reviewed"],
            "source_task_ids": ["task-143"],
        },
    )
    store = ArtifactStore([good, bad])
    train_details = [
        {
            "task_id": "task-143",
            "runs": [{"metrics": {"official_valid": True}}],
        }
    ]
    replay_details = [
        {
            "task_id": "task-143",
            "runs": [
                {
                    "metrics": {
                        "official_valid": False,
                        "retrieved_skills": ["bfcl_state_id_reuse", "bad_cancel_order_143"],
                        "prompt_injected_skills": ["bfcl_state_id_reuse", "bad_cancel_order_143"],
                        "tool_injected_skills": [],
                        "called_skill_tools": [],
                        "used_skills": [],
                    }
                }
            ],
        }
    ]
    _build_bfcl_skill_bundles(store, train_details=train_details, replay_details=replay_details)
    maintenance = _build_maintenance_test_results(
        store,
        train_details=train_details,
        replay_details=replay_details,
        run_label="unit",
    )
    decisions = _refine_bfcl_skill_store(
        store,
        train_details=train_details,
        replay_details=replay_details,
        maintenance_test_results=maintenance,
        min_fail_count=1,
    )
    bad_after = next(skill for skill in store.all() if skill.name == "bad_cancel_order_143")
    good_after = next(skill for skill in store.all() if skill.name == "bfcl_state_id_reuse")
    assert bad_after.is_disabled() is True
    assert good_after.is_disabled() is False
    bad_row = next(item for item in decisions if item["skill_name"] == "bad_cancel_order_143")
    good_row = next(item for item in decisions if item["skill_name"] == "bfcl_state_id_reuse")
    assert bad_row["action"] == "disable_on_regression"
    assert good_row["action"] == "keep"


def test_artifact_store_separates_bundle_and_test_results(tmp_path: Path) -> None:
    artifact = SkillArtifact(
        name="bfcl_state_rule",
        kind="workflow_guardrail_card",
        description="Reuse ids",
        body="Reuse ids from earlier turns.",
    )
    artifact.bundle.positive_cases = [
        SkillBundleCase(
            case_id="bfcl_state_rule:positive:0",
            source="manual",
            prompt="reuse booking id",
            expected={"official_valid": True},
        )
    ]
    store = ArtifactStore([artifact])
    store.add_test_result(
        SkillTestResult(
            result_id="res1",
            skill_name="bfcl_state_rule",
            skill_version=1,
            bundle_id=artifact.bundle.bundle_id,
            bundle_version=artifact.bundle.bundle_version,
            run_label="unit",
        )
    )
    skill_path = tmp_path / "skills.json"
    result_path = tmp_path / "results.json"
    store.save(skill_path)
    store.save_test_results(result_path)
    loaded = ArtifactStore.load(skill_path)
    loaded_results = ArtifactStore.load_test_results(result_path)
    assert len(loaded.all()) == 1
    assert loaded.all()[0].bundle.positive_cases[0].case_id == "bfcl_state_rule:positive:0"
    assert loaded.test_results() == []
    assert len(loaded_results) == 1
    assert loaded_results[0].result_id == "res1"


def test_artifact_store_marks_dependents_stale_on_minor_update() -> None:
    upstream = SkillArtifact(
        name="shared_rule",
        kind="rule_card",
        description="Shared rule",
        body="Common rule text.",
        metadata={"version_kind": "seed"},
    )
    downstream = SkillArtifact(
        name="consumer_rule",
        kind="rule_card",
        description="Consumer",
        body="See shared_rule before acting.",
        metadata={"dependencies": ["shared_rule"], "version_kind": "seed"},
    )
    store = ArtifactStore([upstream, downstream])
    updated = SkillArtifact(
        name="shared_rule",
        kind="rule_card",
        description="Shared rule improved",
        body="Common rule text improved.",
        metadata={"version_kind": "minor"},
    )
    store.add(updated)
    refreshed = next(skill for skill in store.all() if skill.name == "consumer_rule")
    assert refreshed.stale is True
    assert refreshed.status == "stale"
    assert refreshed.metadata["stale_due_to"]["version_kind"] == "minor"


def test_minor_update_cannot_drop_existing_cases() -> None:
    artifact = SkillArtifact(
        name="rule",
        kind="rule_card",
        description="Rule",
        body="Rule body",
        metadata={"version_kind": "seed"},
    )
    artifact.bundle.positive_cases = [
        SkillBundleCase(
            case_id="rule:positive:0",
            source="manual",
            prompt="case one",
            expected={"official_valid": True},
        )
    ]
    store = ArtifactStore([artifact])
    updated = SkillArtifact(
        name="rule",
        kind="rule_card",
        description="Rule updated",
        body="Rule body updated",
        metadata={"version_kind": "minor"},
    )
    store.add(updated)
    latest = next(skill for skill in store.all() if skill.name == "rule")
    latest.bundle.positive_cases = []
    try:
        _validate_refine_output_consistency(latest)
    except ValueError as exc:
        assert "removed existing tests" in str(exc)
    else:
        raise AssertionError("Expected minor update consistency check to fail")


def test_bfcl_maintenance_result_reports_with_without_deltas() -> None:
    artifact = SkillArtifact(
        name="bfcl_state_rule",
        kind="workflow_guardrail_card",
        description="Reuse ids",
        body="Reuse ids from earlier turns.",
        metadata={"source": "evolve_rollouts"},
    )
    artifact.metadata["source_task_ids"] = ["task-1"]
    store = ArtifactStore([artifact])
    train_details = [
        {
            "task_id": "task-1",
            "runs": [
                {
                    "metrics": {
                        "official_valid": False,
                        "total_tokens": 40,
                        "n_model_steps": 3,
                    }
                }
            ],
        }
    ]
    replay_details = [
        {
            "task_id": "task-1",
            "runs": [
                {
                    "metrics": {
                        "official_valid": True,
                        "total_tokens": 32,
                        "n_model_steps": 2,
                        "retrieved_skills": ["bfcl_state_rule"],
                        "prompt_injected_skills": ["bfcl_state_rule"],
                        "tool_injected_skills": [],
                        "called_skill_tools": [],
                        "used_skills": ["bfcl_state_rule"],
                    }
                }
            ],
        }
    ]
    _build_bfcl_skill_bundles(store, train_details=train_details, replay_details=replay_details)
    results = _build_maintenance_test_results(
        store,
        train_details=train_details,
        replay_details=replay_details,
        run_label="train_refine",
    )
    assert len(results) == 1
    aggregate = results[0].aggregate
    assert aggregate["n_improved"] == 1
    assert aggregate["n_regressed"] == 0
    assert aggregate["unit_utility_report"]["delta_accuracy"] == 1.0
    assert aggregate["unit_utility_report"]["delta_tokens"] == -8
    assert aggregate["unit_utility_report"]["delta_steps"] == -1


def test_skill_test_result_uses_train_vs_post_refine_not_heldout_test() -> None:
    artifact = SkillArtifact(
        name="bfcl_state_rule",
        kind="workflow_guardrail_card",
        description="Reuse ids",
        body="Reuse ids from earlier turns.",
        metadata={"source": "evolve_rollouts"},
    )
    artifact.bundle.positive_cases = [
        SkillBundleCase(
            case_id="bfcl_state_rule:positive:0",
            source="train_positive",
            prompt="task-1",
            expected={},
            context={"task_id": "task-1"},
        )
    ]
    train_details = [
        {
            "task_id": "task-1",
            "runs": [
                {
                    "metrics": {
                        "official_valid": False,
                        "total_tokens": 20,
                        "n_model_steps": 2,
                    }
                }
            ],
        }
    ]
    post_refine_details = [
        {
            "task_id": "task-1",
            "runs": [
                {
                    "metrics": {
                        "official_valid": True,
                        "total_tokens": 18,
                        "n_model_steps": 1,
                        "retrieved_skills": ["bfcl_state_rule"],
                        "prompt_injected_skills": ["bfcl_state_rule"],
                        "tool_injected_skills": [],
                        "called_skill_tools": [],
                        "used_skills": ["bfcl_state_rule"],
                    }
                }
            ],
        }
    ]
    heldout_test_details = [
        {
            "task_id": "task-999",
            "runs": [
                {
                    "metrics": {
                        "official_valid": False,
                        "total_tokens": 999,
                        "n_model_steps": 9,
                        "retrieved_skills": ["bfcl_state_rule"],
                        "prompt_injected_skills": ["bfcl_state_rule"],
                        "tool_injected_skills": [],
                        "called_skill_tools": [],
                        "used_skills": ["bfcl_state_rule"],
                    }
                }
            ],
        }
    ]
    result = _build_skill_test_result(
        artifact,
        train_details=train_details,
        replay_details=post_refine_details,
        run_label="post_refine",
    )
    assert result.aggregate["n_improved"] == 1
    assert result.aggregate["unit_utility_report"]["delta_tokens"] == -2
    assert all(run.trace_ref != "task-999" for run in result.unit_case_runs)


def test_historical_bfcl_train_details_produce_nonempty_maintenance_assets() -> None:
    train_path = Path(
        "/home/lixujun/skill_evolving/academic/results/"
        "bfcl_v3_glm47_official_tracecheck_evolve_3x3_partial_train.json"
    )
    if not train_path.exists():
        raise AssertionError(f"Missing historical BFCL fixture: {train_path}")
    details = _load_saved_details(train_path)
    results = [_result_from_dict(run) for item in details for run in item.get("runs", [])]
    tools = load_bfcl_tools(Path("/home/lixujun/skill_evolving/data/benchmarks/bfcl_v3"), data_source="bfcl_eval_bundle")
    artifacts = extract_bfcl_skills_from_results(results, tool_schemas=tools)
    assert artifacts
    store = ArtifactStore(artifacts)
    _build_bfcl_skill_bundles(store, train_details=details, replay_details=details)
    maintenance = _build_maintenance_test_results(
        store,
        train_details=details,
        replay_details=details,
        run_label="historical",
    )
    assert maintenance
    assert any(item.bundle_id for item in maintenance)
    assert any(len(skill.bundle.all_cases()) > 0 for skill in store.all())
