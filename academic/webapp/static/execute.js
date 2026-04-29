const executeState = {
  config: null,
  currentJobId: "",
  currentJob: null,
  currentRun: null,
  expanded: {},
  viewMode: "split",
  memorySessionId: "",
};

document.addEventListener("DOMContentLoaded", async () => {
  bindExecuteEvents();
  await loadExecuteConfig();
});

function bindExecuteEvents() {
  document.getElementById("btn-run-execute").addEventListener("click", runExecutePipeline);
}

async function loadExecuteConfig() {
  const res = await fetch("/api/execute/config");
  const payload = await res.json();
  executeState.config = payload;
  document.getElementById("execute-skills-path").value = payload.default_skills_path || "";
  renderLlmOptions(payload.llm_options || [], payload.llm_config_default || "tool_maker");
  renderSkillLibraries(payload.skill_libraries || []);
}

function renderLlmOptions(options, selected) {
  const select = document.getElementById("execute-llm-config");
  select.innerHTML = options.map((item) => (
    `<option value="${escapeHtml(item.id)}" ${item.id === selected ? "selected" : ""}>${escapeHtml(item.label)}</option>`
  )).join("");
}

function renderSkillLibraries(libraries) {
  const row = document.getElementById("execute-library-row");
  row.innerHTML = libraries.map((lib) => (
    `<button class="btn chip-btn" onclick="setExecuteSkillsPath('${escapeJs(lib.path)}')">${escapeHtml(lib.name)} | ${lib.skill_count}</button>`
  )).join("");
}

function setExecuteSkillsPath(path) {
  document.getElementById("execute-skills-path").value = path;
}

async function runExecutePipeline() {
  const query = document.getElementById("execute-query").value.trim();
  if (!query) {
    setExecuteJobStatus("Query is required.");
    return;
  }
  if (!executeState.memorySessionId) {
    await ensureMemorySession();
  }
  clearExecuteView();

  const payload = await postJson("/api/execute/run", {
    query,
    skills_path: document.getElementById("execute-skills-path").value.trim(),
    llm_config: document.getElementById("execute-llm-config").value || "tool_maker",
    prompt_mode: document.getElementById("execute-prompt-mode").value,
    top_k: Number(document.getElementById("execute-top-k").value || 5),
    run_extract: document.getElementById("execute-run-extract").checked,
    run_test: document.getElementById("execute-run-test").checked,
    memory_session_id: executeState.memorySessionId,
    copy_memory: document.getElementById("execute-copy-memory").checked,
  });
  if (!payload) return;
  executeState.currentJobId = payload.job_id;
  setExecuteJobStatus(`Started execute job ${payload.job_id}`);
  pollJob(payload.job_id, (job) => {
    executeState.currentJob = job;
    renderExecuteJobStatus(job);
    renderExecuteFromJob(job);
    if (job.status === "completed" && job.result?.run) {
      executeState.currentRun = job.result.run;
      renderExecuteFromRun(job.result.run, job);
    } else if (job.status === "failed") {
      setExecuteJobStatus(job.error || "Execute job failed.");
    }
  });
}

function clearExecuteView() {
  document.getElementById("execute-placeholder").style.display = "none";
  document.getElementById("execute-detail").style.display = "block";
  document.getElementById("execute-final-answer").textContent = "Running";
  document.getElementById("execute-token-summary").textContent = "";
  document.getElementById("execute-query-preview").textContent = document.getElementById("execute-query").value.trim();
  document.getElementById("execute-summary-cards").innerHTML = "";
  document.getElementById("execute-timeline").innerHTML = "";
  document.getElementById("execute-memory-panel").innerHTML = "";
}

function renderExecuteJobStatus(job) {
  const progress = job.progress || {};
  const events = (progress.events || []).slice(-10);
  setExecuteJobStatus(
    `Job: ${job.job_id}\nStatus: ${job.status}\nMessage: ${progress.message || ""}\n\n${events.map((evt) => `[${evt.time}] ${evt.message}`).join("\n")}`
  );
}

function renderExecuteFromJob(job) {
  const partial = job.partial_result || {};
  const runLike = {
    query: job.meta?.query || "",
    retrieve: partial.retrieve || null,
    plan: partial.plan || null,
    execute: partial.execute || null,
    extract: partial.extract || null,
    test: partial.test || null,
    evaluation: {},
  };
  renderExecuteFromRun(runLike, job);
}

function renderExecuteFromRun(run, job = null) {
  document.getElementById("execute-placeholder").style.display = "none";
  document.getElementById("execute-detail").style.display = "block";

  const execute = run.execute || {};
  document.getElementById("execute-final-answer").textContent = execute.final_answer
    ? `Answer: ${execute.final_answer}`
    : (job?.status === "running" ? "Running" : "No final answer");
  document.getElementById("execute-token-summary").textContent = execute.total_tokens
    ? `Tokens ${execute.total_tokens} / ${execute.completion_tokens || 0}`
    : (job?.status || "");
  document.getElementById("execute-query-preview").textContent = run.query || job?.meta?.query || "";

  renderSummaryCards(run, job);
  renderMemoryPanel(run);
  renderMemoryEditor(run);
  renderTimeline(run, job);
  bindTimelineState();
}

function renderSummaryCards(run, job) {
  const retrieve = run.retrieve || {};
  const execute = run.execute || {};
  const extract = run.extract || {};
  const test = run.test || {};
  const cards = [
    summaryCard("Status", job?.status || "idle", job?.status === "failed" ? "danger" : "neutral"),
    summaryCard("Skills", String((retrieve.retrieved_skills || []).length), "accent"),
    summaryCard("Workflows", String((retrieve.retrieved_workflows || []).length), "accent"),
    summaryCard("Workflow Store", String(retrieve.workflow_store_count || 0), "accent"),
    summaryCard("Memory Mode", String(run.memory?.mode || retrieve.memory?.mode || "unknown"), "accent"),
    summaryCard("Steps", String((execute.steps || []).length), "accent"),
    summaryCard("Skill Calls", String(sumValues(execute.skill_tool_counts || {})), "accent"),
    summaryCard("Extracted", String((extract.skills || []).length), "success"),
    summaryCard("Tests", String((test.results || []).length), "warning"),
    summaryCard("Loading", loadingLabel(job), job?.status === "running" ? "accent" : "neutral"),
  ];
  document.getElementById("execute-summary-cards").innerHTML = cards.join("");
}

function renderMemoryPanel(run) {
  const memory = run.memory || run.retrieve?.memory;
  const mount = document.getElementById("execute-memory-panel");
  if (!memory) {
    mount.innerHTML = "";
    return;
  }
  executeState.memorySessionId = memory.session_id || executeState.memorySessionId;
  mount.innerHTML = `
    <details class="timeline-node system-node" data-detail-id="memory_panel" ${detailOpen("memory_panel", false)}>
      <summary>Memory Session</summary>
      <div class="timeline-node-body">
        <div class="structured-grid">
          <div class="structured-card">
            <div class="section-kicker">Session</div>
            <pre class="text-block">${escapeHtml(formatJson(memory))}</pre>
          </div>
        </div>
      </div>
    </details>
  `;
}

function renderMemoryEditor(run) {
  const memory = run.memory || run.retrieve?.memory;
  const skills = run.retrieve?.retrieved_skills || [];
  const workflows = run.retrieve?.retrieved_workflows || [];
  const mount = document.getElementById("execute-memory-editor");
  if (!memory) {
    mount.innerHTML = "";
    return;
  }
  mount.innerHTML = `
    <details class="timeline-node plan-node" data-detail-id="memory_editor" ${detailOpen("memory_editor", false)}>
      <summary>Memory Manager</summary>
      <div class="timeline-node-body">
        <div class="structured-grid">
          <div class="structured-card">
            <div class="section-kicker">Current Session Skills</div>
            <div class="structured-stack">
              ${skills.map((skill) => `
                <div class="memory-row">
                  <div>
                    <div class="structured-title">${escapeHtml(skill.name || "")}</div>
                    <div class="structured-subtitle">${escapeHtml(skill.description || "")}</div>
                  </div>
                  <button class="btn" onclick="deleteMemorySkill('${escapeJs(memory.session_id)}', '${escapeJs(skill.name || "")}')">Delete</button>
                </div>
              `).join("") || "<div class='timeline-empty'>No skills loaded in current retrieve snapshot.</div>"}
            </div>
          </div>
          <div class="structured-card">
            <div class="section-kicker">Current Session Workflows</div>
            <div class="structured-stack">
              ${workflows.map((wf, idx) => `
                <details class="inline-details" data-detail-id="memory_workflow_${idx}_${hashKey(wf.query || '')}" ${detailOpen(`memory_workflow_${idx}_${hashKey(wf.query || '')}`, false)}>
                  <summary>${escapeHtml((wf.query || "").slice(0, 80) || `workflow_${idx + 1}`)}</summary>
                  <pre class="text-block">${escapeHtml(formatJson(wf))}</pre>
                </details>
              `).join("") || "<div class='timeline-empty'>No workflows in current retrieve snapshot.</div>"}
            </div>
          </div>
        </div>
        <div class="structured-grid memory-form-grid">
          <div class="structured-card">
            <div class="section-kicker">Add / Update Skill</div>
            <label>Name</label>
            <input id="memory-skill-name" type="text">
            <label>Description</label>
            <input id="memory-skill-description" type="text">
            <label>Code</label>
            <textarea id="memory-skill-code" class="code-editor short-editor"></textarea>
            <label>Test Code</label>
            <textarea id="memory-skill-test-code" class="code-editor short-editor"></textarea>
            <div class="action-row">
              <button class="btn btn-primary" onclick="saveMemorySkill('${escapeJs(memory.session_id)}')">Save Skill</button>
            </div>
          </div>
          <div class="structured-card">
            <div class="section-kicker">Add Workflow</div>
            <label>Query</label>
            <input id="memory-workflow-query" type="text">
            <label>Summary</label>
            <textarea id="memory-workflow-summary" class="code-editor short-editor"></textarea>
            <label>Plan</label>
            <textarea id="memory-workflow-plan" class="code-editor short-editor"></textarea>
            <label>Decision</label>
            <input id="memory-workflow-decision" type="text" placeholder="reuse_plan | adapt_plan | reuse_workflow_fragment | fresh">
            <div class="action-row">
              <button class="btn btn-primary" onclick="saveMemoryWorkflow('${escapeJs(memory.session_id)}')">Save Workflow</button>
              <button class="btn" onclick="refreshMemorySession('${escapeJs(memory.session_id)}')">Refresh Session</button>
            </div>
          </div>
        </div>
      </div>
    </details>
  `;
}

function summaryCard(label, value, tone) {
  return `
    <div class="timeline-summary-card ${tone}">
      <div class="timeline-summary-label">${escapeHtml(label)}</div>
      <div class="timeline-summary-value">${escapeHtml(value)}</div>
    </div>
  `;
}

function renderTimeline(run, job) {
  const timeline = document.getElementById("execute-timeline");
  const nodes = [];

  nodes.push(renderJobEventsNode(job));

  if (run.retrieve) {
    nodes.push(renderRetrieveNode(run.retrieve));
  }
  if (run.plan) {
    nodes.push(renderPlanNode(run.plan));
  }
  if (run.execute) {
    nodes.push(renderExecuteNode(run.execute));
  }
  if (run.extract) {
    nodes.push(renderExtractNode(run.extract));
  }
  if (run.test) {
    nodes.push(renderTestNode(run.test));
  }

  timeline.innerHTML = nodes.join("");
}

function renderJobEventsNode(job) {
  const events = (job?.progress?.events || []).map((evt) => `
    <div class="timeline-event-item">
      <div class="timeline-event-time">${escapeHtml(evt.time || "")}</div>
      <div class="timeline-event-text">${escapeHtml(evt.message || "")}</div>
    </div>
  `).join("");
  return timelineNode("timeline-node system-node", "Execution Timeline", `
    <div class="timeline-pill-row">
      <span class="timeline-pill">${escapeHtml(job?.status || "unknown")}</span>
      <span class="timeline-pill">${escapeHtml(job?.progress?.message || "")}</span>
      ${job?.status === "running" ? `<span class="timeline-pill live-pill">Streaming</span>` : ""}
    </div>
    ${job?.status === "running" ? `<div class="loading-strip"><div class="loading-bar"></div></div>` : ""}
    <div class="timeline-event-list">${events || "<div class='timeline-empty'>No events yet.</div>"}</div>
  `, detailOpen("node_execution_timeline", true), "node_execution_timeline");
}

function renderRetrieveNode(retrieve) {
  const skills = (retrieve.retrieved_skills || []).map((skill) => `
    <div class="structured-card skill-card">
      <div class="structured-title">${escapeHtml(skill.name || "")}</div>
      <div class="structured-subtitle">${escapeHtml(skill.description || "")}</div>
      <div class="timeline-pill-row">
        <span class="timeline-pill">usage ${escapeHtml(String(skill.ui_usage_count || 0))}</span>
        <span class="timeline-pill">success ${escapeHtml(String(skill.ui_success_count || 0))}</span>
      </div>
      <details class="inline-details" data-detail-id="${escapeHtml(detailKeyForSkill(skill.name || ''))}" ${detailOpen(detailKeyForSkill(skill.name || ''), false)}>
        <summary>Code</summary>
        <pre class="code-block">${escapeHtml(skill.code || "")}</pre>
      </details>
    </div>
  `).join("");

  const workflows = (retrieve.retrieved_workflows || []).map((wf) => `
    <div class="structured-card workflow-card">
      <div class="structured-title">${escapeHtml(wf.query || "").slice(0, 120)}</div>
      <div class="structured-subtitle">${escapeHtml(wf.workflow_decision || "unknown")}</div>
      <details class="inline-details" data-detail-id="workflow_summary_${hashKey(wf.query || '')}" ${detailOpen(`workflow_summary_${hashKey(wf.query || '')}`, false)}>
        <summary>Workflow Summary</summary>
        <pre class="text-block">${escapeHtml(wf.workflow_summary || "")}</pre>
      </details>
      <details class="inline-details" data-detail-id="workflow_plan_${hashKey(wf.query || '')}" ${detailOpen(`workflow_plan_${hashKey(wf.query || '')}`, false)}>
        <summary>Workflow Plan</summary>
        <pre class="code-block">${escapeHtml(wf.workflow_plan || "")}</pre>
      </details>
    </div>
  `).join("");

  return timelineNode("timeline-node retrieve-node", "Retrieve", `
    <div class="structured-grid">
      <div>
        <div class="section-kicker">Retrieved Skills</div>
        <div class="structured-stack">${skills || "<div class='timeline-empty'>No skills retrieved yet.</div>"}</div>
      </div>
      <div>
        <div class="section-kicker">Historical Workflows</div>
        <div class="structured-stack">${workflows || `<div class='timeline-empty'>No workflow history retrieved yet. Current workflow store size: ${escapeHtml(String(retrieve.workflow_store_count || 0))}.</div>`}</div>
      </div>
    </div>
  `, detailOpen("node_retrieve", false), "node_retrieve");
}

function renderPlanNode(plan) {
  return timelineNode("timeline-node plan-node", "Plan", `
    <div class="structured-grid">
      <div class="structured-card prompt-card">
        <div class="section-kicker">Planner Artifact</div>
        <pre class="prompt-block">${escapeHtml(formatJson(plan.planner_artifact || {}))}</pre>
      </div>
      <div class="structured-card prompt-card">
        <div class="section-kicker">Executor Plan Context</div>
        <pre class="prompt-block">${escapeHtml(plan.executor_plan_context || "")}</pre>
      </div>
      <div class="structured-card prompt-card">
        <div class="section-kicker">Historical Workflow Prompt</div>
        <pre class="prompt-block">${escapeHtml(plan.historical_workflow_prompt || "")}</pre>
      </div>
      <div class="structured-card prompt-card">
        <div class="section-kicker">Skills Prompt</div>
        <pre class="prompt-block">${escapeHtml(plan.skills_prompt || "")}</pre>
      </div>
    </div>
  `, detailOpen("node_plan", false), "node_plan");
}

function renderExecuteNode(execute) {
  const stepCards = (execute.steps || []).map((step, idx) => `
    <details class="inline-details timeline-step ${stepTone(step.type)}" data-detail-id="step_${idx}" ${detailOpen(`step_${idx}`, idx < 2)}>
      <summary><span class="timeline-step-index">${idx + 1}</span><span class="timeline-step-type">${escapeHtml(step.type || "step")}</span></summary>
      <pre class="${stepPreClass(step.type)}">${escapeHtml(step.content || "")}</pre>
    </details>
  `).join("");

  const messageCards = (execute.messages || []).map((msg, idx) => `
    <details class="inline-details timeline-message ${messageTone(msg.role)}" data-detail-id="msg_${idx}_${hashKey(msg.content || msg.role || '')}" ${detailOpen(`msg_${idx}_${hashKey(msg.content || msg.role || '')}`, idx >= Math.max((execute.messages || []).length - 2, 0))}>
      <summary><span class="timeline-step-index">${idx + 1}</span><span class="timeline-step-type">${escapeHtml(msg.role || "message")}</span></summary>
      ${msg.content ? `<pre class="${messagePreClass(msg.role)}">${escapeHtml(msg.content)}</pre>` : ""}
      ${msg.thinking ? `<pre class="thinking-block">${escapeHtml(msg.thinking)}</pre>` : ""}
      ${msg.tool_calls && msg.tool_calls.length ? `<pre class="code-block">${escapeHtml(formatJson(msg.tool_calls))}</pre>` : ""}
    </details>
  `).join("");

  const alignment = renderAlignmentView(execute);
  return timelineNode("timeline-node execute-node", "Execute", `
    <div class="timeline-pill-row">
      <span class="timeline-pill">${escapeHtml(execute.success ? "success" : "in_progress")}</span>
      <span class="timeline-pill">${escapeHtml(execute.final_answer || "no final answer yet")}</span>
    </div>
    <details class="inline-details" data-detail-id="execute_plan_context" ${detailOpen("execute_plan_context", false)}>
      <summary>Executor Plan Context</summary>
      <pre class="prompt-block">${escapeHtml(execute.plan_context || "")}</pre>
    </details>
    ${renderSkillCallUsage(execute)}
    <div class="toggle-row">
      <button class="btn chip-btn ${executeState.viewMode === "split" ? "active-toggle" : ""}" onclick="setExecuteViewMode('split')">Split View</button>
      <button class="btn chip-btn ${executeState.viewMode === "aligned" ? "active-toggle" : ""}" onclick="setExecuteViewMode('aligned')">Aligned View</button>
    </div>
    <div class="structured-grid">
      <div>
        <div class="section-kicker">Step Timeline</div>
        <div class="structured-stack">${stepCards || "<div class='timeline-empty'>No steps yet.</div>"}</div>
      </div>
      <div>
        <div class="section-kicker">Conversation</div>
        <div class="structured-stack">${messageCards || "<div class='timeline-empty'>No messages yet.</div>"}</div>
      </div>
    </div>
    ${alignment}
  `, detailOpen("node_execute", true), "node_execute");
}

function renderExtractNode(extract) {
  const cards = (extract.skills || []).map((skill) => `
    <div class="structured-card extract-card">
      <div class="structured-title">${escapeHtml(skill.name || "")}</div>
      <div class="structured-subtitle">${escapeHtml(skill.description || "")}</div>
      <div class="timeline-pill-row">
        <span class="timeline-pill">new skill</span>
      </div>
      <details class="inline-details" data-detail-id="extract_skill_code_${escapeHtml(skill.name || '')}" ${detailOpen(`extract_skill_code_${skill.name || ''}`, false)}>
        <summary>Skill Code</summary>
        <pre class="code-block">${escapeHtml(skill.code || "")}</pre>
      </details>
      <details class="inline-details" data-detail-id="extract_skill_test_${escapeHtml(skill.name || '')}" ${detailOpen(`extract_skill_test_${skill.name || ''}`, false)}>
        <summary>Test Code</summary>
        <pre class="code-block">${escapeHtml(skill.test_code || "")}</pre>
      </details>
    </div>
  `).join("");
  const refineCards = (extract.refine_history || []).map((item, idx) => `
    <details class="inline-details" data-detail-id="extract_refine_${idx}_${escapeHtml(item.skill_name || '')}" ${detailOpen(`extract_refine_${idx}_${item.skill_name || ''}`, false)}>
      <summary>Refine Attempt ${idx + 1} | ${escapeHtml(item.skill_name || "")}</summary>
      <pre class="text-block">${escapeHtml(formatJson(item))}</pre>
    </details>
  `).join("");
  return timelineNode("timeline-node extract-node", "Extract", `
    <div class="structured-stack">${cards || "<div class='timeline-empty'>No extracted skills yet.</div>"}</div>
    <div class="structured-stack">${refineCards || ""}</div>
  `, detailOpen("node_extract", false), "node_extract");
}

function renderTestNode(test) {
  const cards = (test.results || []).map((result) => `
    <div class="structured-card test-card ${result.passed ? "success-card" : "warning-card"}">
      <div class="structured-title">${escapeHtml(result.skill_name || "")}</div>
      <div class="timeline-pill-row">
        <span class="timeline-pill">${escapeHtml(result.passed ? "passed" : "failed")}</span>
        <span class="timeline-pill">${escapeHtml(result.final_error || "no error")}</span>
      </div>
      <details class="inline-details" data-detail-id="test_result_${escapeHtml(result.skill_name || '')}" ${detailOpen(`test_result_${result.skill_name || ''}`, false)}>
        <summary>Attempt History</summary>
        <pre class="text-block">${escapeHtml(formatJson(result))}</pre>
      </details>
    </div>
  `).join("");
  return timelineNode("timeline-node test-node", "Test", `
    <div class="structured-stack">${cards || "<div class='timeline-empty'>No test results yet.</div>"}</div>
  `, detailOpen("node_test", false), "node_test");
}

function timelineNode(cls, title, body, open = false, id = "") {
  return `
    <details class="${cls}" data-detail-id="${escapeHtml(id)}" ${open ? "open" : ""}>
      <summary>${escapeHtml(title)}</summary>
      <div class="timeline-node-body">${body}</div>
    </details>
  `;
}

function renderAlignmentView(execute) {
  if (executeState.viewMode !== "aligned") return "";
  const messages = execute.messages || [];
  const steps = execute.steps || [];
  const rows = [];
  const n = Math.max(messages.length, steps.length);
  for (let i = 0; i < n; i += 1) {
    const msg = messages[i];
    const step = steps[i];
    rows.push(`
      <div class="aligned-row">
        <div class="aligned-cell">
          ${msg ? `<div class="aligned-label">${escapeHtml(msg.role || "message")}</div><pre class="${messagePreClass(msg.role)}">${escapeHtml(msg.content || "")}</pre>` : "<div class='timeline-empty'>-</div>"}
        </div>
        <div class="aligned-cell">
          ${step ? `<div class="aligned-label">${escapeHtml(step.type || "step")}</div><pre class="${stepPreClass(step.type)}">${escapeHtml(step.content || "")}</pre>` : "<div class='timeline-empty'>-</div>"}
        </div>
      </div>
    `);
  }
  return `
    <div class="aligned-panel">
      <div class="section-kicker">Aligned Conversation / Steps</div>
      <div class="aligned-grid">
        ${rows.join("")}
      </div>
    </div>
  `;
}

function loadingLabel(job) {
  if (!job) return "idle";
  if (job.status === "running") return "streaming";
  if (job.status === "completed") return "done";
  if (job.status === "failed") return "failed";
  return job.status;
}

function bindTimelineState() {
  document.querySelectorAll("[data-detail-id]").forEach((el) => {
    if (el.__boundToggle) return;
    el.__boundToggle = true;
    el.addEventListener("toggle", () => {
      executeState.expanded[el.dataset.detailId] = el.open;
    });
  });
}

function detailOpen(id, fallbackOpen) {
  return executeState.expanded.hasOwnProperty(id)
    ? (executeState.expanded[id] ? "open" : "")
    : (fallbackOpen ? "open" : "");
}

function setExecuteViewMode(mode) {
  executeState.viewMode = mode;
  if (executeState.currentRun) {
    renderExecuteFromRun(executeState.currentRun, executeState.currentJob);
  } else if (executeState.currentJob) {
    renderExecuteFromJob(executeState.currentJob);
  }
}

function renderSkillCallUsage(execute) {
  const counts = execute.skill_tool_counts || {};
  const names = Object.keys(counts);
  if (!names.length) {
    return `<div class="timeline-empty">No retrieved skill tool was called yet. The model may still be using raw Python, or the run has not reached tool use.</div>`;
  }
  return `
    <div class="structured-card usage-card">
      <div class="section-kicker">Retrieved Skill Call Frequency</div>
      <div class="timeline-pill-row">
        ${names.map((name) => `<span class="timeline-pill">${escapeHtml(name)}: ${escapeHtml(String(counts[name]))}</span>`).join("")}
      </div>
    </div>
  `;
}

function sumValues(obj) {
  return Object.values(obj || {}).reduce((acc, value) => acc + Number(value || 0), 0);
}

function detailKeyForSkill(name) {
  return `retrieve_skill_${name}`;
}

async function ensureMemorySession() {
  const payload = await postJson("/api/execute/memory/session", {
    skills_path: document.getElementById("execute-skills-path").value.trim(),
    copy_mode: document.getElementById("execute-copy-memory").checked,
  });
  if (!payload || !payload.session) return null;
  executeState.memorySessionId = payload.session.session_id;
  return payload.session;
}

async function refreshMemorySession(sessionId) {
  const res = await fetch(`/api/execute/memory?session_id=${encodeURIComponent(sessionId)}`);
  const payload = await res.json();
  if (!res.ok) {
    setExecuteJobStatus(payload.error || "Failed to refresh memory session.");
    return null;
  }
  const run = executeState.currentRun || executeState.currentJob?.partial_result || {};
  const nextRun = {
    ...(executeState.currentRun || {}),
    memory: payload.session,
    retrieve: {
      ...(executeState.currentRun?.retrieve || {}),
      memory: payload.session,
      retrieved_skills: payload.skills,
      retrieved_workflows: payload.workflows,
      workflow_store_count: payload.session.workflow_count,
    },
  };
  executeState.currentRun = nextRun;
  renderExecuteFromRun(nextRun, executeState.currentJob);
  return payload;
}

async function saveMemorySkill(sessionId) {
  const payload = await postJson("/api/execute/memory/skill", {
    session_id: sessionId,
    name: document.getElementById("memory-skill-name").value.trim(),
    description: document.getElementById("memory-skill-description").value.trim(),
    code: document.getElementById("memory-skill-code").value,
    test_code: document.getElementById("memory-skill-test-code").value,
  });
  if (!payload) return;
  await refreshMemorySession(sessionId);
}

async function deleteMemorySkill(sessionId, name) {
  const payload = await postJson("/api/execute/memory/skill/delete", {
    session_id: sessionId,
    name,
  });
  if (!payload) return;
  await refreshMemorySession(sessionId);
}

async function saveMemoryWorkflow(sessionId) {
  const payload = await postJson("/api/execute/memory/workflow", {
    session_id: sessionId,
    query: document.getElementById("memory-workflow-query").value.trim(),
    workflow_summary: document.getElementById("memory-workflow-summary").value,
    workflow_plan: document.getElementById("memory-workflow-plan").value,
    workflow_decision: document.getElementById("memory-workflow-decision").value.trim(),
  });
  if (!payload) return;
  await refreshMemorySession(sessionId);
}

function hashKey(value) {
  let hash = 0;
  const text = String(value || "");
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash << 5) - hash) + text.charCodeAt(i);
    hash |= 0;
  }
  return String(Math.abs(hash));
}

function stepTone(type) {
  if (type === "code") return "step-code";
  if (type === "exec_output") return "step-output";
  if (type === "assistant_raw") return "step-assistant";
  return "step-generic";
}

function stepPreClass(type) {
  if (type === "code") return "code-block";
  if (type === "exec_output") return "output-block";
  return "text-block";
}

function messageTone(role) {
  if (role === "system") return "msg-system";
  if (role === "user") return "msg-user";
  if (role === "assistant") return "msg-assistant";
  if (role === "tool") return "msg-tool";
  return "msg-generic";
}

function messagePreClass(role) {
  if (role === "system") return "prompt-block";
  if (role === "tool") return "output-block";
  return "text-block";
}

function setExecuteJobStatus(text) {
  document.getElementById("execute-job-status").textContent = text;
}

async function pollJob(jobId, onUpdate) {
  while (true) {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
    const payload = await res.json();
    onUpdate(payload);
    if (payload.status === "completed" || payload.status === "failed") break;
    await sleep(700);
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
    setExecuteJobStatus(payload.error || `Request failed: ${url}`);
    return null;
  }
  return payload;
}

function formatJson(value) {
  return JSON.stringify(value, null, 2);
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

window.setExecuteSkillsPath = setExecuteSkillsPath;
window.setExecuteViewMode = setExecuteViewMode;
window.saveMemorySkill = saveMemorySkill;
window.deleteMemorySkill = deleteMemorySkill;
window.saveMemoryWorkflow = saveMemoryWorkflow;
window.refreshMemorySession = refreshMemorySession;
