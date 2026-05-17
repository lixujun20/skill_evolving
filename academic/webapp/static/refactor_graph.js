const state = {
  experiments: [],
  selectedId: "",
  data: null,
  frame: 0,
  mode: "macro",
  showEdgeLabels: true,
  fadeUnrelated: true,
  skillFilter: "",
  selected: null,
  loading: false,
  error: "",
  drag: null,
  pan: null,
  view: { x: 0, y: 0, k: 1 },
  positions: {},
  loadStartedAt: 0,
  loadTimer: null,
  refreshingFrame: false,
  shellWidth: 280,
  taskWidth: 270,
  inspectorWidth: 360,
  resizing: null,
};

document.addEventListener("DOMContentLoaded", async () => {
  bindControls();
  initResizablePanes();
  setMode(modeFromUrl(), { skipRender: true });
  renderShell();
  await loadExperiments();
  autoLoadFromUrl();
});

function bindControls() {
  byId("refactor-search")?.addEventListener("input", renderExperimentList);
  byId("refactor-prev")?.addEventListener("click", () => setFrame(state.frame - 1));
  byId("refactor-next")?.addEventListener("click", () => setFrame(state.frame + 1));
  byId("refactor-frame")?.addEventListener("input", (ev) => setFrame(Number(ev.target.value)));
  byId("refactor-edge-labels")?.addEventListener("change", (ev) => {
    state.showEdgeLabels = Boolean(ev.target.checked);
    render();
  });
  byId("refactor-fade-edges")?.addEventListener("change", (ev) => {
    state.fadeUnrelated = Boolean(ev.target.checked);
    render();
  });
  byId("refactor-skill-filter")?.addEventListener("change", (ev) => {
    state.skillFilter = ev.target.value || "";
    state.selected = null;
    syncUrl();
    render();
  });
  byId("refactor-mode-macro")?.addEventListener("click", () => setMode("macro"));
  byId("refactor-mode-micro")?.addEventListener("click", () => setMode("micro"));
  byId("refactor-diff-close")?.addEventListener("click", closeDiffPanel);
  document.querySelector("[data-close-diff]")?.addEventListener("click", closeDiffPanel);

  const svg = byId("refactor-svg");
  svg?.addEventListener("wheel", onWheelZoom, { passive: false });
  svg?.addEventListener("mousedown", onCanvasMouseDown);
  svg?.addEventListener("click", (ev) => {
    if (ev.target === svg || ev.target.classList?.contains("rg-canvas-hit")) {
      state.selected = null;
      render();
    }
  });
  window.addEventListener("mouseup", () => {
    if (state.drag) savePositions();
    state.drag = null;
    state.pan = null;
  });
  window.addEventListener("mousemove", onDragMove);
  window.addEventListener("mousemove", onPanMove);
  window.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeDiffPanel();
  });
}

async function loadExperiments() {
  setLoading("Loading refactor experiments", "Scanning compact refactor debug events.");
  try {
    const res = await fetch("/api/refactor-graph/experiments");
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || res.statusText);
    state.experiments = data.experiments || [];
    clearLoading();
    renderExperimentList();
  } catch (err) {
    showError("Failed to load experiments", err);
  }
}

async function loadGraph(id, options = {}) {
  state.selectedId = id;
  state.data = null;
  state.selected = null;
  state.skillFilter = options.keepFilter ? state.skillFilter : "";
  state.view = { x: 0, y: 0, k: 1 };
  state.positions = readPositions(id);
  renderExperimentList();
  setLoading("Loading compact graph projection", id);
  try {
    const params = new URLSearchParams(window.location.search);
    const url = new URL("/api/refactor-graph", window.location.origin);
    url.searchParams.set("id", id);
    url.searchParams.set("mode", state.mode);
    for (const key of ["task_id", "skill", "frame", "attempt"]) {
      const value = params.get(key);
      if (value) url.searchParams.set(key, value);
    }
    const res = await fetch(url.toString());
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || res.statusText);
    state.data = data;
    state.frame = Number.isFinite(data.selected_frame_index) ? data.selected_frame_index : 0;
    applyUrlState();
    renderSkillFilter();
    syncUrl({ replace: true });
    clearLoading();
    render();
  } catch (err) {
    showError("Failed to load graph projection", err);
  }
}

function autoLoadFromUrl() {
  const id = new URLSearchParams(window.location.search).get("id");
  if (id && state.experiments.some((item) => item.id === id)) {
    loadGraph(id);
  } else {
    clearLoading();
    render();
  }
}

function setLoading(title, detail) {
  state.loading = true;
  state.error = "";
  state.loadStartedAt = Date.now();
  updateLoading(title, detail, 0);
  clearInterval(state.loadTimer);
  state.loadTimer = setInterval(() => {
    const elapsed = Math.floor((Date.now() - state.loadStartedAt) / 1000);
    updateLoading(title, detail, elapsed);
  }, 1000);
}

function updateLoading(title, detail, elapsed) {
  const node = byId("refactor-loading");
  if (!node) return;
  node.hidden = false;
  node.innerHTML = `
    <span class="spinner"></span>
    <strong>${escapeHtml(title)}</strong>
    <span title="${escapeAttr(detail || "")}">${escapeHtml(detail || "")}</span>
    <small>${elapsed}s</small>
  `;
}

function clearLoading() {
  state.loading = false;
  clearInterval(state.loadTimer);
  state.loadTimer = null;
  const node = byId("refactor-loading");
  if (node) node.hidden = true;
}

function showError(title, err) {
  clearLoading();
  state.error = `${title}: ${err?.message || String(err)}`;
  const node = byId("refactor-error");
  if (node) {
    node.hidden = false;
    node.textContent = state.error;
  }
  render();
}

function renderShell() {
  byId("refactor-mode-macro")?.classList.toggle("active", state.mode === "macro");
  byId("refactor-mode-micro")?.classList.toggle("active", state.mode === "micro");
  byId("refactor-mode-help").textContent = state.mode === "macro"
    ? "Macro: skill-level overlap and change impact."
    : "Micro: current-frame segment similarity and evidence.";
}

function render() {
  renderShell();
  renderExperimentList();
  if (!state.data) {
    byId("refactor-title").textContent = state.selectedId || "Select an experiment";
    byId("refactor-frame-count").textContent = "0 frames";
    byId("refactor-task-timeline").innerHTML = `<div class="rg-empty">Select an experiment to inspect task-level refactors.</div>`;
    byId("refactor-frame-change").innerHTML = `<div class="rg-empty">Frame change summary will appear here.</div>`;
    byId("refactor-inspector-content").innerHTML = `<div class="rg-empty">Select a frame, node, edge, or diff.</div>`;
    drawEmptyGraph();
    return;
  }
  const frames = state.data.frames || [];
  state.frame = clamp(state.frame, 0, Math.max(frames.length - 1, 0));
  const frame = currentFrame();
  const change = primaryChangeForFrame(state.frame);
  byId("refactor-title").textContent = state.data.experiment?.title || state.data.experiment?.id || state.selectedId;
  byId("refactor-frame-count").textContent = `${frames.length} frames`;
  renderStats(frame, change);
  renderTaskTimeline();
  renderFrameControls(frame, change);
  renderFrameChange(frame, change);
  renderGraph();
  renderInspector();
}

function renderExperimentList() {
  const list = byId("refactor-experiment-list");
  if (!list) return;
  const q = (byId("refactor-search")?.value || "").toLowerCase();
  const items = state.experiments.filter((item) => JSON.stringify(item).toLowerCase().includes(q));
  list.innerHTML = items.map((item) => `
    <li class="${item.id === state.selectedId ? "active" : ""}" data-id="${escapeAttr(item.id)}">
      <strong>${escapeHtml(item.title || item.id)}</strong>
      <span>${item.n_segments || 0} segments · ${item.n_edges || 0} edges</span>
      <small>${item.n_commits || 0} commits · ${item.n_rejections || 0} rejected</small>
    </li>
  `).join("") || `<li class="rg-muted">No refactor graph experiments found.</li>`;
  list.querySelectorAll("[data-id]").forEach((el) => {
    el.addEventListener("click", () => loadGraph(el.dataset.id));
  });
}

function renderStats(frame, change) {
  const graph = graphForFrame(frame);
  const segments = segmentsForFrame(frame);
  const edges = edgesForFrame(frame, graph);
  const diffs = skillDiffsForChange(change);
  byId("refactor-stats").innerHTML = [
    ["Task", frame?.task_id || "experiment"],
    ["Step", change?.title || semanticEventLabel(frame?.event_type)],
    ["Segments", segments.length],
    ["Edges", edges.length],
    ["Diffs", diffs.length],
  ].map(([k, v]) => `<span><b>${escapeHtml(v)}</b>${escapeHtml(k)}</span>`).join("");
}

function renderTaskTimeline() {
  const mount = byId("refactor-task-timeline");
  const groups = state.data?.task_frames || [];
  if (!groups.length) {
    mount.innerHTML = `<div class="rg-empty">No task frames were projected.</div>`;
    return;
  }
  mount.innerHTML = groups.map((group) => `
    <section class="rg-task-group">
      <header>
        <strong>${escapeHtml(group.task_label || group.task_id)}</strong>
        <span>${(group.frames || []).length} frames · ${group.n_changes || 0} changes</span>
      </header>
      ${(group.frames || []).map((frame) => renderTimelineFrame(frame)).join("")}
    </section>
  `).join("");
  mount.querySelectorAll("[data-frame]").forEach((button) => {
    button.addEventListener("click", () => {
      setFrame(Number(button.dataset.frame), { changeId: button.dataset.change || "" });
    });
  });
}

function renderTimelineFrame(frame) {
  const active = Number(frame.frame_index) === state.frame;
  const skills = frame.affected_skills || [];
  return `
    <button type="button" class="rg-frame-item ${active ? "active" : ""} ${escapeAttr(frame.status || "")}" data-frame="${frame.frame_index}" data-change="${escapeAttr(frame.change_id || "")}">
      <span>${escapeHtml(frame.step_label || semanticEventLabel(frame.event_type))}</span>
      <strong>${escapeHtml(frame.attempt_id || `frame ${Number(frame.frame_index) + 1}`)}</strong>
      <small>${skills.length ? escapeHtml(skills.slice(0, 3).join(", ")) : "evidence only"}</small>
    </button>
  `;
}

function renderFrameControls(frame, change) {
  const frames = state.data?.frames || [];
  const slider = byId("refactor-frame");
  slider.max = Math.max(frames.length - 1, 0);
  slider.value = String(state.frame);
  byId("refactor-frame-label").textContent = frame
    ? `${state.frame + 1}/${frames.length} · ${frame.task_id || "experiment"} · ${change?.title || semanticEventLabel(frame.event_type)}`
    : "No frame selected";
}

function renderFrameChange(frame, change) {
  const mount = byId("refactor-frame-change");
  const changes = changesForFrame(state.frame);
  const primary = change || changes[0];
  if (!frame) {
    mount.innerHTML = `<div class="rg-empty">No frame selected.</div>`;
    return;
  }
  const diffs = skillDiffsForChange(primary);
  mount.innerHTML = `
    <div class="rg-change-head">
      <div>
        <span>${escapeHtml(primary?.status || "evidence")}</span>
        <h3>${escapeHtml(primary?.title || semanticEventLabel(frame.event_type))}</h3>
      </div>
      <strong>${escapeHtml(frame.task_id || "experiment")}</strong>
    </div>
    <p>${escapeHtml(primary?.summary || "Overlap evidence frame.")}</p>
    <div class="rg-change-grid">
      <span><b>${escapeHtml(primary?.attempt_id || `frame ${state.frame + 1}`)}</b>Attempt</span>
      <span><b>${(primary?.affected_skills || []).length}</b>Affected skills</span>
      <span><b>${(primary?.related_segments || []).length}</b>Related segments</span>
      <span><b>${diffs.length}</b>Diffs</span>
    </div>
    ${renderDiffButtons(diffs)}
    ${changes.length > 1 ? `<div class="rg-secondary-changes">${changes.slice(1).map((item) => `
      <button type="button" data-change="${escapeAttr(item.change_id)}">${escapeHtml(item.title)} · ${escapeHtml(item.status || "")}</button>
    `).join("")}</div>` : ""}
  `;
  mount.querySelectorAll("[data-diff]").forEach((button) => {
    button.addEventListener("click", () => openDiffPanel(button.dataset.diff));
  });
  mount.querySelectorAll("[data-change]").forEach((button) => {
    button.addEventListener("click", () => {
      const selected = changes.find((item) => item.change_id === button.dataset.change);
      if (selected) {
        state.selected = { type: "change", ...selected };
        renderInspector();
      }
    });
  });
}

function renderDiffButtons(diffs) {
  if (!diffs.length) return `<div class="rg-muted">No skill diff attached to this frame.</div>`;
  return `<div class="rg-diff-buttons">${diffs.map((diff) => `
    <button type="button" data-diff="${escapeAttr(diff.diff_id)}">
      <span>${escapeHtml(diff.change_kind || "change")}</span>
      <strong>${escapeHtml(diff.skill_name || "skill")}</strong>
      <small>${(diff.field_diffs || []).length} fields changed</small>
    </button>
  `).join("")}</div>`;
}

function renderGraph() {
  const svg = byId("refactor-svg");
  const empty = byId("refactor-graph-empty");
  const frame = currentFrame();
  if (!svg || !frame) {
    drawEmptyGraph();
    return;
  }
  const graph = graphForFrame(frame);
  const segments = segmentsForFrame(frame);
  const edges = edgesForFrame(frame, graph);
  if (!segments.length) {
    drawEmptyGraph("No segments in this frame.");
    return;
  }
  empty.hidden = true;
  const w = svg.clientWidth || 960;
  const h = svg.clientHeight || 620;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const model = state.mode === "macro"
    ? buildMacroModel(segments, edges, w, h)
    : buildMicroModel(segments, edges, w, h);
  svg.innerHTML = `
    <rect class="rg-canvas-hit" x="0" y="0" width="${w}" height="${h}"></rect>
    <g transform="translate(${state.view.x},${state.view.y}) scale(${state.view.k})">
      <g>${(model.groups || []).map(groupMarkup).join("")}</g>
      <g>${model.edges.map((edge) => edgeMarkup(edge, model.nodesById)).join("")}</g>
      <g>${model.nodes.map(nodeMarkup).join("")}</g>
    </g>
  `;
  bindGraphElements(svg, model);
}

function drawEmptyGraph(message = "Select an experiment to render the graph.") {
  const svg = byId("refactor-svg");
  if (svg) svg.innerHTML = "";
  const empty = byId("refactor-graph-empty");
  if (empty) {
    empty.hidden = false;
    empty.textContent = message;
  }
}

function bindGraphElements(svg, model) {
  svg.querySelectorAll("[data-node]").forEach((el) => {
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      state.selected = model.nodesById[el.dataset.node]?.item || null;
      render();
    });
    el.addEventListener("mousedown", (ev) => {
      if (ev.button !== 0) return;
      const node = model.nodesById[el.dataset.node];
      if (!node) return;
      const point = svgPoint(ev);
      state.drag = { id: node.id, dx: node.x - point.x, dy: node.y - point.y };
      ev.stopPropagation();
      ev.preventDefault();
    });
  });
  svg.querySelectorAll("[data-edge]").forEach((el) => {
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      state.selected = model.edgesById[el.dataset.edge]?.item || null;
      render();
    });
  });
}

function buildMacroModel(segments, edges, w, h) {
  const skillMap = groupSegmentsBySkill(segments);
  const groups = Array.from(skillMap.values()).sort((a, b) => a.name.localeCompare(b.name));
  const nodes = groups.map((group, index) => {
    const pos = stablePosition(`macro:${group.name}`, index, groups.length, w, h);
    return {
      id: `skill:${group.name}`,
      x: pos.x,
      y: pos.y,
      r: group.unassigned ? 28 : 36,
      label: group.name,
      sublabel: group.unassigned ? "unassigned evidence" : `${group.segments.length} seg`,
      kind: group.unassigned ? "unassigned" : "skill",
      item: { type: "skill", ...group },
    };
  });
  const segmentToGroup = {};
  for (const group of groups) {
    for (const seg of group.segments) segmentToGroup[seg.segment_id] = group.name;
  }
  const edgeMap = new Map();
  for (const edge of edges || []) {
    const a = segmentToGroup[edge.source] || groupNameForEdgeEndpoint(edge.source, edge.source_task_id);
    const b = segmentToGroup[edge.target] || groupNameForEdgeEndpoint(edge.target, edge.target_task_id);
    if (!a || !b || a === b) continue;
    const key = [a, b].sort().join("|");
    const existing = edgeMap.get(key);
    if (!existing || Number(edge.weight || 0) > Number(existing.weight || 0)) {
      edgeMap.set(key, {
        id: `macro:${key}`,
        source: `skill:${a}`,
        target: `skill:${b}`,
        weight: Number(edge.weight || 0),
        item: { type: "macro_edge", source_skill: a, target_skill: b, max_weight_edge: edge },
      });
    }
  }
  return finalizeModel(nodes, Array.from(edgeMap.values()));
}

function buildMicroModel(segments, edges, w, h) {
  const groups = Array.from(groupSegmentsBySkill(segments).values()).sort((a, b) => a.name.localeCompare(b.name));
  const nodes = [];
  const hulls = [];
  groups.forEach((group, groupIndex) => {
    const center = stablePosition(`group:${group.name}`, groupIndex, groups.length, w, h);
    const count = Math.max(group.segments.length, 1);
    const cols = Math.ceil(Math.sqrt(count));
    const rows = Math.ceil(count / cols);
    const cell = 84;
    const box = {
      id: `hull:${group.name}`,
      name: group.name,
      x: center.x - (cols * cell) / 2,
      y: center.y - (rows * cell) / 2,
      w: cols * cell,
      h: rows * cell,
      unassigned: group.unassigned,
    };
    hulls.push(box);
    group.segments.forEach((seg, idx) => {
      const key = `${group.name}::${seg.segment_id}`;
      const stored = state.positions[`micro:${key}`] || state.positions[`micro:${seg.segment_id}`];
      nodes.push({
        id: `seg:${seg.segment_id}`,
        x: stored?.x ?? box.x + 42 + (idx % cols) * cell,
        y: stored?.y ?? box.y + 48 + Math.floor(idx / cols) * cell,
        r: 23,
        label: seg.task_id || seg.segment_id,
        sublabel: seg.turn_index == null ? "segment" : `turn ${seg.turn_index}`,
        kind: "segment",
        skill: group.name,
        item: { type: "segment", skill_name: group.name, ...seg },
      });
    });
  });
  const edgeModels = (edges || []).map((edge) => ({
    id: `micro:${edge.source}|${edge.target}`,
    source: `seg:${edge.source}`,
    target: `seg:${edge.target}`,
    weight: Number(edge.weight || 0),
    item: { type: "edge", ...edge },
  }));
  return finalizeModel(nodes, edgeModels, hulls);
}

function finalizeModel(nodes, edges, groups = []) {
  const nodesById = Object.fromEntries(nodes.map((node) => [node.id, node]));
  const focusIds = selectedFocusNodeIds(nodes);
  const visibleIds = visibleNodeIds(nodes, edges, focusIds);
  const edgesById = {};
  const filteredEdges = edges
    .filter((edge) => nodesById[edge.source] && nodesById[edge.target])
    .filter((edge) => edgeVisible(edge, nodesById, focusIds))
    .map((edge) => {
      const row = {
        ...edge,
        faded: false,
        selected: state.selected?.type === "edge" && state.selected?.source === edge.item?.source && state.selected?.target === edge.item?.target,
      };
      edgesById[row.id] = row;
      return row;
    });
  const finalNodes = nodes.map((node) => ({
    ...node,
    faded: state.fadeUnrelated && ((state.skillFilter && node.skill !== state.skillFilter && node.item?.name !== state.skillFilter) ||
      (visibleIds.size && !visibleIds.has(node.id))),
  }));
  return { nodes: finalNodes, edges: filteredEdges, nodesById, edgesById, groups };
}

function edgeVisible(edge, nodesById, focusIds) {
  const a = nodesById[edge.source];
  const b = nodesById[edge.target];
  if (!a || !b) return false;
  if (state.skillFilter) {
    const matchesFilter = a.skill === state.skillFilter || b.skill === state.skillFilter ||
      a.item?.name === state.skillFilter || b.item?.name === state.skillFilter;
    if (!matchesFilter) return false;
  }
  if (focusIds.size) {
    return focusIds.has(edge.source) || focusIds.has(edge.target);
  }
  return true;
}

function selectedFocusNodeIds(nodes) {
  const ids = new Set();
  const selected = state.selected;
  if (!selected) return ids;
  if (selected.type === "skill" && selected.name) {
    nodes.forEach((node) => {
      if (node.item?.name === selected.name || node.skill === selected.name) ids.add(node.id);
    });
  }
  if (selected.type === "segment" && selected.segment_id) {
    nodes.forEach((node) => {
      if (node.item?.segment_id === selected.segment_id) ids.add(node.id);
    });
  }
  if (selected.type === "edge" && selected.source && selected.target) {
    ids.add(`seg:${selected.source}`);
    ids.add(`seg:${selected.target}`);
  }
  if (selected.type === "macro_edge" && selected.source_skill && selected.target_skill) {
    ids.add(`skill:${selected.source_skill}`);
    ids.add(`skill:${selected.target_skill}`);
  }
  return ids;
}

function visibleNodeIds(nodes, edges, focusIds) {
  const ids = new Set(focusIds);
  if (!ids.size) return ids;
  edges.forEach((edge) => {
    if (focusIds.has(edge.source) || focusIds.has(edge.target)) {
      ids.add(edge.source);
      ids.add(edge.target);
    }
  });
  return ids;
}

function edgeMarkup(edge, nodesById) {
  const a = nodesById[edge.source];
  const b = nodesById[edge.target];
  if (!a || !b) return "";
  const width = 1 + Math.min(5, Number(edge.weight || 0) * 8);
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  return `
    <g class="rg-edge ${edge.selected ? "selected" : ""}" data-edge="${escapeAttr(edge.id)}">
      <line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${edgeColor(edge.weight)}" stroke-width="${width}"></line>
      ${state.showEdgeLabels ? `<text x="${mx}" y="${my - 5}">${formatWeight(edge.weight)}</text>` : ""}
    </g>
  `;
}

function nodeMarkup(node) {
  return `
    <g class="rg-node ${escapeAttr(node.kind)} ${node.faded ? "faded" : ""}" data-node="${escapeAttr(node.id)}" transform="translate(${node.x},${node.y})">
      <circle r="${node.r}"></circle>
      <text y="-3">${escapeHtml(short(node.label, state.mode === "macro" ? 18 : 12))}</text>
      <text y="12" class="tiny">${escapeHtml(short(node.sublabel, 24))}</text>
    </g>
  `;
}

function groupMarkup(group) {
  return `
    <g class="rg-hull ${group.unassigned ? "unassigned" : ""}">
      <rect x="${group.x - 12}" y="${group.y - 20}" width="${group.w + 24}" height="${group.h + 42}" rx="12"></rect>
      <text x="${group.x}" y="${group.y - 28}">${escapeHtml(short(group.name, 40))}</text>
    </g>
  `;
}

function renderInspector() {
  const mount = byId("refactor-inspector-content");
  const selected = state.selected || { type: "frame", frame: currentFrame(), change: primaryChangeForFrame(state.frame) };
  mount.innerHTML = renderInspectorItem(selected);
  mount.querySelectorAll("[data-diff]").forEach((button) => {
    button.addEventListener("click", () => openDiffPanel(button.dataset.diff));
  });
  mount.querySelectorAll("[data-change]").forEach((button) => {
    button.addEventListener("click", () => {
      const change = (state.data?.frame_changes || []).find((item) => item.change_id === button.dataset.change);
      if (change) {
        state.selected = { type: "change", ...change };
        renderInspector();
      }
    });
  });
}

function renderInspectorItem(item) {
  if (!item) return `<div class="rg-empty">Select a frame, node, edge, or diff.</div>`;
  if (item.type === "frame") return renderFrameInspector(item.frame, item.change);
  if (item.type === "change") return renderChangeInspector(item);
  if (item.type === "skill") return renderSkillInspector(item);
  if (item.type === "segment") return renderSegmentInspector(item);
  if (item.type === "edge" || item.type === "macro_edge") return renderEdgeInspector(item);
  return `<pre>${escapeHtml(JSON.stringify(item, null, 2))}</pre>`;
}

function renderFrameInspector(frame, change) {
  const changes = changesForFrame(state.frame);
  return `
    <div class="rg-inspector-head">
      <span>Current Frame</span>
      <h3>${escapeHtml(change?.title || semanticEventLabel(frame?.event_type))}</h3>
    </div>
    ${renderChangeInspector(change)}
    <section class="rg-card">
      <b>All changes in this frame</b>
      ${changes.map((item) => `<button type="button" class="rg-inline-button" data-change="${escapeAttr(item.change_id)}">${escapeHtml(item.title)} · ${escapeHtml(item.status || "")}</button>`).join("") || `<span class="rg-muted">No semantic changes.</span>`}
    </section>
  `;
}

function renderChangeInspector(change) {
  if (!change) return `<section class="rg-card"><b>Change</b><span class="rg-muted">No change attached.</span></section>`;
  const diffs = skillDiffsForChange(change);
  return `
    <section class="rg-card">
      <b>Change Summary</b>
      <h4>${escapeHtml(change.title || "Change")}</h4>
      <p>${escapeHtml(change.summary || "")}</p>
      <dl class="rg-dl">
        <dt>Status</dt><dd>${escapeHtml(change.status || "")}</dd>
        <dt>Attempt</dt><dd>${escapeHtml(change.attempt_id || "")}</dd>
        <dt>Task</dt><dd>${escapeHtml(change.task_id || "")}</dd>
      </dl>
      ${change.decision?.reason ? `<div class="rg-reason">${escapeHtml(change.decision.reason)}</div>` : ""}
      ${renderDiffButtons(diffs)}
      ${renderTestSummary(change.test_summary)}
    </section>
  `;
}

function renderSkillInspector(item) {
  const changes = (state.data?.frame_changes || []).filter((change) => (change.affected_skills || []).includes(item.name));
  const diffs = (state.data?.skill_diffs || []).filter((diff) => diff.skill_name === item.name);
  return `
    <div class="rg-inspector-head"><span>${item.unassigned ? "Unassigned Evidence" : "Skill"}</span><h3>${escapeHtml(item.name)}</h3></div>
    <section class="rg-card"><b>Segments</b>${renderSegmentList(item.segments || [])}</section>
    <section class="rg-card"><b>Changes touching this skill</b>${changes.map((change) => `<div class="rg-mini-row">${escapeHtml(change.title)}<span>${escapeHtml(change.status || "")}</span></div>`).join("") || `<span class="rg-muted">No semantic change recorded.</span>`}</section>
    <section class="rg-card"><b>Diffs</b>${renderDiffButtons(diffs)}</section>
  `;
}

function renderSegmentInspector(item) {
  const changes = (state.data?.frame_changes || []).filter((change) => (change.related_segments || []).includes(item.segment_id));
  return `
    <div class="rg-inspector-head"><span>Segment</span><h3>${escapeHtml(item.segment_id || item.task_id || "segment")}</h3></div>
    <section class="rg-card"><b>Task</b>${escapeHtml(item.task_id || "")} ${item.turn_index == null ? "" : `· turn ${escapeHtml(item.turn_index)}`}</section>
    <section class="rg-card"><b>Related Changes</b>${changes.map((change) => `<div class="rg-mini-row">${escapeHtml(change.title)}<span>${escapeHtml(change.status || "")}</span></div>`).join("") || `<span class="rg-muted">No related changes.</span>`}</section>
    <section class="rg-card"><b>Segment Text</b><pre>${escapeHtml(item.text || "")}</pre></section>
    ${item.error_text ? `<section class="rg-card danger"><b>Error Text</b><pre>${escapeHtml(item.error_text)}</pre></section>` : ""}
  `;
}

function renderEdgeInspector(item) {
  const edge = item.max_weight_edge || item;
  return `
    <div class="rg-inspector-head"><span>Similarity Edge</span><h3>${escapeHtml(formatWeight(edge.weight))}</h3></div>
    <section class="rg-card"><b>Source</b>${escapeHtml(item.source_skill || edge.source || "")}</section>
    <section class="rg-card"><b>Target</b>${escapeHtml(item.target_skill || edge.target || "")}</section>
    <section class="rg-card"><b>Shared ngrams</b>${escapeHtml((edge.shared_ngrams || []).join(", ") || "none")}</section>
    <section class="rg-card"><b>Shared error ngrams</b>${escapeHtml((edge.shared_error_ngrams || []).join(", ") || "none")}</section>
  `;
}

function openDiffPanel(diffId) {
  const diff = (state.data?.skill_diffs || []).find((item) => item.diff_id === diffId);
  if (!diff) return;
  byId("refactor-diff-panel").hidden = false;
  byId("refactor-diff-title").textContent = diff.skill_name || "Skill diff";
  byId("refactor-diff-kicker").textContent = `${diff.change_kind || "change"} · ${diff.status || "recorded"}`;
  byId("refactor-diff-content").innerHTML = renderDiffPanel(diff);
}

function closeDiffPanel() {
  const panel = byId("refactor-diff-panel");
  if (panel) panel.hidden = true;
}

function renderDiffPanel(diff) {
  return `
    <section class="rg-diff-summary">
      <div><b>Before</b>${renderSkillSummary(diff.before)}</div>
      <div><b>After</b>${renderSkillSummary(diff.after)}</div>
    </section>
    <section class="rg-card">
      <b>Why this changed</b>
      <p>${escapeHtml(diff.decision?.reason || "No explicit decision reason recorded in compact projection.")}</p>
      <dl class="rg-dl">
        <dt>Task</dt><dd>${escapeHtml(diff.task_id || "")}</dd>
        <dt>Attempt</dt><dd>${escapeHtml(diff.attempt_id || "")}</dd>
        <dt>Source segments</dt><dd>${escapeHtml((diff.source?.segment_ids || []).join(", ") || "none")}</dd>
      </dl>
      ${renderTestSummary(diff.tests)}
    </section>
    <section class="rg-card">
      <b>Field Changes</b>
      ${(diff.field_diffs || []).map((field) => `
        <details class="rg-field-diff" open>
          <summary>${escapeHtml(field.field)}</summary>
          <div><strong>Before</strong><pre>${escapeHtml(formatValue(field.before))}</pre></div>
          <div><strong>After</strong><pre>${escapeHtml(formatValue(field.after))}</pre></div>
        </details>
      `).join("") || `<span class="rg-muted">No field-level change recorded.</span>`}
    </section>
    <section class="rg-card">
      <b>Unified Diff</b>
      <div class="rg-unified-diff">${renderUnifiedDiff(diff.line_diff || [])}</div>
    </section>
  `;
}

function renderSkillSummary(skill) {
  if (!skill || !Object.keys(skill).length) return `<span class="rg-muted">No prior skill snapshot.</span>`;
  return `
    <h4>${escapeHtml(skill.name || "skill")}</h4>
    <p>${escapeHtml(skill.description || "")}</p>
    <dl class="rg-dl">
      <dt>Version</dt><dd>${escapeHtml(skill.version ?? "")}</dd>
      <dt>Status</dt><dd>${escapeHtml(skill.status || "")}</dd>
      <dt>Tools</dt><dd>${escapeHtml((skill.allowed_tools || []).join(", ") || "none")}</dd>
    </dl>
  `;
}

function renderUnifiedDiff(lines) {
  if (!lines.length) return `<div class="rg-diff-line same"><span></span><pre>No content change recorded.</pre></div>`;
  return lines.map((line) => {
    const cls = line.startsWith("+") && !line.startsWith("+++") ? "added" :
      line.startsWith("-") && !line.startsWith("---") ? "removed" :
      line.startsWith("@@") ? "hunk" : "same";
    return `<div class="rg-diff-line ${cls}"><span>${escapeHtml(line.slice(0, 1))}</span><pre>${escapeHtml(line)}</pre></div>`;
  }).join("");
}

function renderTestSummary(summary) {
  if (!summary || !summary.n_results) return "";
  return `
    <div class="rg-test-summary">
      <span><b>${summary.n_passed || 0}/${summary.n_results}</b>tests pass</span>
      <span><b>${summary.n_cases || 0}</b>cases</span>
      <span><b>${escapeHtml((summary.delta_accuracy || []).join(", ") || "n/a")}</b>delta accuracy</span>
    </div>
  `;
}

function setMode(mode, options = {}) {
  state.mode = mode === "micro" ? "micro" : "macro";
  state.selected = null;
  renderShell();
  syncUrl();
  if (!options.skipRender) {
    render();
    refreshFrameEdgesIfNeeded();
  }
}

function setFrame(next, options = {}) {
  const frames = state.data?.frames || [];
  state.frame = clamp(next, 0, Math.max(frames.length - 1, 0));
  const change = options.changeId
    ? (state.data?.frame_changes || []).find((item) => item.change_id === options.changeId)
    : null;
  state.selected = change ? { type: "change", ...change } : null;
  syncUrl();
  render();
  refreshFrameEdgesIfNeeded();
}

async function refreshFrameEdgesIfNeeded() {
  if (state.mode !== "micro" || !state.selectedId || state.refreshingFrame) return;
  if (state.frame === Number(state.data?.selected_frame_index || 0)) return;
  state.refreshingFrame = true;
  try {
    const frame = currentFrame();
    const url = new URL("/api/refactor-graph", window.location.origin);
    url.searchParams.set("id", state.selectedId);
    url.searchParams.set("mode", state.mode);
    url.searchParams.set("frame", String(state.frame));
    if (frame?.task_id) url.searchParams.set("task_id", frame.task_id);
    const res = await fetch(url.toString());
    const data = await res.json();
    if (!data.error) {
      state.data.all_pair_edges = data.all_pair_edges || state.data.all_pair_edges || [];
      state.data.selected_frame_index = data.selected_frame_index;
    }
  } finally {
    state.refreshingFrame = false;
    render();
  }
}

function applyUrlState() {
  const params = new URLSearchParams(window.location.search);
  const frameParam = params.get("frame");
  const taskId = params.get("task_id");
  const skill = params.get("skill");
  const mode = params.get("mode");
  if (mode) state.mode = mode === "micro" ? "micro" : "macro";
  if (skill) state.skillFilter = skill;
  if (frameParam && /^\d+$/.test(frameParam)) state.frame = Number(frameParam);
  if (taskId) {
    const index = (state.data?.frames || []).findIndex((frame) => String(frame.task_id || "") === taskId);
    if (index >= 0) state.frame = index;
  }
}

function syncUrl(options = {}) {
  if (!state.selectedId) return;
  const params = new URLSearchParams();
  params.set("id", state.selectedId);
  params.set("mode", state.mode);
  params.set("frame", String(state.frame));
  const frame = currentFrame();
  if (frame?.task_id) params.set("task_id", frame.task_id);
  if (state.skillFilter) params.set("skill", state.skillFilter);
  const url = `${window.location.pathname}?${params.toString()}`;
  if (options.replace) window.history.replaceState({}, "", url);
  else window.history.pushState({}, "", url);
}

function renderSkillFilter() {
  const select = byId("refactor-skill-filter");
  if (!select || !state.data) return;
  const names = new Set();
  for (const diff of state.data.skill_diffs || []) if (diff.skill_name) names.add(diff.skill_name);
  for (const seg of state.data.segments || []) names.add(skillNameForSegment(seg));
  select.innerHTML = `<option value="">All skills</option>` + Array.from(names).sort().map((name) =>
    `<option value="${escapeAttr(name)}" ${name === state.skillFilter ? "selected" : ""}>${escapeHtml(name)}</option>`
  ).join("");
}

function currentFrame() {
  return (state.data?.frames || [])[state.frame];
}

function changesForFrame(frameIndex) {
  return (state.data?.frame_changes || []).filter((change) => Number(change.frame_index) === Number(frameIndex));
}

function primaryChangeForFrame(frameIndex) {
  const changes = changesForFrame(frameIndex);
  return changes.find((change) => change.kind !== "evidence") || changes[0] || null;
}

function skillDiffsForChange(change) {
  const ids = new Set(change?.skill_diff_ids || []);
  return (state.data?.skill_diffs || []).filter((diff) => ids.has(diff.diff_id));
}

function graphForFrame(frame) {
  return frame?.output?.overlap_graph || state.data?.graph || {};
}

function segmentsForFrame(frame) {
  const graph = graphForFrame(frame);
  return frame?.output?.segments || graph.segments || state.data?.segments || [];
}

function edgesForFrame(frame, graph) {
  const overlapEdges = graph?.edges || state.data?.edges || [];
  if (state.mode === "micro" && state.frame === Number(state.data?.selected_frame_index || 0)) {
    return state.data?.all_pair_edges || overlapEdges;
  }
  return overlapEdges;
}

function groupSegmentsBySkill(segments) {
  const map = new Map();
  for (const seg of segments || []) {
    const name = skillNameForSegment(seg);
    if (!map.has(name)) map.set(name, { name, segments: [], unassigned: name.startsWith("task:") });
    map.get(name).segments.push(seg);
  }
  return map;
}

function skillNameForSegment(seg) {
  return seg.skill_name || `task:${seg.task_id || seg.source_task_id || "unknown"}`;
}

function groupNameForEdgeEndpoint(segmentId, taskId) {
  const seg = (segmentsForFrame(currentFrame()) || []).find((item) => String(item.segment_id) === String(segmentId));
  return seg ? skillNameForSegment(seg) : `task:${taskId || "unknown"}`;
}

function stablePosition(key, index, total, w, h) {
  const stored = state.positions[key];
  if (stored) return stored;
  const cols = Math.max(1, Math.ceil(Math.sqrt(total)));
  const rows = Math.max(1, Math.ceil(total / cols));
  const col = index % cols;
  const row = Math.floor(index / cols);
  return {
    x: 95 + (cols === 1 ? (w - 190) / 2 : col * ((w - 190) / Math.max(cols - 1, 1))),
    y: 95 + (rows === 1 ? (h - 190) / 2 : row * ((h - 190) / Math.max(rows - 1, 1))),
  };
}

function onCanvasMouseDown(ev) {
  const svg = byId("refactor-svg");
  if (ev.button !== 0 || (ev.target !== svg && !ev.target.classList?.contains("rg-canvas-hit"))) return;
  state.pan = { x: ev.clientX, y: ev.clientY, start: { ...state.view } };
  ev.preventDefault();
}

function onPanMove(ev) {
  if (!state.pan) return;
  state.view = {
    ...state.view,
    x: state.pan.start.x + ev.clientX - state.pan.x,
    y: state.pan.start.y + ev.clientY - state.pan.y,
  };
  renderGraph();
}

function onDragMove(ev) {
  if (!state.drag) return;
  const point = svgPoint(ev);
  const key = state.drag.id.startsWith("skill:")
    ? state.drag.id.replace(/^skill:/, "macro:")
    : state.drag.id.replace(/^seg:/, "micro:");
  state.positions[key] = { x: point.x + state.drag.dx, y: point.y + state.drag.dy };
  savePositionsSoon();
  renderGraph();
}

function onWheelZoom(ev) {
  if (!state.data) return;
  ev.preventDefault();
  const svg = byId("refactor-svg");
  const rect = svg.getBoundingClientRect();
  const point = { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  const nextK = clamp(state.view.k * (ev.deltaY < 0 ? 1.12 : 0.88), 0.35, 3.2);
  const scale = nextK / state.view.k;
  state.view = {
    k: nextK,
    x: point.x - (point.x - state.view.x) * scale,
    y: point.y - (point.y - state.view.y) * scale,
  };
  renderGraph();
}

function svgPoint(ev) {
  const svg = byId("refactor-svg");
  const rect = svg.getBoundingClientRect();
  const view = svg.viewBox.baseVal;
  return {
    x: (((ev.clientX - rect.left) / Math.max(rect.width, 1)) * view.width + view.x - state.view.x) / state.view.k,
    y: (((ev.clientY - rect.top) / Math.max(rect.height, 1)) * view.height + view.y - state.view.y) / state.view.k,
  };
}

function savePositions() {
  if (!state.selectedId) return;
  localStorage.setItem(`refactor-graph-positions:${state.selectedId}`, JSON.stringify(state.positions));
}

function savePositionsSoon() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(savePositions, 150);
}

function initResizablePanes() {
  state.shellWidth = readNumber("rg-shell-width", 280);
  state.taskWidth = readNumber("rg-task-width", 270);
  state.inspectorWidth = readNumber("rg-inspector-width", 360);
  applyPaneWidths();
  addResizeHandle(".rg-rail", "shell", "ew");
  addResizeHandle(".rg-task-panel", "task", "ew");
  addResizeHandle(".rg-inspector", "inspector", "ew");
  window.addEventListener("mousemove", onPaneResizeMove);
  window.addEventListener("mouseup", stopPaneResize);
}

function addResizeHandle(selector, pane, axis) {
  const host = document.querySelector(selector);
  if (!host || host.querySelector(".rg-resize-handle")) return;
  const handle = document.createElement("div");
  handle.className = `rg-resize-handle ${axis}`;
  handle.dataset.pane = pane;
  host.appendChild(handle);
  handle.addEventListener("mousedown", (ev) => {
    const rect = host.getBoundingClientRect();
    state.resizing = {
      pane,
      startX: ev.clientX,
      startWidth: rect.width,
    };
    document.body.classList.add("rg-resizing");
    ev.preventDefault();
    ev.stopPropagation();
  });
}

function onPaneResizeMove(ev) {
  if (!state.resizing) return;
  const delta = ev.clientX - state.resizing.startX;
  if (state.resizing.pane === "shell") {
    state.shellWidth = clamp(state.resizing.startWidth + delta, 210, 520);
    localStorage.setItem("rg-shell-width", String(state.shellWidth));
  } else if (state.resizing.pane === "task") {
    state.taskWidth = clamp(state.resizing.startWidth + delta, 190, 520);
    localStorage.setItem("rg-task-width", String(state.taskWidth));
  } else if (state.resizing.pane === "inspector") {
    state.inspectorWidth = clamp(state.resizing.startWidth - delta, 260, 620);
    localStorage.setItem("rg-inspector-width", String(state.inspectorWidth));
  }
  applyPaneWidths();
}

function stopPaneResize() {
  if (!state.resizing) return;
  state.resizing = null;
  document.body.classList.remove("rg-resizing");
}

function applyPaneWidths() {
  document.documentElement.style.setProperty("--rg-shell-left", `${state.shellWidth}px`);
  document.documentElement.style.setProperty("--rg-task-width", `${state.taskWidth}px`);
  document.documentElement.style.setProperty("--rg-inspector-width", `${state.inspectorWidth}px`);
}

function readNumber(key, fallback) {
  const value = Number(localStorage.getItem(key));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function readPositions(id) {
  try {
    return JSON.parse(localStorage.getItem(`refactor-graph-positions:${id}`) || "{}");
  } catch {
    return {};
  }
}

function semanticEventLabel(eventType) {
  const labels = {
    task_overlap_graph_updated: "Build task overlap evidence",
    overlap_graph_built: "Build overlap evidence",
    refactor_llm_done: "Propose shared skill",
    refactor_commit_done: "Commit skill change",
    refactor_commit_rejected: "Reject skill change",
    refactor_attempt: "Record refactor attempt",
    extractor_done: "Extract task skills",
  };
  return labels[eventType] || eventType || "Debug event";
}

function renderSegmentList(segments) {
  if (!segments.length) return `<span class="rg-muted">No segments.</span>`;
  return `<div class="rg-segment-list">${segments.map((seg) => `
    <details>
      <summary>${escapeHtml(seg.segment_id || seg.task_id || "segment")}</summary>
      <pre>${escapeHtml(seg.text || "")}</pre>
      ${seg.error_text ? `<pre class="danger-text">${escapeHtml(seg.error_text)}</pre>` : ""}
    </details>
  `).join("")}</div>`;
}

function formatValue(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function edgeColor(weight) {
  const t = clamp(Number(weight || 0), 0, 1);
  return `hsl(${205 - t * 165}, 82%, ${58 + t * 8}%)`;
}

function formatWeight(value) {
  return Number(value || 0).toFixed(2);
}

function modeFromUrl() {
  return new URLSearchParams(window.location.search).get("mode") === "micro" ? "micro" : "macro";
}

function byId(id) {
  return document.getElementById(id);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, Number(value) || 0));
}

function short(value, n) {
  const text = String(value || "");
  return text.length > n ? `${text.slice(0, n - 1)}...` : text;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}
