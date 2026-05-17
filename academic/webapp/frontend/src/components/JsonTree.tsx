import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { formatValue } from "../utils";

interface JsonTreeProps {
  value: unknown;
  label?: string;
  defaultOpen?: boolean;
  depth?: number;
}

export function JsonTree({ value, label = "payload", defaultOpen = false, depth = 0 }: JsonTreeProps) {
  const expandable = value !== null && typeof value === "object";
  const [open, setOpen] = useState(defaultOpen || depth < 1);

  if (!expandable) {
    return (
      <div className="json-row leaf">
        <span className="json-key">{label}</span>
        <span className="json-value">{formatPrimitive(value)}</span>
      </div>
    );
  }

  const entries = Array.isArray(value)
    ? value.map((item, index) => [String(index), item] as const)
    : Object.entries(value as Record<string, unknown>);

  return (
    <div className="json-node">
      <button type="button" className="json-row json-toggle" onClick={() => setOpen((next) => !next)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="json-key">{label}</span>
        <span className="json-summary">{Array.isArray(value) ? `${entries.length} items` : `${entries.length} fields`}</span>
      </button>
      {open && (
        <div className="json-children">
          {entries.length ? (
            entries.map(([key, child]) => (
              <JsonTree key={key} value={child} label={key} depth={depth + 1} />
            ))
          ) : (
            <div className="json-empty">empty</div>
          )}
        </div>
      )}
    </div>
  );
}

function formatPrimitive(value: unknown): string {
  if (typeof value === "string") return value;
  return formatValue(value);
}
