import { ExternalLink, GitBranch, Maximize2 } from "lucide-react";
import type { ArtifactCard, FlowCard, MaintenanceDetail, PlayerFrame, PlayerTrace, Selection } from "../types";
import { JsonTree } from "./JsonTree";
import { MetricGrid } from "./MetricGrid";
import { artifactId, buildFlowModel, roleLabels } from "../viewModel";
import { compactText, formatValue, roleKeyFromFrame, stringifyJson, titleize, toneClass } from "../utils";

interface Props {
  detail: MaintenanceDetail | null;
  player: PlayerTrace | null;
  pageId: string;
  selected: Selection;
  selectedNodeId: string;
  frameIndex: number;
  onOpenPayload: (title: string, payload: unknown) => void;
}

export function Inspector({ detail, player, pageId, selected, selectedNodeId, frameIndex, onOpenPayload }: Props) {
  if (!detail) {
    return <aside className="inspector"><div className="empty-note">Select an experiment.</div></aside>;
  }
  const page = detail.pages.find((item) => item.page_id === pageId);
  const frame = player?.frames?.[frameIndex];
  const selectedCard = selected.kind === "flow_card" ? cardForSelection(page, selected.id) : undefined;
  const selectedArtifact = selected.kind === "artifact" ? detail.artifacts.find((item) => artifactId(item) === selected.id) : undefined;
  const body = selected.kind === "artifact"
    ? <ArtifactInspector artifact={selectedArtifact} onOpenPayload={onOpenPayload} />
    : selected.kind === "flow_card"
      ? <CardInspector card={selectedCard} detail={detail} page={page} onOpenPayload={onOpenPayload} />
      : selected.kind === "page"
        ? <PageInspector page={page} detail={detail} pageId={pageId} selectedNodeId={selectedNodeId} onOpenPayload={onOpenPayload} />
        : frame
          ? <FrameInspector frame={frame} detail={detail} page={page} onOpenPayload={onOpenPayload} />
          : <OverviewInspector detail={detail} onOpenPayload={onOpenPayload} />;

  return (
    <aside className="inspector">
      <div className="inspector-head">
        <div className="eyebrow">Current Event</div>
        <h2>{selected.kind === "flow_card" ? selectedCard?.title || "Role Detail" : frame ? frameTitle(frame) : titleForSelection(selected, page?.title, frame)}</h2>
      </div>
      {body}
    </aside>
  );
}

function OverviewInspector({ detail, onOpenPayload }: { detail: MaintenanceDetail; onOpenPayload: (title: string, payload: unknown) => void }) {
  return (
    <div className="inspector-body">
      <MetricGrid metrics={detail.overview_metrics} />
      <div className="summary-box">
        <span>Result file</span>
        <strong>{detail.files?.result_json || "unavailable"}</strong>
      </div>
      <button type="button" className="wide-button" onClick={() => onOpenPayload("Experiment Detail", detail)}>
        <Maximize2 size={16} /> Raw detail tree
      </button>
    </div>
  );
}

function PageInspector({
  page,
  detail,
  pageId,
  selectedNodeId,
  onOpenPayload,
}: {
  page: MaintenanceDetail["pages"][number] | undefined;
  detail: MaintenanceDetail;
  pageId: string;
  selectedNodeId: string;
  onOpenPayload: (title: string, payload: unknown) => void;
}) {
  const roleKey = selectedNodeId.replace("role:", "");
  const model = buildFlowModel(detail, pageId, null, 0);
  const node = model.nodes.find((item) => item.roleKey === roleKey);
  return (
    <div className="inspector-body">
      <div className={`summary-box ${toneClass(node?.tone)}`}>
        <span>{roleLabels[roleKey] || titleize(roleKey)}</span>
        <strong>{node?.cards.length || 0} text records</strong>
        <small>{node?.cards.length ? "Showing actual recorded text input/output where available." : "No cards for this role on this page."}</small>
      </div>
      {node?.cards.length ? node.cards.map((entry) => (
        <RoleTextCard key={entry.index} roleKey={roleKey} card={entry.card as FlowCard} onOpenPayload={onOpenPayload} />
      )) : null}
    </div>
  );
}

function ArtifactInspector({ artifact, onOpenPayload }: { artifact: ArtifactCard | undefined; onOpenPayload: (title: string, payload: unknown) => void }) {
  if (!artifact) return <div className="empty-note">Artifact is unavailable.</div>;
  const meta = asRecord(artifact.metadata);
  const iface = asRecord(artifact.interface);
  const lineage = asRecord(artifact.lineage);
  const testSummary = asRecord(artifact.test_summary || meta.test_summary);
  return (
    <div className="inspector-body">
      <section className="artifact-object-panel">
        <div className="structured-panel-head">
          <span>{artifact.kind || "artifact"} v{formatValue(artifact.version)}</span>
          <strong>{artifact.name || "Unnamed artifact"}</strong>
        </div>
        <p>{artifact.description || "No description recorded."}</p>
        <div className="mini-metric-grid">
          <MiniMetric label="status" value={artifact.status || "unknown"} />
          <MiniMetric label="stale" value={String(Boolean(artifact.stale))} />
          <MiniMetric label="version kind" value={artifact.version_kind || "-"} />
          <MiniMetric label="usage" value={formatValue(artifact.usage_count)} />
        </div>
        <KeyValueList
          rows={[
            ["source tasks", asArray(meta.source_task_ids || artifact.source_task_ids).join(", ") || "-"],
            ["allowed tools", asArray(meta.allowed_tools || artifact.allowed_tools).join(", ") || "-"],
            ["intent keywords", asArray(meta.intent_keywords || artifact.intent_keywords).join(", ") || "-"],
            ["dependencies", asArray(artifact.dependencies).join(", ") || "-"],
            ["dependency pins", textFromValue(artifact.dependency_pins || []) || "-"],
          ]}
        />
      </section>
      <TextBlock title="Description" text={String(artifact.description || "No description recorded.")} defaultOpen />
      <TextBlock title="Artifact body" text={String(artifact.body || "No artifact body recorded.")} defaultOpen />
      <section className="structured-panel">
        <div className="structured-panel-head"><span>Interface Contract</span><strong>{String(artifact.interface_summary || iface.summary || "recorded")}</strong></div>
        <KeyValueList
          rows={[
            ["summary", artifact.interface_summary || iface.summary || "-"],
            ["inputs", textFromValue(iface.inputs || iface.input_schema || iface.parameters || {}) || "-"],
            ["outputs", textFromValue(iface.outputs || iface.output_schema || {}) || "-"],
            ["invocation", textFromValue(iface.invocation_contract || iface.call_contract || {}) || "-"],
          ]}
        />
      </section>
      <BundleStructuredView bundle={asRecord(artifact.bundle)} />
      <section className="structured-panel">
        <div className="structured-panel-head"><span>Version / Lineage</span><strong>{artifact.version_kind || "current"}</strong></div>
        <KeyValueList
          rows={[
            ["parent", lineage.parent || lineage.parent_name || "-"],
            ["refactor group", lineage.refactor_group || meta.refactor_group || "-"],
            ["source event", lineage.source_event_id || meta.source_event_id || "-"],
            ["history entries", `${asArray(artifact.history).length}`],
          ]}
        />
      </section>
      {Object.keys(testSummary).length ? (
        <section className="structured-panel">
          <div className="structured-panel-head"><span>Test Summary</span><strong>{String(testSummary.pass_all_tests ?? testSummary.pass ?? "recorded")}</strong></div>
          <div className="mini-metric-grid">
            <MiniMetric label="pass" value={String(testSummary.pass_all_tests ?? testSummary.pass ?? "unknown")} />
            <MiniMetric label="cases" value={formatValue(testSummary.n_cases ?? testSummary.case_count)} />
            <MiniMetric label="delta acc" value={formatValue(testSummary.delta_accuracy ?? testSummary.delta_acc)} />
            <MiniMetric label="delta tokens" value={formatValue(testSummary.delta_tokens)} />
          </div>
        </section>
      ) : null}
      <button type="button" className="wide-button" onClick={() => onOpenPayload(String(artifact.name || "Artifact"), artifact)}>
        <Maximize2 size={16} /> Open artifact payload
      </button>
    </div>
  );
}

function CardInspector({
  card,
  detail,
  page,
  onOpenPayload,
}: {
  card: FlowCard | undefined;
  detail: MaintenanceDetail;
  page: MaintenanceDetail["pages"][number] | undefined;
  onOpenPayload: (title: string, payload: unknown) => void;
}) {
  if (!card) return <div className="empty-note">Card payload is unavailable.</div>;
  const roleKey = roleKeyForCard(card);
  return (
    <div className="inspector-body">
      {roleKey === "extractor" ? (
        <ExtractionPanel card={card} detail={detail} page={page} onOpenPayload={onOpenPayload} />
      ) : roleKey === "bundle_builder" ? (
        <BundleBuilderPanel card={card} onOpenPayload={onOpenPayload} />
      ) : roleKey === "unit_tester" ? (
        <MaintenanceTestPanel card={card} onOpenPayload={onOpenPayload} />
      ) : roleKey === "refiner" ? (
        <RefinerPanel card={card} onOpenPayload={onOpenPayload} />
      ) : (
        <RoleTextCard roleKey={roleKey} card={card} expanded onOpenPayload={onOpenPayload} />
      )}
    </div>
  );
}

function FrameInspector({
  frame,
  detail,
  page,
  onOpenPayload,
}: {
  frame: PlayerFrame | undefined;
  detail: MaintenanceDetail;
  page: MaintenanceDetail["pages"][number] | undefined;
  onOpenPayload: (title: string, payload: unknown) => void;
}) {
  if (!frame) return <div className="empty-note">No player frame selected.</div>;
  const event = asRecord(asRecord(frame.delta).event);
  const input = event.input || asRecord(frame.delta).input;
  const output = event.output || asRecord(frame.delta).output;
  const eventType = String(event.event_type || "");
  const linkedCards = linkedMaintenanceCards(page);
  return (
    <div className="inspector-body">
      <div className="summary-box tone-accent">
        <span>{roleKeyFromFrame(frame)} / frame {frame.index}</span>
        <strong>{String(event.event_type || frame.name || `Frame ${frame.index}`)}</strong>
        <small>{eventSummary(event, frame)}</small>
      </div>
      {eventType === "extractor_done" || eventType === "store_update" ? (
        <ExtractionFramePanel event={event} detail={detail} page={page} onOpenPayload={onOpenPayload} />
      ) : eventType === "unit_test_done" || eventType === "post_refine_test_done" ? (
        <StructuredTestResult result={asRecord(output)} onOpenPayload={onOpenPayload} />
      ) : eventType === "bundle_builder_done" ? (
        <BundleFramePanel output={asRecord(output)} onOpenPayload={onOpenPayload} />
      ) : (
        <EventTextBlocks event={event} input={input} output={output} />
      )}
      {eventType === "extractor_done" && linkedCards.tests.length > 0 ? (
        <div className="structured-panel">
          <div className="structured-panel-head">
            <span>Linked Unit Tests</span>
            <strong>{linkedCards.tests.length}</strong>
          </div>
          {linkedCards.tests.map((card, index) => (
            <MaintenanceTestPanel key={`${card.skill_name || card.subtitle || index}`} card={card} compact onOpenPayload={onOpenPayload} />
          ))}
        </div>
      ) : null}
      <button type="button" className="wide-button" onClick={() => onOpenPayload(String(frame.name || "Frame"), frame)}>
        <Maximize2 size={16} /> Open frame payload
      </button>
    </div>
  );
}

function EventTextBlocks({ event, input, output }: { event: Record<string, unknown>; input: unknown; output: unknown }) {
  const eventType = String(event.event_type || "");
  if (eventType === "executor_step") {
    const inRecord = asRecord(input);
    const outRecord = asRecord(output);
    return (
      <div className="message-stack">
        <TextBlock title="LLM system input" text={String(inRecord.system || "No system prompt recorded.")} defaultOpen />
        <TextBlock title="LLM messages input" text={messagesText(asArray(inRecord.messages))} defaultOpen />
        <TextBlock title="LLM text output" text={String(outRecord.content || "No assistant text content recorded.")} defaultOpen />
        <TextBlock title="LLM tool calls output" text={textFromValue(outRecord.tool_calls)} defaultOpen />
      </div>
    );
  }
  if (eventType === "prompt_injection") {
    const inRecord = asRecord(input);
    const outRecord = asRecord(output);
    return (
      <div className="message-stack">
        <TextBlock title="User messages" text={messagesText(asArray(inRecord.user_messages))} defaultOpen />
        <TextBlock title="Injected skill prompt" text={String(outRecord.skill_prompt || "No skill prompt recorded.")} defaultOpen />
        <TextBlock title="Turn instruction" text={String(outRecord.turn_instruction || "No turn instruction recorded.")} />
        <TextBlock title="System addition" text={String(outRecord.system || "No system addition recorded.")} />
      </div>
    );
  }
  if (eventType === "retrieval") {
    const inRecord = asRecord(input);
    const outRecord = asRecord(output);
    return (
      <div className="message-stack">
        <TextBlock title="Retrieval query" text={String(inRecord.query || outRecord.query || "No query recorded.")} defaultOpen />
        <TextBlock title="User messages" text={messagesText(asArray(inRecord.user_messages))} />
        <TextBlock title="Selected candidates" text={textFromValue(outRecord.selected || [])} defaultOpen />
        <TextBlock title="All candidates" text={textFromValue(outRecord.candidates || [])} />
      </div>
    );
  }
  if (eventType === "tool_call") {
    return <TextBlock title="Tool call input" text={textFromValue(input)} defaultOpen />;
  }
  if (eventType === "tool_result") {
    return <TextBlock title="Tool result output" text={textFromValue(output)} defaultOpen />;
  }
  return (
    <div className="message-stack">
      <TextBlock title="Event input" text={textFromValue(input) || "No input recorded for this event."} defaultOpen />
      <TextBlock title="Event output" text={textFromValue(output) || "No output recorded for this event."} defaultOpen />
    </div>
  );
}

function ExtractionFramePanel({
  event,
  detail,
  page,
  onOpenPayload,
}: {
  event: Record<string, unknown>;
  detail: MaintenanceDetail;
  page: MaintenanceDetail["pages"][number] | undefined;
  onOpenPayload: (title: string, payload: unknown) => void;
}) {
  const linked = linkedMaintenanceCards(page);
  const extractor = linked.extractor;
  if (extractor) {
    return <ExtractionPanel card={extractor} detail={detail} page={page} onOpenPayload={onOpenPayload} />;
  }
  const output = asRecord(event.output);
  const artifacts = asArray(output.artifacts).map(asRecord);
  return (
    <div className="structured-panel">
      <div className="structured-panel-head">
        <span>Extracted Skills</span>
        <strong>{artifacts.length || asArray(output.new_skill_names).length}</strong>
      </div>
      {artifacts.length ? artifacts.map((skill) => <SkillStructuredCard key={String(skill.name)} skill={skill} />) : (
        <Unavailable reason="The player frame only contains compact extraction output. Select the Extractor card on this page for full skill body, bundle, and diff detail." />
      )}
    </div>
  );
}

function ExtractionPanel({
  card,
  detail,
  page,
  onOpenPayload,
}: {
  card: FlowCard;
  detail: MaintenanceDetail;
  page: MaintenanceDetail["pages"][number] | undefined;
  onOpenPayload: (title: string, payload: unknown) => void;
}) {
  const raw = asRecord(asRecord(card.detail).debug_raw);
  const output = asRecord(asRecord(card.detail).output);
  const skillsSource = raw.raw_skills || output.skills || card.skills;
  const skills = asArray(skillsSource).map(asRecord);
  const taskId = taskIdFromPageTitle(page?.title || page?.subtitle || "");
  return (
    <div className="structured-panel">
      <div className="structured-panel-head">
        <span>Extractor Output</span>
        <strong>{skills.length} new skills</strong>
      </div>
      <a className="wide-button" href={refactorGraphHref(detail, taskId)} target="_blank" rel="noreferrer">
        <GitBranch size={16} /> Open linked refactor graph frame
      </a>
      {skills.length ? skills.map((skill) => (
        <SkillStructuredCard
          key={String(skill.name)}
          skill={skill}
          refactorHref={refactorGraphHref(detail, taskId, String(skill.name || ""))}
          onOpenPayload={onOpenPayload}
        />
      )) : <Unavailable reason="No extracted skill payload was recorded on this card." />}
    </div>
  );
}

function SkillStructuredCard({
  skill,
  refactorHref,
  onOpenPayload,
}: {
  skill: Record<string, unknown>;
  refactorHref?: string;
  onOpenPayload?: (title: string, payload: unknown) => void;
}) {
  const bundle = asRecord(skill.bundle);
  return (
    <section className="skill-structured-card">
      <header>
        <div>
          <span>{String(skill.kind || "skill")} v{formatValue(skill.version || 1)}</span>
          <strong>{String(skill.name || "Unnamed skill")}</strong>
          <small>{String(skill.description || "")}</small>
        </div>
        {refactorHref ? <a className="icon-button small" href={refactorHref} target="_blank" rel="noreferrer" title="Open in refactor graph"><GitBranch size={14} /></a> : null}
      </header>
      <TextBlock title="Skill body" text={String(skill.body || "No body recorded.")} defaultOpen />
      <TextBlock title="Interface" text={textFromValue(skill.interface)} />
      <details className="text-io-block" open>
        <summary>Content diff</summary>
        <DiffView before="" after={skillTextForDiff(skill)} />
      </details>
      {Object.keys(bundle).length ? <BundleStructuredView bundle={bundle} /> : <Unavailable reason="Bundle was not attached to this skill at extraction time; check Bundle Builder output for generated cases." />}
      {onOpenPayload ? (
        <button type="button" className="wide-button" onClick={() => onOpenPayload(String(skill.name || "Skill"), skill)}>
          <Maximize2 size={16} /> Open skill payload
        </button>
      ) : null}
    </section>
  );
}

function BundleBuilderPanel({ card, onOpenPayload }: { card: FlowCard; onOpenPayload: (title: string, payload: unknown) => void }) {
  const rawBundles = asRecord(asRecord(asRecord(card.detail).debug_raw).raw_bundles);
  const bundles = Object.entries(rawBundles);
  return (
    <div className="structured-panel">
      <div className="structured-panel-head">
        <span>Bundle Builder Output</span>
        <strong>{bundles.length} bundles</strong>
      </div>
      {bundles.length ? bundles.map(([skillName, bundle]) => (
        <section className="skill-structured-card" key={skillName}>
          <header>
            <div>
              <span>bundle</span>
              <strong>{skillName}</strong>
            </div>
          </header>
          <BundleStructuredView bundle={asRecord(bundle)} />
        </section>
      )) : <Unavailable reason="No structured bundle payload is available on this card." />}
      <button type="button" className="wide-button" onClick={() => onOpenPayload(String(card.title || "Bundle Builder"), card)}>
        <Maximize2 size={16} /> Open bundle payload
      </button>
    </div>
  );
}

function BundleFramePanel({ output, onOpenPayload }: { output: Record<string, unknown>; onOpenPayload: (title: string, payload: unknown) => void }) {
  const bundles = Object.entries(asRecord(output.bundles));
  return (
    <div className="structured-panel">
      <div className="structured-panel-head">
        <span>Bundle Builder Event</span>
        <strong>{bundles.length} bundles</strong>
      </div>
      {bundles.map(([skillName, bundle]) => (
        <section className="skill-structured-card" key={skillName}>
          <header><div><span>bundle</span><strong>{skillName}</strong></div></header>
          <BundleStructuredView bundle={asRecord(bundle)} />
        </section>
      ))}
      <button type="button" className="wide-button" onClick={() => onOpenPayload("Bundle Builder Event", output)}>
        <Maximize2 size={16} /> Open event payload
      </button>
    </div>
  );
}

function BundleStructuredView({ bundle }: { bundle: Record<string, unknown> }) {
  const groups = [
    ["Positive", asArray(bundle.positive_cases)],
    ["Negative", asArray(bundle.negative_cases)],
    ["Integration", asArray(bundle.integration_cases)],
  ] as const;
  return (
    <div className="bundle-structured-view">
      <div className="mini-metric-grid">
        <MiniMetric label="positive" value={groups[0][1].length} />
        <MiniMetric label="negative" value={groups[1][1].length} />
        <MiniMetric label="integration" value={groups[2][1].length} />
        <MiniMetric label="bundle" value={String(bundle.bundle_id || "unavailable")} />
      </div>
      {groups.map(([label, cases]) => (
        <details className="text-io-block" key={label} open={cases.length > 0}>
          <summary>{label} cases ({cases.length})</summary>
          <div className="case-stack">
            {cases.length ? cases.map((item, index) => <BundleCaseCard key={`${label}-${index}`} item={asRecord(item)} />) : <div className="empty-note">No {label.toLowerCase()} cases.</div>}
          </div>
        </details>
      ))}
    </div>
  );
}

function BundleCaseCard({ item }: { item: Record<string, unknown> }) {
  const context = asRecord(item.context);
  const fragment = asRecord(context.task_fragment);
  return (
    <div className="structured-case-card">
      <strong>{String(item.case_id || "case")}</strong>
      <small>{String(item.prompt || item.source || "")}</small>
      <TextBlock title="Question" text={messagesTextFromTurns(asArray(fragment.question))} />
      <TextBlock title="Expected" text={textFromValue(fragment.expected || item.expected)} defaultOpen />
      <TextBlock title="Context" text={textFromValue(context)} />
    </div>
  );
}

function MaintenanceTestPanel({ card, compact = false, onOpenPayload }: { card: FlowCard; compact?: boolean; onOpenPayload: (title: string, payload: unknown) => void }) {
  return (
    <div className="structured-panel compact-test-panel">
      <div className="structured-panel-head">
        <span>Unit Test</span>
        <strong>{String(card.skill_name || card.subtitle || "skill")}</strong>
      </div>
      <StructuredTestResult result={card as Record<string, unknown>} compact={compact} onOpenPayload={onOpenPayload} />
    </div>
  );
}

function StructuredTestResult({ result, compact = false, onOpenPayload }: { result: Record<string, unknown>; compact?: boolean; onOpenPayload: (title: string, payload: unknown) => void }) {
  const aggregate = asRecord(result.aggregate || asRecord(result.detail).aggregate);
  const unitRuns = asArray(result.unit_case_runs || asRecord(result.detail).unit_case_runs).map(asRecord);
  const utility = asRecord(aggregate.unit_utility_report);
  return (
    <section className="test-structured-card">
      <div className="mini-metric-grid">
        <MiniMetric label="pass" value={String(aggregate.pass_all_tests ?? "unknown")} />
        <MiniMetric label="cases" value={formatValue(aggregate.n_cases)} />
        <MiniMetric label="delta acc" value={formatValue(utility.delta_accuracy)} />
        <MiniMetric label="delta tokens" value={formatValue(utility.delta_tokens)} />
      </div>
      {!unitRuns.length ? <Unavailable reason="No compact unit case runs were persisted for this test result." /> : null}
      {!compact && unitRuns.map((run, index) => <UnitCaseRunCard key={`${run.case_id || index}-${index}`} run={run} />)}
      {compact && unitRuns.slice(0, 1).map((run, index) => <UnitCaseRunCard key={`${run.case_id || index}-${index}`} run={run} compact />)}
      <button type="button" className="wide-button" onClick={() => onOpenPayload(String(result.skill_name || result.subtitle || "Unit Test"), result)}>
        <Maximize2 size={16} /> Open test payload
      </button>
    </section>
  );
}

function UnitCaseRunCard({ run, compact = false }: { run: Record<string, unknown>; compact?: boolean }) {
  const actual = asRecord(run.actual_output);
  const metrics = asRecord(actual.metrics || asRecord(run.metadata).metrics);
  return (
    <details className="unit-case-card" open={!compact}>
      <summary>
        <span>{String(run.case_id || "case")}</span>
        <strong>acc {formatValue(run.accuracy)} · valid {formatValue(metrics.official_valid)}</strong>
      </summary>
      <div className="mini-metric-grid">
        <MiniMetric label="call f1" value={formatValue(metrics.call_f1)} />
        <MiniMetric label="steps" value={formatValue(metrics.n_model_steps)} />
        <MiniMetric label="tokens" value={formatValue(metrics.total_tokens)} />
        <MiniMetric label="error" value={String(metrics.official_error_type || "none")} />
      </div>
      <TextBlock title="Expected" text={textFromValue(asRecord(run.bundle_case_snapshot).expected || run.expected)} defaultOpen />
      <TextBlock title="Tool calls" text={textFromValue(run.tool_calls || actual.tool_calls || asRecord(actual.trace_summary).tool_calls)} />
      <TextBlock title="Call errors" text={textFromValue(metrics.call_errors || asRecord(run.trace_summary).call_errors)} defaultOpen />
    </details>
  );
}

function RefinerPanel({ card, onOpenPayload }: { card: FlowCard; onOpenPayload: (title: string, payload: unknown) => void }) {
  const output = asRecord(asRecord(card.detail).output);
  const raw = asRecord(asRecord(card.detail).debug_raw);
  const decisions = asArray(output.refine_decisions || raw.raw_refine_decisions).map(asRecord);
  return (
    <div className="structured-panel">
      <div className="structured-panel-head">
        <span>Refiner Decisions</span>
        <strong>{decisions.length}</strong>
      </div>
      {decisions.map((decision, index) => (
        <div className="structured-case-card" key={`${decision.skill_name || index}`}>
          <strong>{String(decision.skill_name || "skill")}: {String(decision.action || "unknown")}</strong>
          <small>{String(decision.reason || "")}</small>
          <div className="mini-metric-grid">
            <MiniMetric label="before" value={formatValue(decision.version_before)} />
            <MiniMetric label="after" value={formatValue(decision.version_after)} />
          </div>
        </div>
      ))}
      <button type="button" className="wide-button" onClick={() => onOpenPayload(String(card.title || "Refiner"), card)}>
        <Maximize2 size={16} /> Open refiner payload
      </button>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: unknown }) {
  return <div className="mini-metric"><span>{label}</span><strong>{formatValue(value)}</strong></div>;
}

function DiffView({ before, after }: { before: string; after: string }) {
  if (!before && after) {
    return <div className="diff-viewer">{after.split(/\n/).map((line, index) => <div className="diff-line added" key={index}><span>+</span><pre>{line}</pre></div>)}</div>;
  }
  if (before === after) {
    return <div className="diff-viewer"><div className="diff-line same"><span></span><pre>No content change recorded.</pre></div></div>;
  }
  const beforeLines = before.split(/\n/);
  const afterLines = after.split(/\n/);
  const rows = [];
  const max = Math.max(beforeLines.length, afterLines.length);
  for (let index = 0; index < max; index += 1) {
    if (beforeLines[index] === afterLines[index]) rows.push(<div className="diff-line same" key={`s-${index}`}><span></span><pre>{beforeLines[index] || ""}</pre></div>);
    else {
      if (beforeLines[index] !== undefined) rows.push(<div className="diff-line removed" key={`r-${index}`}><span>-</span><pre>{beforeLines[index]}</pre></div>);
      if (afterLines[index] !== undefined) rows.push(<div className="diff-line added" key={`a-${index}`}><span>+</span><pre>{afterLines[index]}</pre></div>);
    }
  }
  return <div className="diff-viewer">{rows}</div>;
}

function RoleTextCard({
  roleKey,
  card,
  expanded = false,
  onOpenPayload,
}: {
  roleKey: string;
  card: FlowCard;
  expanded?: boolean;
  onOpenPayload: (title: string, payload: unknown) => void;
}) {
  return (
    <section className={`role-detail-card text-first ${toneClass(card.tone)}`}>
      <header>
        <div>
          <span>{roleLabels[roleKey] || titleize(roleKey)}</span>
          <strong>{card.title || card.type || "Role Card"}</strong>
          <small>{compactText(card.subtitle || "", 160)}</small>
        </div>
        <button type="button" className="icon-button small" onClick={() => onOpenPayload(String(card.title || card.type || "Card"), card)} title="Open full payload">
          <ExternalLink size={14} />
        </button>
      </header>
      {roleKey === "executor" ? (
        <ExecutorMessages card={card} />
      ) : (
        <RolePromptResponse card={card} roleKey={roleKey} expanded={expanded} />
      )}
    </section>
  );
}

function ExecutorMessages({ card }: { card: FlowCard }) {
  const run = asRecord(card.run);
  const detail = asRecord(run.detail);
  const messages = asArray(detail.messages);
  const turns = asArray(detail.turns);
  if (!messages.length && !turns.length) {
    return <Unavailable reason="This executor card does not contain recorded LLM messages. It may be an aggregate/reconstructed executor summary." />;
  }
  return (
    <div className="message-stack">
      {turns.length > 0 && (
        <TextBlock
          title="User messages by turn"
          text={turns.map((turn) => {
            const row = asRecord(turn);
            return `Turn ${formatValue(row.turn_index)}\n${textFromValue(row.user_messages)}`;
          }).join("\n\n")}
          defaultOpen
        />
      )}
      {messages.map((message, index) => {
        const row = asRecord(message);
        const role = String(row.role || `message_${index}`);
        return (
          <MessageCard key={`${role}-${index}`} message={row} index={index} defaultOpen={index < 2} />
        );
      })}
    </div>
  );
}

function MessageCard({ message, index, defaultOpen }: { message: Record<string, unknown>; index: number; defaultOpen: boolean }) {
  const role = String(message.role || `message_${index}`);
  const toolCalls = extractToolCalls(message);
  return (
    <details className="text-io-block" open={defaultOpen}>
      <summary>{index + 1}. {role}</summary>
      <div className="message-card-body">
        <TextBlock title="Text" text={messageText({ ...message, tool_calls: undefined }) || "No text content recorded."} defaultOpen />
        {toolCalls.length ? (
          <div className="tool-card-stack">
            {toolCalls.map((call, callIndex) => (
              <ToolCallCard key={`${call.id || call.name || callIndex}`} call={call} index={callIndex} />
            ))}
          </div>
        ) : null}
      </div>
    </details>
  );
}

function ToolCallCard({ call, index }: { call: Record<string, unknown>; index: number }) {
  const args = asRecord(call.arguments || call.args || call.input || call.function_arguments);
  const result = call.result || call.output;
  const error = call.error;
  return (
    <section className={`tool-call-card ${error ? "danger" : ""}`}>
      <header>
        <strong>{String(call.name || asRecord(call.function).name || "tool_call")}</strong>
        <span>#{index + 1}</span>
      </header>
      <KeyValueList rows={Object.entries(args).map(([key, value]) => [key, textFromValue(value)])} />
      {result !== undefined ? <TextBlock title="Result" text={textFromValue(result)} defaultOpen /> : null}
      {error ? <TextBlock title="Error" text={textFromValue(error)} defaultOpen /> : null}
      <div className="tool-secondary">
        {call.id || call.tool_call_id ? <span>{String(call.id || call.tool_call_id)}</span> : null}
        {call.turn_index !== undefined ? <span>turn {formatValue(call.turn_index)}</span> : null}
        {call.step_index !== undefined ? <span>step {formatValue(call.step_index)}</span> : null}
      </div>
    </section>
  );
}

function extractToolCalls(message: Record<string, unknown>): Record<string, unknown>[] {
  const direct = asArray(message.tool_calls).map(asRecord).filter((item) => Object.keys(item).length);
  const contentCalls = asArray(message.content).map(asRecord).filter((item) => item.type === "tool_use" || item.type === "tool_call");
  return [...direct, ...contentCalls];
}

function RolePromptResponse({ card, roleKey, expanded }: { card: FlowCard; roleKey: string; expanded: boolean }) {
  const detail = asRecord(card.detail);
  const input = asRecord(detail.input);
  const output = asRecord(detail.output);
  const system = firstText(card.system, input.system, asRecord(asRecord(card.model_output).role_io)?.[roleKey] && asRecord(asRecord(asRecord(card.model_output).role_io)[roleKey]).system);
  const user = firstText(card.user, input.user, card.user_preview);
  const rawResponse = firstText(card.raw_response, output.raw_response);
  const parsed = output.parsed_response || asRecord(card.model_output).stale_resolver || output.refine_decisions || output.skills || output.bundles;

  const hasRealText = Boolean(system || user || rawResponse);
  if (!hasRealText) {
    return (
      <>
        <Unavailable reason="This result does not include the actual LLM system/user/raw_response text for this role. The API only has reconstructed artifacts or summaries for this card." />
        {expanded && <JsonTree value={card} label="available_payload" />}
      </>
    );
  }

  return (
    <div className="message-stack">
      <TextBlock title="System input" text={system || "No system prompt recorded."} defaultOpen />
      <TextBlock title="User input" text={user || "No user prompt recorded."} defaultOpen />
      <TextBlock title="LLM output" text={rawResponse || textFromValue(parsed) || "No raw response recorded."} defaultOpen />
      {expanded && parsed ? <TextBlock title="Parsed output" text={textFromValue(parsed)} /> : null}
    </div>
  );
}

function TextBlock({ title, text, defaultOpen = false }: { title: string; text: string; defaultOpen?: boolean }) {
  return (
    <details className="text-io-block" open={defaultOpen}>
      <summary>{title}</summary>
      <pre>{text || "unavailable"}</pre>
    </details>
  );
}

function KeyValueList({ rows }: { rows: Array<[string, unknown]> }) {
  return (
    <div className="role-kv-list">
      {rows.map(([key, value]) => (
        <div className="info-block" key={key}>
          <span>{key}</span>
          <strong>{formatValue(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function Unavailable({ reason }: { reason: string }) {
  return <div className="unavailable-note">{reason}</div>;
}

function titleForSelection(selection: Selection, pageTitle?: string, frame?: PlayerFrame): string {
  if (selection.kind === "artifact") return "Artifact Text";
  if (selection.kind === "frame") return frame?.name || "Frame Text";
  if (selection.kind === "flow_card") return "Role Text";
  if (selection.kind === "page") return pageTitle || "Page Text";
  return "Overview";
}

function cardForSelection(page: MaintenanceDetail["pages"][number] | undefined, id: string): FlowCard | undefined {
  const match = id.match(/^card:(\d+)$/);
  const index = match ? Number(match[1]) : -1;
  return Number.isFinite(index) && index >= 0 ? page?.flow_cards?.[index] : undefined;
}

function roleKeyForCard(card: FlowCard): string {
  const value = `${card.role || card.type || card.title || ""}`.toLowerCase();
  if (value.includes("retriev")) return "retriever";
  if (value.includes("executor") || value.includes("replay") || value === "run") return "executor";
  if (value.includes("extractor")) return "extractor";
  if (value.includes("bundle")) return "bundle_builder";
  if (value.includes("test")) return "unit_tester";
  if (value.includes("refiner") || value.includes("refine")) return "refiner";
  if (value.includes("store") || value.includes("skill_delta")) return "skill_store";
  return "executor";
}

function linkedMaintenanceCards(page: MaintenanceDetail["pages"][number] | undefined): { extractor?: FlowCard; bundle?: FlowCard; tests: FlowCard[]; refiner?: FlowCard } {
  const cards = page?.flow_cards || [];
  return {
    extractor: cards.find((card) => roleKeyForCard(card) === "extractor"),
    bundle: cards.find((card) => roleKeyForCard(card) === "bundle_builder"),
    tests: cards.filter((card) => roleKeyForCard(card) === "unit_tester"),
    refiner: cards.find((card) => roleKeyForCard(card) === "refiner"),
  };
}

function taskIdFromPageTitle(value: string): string {
  const match = value.match(/\|\s*([^|]+)$/);
  return match?.[1]?.trim() || "";
}

function refactorGraphHref(detail: MaintenanceDetail, taskId = "", skill = ""): string {
  const params = new URLSearchParams({ id: detail.experiment.id || "" });
  if (taskId) params.set("task_id", taskId);
  if (skill) params.set("skill", skill);
  return `/refactor-graph?${params.toString()}`;
}

function skillTextForDiff(skill: Record<string, unknown>): string {
  return [
    `name: ${String(skill.name || "")}`,
    `description: ${String(skill.description || "")}`,
    "",
    String(skill.body || ""),
    "",
    skill.interface ? `interface:\n${textFromValue(skill.interface)}` : "",
  ].filter(Boolean).join("\n");
}

function messagesTextFromTurns(turns: unknown[]): string {
  if (!turns.length) return "No question messages recorded.";
  return turns.map((turn, index) => {
    const messages = Array.isArray(turn) ? turn : [turn];
    return `Turn ${index + 1}\n${messagesText(messages)}`;
  }).join("\n\n");
}

function messageText(message: Record<string, unknown>): string {
  const content = message.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((part) => {
      if (typeof part === "string") return part;
      const row = asRecord(part);
      if (row.type === "text" && row.text) return String(row.text);
      if (row.type === "tool_use") {
        return `tool_use ${row.name || ""}\n${stringifyJson(row.input || {})}`;
      }
      if (row.type === "tool_result") {
        return `tool_result ${row.tool_use_id || ""}\n${textFromValue(row.content)}`;
      }
      return stringifyJson(row);
    }).join("\n\n");
  }
  return textFromValue(content || message);
}

function messagesText(messages: unknown[]): string {
  if (!messages.length) return "No messages recorded.";
  return messages.map((message, index) => {
    const row = asRecord(message);
    return `${index + 1}. ${row.role || "message"}\n${messageText(row)}`;
  }).join("\n\n");
}

function eventSummary(event: Record<string, unknown>, frame: PlayerFrame): string {
  const parts = [
    event.phase ? `phase=${event.phase}` : "",
    event.task_id ? `task=${event.task_id}` : "",
    event.turn_index !== null && event.turn_index !== undefined ? `turn=${event.turn_index}` : "",
    event.step_index !== null && event.step_index !== undefined ? `step=${event.step_index}` : "",
  ].filter(Boolean);
  return parts.join(" | ") || frame.summary || "No frame summary recorded.";
}

function frameTitle(frame: PlayerFrame): string {
  const event = asRecord(asRecord(frame.delta).event);
  return String(event.event_type || frame.name || `Frame ${frame.index}`);
}

function textFromValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string") return value;
  return stringifyJson(value);
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}
