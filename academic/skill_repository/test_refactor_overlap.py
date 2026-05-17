from academic.skill_repository.refactor_overlap import (
    OverlapGraphState,
    TraceSegment,
    _coarse_skill_candidates_for_clique,
    apply_affected_skill_updates,
    build_overlap_graph_state,
    artifact_from_refactor_payload,
    discover_overlap_graph,
    find_refactor_cliques,
    materialize_overlap_graph,
    token_ngrams,
    update_overlap_graph_state,
)
from academic.skill_repository.types import SkillArtifact, SkillBundle, SkillBundleCase


def test_token_ngrams_extracts_structural_tokens() -> None:
    grams = token_ngrams("call diff(file_name1='a.txt', file_name2='b.txt')", n_values=(2,))
    assert "call diff" in grams
    assert "diff file_name1" in grams


def test_overlap_graph_prioritizes_shared_error_and_text_ngrams() -> None:
    segments = [
        TraceSegment(
            segment_id="t1:turn:0",
            task_id="t1",
            turn_index=0,
            text="explicit filenames are present call diff directly avoid directory discovery",
            error_text="extra call ls before diff explicit filenames",
        ),
        TraceSegment(
            segment_id="t2:turn:0",
            task_id="t2",
            turn_index=0,
            text="explicit record ids are present call compare directly avoid discovery lookup",
            error_text="extra call lookup before compare explicit ids",
        ),
        TraceSegment(
            segment_id="t3:turn:0",
            task_id="t3",
            turn_index=0,
            text="authenticate user before protected message send operation",
            error_text="missing authentication",
        ),
    ]
    graph = discover_overlap_graph(segments, min_weight=0.05, top_k_per_segment=4)
    pairs = {frozenset([edge.source, edge.target]): edge for edge in graph.edges}
    key = frozenset(["t1:turn:0", "t2:turn:0"])
    assert key in pairs
    assert pairs[key].weight > 0
    assert pairs[key].shared_ngrams or pairs[key].shared_error_ngrams


def test_find_refactor_cliques_from_overlap_graph() -> None:
    segments = [
        TraceSegment(segment_id=f"t{i}:turn:0", task_id=f"t{i}", turn_index=0, text="shared direct compare explicit ids")
        for i in range(3)
    ]
    graph = discover_overlap_graph(segments, min_weight=0.01, top_k_per_segment=4)
    cliques = find_refactor_cliques(graph, min_size=2, max_size=3, min_edge_weight=0.01)
    assert cliques
    assert len(cliques[0].segment_ids) >= 2


def test_find_refactor_cliques_requires_distinct_tasks_by_default() -> None:
    segments = [
        TraceSegment(segment_id=f"t1:turn:{i}", task_id="t1", turn_index=i, text="shared direct compare explicit ids")
        for i in range(3)
    ]
    graph = discover_overlap_graph(segments, min_weight=0.01, top_k_per_segment=4)
    assert find_refactor_cliques(graph, min_edge_weight=0.01) == []
    assert find_refactor_cliques(graph, min_edge_weight=0.01, min_distinct_tasks=1)


def test_find_refactor_cliques_can_be_filtered_by_seen_segment_sets() -> None:
    segments = [
        TraceSegment(segment_id=f"t{i}:turn:0", task_id=f"t{i}", turn_index=0, text="shared direct compare explicit ids")
        for i in range(3)
    ]
    graph = discover_overlap_graph(segments, min_weight=0.01, top_k_per_segment=4)
    cliques = find_refactor_cliques(graph, min_edge_weight=0.01)
    assert cliques
    seen = {tuple(sorted(cliques[0].segment_ids))}
    filtered = [clique for clique in cliques if tuple(sorted(clique.segment_ids)) not in seen]
    assert filtered == []


def test_refactor_payload_to_shared_artifact_and_updates() -> None:
    payload = {
        "decision": {"action": "extract_shared", "reason": "same latent rule", "confidence": 0.9},
        "shared_skill": {
            "name": "direct_compare_when_ids_explicit",
            "kind": "atomic_tool_rule_card",
            "description": "Call compare tools directly when all ids are explicit.",
            "body": "If all required comparison identifiers are explicit, call the comparison tool directly.",
            "interface": {"summary": "direct explicit-id comparison"},
            "metadata": {"allowed_tools": ["diff"], "source_task_ids": ["t1", "t2"]},
        },
        "affected_skill_updates": [
            {
                "name": "old_compare_rule",
                "action": "rewrite",
                "reason": "delegate invariant",
                "body": "Use direct_compare_when_ids_explicit for explicit id cases, then keep old residual constraints.",
            }
        ],
        "instance_mappings": [{"segment_id": "t1:turn:0", "is_instance": True}],
    }
    shared = artifact_from_refactor_payload(payload, group_id="g1")
    assert shared is not None
    assert shared.name == "direct_compare_when_ids_explicit"
    assert shared.lineage.version_kind == "refactor"
    old = SkillArtifact(
        name="old_compare_rule",
        kind="atomic_tool_rule_card",
        description="Old compare rule",
        body="Old body",
        bundle=SkillBundle(
            positive_cases=[
                SkillBundleCase(case_id="old:positive:0", source="manual", prompt="p")
            ]
        ),
    )
    updates = apply_affected_skill_updates(
        payload,
        existing_by_name={old.name: old},
        shared_skill=shared,
        group_id="g1",
    )
    assert len(updates) == 1
    assert updates[0].name == old.name
    assert shared.name in updates[0].dependencies
    assert updates[0].bundle.all_cases()[0].case_id == "old:positive:0"


def test_coarse_skill_candidates_for_clique_returns_weak_hypotheses() -> None:
    segments = [
        TraceSegment(
            segment_id="t1:turn:0",
            task_id="t1",
            turn_index=0,
            text="explicit stock symbol present call remove_stock_from_watchlist directly",
            error_text="avoid redundant symbol lookup",
        ),
        TraceSegment(
            segment_id="t2:turn:0",
            task_id="t2",
            turn_index=0,
            text="ticker already explicit call remove_stock_from_watchlist directly",
            error_text="avoid extra lookup for explicit ticker",
        ),
    ]
    skills = [
        SkillArtifact(
            name="remove_watchlist_when_symbol_explicit",
            kind="atomic_tool_rule_card",
            description="Call remove_stock_from_watchlist directly when the symbol is explicit.",
            body="When the stock symbol is already visible in the request, skip lookup and call remove_stock_from_watchlist directly.",
            metadata={"source_task_ids": ["train_a"], "allowed_tools": ["remove_stock_from_watchlist"]},
        ),
        SkillArtifact(
            name="flight_search_before_booking",
            kind="workflow_guardrail_card",
            description="Search flights before booking.",
            body="Always search available flights before booking a seat.",
        ),
    ]
    candidates = _coarse_skill_candidates_for_clique(
        selected_segments=segments,
        existing_skills=skills,
        top_k=2,
    )
    assert len(candidates) == 2
    assert candidates[0]["name"] == "remove_watchlist_when_symbol_explicit"
    assert candidates[0]["combined_similarity"] >= candidates[1]["combined_similarity"]
    assert "weak hypotheses" in candidates[0]["retrieval_warning"] or "coarse" in candidates[0]["retrieval_warning"].lower()


def test_coarse_skill_candidates_include_pending_but_not_disabled_skills() -> None:
    segments = [
        TraceSegment(
            segment_id="t1:turn:0",
            task_id="t1",
            turn_index=0,
            text="explicit stock symbol present call remove_stock_from_watchlist directly",
            error_text="avoid redundant symbol lookup",
        )
    ]
    pending = SkillArtifact(
        name="pending_symbol_rule",
        kind="atomic_tool_rule_card",
        description="Call watchlist tool directly for explicit symbols.",
        body="When a ticker is explicit, avoid lookup and call remove_stock_from_watchlist directly.",
        status="pending",
        metadata={"is_pending_skill": True},
    )
    disabled = SkillArtifact(
        name="disabled_symbol_rule",
        kind="atomic_tool_rule_card",
        description="Disabled duplicate.",
        body="When a ticker is explicit, avoid lookup and call remove_stock_from_watchlist directly.",
        status="disabled",
        metadata={"disabled": True},
    )
    candidates = _coarse_skill_candidates_for_clique(
        selected_segments=segments,
        existing_skills=[pending, disabled],
        top_k=4,
    )
    names = [row["name"] for row in candidates]
    assert "pending_symbol_rule" in names
    assert "disabled_symbol_rule" not in names


def test_incremental_overlap_state_materializes_same_edges_as_full_build() -> None:
    initial = [
        TraceSegment(segment_id="t1:turn:0", task_id="t1", turn_index=0, text="explicit ids direct compare", error_text="avoid lookup first"),
        TraceSegment(segment_id="t2:turn:0", task_id="t2", turn_index=0, text="explicit ids direct compare", error_text="avoid search first"),
    ]
    later = [
        TraceSegment(segment_id="t3:turn:0", task_id="t3", turn_index=0, text="explicit ids direct compare", error_text="avoid discovery first"),
    ]
    state = build_overlap_graph_state(initial)
    added = update_overlap_graph_state(state, new_segments=later)
    assert added == 1
    incremental = materialize_overlap_graph(state, min_weight=0.01, top_k_per_segment=8)
    full = discover_overlap_graph([*initial, *later], min_weight=0.01, top_k_per_segment=8)
    inc_pairs = {tuple(sorted((edge.source, edge.target))) for edge in incremental.edges}
    full_pairs = {tuple(sorted((edge.source, edge.target))) for edge in full.edges}
    assert inc_pairs == full_pairs


def test_overlap_state_roundtrips_with_incremental_fields() -> None:
    segments = [
        TraceSegment(segment_id="t1:turn:0", task_id="t1", turn_index=0, text="lookup then cancel", error_text="wrong id"),
        TraceSegment(segment_id="t2:turn:0", task_id="t2", turn_index=0, text="lookup then cancel", error_text="wrong order id"),
    ]
    state = build_overlap_graph_state(segments)
    restored = OverlapGraphState.from_dict(state.as_dict())
    graph = materialize_overlap_graph(restored, min_weight=0.01)
    assert len(restored.text_postings) > 0
    assert len(graph.segments) == 2
