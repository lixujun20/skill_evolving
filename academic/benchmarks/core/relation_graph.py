"""Small benchmark-neutral relation graph used by maintenance code."""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Tuple


@dataclass
class RelationNode:
    node_id: str
    node_type: str
    label: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RelationEdge:
    source: str
    target: str
    relation: str
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def key(self) -> Tuple[str, str, str]:
        left, right = sorted([self.source, self.target])
        return (left, right, self.relation)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RelationGraphState:
    """Thread-safe in-memory graph for skill/trace/pending relations."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._nodes: Dict[str, RelationNode] = {}
        self._edges: Dict[Tuple[str, str, str], RelationEdge] = {}

    def upsert_node(self, node: RelationNode) -> None:
        with self._lock:
            existing = self._nodes.get(node.node_id)
            if existing is None:
                self._nodes[node.node_id] = node
                return
            merged = dict(existing.metadata)
            merged.update(node.metadata)
            existing.node_type = node.node_type or existing.node_type
            existing.label = node.label or existing.label
            existing.metadata = merged

    def upsert_edge(self, edge: RelationEdge) -> None:
        with self._lock:
            key = edge.key()
            existing = self._edges.get(key)
            if existing is None:
                self._edges[key] = edge
                return
            merged = dict(existing.metadata)
            merged.update(edge.metadata)
            existing.weight = max(float(existing.weight or 0.0), float(edge.weight or 0.0))
            existing.metadata = merged

    def update(self, *, nodes: Iterable[RelationNode] = (), edges: Iterable[RelationEdge] = ()) -> None:
        with self._lock:
            for node in nodes:
                self.upsert_node(node)
            for edge in edges:
                self.upsert_edge(edge)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "nodes": [node.as_dict() for node in self._nodes.values()],
                "edges": [edge.as_dict() for edge in self._edges.values()],
            }

    def neighbors(self, node_id: str) -> List[RelationNode]:
        with self._lock:
            neighbor_ids = {
                edge.target if edge.source == node_id else edge.source
                for edge in self._edges.values()
                if edge.source == node_id or edge.target == node_id
            }
            return [self._nodes[item] for item in neighbor_ids if item in self._nodes]


def skill_relation_node(skill_name: str, **metadata: Any) -> RelationNode:
    return RelationNode(
        node_id=f"skill:{skill_name}",
        node_type="skill",
        label=skill_name,
        metadata={"skill_name": skill_name, **metadata},
    )


def pending_skill_relation_node(skill_name: str, **metadata: Any) -> RelationNode:
    return RelationNode(
        node_id=f"pending_skill:{skill_name}",
        node_type="pending_skill",
        label=skill_name,
        metadata={"skill_name": skill_name, "status": "pending", **metadata},
    )


def trace_relation_node(task_id: str, segment_id: str = "", **metadata: Any) -> RelationNode:
    suffix = segment_id or task_id
    return RelationNode(
        node_id=f"trace_segment:{suffix}",
        node_type="trace_segment",
        label=suffix,
        metadata={"task_id": task_id, "segment_id": segment_id or suffix, **metadata},
    )
