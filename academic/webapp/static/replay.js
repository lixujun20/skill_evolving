const replayState = {
  files: {},
  actions: [],
  cases: [],
  currentCase: null,
  selectedCaseIds: new Set(),
  resultPayload: null,
  resultById: {},
  currentJobId: "",
  currentJob: null,
};

document.addEventListener("DOMContentLoaded", async () => {
  bindReplayEvents();
  await loadReplayConfig();
  await loadCases();
  await loadResults();
});

function bindReplayEvents() {
  document.getElementById("btn-cases-validated").addEventListener("click", () => setCasesPath("validated_cases"));
  document.getElementById("btn-cases-drafts").addEventListener("click", () => setCasesPath("draft_cases"));
  document.getElementById("btn-cases-merged").addEventListener("click", () => setCasesPath("merged_cases"));
  document.getElementById("btn-results-validated").addEventListener("click", () => setResultsPath("benchmark_output"));
  document.getElementById("btn-results-merged").addEventListener("click", () => setResultsPath("merged_benchmark_output"));
  document.getElementById("btn-load-cases").addEventListener("click", loadCases);
  document.getElementById("btn-load-results").addEventListener("click", loadResults);
  document.getElementById("btn-run-benchmark").addEventListener("click", runBenchmark);
  document.getElementById("btn-mine-candidates").addEventListener("click", mineCandidates);
  document.getElementById("btn-build-drafts").addEventListener("click", buildDrafts);
  document.getElementById("btn-merge-cases").addEventListener("click", mergeCases);
  document.getElementById("btn-select-all-visible").addEventListener("click", selectAllVisibleCases);
  document.getElementById("btn-clear-selection").addEventListener("click", clearSelection);
  document.getElementById("btn-run-annotation").addEventListener("click", runAnnotation);

  document.getElementById("case-search").addEventListener("input", renderCaseList);
  document.getElementById("status-filter").addEventListener("change", renderCaseList);
  document.getElementById("selection-filter").addEventListener("change", renderCaseList);
}

async function loadReplayConfig() {
  const res = await fetch("/api/replay/files");
  const payload = await res.json();
  replayState.files = payload.files;
  replayState.actions = payload.actions || [];
  document.getElementById("cases-path").value = payload.files.merged_cases.path;
  document.getElementById("results-path").value = payload.files.merged_benchmark_output.path;
  document.getElementById("db-url").value = payload.db_url || "";
}

function setCasesPath(key) {
  document.getElementById("cases-path").value = replayState.files[key].path;
  loadCases();
}

function setResultsPath(key) {
  document.getElementById("results-path").value = replayState.files[key].path;
  loadResults();
}

async function loadCases() {
  const path = document.getElementById("cases-path").value;
  const res = await fetch(`/api/replay/cases?path=${encodeURIComponent(path)}`);
  const payload = await res.json();
  if (!res.ok) {
    setPipelineOutput(payload.error || "Failed to load cases");
    return;
  }
  replayState.cases = payload.cases || [];
  renderReplayStats(payload.summary || {});
  renderCaseList();
  if (replayState.currentCase) {
    selectCase(replayState.currentCase.case_id);
  } else if (replayState.cases.length > 0) {
    selectCase(replayState.cases[0].case_id);
  } else {
    replayState.currentCase = null;
    document.getElementById("case-placeholder").style.display = "flex";
    document.getElementById("case-detail").style.display = "none";
  }
}

function renderReplayStats(summary) {
  const bar = document.getElementById("replay-stats");
  const statusCounts = summary.status_counts || {};
  bar.innerHTML = `
    <div class="stat-chip"><strong>${summary.n_cases || 0}</strong> Cases</div>
    <div class="stat-chip"><strong>${summary.validated_cases || 0}</strong> Validated</div>
    <div class="stat-chip"><strong>${summary.draft_cases || 0}</strong> Drafts</div>
    <div class="stat-chip"><strong>${statusCounts.rejected || 0}</strong> Rejected</div>
    <div class="stat-chip"><strong>${replayState.selectedCaseIds.size}</strong> Selected</div>
  `;
}

function getFilteredCases() {
  const q = document.getElementById("case-search").value.toLowerCase();
  const statusFilter = document.getElementById("status-filter").value;
  const selectionFilter = document.getElementById("selection-filter").value;
  return replayState.cases.filter((item) => {
    const history = item.history_context || {};
    const haystack = [
      item.case_id,
      item.problem_id,
      item.query,
      item.failure_type,
      item.source_experiment,
      history.previous_query,
      history.workflow_summary,
      history.previous_workflow_plan,
    ].join(" ").toLowerCase();
    if (q && !haystack.includes(q)) return false;
    if (statusFilter && item.status !== statusFilter) return false;
    if (selectionFilter === "selected" && !replayState.selectedCaseIds.has(item.case_id)) return false;
    if (selectionFilter === "annotated" && !item.llm_annotation) return false;
    if (selectionFilter === "unannotated" && item.llm_annotation) return false;
    return true;
  });
}

function renderCaseList() {
  const ul = document.getElementById("case-list");
  const filtered = getFilteredCases();
  ul.innerHTML = filtered.map((item) => {
    const active = replayState.currentCase?.case_id === item.case_id ? "active" : "";
    const selected = replayState.selectedCaseIds.has(item.case_id) ? "selected-case" : "";
    const history = item.history_context || {};
    return `
      <li class="${active} ${selected}">
        <div class="case-row-top">
          <label class="case-check">
            <input type="checkbox" ${replayState.selectedCaseIds.has(item.case_id) ? "checked" : ""} onchange="toggleCaseSelection('${escapeJs(item.case_id)}', this.checked)">
            <span></span>
          </label>
          <div class="case-click-target" onclick="selectCase('${escapeJs(item.case_id)}')">
            <div class="skill-name">${escapeHtml(item.case_id || "")}</div>
            <div class="skill-meta">${escapeHtml(item.status || "unknown")} | ${escapeHtml(item.source_experiment || "unknown")}</div>
            <div class="skill-desc">${escapeHtml(truncate(item.query || "", 88))}</div>
            <div class="skill-meta subtle">History: ${escapeHtml(truncate(history.previous_query || "missing historical query", 84))}</div>
          </div>
        </div>
      </li>
    `;
  }).join("");
  renderReplayStats({
    n_cases: replayState.cases.length,
    validated_cases: replayState.cases.filter((c) => c.status === "validated").length,
    draft_cases: replayState.cases.filter((c) => c.status === "draft").length,
    status_counts: {
      rejected: replayState.cases.filter((c) => c.status === "rejected").length,
    },
  });
}

function toggleCaseSelection(caseId, checked) {
  if (checked) replayState.selectedCaseIds.add(caseId);
  else replayState.selectedCaseIds.delete(caseId);
  renderCaseList();
}

function selectAllVisibleCases() {
  getFilteredCases().forEach((item) => replayState.selectedCaseIds.add(item.case_id));
  renderCaseList();
}

function clearSelection() {
  replayState.selectedCaseIds.clear();
  renderCaseList();
}

function selectCase(caseId) {
  const item = replayState.cases.find((row) => row.case_id === caseId);
  if (!item) return;
  replayState.currentCase = item;
  renderCaseList();

  document.getElementById("case-placeholder").style.display = "none";
  document.getElementById("case-detail").style.display = "block";
  document.getElementById("case-title").textContent = item.case_id;
  document.getElementById("case-status").textContent = item.status || "unknown";
  document.getElementById("case-source").textContent = item.source_experiment || "unknown";
  document.getElementById("case-query").textContent = item.query || "";
  document.getElementById("meta-problem-id").textContent = item.problem_id || "—";
  document.getElementById("meta-failure-type").textContent = item.failure_type || "—";
  document.getElementById("meta-skill-count").textContent = (item.retrieved_skills || []).length;
  document.getElementById("meta-fragment-count").textContent = (item.history_context?.workflow_fragments || []).length;

  const history = item.history_context || {};
  document.getElementById("history-query").textContent = history.previous_query || "—";
  document.getElementById("history-summary").textContent = history.workflow_summary || history.historical_agent_summary || "—";
  document.getElementById("history-plan").textContent = history.previous_workflow_plan || "—";
  document.getElementById("history-fragments").textContent = formatJson(history.workflow_fragments || []);
  document.getElementById("history-traces").textContent = formatJson(history.trace_snippets || []);

  const skills = item.retrieved_skills || [];
  document.getElementById("retrieved-skills").innerHTML = skills.length
    ? skills.map((skill) => `<span class="dep-chip replay-chip" title="${escapeHtml(skill.description || "")}">${escapeHtml(skill.name)}</span>`).join("")
    : `<span class="empty-inline">No retrieved skills</span>`;

  document.getElementById("annotation-notes").textContent = item.annotation_notes || "—";
  document.getElementById("annotation-references").textContent = formatJson(item.references || {});
  document.getElementById("annotation-judge-summary").textContent = formatJson(item.llm_annotation?.judge_summary || {});
  document.getElementById("candidate-metadata").textContent = formatJson(item.candidate_metadata || {});

  renderAnnotationTrace(item.llm_annotation || null);
  renderResultForCurrentCase();
}

function renderAnnotationTrace(annotation) {
  const empty = document.getElementById("annotation-trace-empty");
  const detail = document.getElementById("annotation-trace-detail");
  if (!annotation || (!annotation.full_prompt && !annotation.error)) {
    empty.style.display = "block";
    detail.style.display = "none";
    return;
  }
  empty.style.display = "none";
  detail.style.display = "block";
  document.getElementById("annotation-time").textContent = annotation.annotated_at || "—";
  document.getElementById("annotation-llm-name").textContent = annotation.llm_config || "—";
  document.getElementById("annotation-full-prompt").textContent = annotation.full_prompt || annotation.error || "—";
  document.getElementById("annotation-full-output").textContent = annotation.full_output || annotation.error || "—";
  document.getElementById("annotation-parsed-output").textContent = formatJson(annotation.parsed_output || { error: annotation.error || "" });
}

async function runAnnotation() {
  const caseIds = Array.from(replayState.selectedCaseIds);
  const payload = await postJson("/api/replay/annotate", {
    path: document.getElementById("cases-path").value,
    case_ids: caseIds,
    llm_config: document.getElementById("annotation-llm-config").value.trim() || "tool_maker",
    save_results: document.getElementById("annotation-save-results").checked,
  });
  if (!payload) return;
  replayState.currentJobId = payload.job_id;
  setAnnotationJobStatus(`Started annotation job ${payload.job_id}`);
  pollJob(payload.job_id, async (job) => {
    replayState.currentJob = job;
    renderJobStatus(job);
    if (job.status === "completed" || job.status === "failed") {
      await loadCases();
    }
  });
}

function renderJobStatus(job) {
  const progress = job.progress || {};
  const events = (progress.events || []).slice(-8);
  const eventText = events.map((evt) => `[${evt.time}] ${evt.message}`).join("\n");
  setAnnotationJobStatus(
    `Job: ${job.job_id}\nStatus: ${job.status}\nProgress: ${progress.completed || 0}/${progress.total || 0}\nCurrent: ${progress.running_case_id || "-"}\nMessage: ${progress.message || ""}\n\n${eventText}`
  );
}

function setAnnotationJobStatus(text) {
  document.getElementById("annotation-job-status").textContent = text;
}

async function loadResults() {
  const path = document.getElementById("results-path").value;
  const res = await fetch(`/api/replay/results?path=${encodeURIComponent(path)}`);
  const payload = await res.json();
  if (!res.ok) {
    replayState.resultPayload = null;
    replayState.resultById = {};
    setPipelineOutput(payload.error || "Failed to load results");
    renderResultForCurrentCase();
    return;
  }

  replayState.resultPayload = payload;
  replayState.resultById = {};
  (payload.cases || []).forEach((item) => {
    const caseId = item.case?.case_id;
    if (caseId) replayState.resultById[caseId] = item;
  });
  setPipelineOutput(`Loaded results from ${path}`);
  renderResultForCurrentCase();
}

function renderResultForCurrentCase() {
  const empty = document.getElementById("result-empty");
  const detail = document.getElementById("result-detail");
  const current = replayState.currentCase;
  if (!current) {
    empty.style.display = "block";
    detail.style.display = "none";
    return;
  }
  const result = replayState.resultById[current.case_id];
  if (!result) {
    empty.style.display = "block";
    detail.style.display = "none";
    return;
  }

  empty.style.display = "none";
  detail.style.display = "block";
  document.getElementById("result-winner").textContent = result.winner || "—";
  document.getElementById("result-judge-type").textContent = result.judge?.judge_type || "—";
  document.getElementById("result-confidence").textContent = result.judge?.confidence || "—";
  document.getElementById("result-heuristic").textContent = result.heuristic_winner || "—";
  document.getElementById("result-reasoning").textContent = result.judge?.reasoning || "—";
  document.getElementById("result-joint").textContent = formatJson({
    diagnostics: result.joint_refactor?.diagnostics || {},
    workflow_plan: result.joint_refactor?.workflow_plan || "",
    proposed_skill_names: result.joint_refactor?.proposed_skill_names || [],
  });
  document.getElementById("result-legacy").textContent = formatJson({
    diagnostics: result.legacy_planner?.diagnostics || {},
    workflow_plan: result.legacy_planner?.workflow_plan || "",
    proposed_skill_names: result.legacy_planner?.proposed_skill_names || [],
  });
}

async function runBenchmark() {
  const payload = await postJson("/api/replay/run-benchmark", {
    cases_path: document.getElementById("cases-path").value,
    output_path: document.getElementById("results-path").value,
    db_url: document.getElementById("db-url").value,
    allow_live_llm: document.getElementById("allow-live-llm").checked,
  });
  if (!payload) return;
  setPipelineOutput(`Benchmark finished.\n${formatJson(payload.result?.summary || payload.result || {})}`);
  await loadResults();
}

async function mineCandidates() {
  const payload = await postJson("/api/replay/mine-candidates", {});
  if (!payload) return;
  setPipelineOutput(`Mined candidates.\n${formatJson(payload.result || {})}`);
}

async function buildDrafts() {
  const payload = await postJson("/api/replay/build-drafts", {});
  if (!payload) return;
  setPipelineOutput(`Built drafts.\n${formatJson(payload.result || {})}`);
}

async function mergeCases() {
  const payload = await postJson("/api/replay/merge-cases", {});
  if (!payload) return;
  setPipelineOutput(`Merged cases.\n${formatJson(payload)}`);
  await loadCases();
}

function setPipelineOutput(text) {
  document.getElementById("pipeline-output").textContent = text;
}

async function pollJob(jobId, onUpdate) {
  while (true) {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
    const payload = await res.json();
    await onUpdate(payload);
    if (payload.status === "completed" || payload.status === "failed") break;
    await sleep(1500);
  }
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await res.json();
  if (!res.ok) {
    setPipelineOutput(payload.error || `Request failed: ${url}`);
    return null;
  }
  return payload;
}

function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

function truncate(text, n) {
  return text.length <= n ? text : text.slice(0, n - 3) + "...";
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeJs(str) {
  return String(str).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

window.selectCase = selectCase;
window.toggleCaseSelection = toggleCaseSelection;
