import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeChange,
  useNodesState,
} from "@xyflow/react";
import { Database, FlaskConical, Hammer, PackagePlus, Search, TestTube2, Wrench, Zap } from "lucide-react";
import type { FlowNodeModel } from "../viewModel";
import { buildFlowModel, roleLabels, roleOrder } from "../viewModel";
import type { MaintenanceDetail, PlayerTrace } from "../types";
import { cx, toneClass } from "../utils";

interface Props {
  detail: MaintenanceDetail | null;
  pageId: string;
  player: PlayerTrace | null;
  frameIndex: number;
  selectedNodeId: string;
  onSelectNode: (id: string) => void;
  onOpenCard: (roleKey: string, cardIndex: number) => void;
}

const positions: Record<string, { x: number; y: number }> = {
  retriever: { x: 30, y: 160 },
  executor: { x: 30, y: 20 },
  extractor: { x: 320, y: 20 },
  bundle_builder: { x: 610, y: 20 },
  unit_tester: { x: 610, y: 190 },
  refiner: { x: 900, y: 120 },
  skill_store: { x: 1180, y: 120 },
};

export function FlowBoard(props: Props) {
  const model = useMemo(
    () => buildFlowModel(props.detail, props.pageId, props.player, props.frameIndex),
    [props.detail, props.pageId, props.player, props.frameIndex],
  );
  const storageKey = `maintenance-v2-flow:${props.detail?.experiment?.id || "global"}:${props.pageId}`;
  const [nodes, setNodes, onNodesChangeBase] = useNodesState<Node<FlowNodeModel>>(
    model.nodes.map((node): Node<FlowNodeModel> => ({
      id: node.id,
      type: "industrial",
      position: readStoredPosition(storageKey, node.id) || positions[node.roleKey] || { x: 0, y: 0 },
      data: node,
      selected: props.selectedNodeId === node.id,
    })),
  );
  const edges: Edge[] = model.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.label,
    animated: edge.source === props.selectedNodeId || edge.target === props.selectedNodeId,
    className: "industrial-edge",
  }));

  useEffect(() => {
    setNodes((current) => {
      const byId = new Map(current.map((node) => [node.id, node]));
      return model.nodes.map((node): Node<FlowNodeModel> => {
        const existing = byId.get(node.id);
        return {
          id: node.id,
          type: "industrial",
          position: existing?.position || readStoredPosition(storageKey, node.id) || positions[node.roleKey] || { x: 0, y: 0 },
          data: node,
          selected: props.selectedNodeId === node.id,
        };
      });
    });
  }, [model.nodes, props.selectedNodeId, setNodes, storageKey]);

  const onNodesChange = useCallback((changes: NodeChange<Node<FlowNodeModel>>[]) => {
    onNodesChangeBase(changes);
  }, [onNodesChangeBase]);

  const saveNodePosition = useCallback((_: unknown, node: Node<FlowNodeModel>) => {
    writeStoredPosition(storageKey, node.id, node.position);
  }, [storageKey]);

  return (
    <div className="flow-shell">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={{ industrial: IndustrialNode }}
        fitView
        minZoom={0.35}
        maxZoom={1.5}
        onNodeClick={(_, node) => props.onSelectNode(node.id)}
        onNodesChange={onNodesChange}
        onNodeDragStop={saveNodePosition}
        nodesDraggable
        nodesConnectable={false}
      >
        <Background color="#26313b" gap={24} />
        <Controls />
      </ReactFlow>
      <div className="flow-card-strip">
        {model.nodes.flatMap((node) =>
          node.cards.map((entry) => (
            <button
              type="button"
              key={`${node.id}-${entry.index}`}
              className={cx("chip", props.selectedNodeId === node.id && "active")}
              onClick={() => props.onOpenCard(node.roleKey, entry.index)}
            >
              {roleLabels[node.roleKey] || node.label}: {entry.title}
            </button>
          )),
        )}
      </div>
    </div>
  );
}

function readStoredPosition(storageKey: string, nodeId: string): { x: number; y: number } | null {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, { x: number; y: number }>;
    const pos = parsed[nodeId];
    if (Number.isFinite(pos?.x) && Number.isFinite(pos?.y)) return pos;
  } catch {
    return null;
  }
  return null;
}

function writeStoredPosition(storageKey: string, nodeId: string, position: { x: number; y: number }) {
  try {
    const raw = window.localStorage.getItem(storageKey);
    const parsed = raw ? JSON.parse(raw) as Record<string, { x: number; y: number }> : {};
    parsed[nodeId] = position;
    window.localStorage.setItem(storageKey, JSON.stringify(parsed));
  } catch {
    // Local storage is optional; dragging should still work without persistence.
  }
}

function IndustrialNode({ data, selected }: NodeProps<Node<FlowNodeModel>>) {
  const Icon = iconForRole(data.roleKey);
  return (
    <div className={cx("industrial-node", toneClass(data.tone), selected && "selected", data.active && "active-frame")}>
      <Handle type="target" position={Position.Left} />
      <div className="node-top">
        <span className="node-icon"><Icon size={18} /></span>
        <span className="node-title">{data.label}</span>
        <span className="node-count">{data.cards.length}</span>
      </div>
      <div className="node-subtitle">{data.subtitle}</div>
      <div className="node-slots">
        <span>consume</span>
        <span>{slotIn(data.roleKey)}</span>
      </div>
      <div className="node-slots out">
        <span>produce</span>
        <span>{slotOut(data.roleKey)}</span>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function iconForRole(role: string) {
  const map = {
    retriever: Search,
    executor: Zap,
    extractor: PackagePlus,
    bundle_builder: Hammer,
    unit_tester: TestTube2,
    refiner: Wrench,
    skill_store: Database,
  };
  return map[role as keyof typeof map] || FlaskConical;
}

function slotIn(role: string): string {
  const map: Record<string, string> = {
    retriever: "trace + store",
    executor: "retrieval",
    extractor: "trace",
    bundle_builder: "skill",
    unit_tester: "bundle",
    refiner: "test result",
    skill_store: "skill version",
  };
  return map[role] || "payload";
}

function slotOut(role: string): string {
  const map: Record<string, string> = {
    retriever: "candidates",
    executor: "trace",
    extractor: "skill",
    bundle_builder: "bundle",
    unit_tester: "result",
    refiner: "decision",
    skill_store: "repository",
  };
  return map[role] || "payload";
}

export function defaultSelectedRoleForPage(detail: MaintenanceDetail | null, pageId: string): string {
  const model = buildFlowModel(detail, pageId, null, 0);
  return model.nodes.find((node) => node.cards.length)?.id || `role:${roleOrder[1]}`;
}
