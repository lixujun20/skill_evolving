import type { ArtifactCard, MaintenanceDetail, PlayerTrace, TreeNode } from "./types";
import { roleFromCard, roleKey } from "./utils";

const GLOBAL_PAGE_IDS = new Set(["algorithm", "refine", "test"]);

export function buildFileTree(detail: MaintenanceDetail | null): TreeNode[] {
  if (!detail) return [];
  const trainPages: TreeNode[] = [];
  const replayPages: TreeNode[] = [];
  const globalPages: TreeNode[] = [];
  const methodPages: TreeNode[] = [];
  const roundPages: TreeNode[] = [];

  for (const page of detail.pages || []) {
    const id = page.page_id;
    const node: TreeNode = {
      id,
      label: page.label || page.title || id,
      subtitle: page.title,
      tone: page.status_tone,
      kind: "page",
    };
    if (detail.kind === "method_validation" || id.includes("method")) {
      methodPages.push(node);
    } else if (id.startsWith("train")) {
      trainPages.push(node);
    } else if (id.startsWith("replay")) {
      replayPages.push(node);
    } else if (GLOBAL_PAGE_IDS.has(id) || id.includes("algorithm") || id.includes("refine") || id.includes("test")) {
      globalPages.push(node);
    } else {
      roundPages.push(node);
    }
  }

  const artifactNodes = (detail.artifacts || []).map((artifact) => ({
    id: artifactId(artifact),
    label: artifact.name || "Skill Artifact",
    subtitle: `${artifact.kind || "skill"} v${artifact.version || 1}`,
    tone: artifact.status === "disabled" ? "warning" : "success",
    kind: "artifact" as const,
  }));

  const overviewNode: TreeNode = { id: "overview", label: "Overview", kind: "overview" };
  const children: TreeNode[] = [
    overviewNode,
    folder("Train Tasks", trainPages),
    folder("Replay Tasks", replayPages),
    folder("Rounds", roundPages),
    folder("Global Pipeline Pages", globalPages),
    folder("Method Cases", methodPages),
    folder("Artifacts", artifactNodes),
  ].filter((node) => node.kind !== "folder" || (node.children && node.children.length > 0));

  return [
    {
      id: "experiment",
      label: detail.experiment.title || detail.experiment.id,
      kind: "folder",
      children,
    },
  ];
}

function folder(label: string, children: TreeNode[]): TreeNode {
  return { id: label.toLowerCase().replace(/\s+/g, "_"), label, kind: "folder", children };
}

export function artifactId(artifact: ArtifactCard): string {
  return String(artifact.name || artifact.bundle_id || "artifact");
}

export interface FlowNodeModel {
  [key: string]: unknown;
  id: string;
  roleKey: string;
  label: string;
  subtitle: string;
  tone: string;
  cards: Array<{ index: number; title: string; card: unknown }>;
  active?: boolean;
}

export interface FlowModel {
  nodes: FlowNodeModel[];
  edges: Array<{ id: string; source: string; target: string; label: string }>;
}

export const roleOrder = [
  "retriever",
  "executor",
  "extractor",
  "bundle_builder",
  "unit_tester",
  "refiner",
  "skill_store",
];

export const roleLabels: Record<string, string> = {
  retriever: "Retriever",
  executor: "Executor",
  extractor: "Extractor",
  bundle_builder: "Bundle Builder",
  unit_tester: "Unit Tester",
  refiner: "Refiner",
  skill_store: "Skill Store",
};

export function buildFlowModel(detail: MaintenanceDetail | null, pageId: string, player: PlayerTrace | null, frameIndex: number): FlowModel {
  const buckets: Record<string, FlowNodeModel> = {};
  for (const key of roleOrder) {
    buckets[key] = {
      id: `role:${key}`,
      roleKey: key,
      label: roleLabels[key],
      subtitle: "No payload for current page",
      tone: "neutral",
      cards: [],
    };
  }
  const page = detail?.pages?.find((item) => item.page_id === pageId);
  for (const [index, card] of (page?.flow_cards || []).entries()) {
    const role = roleKey(roleFromCard(card));
    const key = buckets[role] ? role : "executor";
    buckets[key].cards.push({
      index,
      title: String(card.title || card.type || `Card ${index + 1}`),
      card,
    });
    buckets[key].subtitle = String(card.subtitle || card.title || card.type || "Available");
    buckets[key].tone = String(card.tone || "accent");
  }

  const activeRole = player?.frames?.[frameIndex] ? frameRoleKey(player.frames[frameIndex]) : "";
  if (buckets[activeRole]) buckets[activeRole].active = true;

  return {
    nodes: roleOrder.map((key) => buckets[key]),
    edges: [
      { id: "trace-to-retriever", source: "role:executor", target: "role:retriever", label: "query" },
      { id: "retriever-to-executor", source: "role:retriever", target: "role:executor", label: "skills" },
      { id: "executor-to-extractor", source: "role:executor", target: "role:extractor", label: "trace" },
      { id: "extractor-to-bundle", source: "role:extractor", target: "role:bundle_builder", label: "skill" },
      { id: "bundle-to-test", source: "role:bundle_builder", target: "role:unit_tester", label: "bundle" },
      { id: "test-to-refiner", source: "role:unit_tester", target: "role:refiner", label: "result" },
      { id: "refiner-to-store", source: "role:refiner", target: "role:skill_store", label: "version" },
      { id: "store-to-retriever", source: "role:skill_store", target: "role:retriever", label: "index" },
    ],
  };
}

function frameRoleKey(frame: { role_group?: unknown; action_kind?: unknown }): string {
  const role = `${frame.role_group || frame.action_kind || ""}`;
  if (role.includes("retriev")) return "retriever";
  if (role.includes("executor")) return "executor";
  if (role.includes("extractor")) return "extractor";
  if (role.includes("bundle")) return "bundle_builder";
  if (role.includes("unit") || role.includes("test")) return "unit_tester";
  if (role.includes("refiner") || role.includes("refine")) return "refiner";
  if (role.includes("store")) return "skill_store";
  return "";
}
