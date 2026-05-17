export type Tone = "success" | "warning" | "danger" | "accent" | "neutral" | string;

export interface ExperimentMeta {
  id: string;
  suite_id?: string;
  suite_label?: string;
  title?: string;
  folder_name?: string;
  kind?: string;
  folder_path?: string;
  result_path?: string;
  readme_path?: string;
  suite_readme_path?: string;
  role_log_path?: string;
  role_log_exists?: boolean;
  role_log_count?: number;
  subtitle?: string;
  passed?: boolean | null;
}

export interface MetricItem {
  label: string;
  value: unknown;
  tone?: Tone;
  help?: string;
}

export interface FlowCard {
  type?: string;
  title?: string;
  subtitle?: string;
  tone?: Tone;
  role?: string;
  detail?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface PageModel {
  page_id: string;
  label: string;
  title: string;
  subtitle?: string;
  semantic_note?: string;
  status_tone?: Tone;
  summary_metrics?: MetricItem[];
  flow_cards?: FlowCard[];
}

export interface ArtifactCard {
  name?: string;
  kind?: string;
  description?: string;
  status?: string;
  version?: number;
  version_kind?: string;
  stale?: boolean;
  body?: string;
  interface?: Record<string, unknown>;
  bundle?: Record<string, unknown>;
  bundle_counts?: Record<string, number>;
  lineage?: Record<string, unknown>;
  history?: unknown[];
  [key: string]: unknown;
}

export interface MaintenanceDetail {
  kind: string;
  experiment: ExperimentMeta;
  overview_metrics: MetricItem[];
  files: Record<string, string>;
  artifacts: ArtifactCard[];
  readme_text?: string;
  docs?: Array<Record<string, unknown>>;
  pages: PageModel[];
}

export interface PlayerElement {
  element_id?: string;
  kind?: string;
  label?: string;
  icon?: string;
  state?: Record<string, unknown>;
  position?: { x?: number; y?: number };
  [key: string]: unknown;
}

export interface PlayerFrame {
  frame_id: string;
  index: number;
  name: string;
  action_kind?: string;
  role_group?: string;
  summary?: string;
  changed_elements?: string[];
  highlighted_elements?: string[];
  consumed_slots?: string[];
  produced_slots?: string[];
  condition_result?: string;
  delta?: Record<string, unknown>;
  elements?: Record<string, PlayerElement>;
  element_deltas?: Record<string, PlayerElement>;
  is_marker_candidate?: boolean;
  [key: string]: unknown;
}

export interface PlayerTrace {
  run_id: string;
  kind: string;
  title: string;
  terminal?: boolean;
  current_phase?: string;
  snapshot_mode?: string;
  source_mode?: string;
  initial_elements?: Record<string, PlayerElement>;
  elements?: Record<string, PlayerElement>;
  frames: PlayerFrame[];
}

export interface ExperimentsResponse {
  experiments: ExperimentMeta[];
}

export type Selection =
  | { kind: "overview"; id: "overview" }
  | { kind: "page"; id: string }
  | { kind: "artifact"; id: string }
  | { kind: "frame"; id: string }
  | { kind: "flow_card"; id: string };

export interface TreeNode {
  id: string;
  label: string;
  kind: Selection["kind"] | "folder";
  subtitle?: string;
  tone?: Tone;
  children?: TreeNode[];
}
