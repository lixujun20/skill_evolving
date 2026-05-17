import type { MetricItem } from "../types";
import { formatValue, toneClass } from "../utils";

export function MetricGrid({ metrics }: { metrics?: MetricItem[] }) {
  if (!metrics?.length) return <div className="empty-note">No metrics recorded.</div>;
  return (
    <div className="metric-grid">
      {metrics.map((item, index) => (
        <div className={`metric-card ${toneClass(item.tone)}`} key={`${item.label}-${index}`} title={item.help || ""}>
          <span>{item.label}</span>
          <strong>{formatValue(item.value)}</strong>
        </div>
      ))}
    </div>
  );
}
