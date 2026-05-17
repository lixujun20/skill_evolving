import type { FlowCard, PlayerElement, PlayerFrame, Tone } from "./types";

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value);
    return Math.abs(value) >= 1000 ? value.toLocaleString() : String(value);
  }
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return `${value.length} items`;
  if (typeof value === "object") return `${Object.keys(value as Record<string, unknown>).length} fields`;
  return String(value);
}

export function compactText(value: unknown, limit = 180): string {
  const text = typeof value === "string" ? value : formatValue(value);
  if (text.length <= limit) return text;
  return `${text.slice(0, limit - 3)}...`;
}

export function titleize(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function toneClass(tone?: Tone): string {
  if (!tone) return "tone-neutral";
  if (String(tone).includes("danger")) return "tone-danger";
  if (String(tone).includes("warning")) return "tone-warning";
  if (String(tone).includes("success")) return "tone-success";
  if (String(tone).includes("accent")) return "tone-accent";
  return "tone-neutral";
}

export function roleFromCard(card: FlowCard): string {
  if (card.role) return String(card.role);
  const type = String(card.type || "");
  if (type.includes("retriev")) return "Retriever";
  if (type.includes("executor") || type === "run") return "Executor";
  if (type.includes("extractor")) return "Extractor";
  if (type.includes("bundle")) return "Bundle Builder";
  if (type.includes("test")) return "Unit Tester";
  if (type.includes("refiner") || type.includes("refine")) return "Refiner";
  if (type.includes("store") || type.includes("skill_delta")) return "Skill Store";
  if (type.includes("method")) return "Method Case";
  return titleize(String(card.title || card.type || "Card"));
}

export function roleKey(role: string): string {
  const normalized = role.toLowerCase();
  if (normalized.includes("retriev")) return "retriever";
  if (normalized.includes("executor") || normalized.includes("replay")) return "executor";
  if (normalized.includes("extract")) return "extractor";
  if (normalized.includes("bundle")) return "bundle_builder";
  if (normalized.includes("test")) return "unit_tester";
  if (normalized.includes("refin")) return "refiner";
  if (normalized.includes("store")) return "skill_store";
  return normalized.replace(/\s+/g, "_");
}

export function roleKeyFromFrame(frame?: PlayerFrame): string {
  const role = String(frame?.role_group || frame?.action_kind || "");
  if (role.includes("retriev")) return "retriever";
  if (role.includes("executor")) return "executor";
  if (role.includes("extractor")) return "extractor";
  if (role.includes("bundle")) return "bundle_builder";
  if (role.includes("unit") || role.includes("test")) return "unit_tester";
  if (role.includes("refiner") || role.includes("refine")) return "refiner";
  if (role.includes("store")) return "skill_store";
  return "executor";
}

export function mergeFrameElements(
  initial: Record<string, PlayerElement> | undefined,
  frames: PlayerFrame[],
  frameIndex: number,
): Record<string, PlayerElement> {
  const merged: Record<string, PlayerElement> = { ...(initial || {}) };
  for (const frame of frames.slice(0, frameIndex + 1)) {
    Object.assign(merged, frame.elements || {}, frame.element_deltas || {});
  }
  return merged;
}

export function stringifyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
