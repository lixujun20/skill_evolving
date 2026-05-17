import { type CSSProperties, type Dispatch, type SetStateAction, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, BookOpen, GitBranch, Loader2, Maximize2, X } from "lucide-react";
import { fetchExperimentDetail, fetchExperiments, fetchPlayerTrace } from "./api";
import { FileTree } from "./components/FileTree";
import { FlowBoard, defaultSelectedRoleForPage } from "./components/FlowBoard";
import { Inspector } from "./components/Inspector";
import { JsonTree } from "./components/JsonTree";
import { MetricGrid } from "./components/MetricGrid";
import { Player } from "./components/Player";
import type { ExperimentMeta, MaintenanceDetail, PlayerTrace, Selection } from "./types";
import { artifactId, buildFileTree } from "./viewModel";
import { cx, stringifyJson } from "./utils";

const routeMode = window.location.pathname.startsWith("/method-tests") ? "method" : "maintenance";

export function App() {
  const [experiments, setExperiments] = useState<ExperimentMeta[]>([]);
  const [selectedExperimentId, setSelectedExperimentId] = useState("");
  const [detail, setDetail] = useState<MaintenanceDetail | null>(null);
  const [player, setPlayer] = useState<PlayerTrace | null>(null);
  const [selection, setSelection] = useState<Selection>({ kind: "overview", id: "overview" });
  const [activePageId, setActivePageId] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState("role:executor");
  const [frameIndex, setFrameIndex] = useState(0);
  const [query, setQuery] = useState("");
  const [sidebarWidth, setSidebarWidth] = useState(() => readStoredSidebarWidth());
  const [resizingSidebar, setResizingSidebar] = useState(false);
  const [experimentsLoading, setExperimentsLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [playerLoading, setPlayerLoading] = useState(false);
  const [loadTimers, setLoadTimers] = useState<Record<string, number>>({});
  const [error, setError] = useState("");
  const [modal, setModal] = useState<{ title: string; payload: unknown } | null>(null);
  const gridRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    setExperimentsLoading(true);
    startTimer("experiments", setLoadTimers);
    fetchExperiments()
      .then((payload) => {
        if (!alive) return;
        const rows = payload.experiments || [];
        setExperiments(rows);
        const preferred = rows.find((item) => routeMode === "method" ? item.kind === "method_validation" : item.kind !== "method_validation") || rows[0];
        setSelectedExperimentId(preferred?.id || "");
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setExperimentsLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedExperimentId) return;
    let alive = true;
    setDetailLoading(true);
    setPlayer(null);
    startTimer("detail", setLoadTimers);
    setError("");
    fetchExperimentDetail(selectedExperimentId)
      .then((nextDetail) => {
        if (!alive) return;
        setDetail(nextDetail);
        const firstPage = preferredInitialPageId(nextDetail);
        setActivePageId(firstPage || "");
        setSelection(firstPage ? { kind: "page", id: firstPage } : { kind: "overview", id: "overview" });
        setSelectedNodeId(defaultSelectedRoleForPage(nextDetail, firstPage || "overview"));
        setFrameIndex(0);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setDetailLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [selectedExperimentId]);

  const tree = useMemo(() => buildFileTree(detail), [detail]);
  const pageId = activePageId || detail?.pages?.[0]?.page_id || "overview";
  const page = detail?.pages?.find((item) => item.page_id === pageId);
  const taskMessages = useMemo(() => extractTaskMessages(page), [page]);
  const scopedPlayer = useMemo(() => scopePlayerToPage(player, page), [player, page]);

  useEffect(() => {
    if (!resizingSidebar) return;
    const onMove = (event: MouseEvent) => {
      const rect = gridRef.current?.getBoundingClientRect();
      const left = rect?.left ?? 0;
      const next = Math.max(240, Math.min(520, Math.round(event.clientX - left - 12)));
      setSidebarWidth(next);
      window.localStorage.setItem("maintenance-v2-sidebar-width", String(next));
    };
    const onUp = () => setResizingSidebar(false);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    document.body.classList.add("resizing-sidebar");
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.classList.remove("resizing-sidebar");
    };
  }, [resizingSidebar]);

  useEffect(() => {
    if (!selectedExperimentId || !detail) return;
    let alive = true;
    const taskId = taskIdFromPage(page);
    setPlayer(null);
    setPlayerLoading(true);
    startTimer("player", setLoadTimers);
    fetchPlayerTrace(selectedExperimentId, playerQueryForPage(page, taskId))
      .then((nextPlayer) => {
        if (!alive) return;
        setPlayer(nextPlayer);
        setFrameIndex(0);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setPlayerLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [selectedExperimentId, detail, page]);

  useEffect(() => {
    const frameCount = scopedPlayer?.frames?.length || 0;
    if (!frameCount && frameIndex !== 0) {
      setFrameIndex(0);
      return;
    }
    if (frameCount && frameIndex >= frameCount) {
      setFrameIndex(0);
    }
  }, [scopedPlayer, frameIndex]);

  const handleSelect = useCallback((next: Selection) => {
    setSelection(next);
    if (next.kind === "page") {
      setActivePageId(next.id);
      setSelectedNodeId(defaultSelectedRoleForPage(detail, next.id));
      setFrameIndex(0);
    }
  }, [detail]);

  const openRoleCard = (roleKey: string, cardIndex: number) => {
    setSelectedNodeId(`role:${roleKey}`);
    setSelection({ kind: "flow_card", id: `card:${cardIndex}` });
  };

  const selectFrame = (index: number) => {
    setFrameIndex(index);
  };

  return (
    <div className="maintenance-v2">
      <TopBar mode={routeMode} />
      <div
        className="lab-grid"
        ref={gridRef}
        style={{ "--sidebar-width": `${sidebarWidth}px` } as CSSProperties}
      >
        <FileTree
          experiments={experiments}
          selectedExperimentId={selectedExperimentId}
          query={query}
          mode={routeMode}
          tree={tree}
          selected={selection}
          onQueryChange={setQuery}
          onSelectExperiment={setSelectedExperimentId}
          onSelect={handleSelect}
        />
        <div
          className="sidebar-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize navigation"
          onMouseDown={(event) => {
            event.preventDefault();
            setResizingSidebar(true);
          }}
        />
        <main className="lab-main">
          {(experimentsLoading || detailLoading) && !detail ? (
            <LoadingState
              title={experimentsLoading ? "Loading experiment list" : "Loading detail projection"}
              object={experimentsLoading ? routeMode : selectedExperimentId}
              elapsedMs={loadTimers[experimentsLoading ? "experiments" : "detail"] || 0}
              hint={experimentsLoading ? "Scanning result folders." : "First load parses the result JSON; repeat loads use the in-process projection cache."}
            />
          ) : error ? (
            <StatePanel icon="error" title="Unable to load maintenance data" detail={error} />
          ) : detail ? (
            <>
              <section className="context-bar">
                <div>
                  <div className="eyebrow">{detail.kind}</div>
                  <h1>{detail.experiment.title || detail.experiment.id}</h1>
                  <p>{detail.experiment.subtitle || detail.experiment.folder_name || ""}</p>
                </div>
                <div className="context-actions">
                  <a href="/refactor-graph" className="text-button"><GitBranch size={16} /> Refactor Graph</a>
                  <a href="/maintenance-docs" className="text-button"><BookOpen size={16} /> Docs</a>
                  <button type="button" className="icon-button" onClick={() => setModal({ title: "Detail JSON", payload: detail })} title="Open detail JSON">
                    <Maximize2 size={17} />
                  </button>
                </div>
              </section>
              {selection.kind === "overview" ? (
                <Overview
                  detail={detail}
                  player={player}
                  playerLoading={playerLoading}
                  elapsedMs={loadTimers.player || 0}
                  onOpenPayload={(title, payload) => setModal({ title, payload })}
                />
              ) : (
                <>
                  <MetricGrid metrics={page?.summary_metrics} />
                  <PageNotice page={page} taskId={taskIdFromPage(page)} />
                  <TaskQueryPanel messages={taskMessages} taskId={taskIdFromPage(page)} />
                  {playerLoading && !scopedPlayer ? (
                    <InlineLoading
                      title="Loading scoped player frames"
                      object={taskIdFromPage(page) || pageId}
                      elapsedMs={loadTimers.player || 0}
                      hint="Task pages request only matching task and phase debug events, so the flow board and inspector stay usable while frames load."
                    />
                  ) : (
                    <Player player={scopedPlayer} frameIndex={frameIndex} onFrameIndex={selectFrame} />
                  )}
                  <section className="board-header">
                    <div>
                      <div className="eyebrow">Current Page</div>
                      <h2>{page?.title || pageId}</h2>
                    </div>
                    <span className={cx("status-pill", page?.status_tone ? `tone-${page.status_tone}` : "tone-neutral")}>{page?.label || "page"}</span>
                  </section>
                  <FlowBoard
                    detail={detail}
                    pageId={pageId}
                    player={scopedPlayer}
                    frameIndex={frameIndex}
                    selectedNodeId={selectedNodeId}
                    onSelectNode={(nodeId) => {
                      setSelectedNodeId(nodeId);
                      if (selection.kind !== "page") setSelection({ kind: "page", id: pageId });
                    }}
                    onOpenCard={openRoleCard}
                  />
                </>
              )}
            </>
          ) : (
            <StatePanel icon="error" title="No experiments found" detail="The maintenance results directory did not contain loadable result JSON files." />
          )}
        </main>
        <Inspector
          detail={detail}
          player={scopedPlayer}
          pageId={pageId}
          selected={selection}
          selectedNodeId={selectedNodeId}
          frameIndex={frameIndex}
          onOpenPayload={(title, payload) => setModal({ title, payload })}
        />
      </div>
      {modal && <PayloadModal title={modal.title} payload={modal.payload} onClose={() => setModal(null)} />}
    </div>
  );
}

function TopBar({ mode }: { mode: "maintenance" | "method" }) {
  return (
    <nav className="top-nav topbar">
      <a href="/" className="nav-link">Skill Explorer</a>
      <a href="/replay" className="nav-link">Replay Lab</a>
      <a href="/execute" className="nav-link">Execute Lab</a>
      <a className={`nav-link ${mode === "maintenance" ? "active" : ""}`} href="/maintenance" data-section-link="maintenance">Maintenance Lab</a>
      <a className={`nav-link ${mode === "method" ? "active" : ""}`} href="/method-tests" data-section-link="method_tests">Method Tests</a>
      <a href="/refactor-graph" className="nav-link">Refactor Graph</a>
      <a href="/maintenance-docs" className="nav-link">Maintenance Docs</a>
    </nav>
  );
}

function preferredInitialPageId(detail: MaintenanceDetail): string {
  return detail.pages?.find((item) => item.page_id.startsWith("train_task_"))?.page_id || detail.pages?.[0]?.page_id || "";
}

function scopePlayerToPage(player: PlayerTrace | null, page: MaintenanceDetail["pages"][number] | undefined): PlayerTrace | null {
  if (!player || !page) return player;
  const taskId = taskIdFromPage(page);
  if (!taskId) return player;
  const frames = (player.frames || []).filter((frame) => {
    const event = (frame.delta || {}).event as Record<string, unknown> | undefined;
    return String(event?.task_id || "") === taskId && phaseMatchesPage(page.page_id, event);
  }).map((frame, index) => ({ ...frame, index }));
  return { ...player, title: `${player.title} / ${page.label}`, frames };
}

function phaseMatchesPage(pageId: string, event: Record<string, unknown> | undefined): boolean {
  const phase = String(event?.phase || "");
  if (pageId.startsWith("train")) {
    return phase === "train" || phase === "extract" || phase === "";
  }
  if (pageId.startsWith("replay")) {
    return phase.includes("replay") || phase === "final_test_rollout";
  }
  return true;
}

function playerQueryForPage(page: MaintenanceDetail["pages"][number] | undefined, taskId: string): { taskId?: string; phases?: string[]; compact?: boolean } {
  if (!page || !taskId) return { compact: true };
  if (page.page_id.startsWith("train")) return { taskId, phases: ["train", "extract"] };
  if (page.page_id.startsWith("replay")) return { taskId, phases: ["integration_replay_before_refine", "post_refine_replay", "final_test_rollout"] };
  return {};
}

function taskIdFromPage(page: MaintenanceDetail["pages"][number] | undefined): string {
  if (!page) return "";
  const title = page.title || "";
  const match = title.match(/\|\s*([^|]+)$/);
  const fromTitle = match?.[1]?.trim();
  if (fromTitle && fromTitle !== "unknown_task") return fromTitle;
  for (const card of page.flow_cards || []) {
    const run = card.run as Record<string, unknown> | undefined;
    const taskId = run?.task_id;
    if (typeof taskId === "string" && taskId.trim()) return taskId.trim();
    if (typeof card.subtitle === "string" && card.subtitle.trim()) return card.subtitle.trim();
  }
  return "";
}

function startTimer(key: string, setTimers: Dispatch<SetStateAction<Record<string, number>>>) {
  const started = performance.now();
  setTimers((current) => ({ ...current, [key]: 0 }));
  const tick = () => {
    setTimers((current) => ({ ...current, [key]: performance.now() - started }));
  };
  tick();
}

type TaskMessage = { role: string; content: string; turn: number };

function extractTaskMessages(page: MaintenanceDetail["pages"][number] | undefined): TaskMessage[] {
  for (const card of page?.flow_cards || []) {
    const run = card.run as Record<string, unknown> | undefined;
    const detail = (run?.detail || card.detail) as Record<string, unknown> | undefined;
    const task = detail?.task as Record<string, unknown> | undefined;
    const question = task?.question;
    if (!Array.isArray(question)) continue;
    const rows: TaskMessage[] = [];
    question.forEach((turn, turnIndex) => {
      if (Array.isArray(turn)) {
        turn.forEach((message) => {
          if (!message || typeof message !== "object") return;
          const item = message as Record<string, unknown>;
          rows.push({
            turn: turnIndex,
            role: String(item.role || "message"),
            content: String(item.content || ""),
          });
        });
      } else if (turn && typeof turn === "object") {
        const item = turn as Record<string, unknown>;
        rows.push({
          turn: turnIndex,
          role: String(item.role || "message"),
          content: String(item.content || ""),
        });
      } else if (typeof turn === "string") {
        rows.push({ turn: turnIndex, role: "user", content: turn });
      }
    });
    if (rows.length) return rows;
  }
  return [];
}

function readStoredSidebarWidth(): number {
  const raw = window.localStorage.getItem("maintenance-v2-sidebar-width");
  const value = Number(raw);
  return Number.isFinite(value) ? Math.max(240, Math.min(520, value)) : 300;
}

function TaskQueryPanel({ messages, taskId }: { messages: TaskMessage[]; taskId: string }) {
  if (!messages.length) return null;
  const firstUser = messages.find((message) => message.role === "user") || messages[0];
  return (
    <details className="task-query-panel" open>
      <summary>
        <span>
          <span className="eyebrow">Task Query</span>
          <strong>{taskId || "Current task"}</strong>
        </span>
        <small>{firstUser.content.slice(0, 160)}</small>
      </summary>
      <div className="task-message-list">
        {messages.map((message, index) => (
          <details className="text-io-block" key={`${message.turn}-${message.role}-${index}`} open={index === 0}>
            <summary>turn {message.turn + 1} · {message.role}</summary>
            <pre>{message.content || "No content recorded."}</pre>
          </details>
        ))}
      </div>
    </details>
  );
}

function PageNotice({ page, taskId }: { page: MaintenanceDetail["pages"][number] | undefined; taskId: string }) {
  const note = page?.semantic_note;
  if (note) {
    return <div className="page-notice">{note}</div>;
  }
  if (!taskId) return null;
  return (
    <div className="page-notice subtle">
      Bundle, test, and refine events with task_id=None are experiment-level records, so they may appear as linked cards instead of in this task-scoped player timeline.
    </div>
  );
}

function Overview({
  detail,
  player,
  playerLoading,
  elapsedMs,
  onOpenPayload,
}: {
  detail: MaintenanceDetail;
  player: PlayerTrace | null;
  playerLoading: boolean;
  elapsedMs: number;
  onOpenPayload: (title: string, payload: unknown) => void;
}) {
  return (
    <div className="overview-grid">
      <section className="overview-section">
        <div className="section-title">
          <div>
            <div className="eyebrow">Overview</div>
            <h2>Experiment Metrics</h2>
          </div>
        </div>
        <MetricGrid metrics={detail.overview_metrics} />
      </section>
      <section className="overview-section">
        <div className="section-title">
          <div>
            <div className="eyebrow">Player</div>
            <h2>State Machine</h2>
          </div>
        </div>
        {playerLoading && !player ? (
          <InlineLoading
            title="Loading global trace"
            object={detail.experiment.id}
            elapsedMs={elapsedMs}
            hint="Overview requests the unscoped player, which can be slow on large debug logs. Task pages use scoped player projections."
          />
        ) : (
          <>
            <Player player={player} frameIndex={0} onFrameIndex={() => undefined} />
            <JsonTree
              value={{
                run_id: player?.run_id,
                frames: player?.frames?.length || 0,
                snapshot_mode: player?.snapshot_mode,
                source_mode: player?.source_mode,
              }}
              label="trace"
            />
          </>
        )}
      </section>
      <section className="overview-section wide">
        <div className="section-title">
          <div>
            <div className="eyebrow">Artifacts</div>
            <h2>Skill Store</h2>
          </div>
        </div>
        <div className="artifact-grid">
          {(detail.artifacts || []).map((artifact) => (
            <button type="button" className="artifact-card" key={artifactId(artifact)} onClick={() => onOpenPayload(String(artifact.name || "Artifact"), artifact)}>
              <span>{artifact.kind || "skill"}</span>
              <strong>{artifact.name || "Unnamed artifact"}</strong>
              <small>{artifact.description || "No description recorded."}</small>
            </button>
          ))}
          {!detail.artifacts?.length && <div className="empty-note">No skill artifacts recorded for this experiment.</div>}
        </div>
      </section>
    </div>
  );
}

function PayloadModal({ title, payload, onClose }: { title: string; payload: unknown; onClose: () => void }) {
  const [raw, setRaw] = useState(false);
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="payload-modal">
        <header>
          <div>
            <div className="eyebrow">Payload</div>
            <h2>{title}</h2>
          </div>
          <div className="modal-actions">
            <button type="button" className="text-button" onClick={() => setRaw((next) => !next)}>{raw ? "Tree" : "Raw"}</button>
            <button type="button" className="icon-button" onClick={onClose} title="Close"><X size={18} /></button>
          </div>
        </header>
        <div className="modal-content">
          {raw ? <pre className="raw-json">{stringifyJson(payload)}</pre> : <JsonTree value={payload} label="root" defaultOpen />}
        </div>
      </div>
    </div>
  );
}

function StatePanel({ icon, title, detail }: { icon: "loading" | "error"; title: string; detail: string }) {
  return (
    <div className="state-panel">
      {icon === "loading" ? <Loader2 className="spin" size={28} /> : <AlertTriangle size={28} />}
      <h2>{title}</h2>
      <p>{detail}</p>
    </div>
  );
}

function LoadingState({ title, object, elapsedMs, hint }: { title: string; object: string; elapsedMs: number; hint: string }) {
  return (
    <div className="state-panel">
      <Loader2 className="spin" size={28} />
      <h2>{title}</h2>
      <p>{object}</p>
      <LoadingMeta elapsedMs={elapsedMs} hint={hint} />
    </div>
  );
}

function InlineLoading({ title, object, elapsedMs, hint }: { title: string; object: string; elapsedMs: number; hint: string }) {
  return (
    <div className="inline-loading">
      <Loader2 className="spin" size={18} />
      <div>
        <strong>{title}</strong>
        <span>{object}</span>
      </div>
      <LoadingMeta elapsedMs={elapsedMs} hint={hint} />
    </div>
  );
}

function LoadingMeta({ elapsedMs, hint }: { elapsedMs: number; hint: string }) {
  const [displayMs, setDisplayMs] = useState(elapsedMs);
  useEffect(() => {
    setDisplayMs(elapsedMs);
    const id = window.setInterval(() => setDisplayMs((value) => value + 1000), 1000);
    return () => window.clearInterval(id);
  }, [elapsedMs]);
  return (
    <small className="loading-meta">
      {formatDuration(displayMs)} elapsed. {hint}
    </small>
  );
}

function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
