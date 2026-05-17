import { ChevronDown, ChevronRight, FileJson, Folder, Package, PlaySquare } from "lucide-react";
import { useMemo, useState } from "react";
import type { ExperimentMeta, Selection, TreeNode } from "../types";
import { cx, toneClass } from "../utils";

interface Props {
  experiments: ExperimentMeta[];
  selectedExperimentId: string;
  query: string;
  mode: "maintenance" | "method";
  tree: TreeNode[];
  selected: Selection;
  onQueryChange: (value: string) => void;
  onSelectExperiment: (id: string) => void;
  onSelect: (selection: Selection) => void;
}

export function FileTree(props: Props) {
  const filtered = useMemo(() => {
    const q = props.query.trim().toLowerCase();
    return props.experiments.filter((item) => {
      if (props.mode === "method" && item.kind !== "method_validation") return false;
      if (props.mode === "maintenance" && item.kind === "method_validation") return false;
      if (!q) return true;
      return `${item.title || ""} ${item.id} ${item.kind || ""}`.toLowerCase().includes(q);
    });
  }, [props.experiments, props.mode, props.query]);

  return (
    <aside className="lab-sidebar">
      <div className="sidebar-head">
        <div>
          <div className="eyebrow">Experiments</div>
          <h2>{props.mode === "method" ? "Method Tests" : "Maintenance Lab"}</h2>
        </div>
      </div>
      <input
        className="search-input"
        value={props.query}
        onChange={(event) => props.onQueryChange(event.target.value)}
        placeholder="Search experiment files"
      />
      <div className="experiment-list">
        {filtered.map((item) => (
          <button
            type="button"
            className={cx("experiment-row", item.id === props.selectedExperimentId && "active")}
            key={item.id}
            onClick={() => props.onSelectExperiment(item.id)}
          >
            <span>{item.title || item.folder_name || item.id}</span>
            <small>{item.kind || item.suite_label || "experiment"}</small>
          </button>
        ))}
      </div>
      <div className="tree-scroll">
        {props.tree.map((node) => (
          <TreeNodeView key={node.id} node={node} selected={props.selected} onSelect={props.onSelect} />
        ))}
      </div>
    </aside>
  );
}

function TreeNodeView({ node, selected, onSelect }: { node: TreeNode; selected: Selection; onSelect: (selection: Selection) => void }) {
  const [open, setOpen] = useState(true);
  const isFolder = node.kind === "folder";
  const active = selected.id === node.id && selected.kind === node.kind;
  const Icon = isFolder ? Folder : node.kind === "artifact" ? Package : node.kind === "frame" ? PlaySquare : FileJson;

  if (isFolder) {
    return (
      <div className="tree-group">
        <button type="button" className="tree-row folder" onClick={() => setOpen((next) => !next)}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <Icon size={15} />
          <span>{node.label}</span>
        </button>
        {open && (
          <div className="tree-children">
            {(node.children || []).map((child) => (
              <TreeNodeView key={child.id} node={child} selected={selected} onSelect={onSelect} />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <button
      type="button"
      className={cx("tree-row", active && "active", toneClass(node.tone))}
      onClick={() => onSelect({ kind: node.kind, id: node.id } as Selection)}
      title={node.subtitle}
    >
      <span className="tree-spacer" />
      <Icon size={15} />
      <span>{node.label}</span>
    </button>
  );
}
