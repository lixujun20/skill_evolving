const maintenanceState = {
  experiments: [],
  filtered: [],
  currentId: "",
  currentDetail: null,
  currentPlayer: null,
  currentFrameIndex: 0,
  selectedPlayerElementId: "",
  currentPageId: "",
  currentDocId: "",
  route: { view: "home" },
  selectedBoardEntityId: "",
  selectedTurnIndex: -1,
  selectedRoleTab: "summary",
  showVerbose: false,
  loadError: "",
  isResizingInspector: false,
  draggingCoreNode: null,
  suppressCoreNodeClick: false,
  collapsedPanes: new Set(),
  routeHistory: [],
  modalPayload: null,
  modalPayloadRegistry: {},
  modalPayloadCounter: 0,
  overlayStack: [],
  detailOpenState: {},
};

document.addEventListener("DOMContentLoaded", async () => {
  restoreMaintenancePaneSizes();
  restoreMaintenanceCollapsedPanes();
  bindMaintenanceEvents();
  await loadMaintenanceExperiments();
});

function bindMaintenanceEvents() {
  const searchEl = document.getElementById("maintenance-search");
  if (searchEl) {
    searchEl.addEventListener("input", renderMaintenanceList);
  }
  const toggleEl = document.getElementById("maintenance-verbosity-toggle");
  if (toggleEl) {
    toggleEl.addEventListener("click", toggleMaintenanceVerbosity);
  }
  const prevEl = document.getElementById("maintenance-prev-page");
  if (prevEl) {
    prevEl.addEventListener("click", () => shiftMaintenancePage(-1));
  }
  const nextEl = document.getElementById("maintenance-next-page");
  if (nextEl) {
    nextEl.addEventListener("click", () => shiftMaintenancePage(1));
  }
  document.addEventListener("keydown", (event) => {
    const tag = String(event.target?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || event.metaKey || event.ctrlKey || event.altKey) {
      return;
    }
    if (event.key === "ArrowLeft") {
      shiftMaintenancePage(-1);
    } else if (event.key === "ArrowRight") {
      shiftMaintenancePage(1);
    }
  });
  document.addEventListener("pointermove", resizeMaintenanceInspector);
  document.addEventListener("pointerup", stopMaintenanceInspectorResize);
  document.addEventListener("pointermove", dragCoreDataNode);
  document.addEventListener("pointerup", stopDragCoreDataNode);
  window.addEventListener("popstate", handleMaintenanceRoute);
}

function parseMaintenanceRoute() {
  const path = window.location.pathname.replace(/\/+$/, "");
  const parts = path.split("/").filter(Boolean);
  if (!["maintenance", "method-tests"].includes(parts[0])) return { view: "home", section: "maintenance" };
  const section = parts[0] === "method-tests" ? "method_tests" : "maintenance";
  if (parts.length === 1) return { view: "home", section };
  if (parts[1] === "experiment" && parts[2]) {
    const route = {
      view: "experiment",
      section,
      experimentId: decodeURIComponent(parts[2]),
    };
    if (parts[3] === "metrics") route.view = "metrics";
    if (parts[3] === "docs") route.view = "docs";
    if (parts[3] === "round" && parts[4]) {
      route.view = "round";
      route.pageId = decodeURIComponent(parts[4]);
      if (parts[5] === "executor") route.view = "executor";
      if (parts[5] === "role" && parts[6]) {
        route.view = "role";
        route.cardId = decodeURIComponent(parts[6]);
      }
      if (parts[5] === "artifact" && parts[6]) {
        route.view = "artifact";
        route.artifactId = decodeURIComponent(parts[6]);
        if (parts[7]) route.focusId = decodeURIComponent(parts.slice(7).join("/"));
      }
    }
    return route;
  }
  return { view: "home", section };
}

function pushMaintenanceRoute(route) {
  const path = maintenancePathForRoute(route);
  rememberCurrentMaintenancePath();
  window.history.pushState({}, "", path);
  handleMaintenanceRoute();
}

function replaceMaintenanceRoute(route) {
  window.history.replaceState({}, "", maintenancePathForRoute(route));
  handleMaintenanceRoute();
}

function rememberCurrentMaintenancePath() {
  const current = window.location.pathname + window.location.search + window.location.hash;
  const last = maintenanceState.routeHistory[maintenanceState.routeHistory.length - 1];
  if (current && current !== last) {
    maintenanceState.routeHistory.push(current);
    if (maintenanceState.routeHistory.length > 40) maintenanceState.routeHistory.shift();
  }
}

function goMaintenanceBack() {
  const previous = maintenanceState.routeHistory.pop();
  if (previous) {
    window.history.pushState({}, "", previous);
    handleMaintenanceRoute();
    return;
  }
  window.history.back();
}

function maintenancePathForRoute(route) {
  const root = (route?.section || currentSection()) === "method_tests" ? "/method-tests" : "/maintenance";
  if (!route?.experimentId) return root;
  const exp = encodeURIComponent(route.experimentId);
  if (route.view === "metrics") return `${root}/experiment/${exp}/metrics`;
  if (route.view === "docs") return `${root}/experiment/${exp}/docs`;
  if (route.pageId) {
    const page = encodeURIComponent(route.pageId);
    if (route.view === "executor") return `${root}/experiment/${exp}/round/${page}/executor`;
    if (route.view === "role") return `${root}/experiment/${exp}/round/${page}/role/${encodeURIComponent(route.cardId || "")}`;
    if (route.view === "artifact") {
      const focus = route.focusId ? `/${encodeURIComponent(route.focusId)}` : "";
      return `${root}/experiment/${exp}/round/${page}/artifact/${encodeURIComponent(route.artifactId || "")}${focus}`;
    }
    return `${root}/experiment/${exp}/round/${page}`;
  }
  return `${root}/experiment/${exp}`;
}

function restoreMaintenancePaneSizes() {
  const raw = Number(localStorage.getItem("maintenanceInspectorWidth") || "");
  if (Number.isFinite(raw) && raw >= 260 && raw <= 720) {
    document.documentElement.style.setProperty("--maintenance-inspector-width", `${raw}px`);
  }
}

function startMaintenanceInspectorResize(event) {
  maintenanceState.isResizingInspector = true;
  document.body.classList.add("maintenance-resizing");
  event.currentTarget?.setPointerCapture?.(event.pointerId);
  resizeMaintenanceInspector(event);
}

function resizeMaintenanceInspector(event) {
  if (!maintenanceState.isResizingInspector) return;
  const workbench = document.querySelector(".maintenance-page-workbench") || document.querySelector(".player-workspace");
  if (!workbench) return;
  const rect = workbench.getBoundingClientRect();
  const isPlayer = workbench.classList.contains("player-workspace");
  const minFlowWidth = isPlayer ? 560 : 420;
  const minInspectorWidth = isPlayer ? 320 : 260;
  const maxInspectorWidth = Math.max(minInspectorWidth, rect.width - minFlowWidth - 18);
  const next = Math.round(Math.min(maxInspectorWidth, Math.max(minInspectorWidth, rect.right - event.clientX)));
  document.documentElement.style.setProperty("--maintenance-inspector-width", `${next}px`);
  localStorage.setItem("maintenanceInspectorWidth", String(next));
}

function stopMaintenanceInspectorResize() {
  if (!maintenanceState.isResizingInspector) return;
  maintenanceState.isResizingInspector = false;
  document.body.classList.remove("maintenance-resizing");
}

function resetMaintenanceInspectorWidth(event) {
  event.preventDefault();
  document.documentElement.style.removeProperty("--maintenance-inspector-width");
  localStorage.removeItem("maintenanceInspectorWidth");
}

function restoreMaintenanceCollapsedPanes() {
  try {
    const raw = JSON.parse(localStorage.getItem("maintenanceCollapsedPanes") || "[]");
    maintenanceState.collapsedPanes = new Set(Array.isArray(raw) ? raw : []);
    applyMaintenanceCollapsedPanes();
  } catch (_err) {
    maintenanceState.collapsedPanes = new Set();
  }
}

function applyMaintenanceCollapsedPanes() {
  document.body.classList.toggle("maintenance-collapse-overview", maintenanceState.collapsedPanes.has("overview"));
  document.body.classList.toggle("maintenance-collapse-rounds", maintenanceState.collapsedPanes.has("rounds"));
  document.body.classList.toggle("maintenance-collapse-docs", maintenanceState.collapsedPanes.has("docs"));
  document.body.classList.toggle("maintenance-collapse-inspector", maintenanceState.collapsedPanes.has("inspector"));
}

function toggleMaintenancePane(pane) {
  if (maintenanceState.collapsedPanes.has(pane)) {
    maintenanceState.collapsedPanes.delete(pane);
  } else {
    maintenanceState.collapsedPanes.add(pane);
  }
  localStorage.setItem("maintenanceCollapsedPanes", JSON.stringify([...maintenanceState.collapsedPanes]));
  applyMaintenanceCollapsedPanes();
  if (maintenanceState.currentDetail) {
    renderMaintenanceDetail(maintenanceState.currentDetail);
  }
}

function paneToggleButton(pane, label) {
  const collapsed = maintenanceState.collapsedPanes.has(pane);
  return `
    <button class="pane-toggle-btn ${collapsed ? "pane-collapsed" : ""}" title="${collapsed ? `Show ${label}` : `Hide ${label}`}" onclick="toggleMaintenancePane('${escapeJs(pane)}')">
      ${collapsed ? "+" : "−"}
    </button>
  `;
}

async function loadMaintenanceExperiments() {
  try {
    const res = await fetch("/api/maintenance/experiments");
    const payload = await res.json();
    maintenanceState.experiments = payload.experiments || [];
    maintenanceState.loadError = "";
    renderMaintenanceStats();
    renderMaintenanceList();
    await handleMaintenanceRoute();
  } catch (err) {
    maintenanceState.experiments = [];
    maintenanceState.loadError = String(err?.message || err || "Failed to load experiments");
    renderMaintenanceStats();
    renderMaintenanceList();
  }
}

async function handleMaintenanceRoute() {
  const route = parseMaintenanceRoute();
  maintenanceState.route = route;
  updateSectionNavState();
  if (!maintenanceState.experiments.length) return;
  const visible = getVisibleMaintenanceExperiments();
  const currentStillVisible = visible.some((item) => item.id === maintenanceState.currentId);
  const experimentId = route.experimentId || (currentStillVisible ? maintenanceState.currentId : "") || visible[0]?.id || "";
  if (!experimentId) return;
  if (!route.experimentId) {
    replaceMaintenanceRoute({ view: "experiment", section: route.section, experimentId });
    return;
  }
  if (maintenanceState.currentId !== experimentId || !maintenanceState.currentDetail) {
    await loadMaintenanceExperimentDetail(experimentId);
  }
  if (route.pageId) maintenanceState.currentPageId = route.pageId;
  renderMaintenanceList();
  renderMaintenanceDetail(maintenanceState.currentDetail);
}

function updateSectionNavState() {
  const section = currentSection();
  document.querySelectorAll("[data-section-link]").forEach((el) => {
    el.classList.toggle("active", el.getAttribute("data-section-link") === section);
  });
}

async function loadMaintenanceExperimentDetail(id) {
  maintenanceState.currentId = id;
  const [detailRes, playerRes] = await Promise.all([
    fetch(`/api/maintenance/experiment?id=${encodeURIComponent(id)}`),
    fetch(`/api/maintenance/player?id=${encodeURIComponent(id)}`),
  ]);
  const payload = await detailRes.json();
  if (payload.error) {
    console.error(payload.error);
    return;
  }
  const player = await playerRes.json();
  maintenanceState.currentDetail = payload;
  maintenanceState.currentPlayer = player.error ? null : player;
  maintenanceState.currentFrameIndex = 0;
  maintenanceState.selectedPlayerElementId = "";
  maintenanceState.currentPageId = maintenanceState.route.pageId || payload.pages?.[0]?.page_id || "";
  maintenanceState.currentDocId = payload.docs?.[0]?.id || "";
  maintenanceState.selectedBoardEntityId = "";
  maintenanceState.selectedTurnIndex = -1;
  maintenanceState.selectedRoleTab = "summary";
  maintenanceState.draggingCoreNode = null;
  maintenanceState.suppressCoreNodeClick = false;
}

function renderMaintenanceStats() {
  const stats = document.getElementById("maintenance-stats");
  const exps = getVisibleMaintenanceExperiments();
  const withAudit = exps.filter((item) => item.role_log_exists).length;
  const sectionLabel = currentSection() === "method_tests" ? "Method Tests" : "Experiments";
  stats.innerHTML = `
    <div class="stat-chip"><strong>${exps.length}</strong> ${sectionLabel}</div>
    <div class="stat-chip"><strong>${withAudit}</strong> With Audit Logs</div>
    <div class="stat-chip"><strong>${exps.filter((item) => item.kind === "medium").length}</strong> Medium Runs</div>
    <div class="stat-chip"><strong>${exps.filter((item) => item.kind.startsWith("exp")).length}</strong> Probe Runs</div>
    <div class="stat-chip"><strong>${exps.filter((item) => item.kind === "method_validation").length}</strong> Method Cases</div>
    ${maintenanceState.loadError ? `<div class="stat-chip"><strong>Load Error</strong> ${escapeHtml(maintenanceState.loadError)}</div>` : ""}
  `;
}

function currentSection() {
  return maintenanceState.route?.section || (window.location.pathname.startsWith("/method-tests") ? "method_tests" : "maintenance");
}

function getVisibleMaintenanceExperiments() {
  const section = currentSection();
  return (maintenanceState.experiments || []).filter((item) => {
    const isMethod = item.kind === "method_validation";
    return section === "method_tests" ? isMethod : !isMethod;
  });
}

function getFilteredMaintenanceExperiments() {
  const q = document.getElementById("maintenance-search").value.trim().toLowerCase();
  const visible = getVisibleMaintenanceExperiments();
  if (!q) return visible;
  return visible.filter((item) => {
    return [item.title, item.kind, item.folder_name, item.suite_id]
      .filter(Boolean)
      .some((value) => value.toLowerCase().includes(q));
  });
}

function renderMaintenanceList() {
  const items = getFilteredMaintenanceExperiments();
  maintenanceState.filtered = items;
  const list = document.getElementById("maintenance-experiment-list");
  if (maintenanceState.loadError) {
    list.innerHTML = `
      <li class="skill-item">
        <div class="skill-name">Failed To Load Experiments</div>
        <div class="skill-desc">${escapeHtml(maintenanceState.loadError)}</div>
      </li>
    `;
    return;
  }
  if (!items.length) {
    const noun = currentSection() === "method_tests" ? "Method Tests" : "Experiments";
    list.innerHTML = `
      <li class="skill-item">
        <div class="skill-name">No ${noun} Found</div>
        <div class="skill-desc">Try clearing the search box or refresh the page.</div>
      </li>
    `;
    return;
  }
  list.innerHTML = items.map((item) => {
    const active = item.id === maintenanceState.currentId ? "active" : "";
    return `
      <li class="skill-item ${active}" onclick="selectMaintenanceExperiment('${escapeJs(item.id)}')">
        <div class="skill-name">${escapeHtml(item.title)}</div>
        <div class="skill-desc">${escapeHtml(item.folder_name)}</div>
        <div class="timeline-pill-row maintenance-pill-row">
          <span class="timeline-pill">${escapeHtml(item.kind.toUpperCase())}</span>
          <span class="timeline-pill">${escapeHtml(item.role_log_exists ? `audit ${item.role_log_count}` : "no audit")}</span>
        </div>
      </li>
    `;
  }).join("");
}

async function selectMaintenanceExperiment(id) {
  pushMaintenanceRoute({ view: "experiment", section: currentSection(), experimentId: id });
}

function selectMaintenancePage(pageId) {
  maintenanceState.selectedBoardEntityId = "";
  maintenanceState.selectedTurnIndex = -1;
  maintenanceState.selectedRoleTab = "summary";
  pushMaintenanceRoute({ view: "round", section: currentSection(), experimentId: maintenanceState.currentId, pageId });
}

function shiftMaintenancePage(offset) {
  const pages = maintenanceState.currentDetail?.pages || [];
  if (!pages.length) return;
  const index = Math.max(0, pages.findIndex((item) => item.page_id === maintenanceState.currentPageId));
  const nextIndex = index + offset;
  if (nextIndex < 0 || nextIndex >= pages.length) return;
  pushMaintenanceRoute({ view: "round", section: currentSection(), experimentId: maintenanceState.currentId, pageId: pages[nextIndex].page_id });
}

function selectMaintenanceDoc(docId) {
  maintenanceState.currentDocId = docId;
  if (maintenanceState.currentDetail) {
    renderMaintenanceDetail(maintenanceState.currentDetail);
  }
}

function toggleMaintenanceVerbosity() {
  maintenanceState.showVerbose = !maintenanceState.showVerbose;
  const btn = document.getElementById("maintenance-verbosity-toggle");
  if (btn) {
    btn.textContent = maintenanceState.showVerbose ? "Detailed View" : "Compact View";
    btn.classList.toggle("active-toggle", maintenanceState.showVerbose);
  }
  if (maintenanceState.currentDetail) {
    renderMaintenanceDetail(maintenanceState.currentDetail);
  }
}

function renderMaintenanceDetail(detail) {
  document.getElementById("maintenance-placeholder").style.display = "none";
  document.getElementById("maintenance-detail").style.display = "block";
  const root = document.getElementById("maintenance-detail");
  const scrollState = captureMaintenanceScrollState();
  root.innerHTML = renderMaintenanceView(detail, maintenanceState.route) + renderFloatingDetailModal();
  restoreMaintenanceScrollState(scrollState);
}

function captureMaintenanceScrollState() {
  return {
    inspector: document.querySelector(".player-inspector")?.scrollTop || 0,
    overlay: document.querySelector(".player-overlay-body")?.scrollTop || 0,
    storeList: document.querySelector("#player-store-skill-list")?.scrollTop || 0,
    storeSummary: document.querySelector(".store-summary-pane")?.scrollTop || 0,
  };
}

function restoreMaintenanceScrollState(state) {
  requestAnimationFrame(() => {
    const pairs = [
      [".player-inspector", state.inspector],
      [".player-overlay-body", state.overlay],
      ["#player-store-skill-list", state.storeList],
      [".store-summary-pane", state.storeSummary],
    ];
    for (const [selector, value] of pairs) {
      const el = document.querySelector(selector);
      if (el && Number(value) > 0) el.scrollTop = Number(value);
    }
  });
}

function renderMaintenanceView(detail, route) {
  const page = resolveCurrentPage(detail, route);
  if (page && !maintenanceState.currentPageId) maintenanceState.currentPageId = page.page_id;
  if (route.view === "metrics") return renderMetricsView(detail);
  if (route.view === "docs") return renderDocsView(detail);
  if (route.view === "executor") return renderExecutorRouteView(detail, page);
  if (route.view === "role") return renderRoleRouteView(detail, page, route.cardId);
  if (route.view === "artifact") return renderArtifactRouteView(detail, page, route.artifactId);
  if (route.view === "round") return renderRoundRouteView(detail, page);
  return renderExperimentOverviewView(detail);
}

function resolveCurrentPage(detail, route = maintenanceState.route) {
  const pages = detail?.pages || [];
  const page = pages.find((item) => item.page_id === route.pageId) || pages.find((item) => item.page_id === maintenanceState.currentPageId) || pages[0] || null;
  if (page) maintenanceState.currentPageId = page.page_id;
  return page;
}

function renderViewChrome(detail, title, subtitle, body, actions = "") {
  return `
    <div class="maintenance-view-shell">
      <div class="maintenance-view-top">
        <div>
          <div class="maintenance-breadcrumb">${renderBreadcrumb(detail)}</div>
          <h2>${escapeHtml(title)}</h2>
          <p>${escapeHtml(subtitle || detail.experiment?.subtitle || "")}</p>
        </div>
        <div class="maintenance-view-actions">
          <button class="btn chip-btn" onclick="goMaintenanceBack()">Back</button>
          ${actions}
        </div>
      </div>
      <div class="maintenance-view-body">${body}</div>
    </div>
  `;
}

function renderBreadcrumb(detail) {
  const expId = maintenanceState.currentId;
  const crumbs = [
    `<button onclick="pushMaintenanceRoute({view:'experiment', experimentId:'${escapeJs(expId)}'})">${escapeHtml(detail.experiment?.title || "Experiment")}</button>`,
  ];
  if (maintenanceState.currentPageId) {
    const page = resolveCurrentPage(detail);
    crumbs.push(`<button onclick="pushMaintenanceRoute({view:'round', experimentId:'${escapeJs(expId)}', pageId:'${escapeJs(maintenanceState.currentPageId)}'})">${escapeHtml(page?.label || page?.title || maintenanceState.currentPageId)}</button>`);
  }
  if (maintenanceState.route?.view && !["experiment", "round"].includes(maintenanceState.route.view)) {
    crumbs.push(`<span>${escapeHtml(maintenanceState.route.view)}</span>`);
  }
  return crumbs.join("<span>/</span>");
}

function renderPaneHeaders() {
  const overview = document.querySelector(".maintenance-overview-pane > h3");
  if (overview) overview.innerHTML = `Overview ${paneToggleButton("overview", "Overview")}`;
  const rounds = document.querySelector(".maintenance-rounds-pane .detail-header h3");
  if (rounds) rounds.innerHTML = `Turns ${paneToggleButton("rounds", "Turns")}`;
  const docs = document.querySelector(".maintenance-docs-pane .detail-header h3");
  if (docs) docs.innerHTML = `Documentation ${paneToggleButton("docs", "Documentation")}`;
}

function renderExperimentOverviewView(detail) {
  const algorithmPage = (detail.pages || []).find((page) => page.page_id === "algorithm") || resolveCurrentPage(detail);
  const algorithmFlow = buildSequentialRoleFlow(algorithmPage);
  const actions = `
    <button class="btn chip-btn" onclick="pushMaintenanceRoute({view:'metrics', experimentId:'${escapeJs(maintenanceState.currentId)}'})">Metrics</button>
    <button class="btn chip-btn" onclick="pushMaintenanceRoute({view:'docs', experimentId:'${escapeJs(maintenanceState.currentId)}'})">Docs</button>
  `;
  const pageCards = (detail.pages || []).map((page, idx) => `
    <button class="maintenance-round-thumb" onclick="pushMaintenanceRoute({view:'round', experimentId:'${escapeJs(maintenanceState.currentId)}', pageId:'${escapeJs(page.page_id)}'})">
      <div class="maintenance-stage-kicker">Turn ${idx + 1}</div>
      <div class="maintenance-round-thumb-title">${escapeHtml(page.title || page.page_id)}</div>
      <div class="maintenance-round-thumb-metrics">
        ${(page.summary_metrics || []).slice(0, 4).map((metric) => `<span class="timeline-pill">${escapeHtml(`${metric.label}: ${metric.value}`)}</span>`).join("")}
      </div>
    </button>
  `).join("");
  return renderViewChrome(
    detail,
    "Algorithm Monitor",
    detail.experiment?.subtitle || "",
    `
      <div class="maintenance-hero-grid">${(detail.overview_metrics || []).slice(0, 8).map((card) => `
        <div class="timeline-summary-card ${escapeHtml(card.tone || "neutral")}" title="${escapeHtml(metricHelp(card.label || ""))}">
          <div class="timeline-summary-label">${escapeHtml(card.label || "")}</div>
          <div class="timeline-summary-value">${escapeHtml(String(card.value ?? ""))}</div>
        </div>
      `).join("")}</div>
      ${renderAuditAvailabilityNotice(detail)}
      ${algorithmPage ? `
        <div class="round-sequence-route algorithm-monitor-route">
          <section class="algorithm-monitor-intro">
            <div>
              <div class="maintenance-stage-kicker">Monitor Scope</div>
              <div class="maintenance-stage-title">${escapeHtml(algorithmPage.title || "Algorithm Monitor")}</div>
              <div class="maintenance-stage-subtitle">这页按算法调用顺序展示：executor 运行、skill 产出、bundle 产出、integration replay、unit utility test、refiner 决策和最终 skill store。</div>
            </div>
            <button class="btn chip-btn" onclick="pushMaintenanceRoute({view:'round', experimentId:'${escapeJs(maintenanceState.currentId)}', pageId:'${escapeJs(algorithmPage.page_id)}'})">Open Page</button>
          </section>
          ${renderSequentialRoleFlow(algorithmPage, algorithmFlow, {
            kicker: "Algorithm Flow",
            title: "核心 role 与 artifact 输出",
            subtitle: "正面只显示算法输入摘要、输出摘要和关键指标；完整输入/输出/debug raw 在弹窗里查看。"
          })}
          ${renderRoundTestResults(algorithmPage)}
        </div>
      ` : ""}
      <div class="maintenance-turn-intro">
        <div class="maintenance-stage-kicker">Pages</div>
        <div class="maintenance-stage-subtitle">Algorithm 是默认监控页；Train/Refine/Test 保留原始阶段汇总，Debug 信息不在主视图展开。</div>
      </div>
      <div class="maintenance-round-gallery">${pageCards || "<div class='timeline-empty'>No turns recorded.</div>"}</div>
    `,
    actions
  );
}

function renderAuditAvailabilityNotice(detail) {
  const auditMetric = (detail.overview_metrics || []).find((item) => item.label === "Audit Rows");
  const auditRows = Number(auditMetric?.value || 0);
  if (auditRows > 0) return "";
  return `
    <section class="algorithm-audit-notice">
      <div class="maintenance-stage-kicker">Audit I/O Notice</div>
      <div>
        当前实验没有保存 extractor / bundle builder / refiner 的原始 prompt 和 raw response。
        页面会优先展示 result.json 中可复原的真实算法产物；缺失的 role 原始 I/O 会在详情中明确标注。
      </div>
    </section>
  `;
}

function renderRoundRouteView(detail, page) {
  if (!page) return renderViewChrome(detail, "Round", "No round selected.", "<div class='timeline-empty'>No page selected.</div>");
  const actions = `
    <button class="btn chip-btn" onclick="pushMaintenanceRoute({view:'executor', experimentId:'${escapeJs(maintenanceState.currentId)}', pageId:'${escapeJs(page.page_id)}'})">Executor</button>
    <button class="btn chip-btn" onclick="pushMaintenanceRoute({view:'metrics', experimentId:'${escapeJs(maintenanceState.currentId)}'})">Metrics</button>
  `;
  const roleFlow = buildSequentialRoleFlow(page);
  return renderViewChrome(
    detail,
    page.title || "Round",
    `${roleFlow.length} executed role/test cards`,
    `
      <div class="round-sequence-route">
        ${renderTurnSwitcher(detail, page)}
        ${renderTaskProblemBar(page, { nodes: roleFlow })}
        ${renderSequentialRoleFlow(page, roleFlow)}
        ${renderRoundTestResults(page)}
      </div>
    `,
    actions
  );
}

function renderTurnSwitcher(detail, currentPage) {
  const pages = detail?.pages || [];
  if (pages.length <= 1) return "";
  return `
    <section class="turn-switcher">
      <div>
        <div class="maintenance-stage-kicker">Turns</div>
        <div class="maintenance-stage-subtitle">选择一个 turn 查看其内部 role/test 顺序执行详情。</div>
      </div>
      <div class="turn-switcher-list">
        ${pages.map((page, idx) => `
          <button class="turn-switcher-btn ${page.page_id === currentPage?.page_id ? "active" : ""}" onclick="selectMaintenancePage('${escapeJs(page.page_id)}')">
            <span>Turn ${idx + 1}</span>
            <small>${escapeHtml(compactLabel(page.label || page.page_id, 28))}</small>
          </button>
        `).join("")}
      </div>
    </section>
  `;
}

function renderPlayerSection() {
  const player = maintenanceState.currentPlayer;
  const frames = player?.frames || [];
  if (!player || !frames.length) {
    return `
      <section class="player-shell">
        <div class="maintenance-stage-head">
          <div>
            <div class="maintenance-stage-kicker">State Player</div>
            <div class="maintenance-stage-title">No Player Frames</div>
            <div class="maintenance-stage-subtitle">当前结果没有可回放的状态机帧。</div>
          </div>
        </div>
      </section>
    `;
  }
  const idx = Math.max(0, Math.min(Number(maintenanceState.currentFrameIndex || 0), frames.length - 1));
  maintenanceState.currentFrameIndex = idx;
  const frame = resolvePlayerFrame(idx);
  const scene = buildPlayerScene(frame);
  const selectedId = maintenanceState.selectedPlayerElementId && scene.elementsById[maintenanceState.selectedPlayerElementId]
    ? maintenanceState.selectedPlayerElementId
    : scene.defaultSelectedId;
  maintenanceState.selectedPlayerElementId = selectedId;
  const selected = scene.elementsById[selectedId] || null;
  return `
    <section class="player-shell">
      <div class="player-toolbar">
        <div>
          <div class="maintenance-stage-kicker">State Machine Player</div>
          <div class="maintenance-stage-title">${escapeHtml(frame.name || "Frame")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(frame.summary || "")}</div>
        </div>
        <div class="player-controls">
          <button class="btn chip-btn" onclick="shiftPlayerMarker(-1)">Prev Mark</button>
          <button class="btn chip-btn" onclick="shiftPlayerMarker(1)">Next Mark</button>
          <button class="btn chip-btn" onclick="jumpNextNonExecutor()">Next Role</button>
          <button class="btn chip-btn" onclick="shiftPlayerFrame(-1)">Prev</button>
          <span class="timeline-pill player-frame-count">${escapeHtml(`${idx + 1}/${frames.length}`)}</span>
          <button class="btn chip-btn" onclick="shiftPlayerFrame(1)">Next</button>
        </div>
      </div>
      <input class="player-slider" data-player-slider type="range" min="0" max="${frames.length - 1}" value="${idx}" oninput="previewPlayerFrame(Number(this.value))" onchange="commitPlayerFrame(Number(this.value))">
      ${renderPlayerMarkerRail(frames, idx)}
      <div class="player-workspace">
        <div class="player-board" id="player-board-dynamic">
          ${renderPixelPlayerBoard(scene, frame)}
        </div>
        <div class="maintenance-splitter player-splitter" title="Drag to resize inspector" onpointerdown="startMaintenanceInspectorResize(event)" ondblclick="resetMaintenanceInspectorWidth(event)"></div>
        <aside class="player-inspector" id="player-inspector-dynamic">
          ${selected ? renderPlayerInspector(selected, frame) : "<div class='timeline-empty'>Select an element.</div>"}
        </aside>
      </div>
      <div id="player-overlay-root">${renderPlayerOverlayStack()}</div>
    </section>
  `;
}

const PLAYER_SLOT_DEFS = [
  { id: "trace", lane: "state", column: 1, row: 2, label: "Trace", icon: "TRC", kind: "trace", persistent: false, match: (el) => ["trace", "trace_event"].includes(el.kind) || String(el.element_id || "").startsWith("trace") },
  { id: "role:retriever", lane: "role", column: 2, row: 1, label: "Retriever", icon: "RET", kind: "role", persistent: false, match: (el) => el.element_id === "role:retriever" },
  { id: "retrieval", lane: "state", column: 2, row: 2, label: "Retrieval", icon: "RAD", kind: "retrieval", persistent: false, match: (el) => el.kind === "retrieval" },
  { id: "role:executor", lane: "role", column: 3, row: 1, label: "Executor", icon: "EXE", kind: "role", persistent: false, match: (el) => el.element_id === "role:executor" },
  { id: "role:extractor", lane: "role", column: 4, row: 1, label: "Extractor", icon: "EXT", kind: "role", persistent: false, match: (el) => el.element_id === "role:extractor" },
  { id: "skill", lane: "state", column: 4, row: 2, label: "Skill", icon: "SKL", kind: "skill", persistent: false, match: (el) => el.kind === "skill" },
  { id: "role:bundle_builder", lane: "role", column: 5, row: 1, label: "Bundle Builder", icon: "BUN", kind: "role", persistent: false, match: (el) => el.element_id === "role:bundle_builder" },
  { id: "bundle", lane: "state", column: 5, row: 2, label: "Bundle", icon: "BOX", kind: "bundle", persistent: false, match: (el) => el.kind === "bundle" },
  { id: "role:unit_tester", lane: "role", column: 6, row: 1, label: "Tester", icon: "TST", kind: "role", persistent: false, match: (el) => el.element_id === "role:unit_tester" },
  { id: "test_result", lane: "state", column: 6, row: 2, label: "Test Result", icon: "RPT", kind: "test_result", persistent: false, match: (el) => el.kind === "test_result" },
  { id: "role:refiner", lane: "role", column: 7, row: 1, label: "Refiner", icon: "REF", kind: "role", persistent: false, match: (el) => el.element_id === "role:refiner" },
  { id: "skill_store", lane: "state", column: 8, row: 2, label: "Skill Store", icon: "LIB", kind: "skill_store", persistent: true, match: (el) => el.element_id === "skill_store" },
];

const PLAYER_EDGE_DEFS = [
  { id: "store_to_retriever", from: "skill_store", to: "role:retriever", consumes: ["skill_store"], produces: [], to_port: "in" },
  { id: "trace_to_retriever", from: "trace", to: "role:retriever", consumes: ["trace"], produces: [], to_port: "in" },
  { id: "retriever_to_retrieval", from: "role:retriever", to: "retrieval", consumes: [], produces: ["retrieval"], from_port: "out" },
  { id: "retrieval_to_executor", from: "retrieval", to: "role:executor", consumes: ["retrieval"], produces: [], to_port: "in" },
  { id: "store_to_executor", from: "skill_store", to: "role:executor", consumes: ["skill_store"], produces: [], to_port: "in" },
  { id: "executor_to_trace", from: "role:executor", to: "trace", consumes: [], produces: ["trace"], from_port: "out" },
  { id: "trace_to_extractor", from: "trace", to: "role:extractor", consumes: ["trace"], produces: [], to_port: "in" },
  { id: "extractor_to_skill", from: "role:extractor", to: "skill", consumes: [], produces: ["skill"], from_port: "out" },
  { id: "skill_to_bundle_builder", from: "skill", to: "role:bundle_builder", consumes: ["skill"], produces: [], to_port: "in" },
  { id: "trace_to_bundle_builder", from: "trace", to: "role:bundle_builder", consumes: ["trace"], produces: [], to_port: "in" },
  { id: "bundle_builder_to_bundle", from: "role:bundle_builder", to: "bundle", consumes: [], produces: ["bundle"], from_port: "out" },
  { id: "skill_to_tester", from: "skill", to: "role:unit_tester", consumes: ["skill"], produces: [], to_port: "in" },
  { id: "bundle_to_tester", from: "bundle", to: "role:unit_tester", consumes: ["bundle"], produces: [], to_port: "in" },
  { id: "tester_to_result", from: "role:unit_tester", to: "test_result", consumes: [], produces: ["test_result"], from_port: "out" },
  { id: "result_to_refiner", from: "test_result", to: "role:refiner", consumes: ["test_result"], produces: [], to_port: "in" },
  { id: "bundle_to_refiner", from: "bundle", to: "role:refiner", consumes: ["bundle"], produces: [], to_port: "in" },
  { id: "refiner_to_skill", from: "role:refiner", to: "skill", consumes: [], produces: ["skill"], from_port: "out" },
  { id: "skill_to_store", from: "skill", to: "skill_store", consumes: ["skill"], produces: ["skill_store"], from_port: "out", to_port: "in" },
  { id: "refiner_to_store", from: "role:refiner", to: "skill_store", consumes: [], produces: ["skill_store"], from_port: "out", to_port: "in" },
  { id: "store_loop_to_retriever", from: "skill_store", to: "role:retriever", loop: true, consumes: ["skill_store"], produces: [], to_port: "in" },
];

function buildPlayerScene(frame) {
  const allElements = Object.values(frame.elements || {});
  const changed = new Set(frame.changed_elements || []);
  const consumed = new Set(frame.consumed_slots || []);
  const produced = new Set(frame.produced_slots || []);
  const slots = PLAYER_SLOT_DEFS.map((slot) => {
    const matches = allElements.filter(slot.match);
    const element = choosePlayerSlotElement(matches, changed) || syntheticPlayerSlotElement(slot);
    const currentDetail = slotHasCurrentFrameDetail(slot, element, frame);
    return {
      ...slot,
      element,
      active: matches.length > 0,
      changed: matches.some((item) => changed.has(item.element_id)) || changed.has(slot.id),
      consumed: consumed.has(slot.id),
      produced: produced.has(slot.id),
      currentDetail,
      count: matches.length,
    };
  });
  const elementsById = {};
  for (const slot of slots) {
    elementsById[slot.id] = playerSlotElementForInspector(slot);
  }
  const defaultSlot = slots.find((slot) => slot.currentDetail && slot.changed)
    || slots.find((slot) => slot.currentDetail)
    || slots.find((slot) => slot.id === "skill_store")
    || slots[0];
  return {
    slots,
    elementsById,
    defaultSelectedId: defaultSlot?.id || "",
    edges: buildPlayerEdges(slots, frame),
  };
}

function choosePlayerSlotElement(matches, changed) {
  if (!matches.length) return null;
  return matches.find((item) => changed.has(item.element_id)) || matches[matches.length - 1];
}

function syntheticPlayerSlotElement(slot) {
  return {
    element_id: slot.id,
    kind: slot.kind,
    label: slot.label,
    icon: slot.icon,
    state: { status: "empty", slot: slot.id, description: "No element has reached this slot in the current frame." },
  };
}

function slotHasCurrentFrameDetail(slot, element, frame) {
  if (slot.persistent) return true;
  const changed = new Set(frame.changed_elements || []);
  const consumed = new Set(frame.consumed_slots || []);
  const produced = new Set(frame.produced_slots || []);
  if (changed.has(slot.id) || changed.has(element?.element_id)) return true;
  if (consumed.has(slot.id) || produced.has(slot.id)) return true;
  return false;
}

function playerSlotElementForInspector(slot) {
  return {
    ...slot.element,
    element_id: slot.id,
    label: slot.label,
    kind: slot.kind,
    state: {
      slot: {
        id: slot.id,
        label: slot.label,
        kind: slot.kind,
        active: slot.active,
        changed: slot.changed,
        consumed: slot.consumed,
        produced: slot.produced,
        current_detail: slot.currentDetail,
        persistent: slot.persistent,
        matching_elements: slot.count,
        represented_element_id: slot.element.element_id,
      },
      represented_element: slot.element,
    },
  };
}

function buildPlayerEdges(slots, frame) {
  const slotIds = new Set(slots.map((slot) => slot.id));
  const consumed = new Set(frame.consumed_slots || []);
  const produced = new Set(frame.produced_slots || []);
  const roleSlot = slotIdForRoleGroup(frame.role_group || frame.action_kind || "");
  return PLAYER_EDGE_DEFS
    .filter((edge) => slotIds.has(edge.from) && slotIds.has(edge.to))
    .map((edge) => {
      const activeByConsume = roleSlot && edge.to === roleSlot && (edge.consumes || []).some((slot) => consumed.has(slot));
      const activeByProduce = roleSlot && edge.from === roleSlot && (edge.produces || []).some((slot) => produced.has(slot));
      const activeStoreUpdate = roleSlot === "skill_store"
        && edge.to === "skill_store"
        && ((edge.consumes || []).some((slot) => consumed.has(slot)) || (edge.produces || []).some((slot) => produced.has(slot)));
      const mode = edge.loop
        ? "loop"
        : (activeByConsume ? "consume" : (activeByProduce || activeStoreUpdate ? "produce" : (edge.from === "skill_store" || edge.to === "skill_store" ? "store" : "context")));
      const activeSlots = [
        ...(edge.consumes || []).filter((slot) => consumed.has(slot)),
        ...(edge.produces || []).filter((slot) => produced.has(slot)),
      ];
      return {
        ...edge,
        active: activeByConsume || activeByProduce || activeStoreUpdate,
        mode,
        active_slots: activeSlots,
        label: edge.label || activeSlots.join(" + ") || edge.id.replace(/_/g, " "),
      };
    });
}

function slotIdForRoleGroup(group) {
  const key = String(group || "").replace("_step", "");
  return {
    retriever: "role:retriever",
    retrieval: "role:retriever",
    executor: "role:executor",
    extractor: "role:extractor",
    bundle_builder: "role:bundle_builder",
    unit_tester: "role:unit_tester",
    tester: "role:unit_tester",
    refiner: "role:refiner",
    skill_store: "skill_store",
  }[key] || "";
}

function renderPixelPlayerBoard(scene, frame) {
  const slots = [...scene.slots].sort((a, b) => (a.column - b.column) || (a.row - b.row));
  return `
    <div class="factory-board-stage">
      <div class="factory-board-title">
        <span>Maintenance State Machine</span>
        <small>${escapeHtml(frame.action_kind || "frame")} ${frame.condition_result ? `| ${frame.condition_result}` : ""}</small>
      </div>
      ${renderFactoryFlowStrip(scene, frame)}
      <div class="factory-grid">
        <div class="factory-canvas">
          ${renderFactoryEdges(scene)}
          ${slots.map(renderPixelSlot).join("")}
          ${renderFactoryGates(frame)}
        </div>
      </div>
      <div class="factory-legend">
        <span><b class="legend-dot active"></b> current data flow</span>
        <span><b class="legend-dot changed"></b> changed this frame</span>
        <span><b class="legend-dot persistent"></b> persistent store</span>
      </div>
    </div>
  `;
}

function renderFactoryFlowStrip(scene, frame) {
  const activeEdges = (scene.edges || []).filter((edge) => edge.active);
  const roleLabel = slotIdForRoleGroup(frame.role_group || frame.action_kind || "").replace(/^role:/, "") || (frame.role_group || "frame");
  return `
    <div class="factory-flow-strip">
      <div class="flow-chip role-chip"><span>ACTIVE</span><strong>${escapeHtml(roleLabel)}</strong></div>
      <div class="flow-chip consume-chip"><span>Consumes</span>${renderChipList(frame.consumed_slots || [])}</div>
      <div class="flow-chip produce-chip"><span>Produces</span>${renderChipList(frame.produced_slots || [])}</div>
      <div class="flow-chip wire-chip"><span>Wires</span><strong>${escapeHtml(activeEdges.length ? activeEdges.map((edge) => edge.label).join(" / ") : "idle")}</strong></div>
    </div>
  `;
}

function renderPixelSlot(slot) {
  const selected = maintenanceState.selectedPlayerElementId === slot.id;
  const summary = summarizePlayerSlot(slot);
  const bounds = nodeBoundsForSlot(slot);
  const style = `left:${bounds.left}px; top:${bounds.top}px; width:${bounds.width}px; height:${bounds.height}px;`;
  const pulse = slot.consumed ? "consumed" : (slot.produced ? "produced" : "");
  return `
    <button
      class="pixel-slot factory-slot pixel-${escapeHtml(slot.kind)} ${slot.lane === "role" ? "factory-role" : "factory-artifact"} ${slot.active ? "active" : "empty"} ${slot.changed ? "changed" : ""} ${pulse} ${slot.persistent ? "persistent" : ""} ${selected ? "selected" : ""}"
      style="${style}"
      onclick="selectPlayerElement('${escapeJs(slot.id)}')"
      title="${escapeHtml(summary)}"
    >
      <span class="pixel-sprite">${escapeHtml(slot.icon)}</span>
      <span class="pixel-slot-label">${escapeHtml(slot.label)}</span>
      <span class="pixel-slot-status">${escapeHtml(summary)}</span>
      <span class="pixel-count">${escapeHtml(slot.persistent ? `${slot.count || 0} total` : (slot.currentDetail ? "frame" : "idle"))}</span>
      <span class="slot-port in-port ${slot.consumed ? "active" : ""}" title="Consumed in this frame">IN</span>
      <span class="slot-port out-port ${slot.produced ? "active" : ""}" title="Produced in this frame">OUT</span>
      <span class="slot-jack jack-top"></span>
      <span class="slot-jack jack-right"></span>
      <span class="slot-jack jack-bottom"></span>
      <span class="slot-jack jack-left"></span>
    </button>
  `;
}

function renderFactoryEdges(scene) {
  const slotById = Object.fromEntries(scene.slots.map((slot) => [slot.id, slot]));
  const orderedEdges = [...(scene.edges || [])].sort((a, b) => Number(a.active) - Number(b.active));
  const wires = orderedEdges.map((edge, index) => {
    const from = slotById[edge.from];
    const to = slotById[edge.to];
    if (!from || !to) return "";
    const route = circuitRouteForEdge(from, to, edge, index);
    const cls = ["factory-wire", edge.mode || "context", edge.active ? "active" : ""].filter(Boolean).join(" ");
    const markerId = edge.active ? "wire-arrow-active" : "wire-arrow-muted";
    const label = compactLabel(edge.label || edge.id, 30);
    return `
      <path class="${cls}" d="${route.path}" marker-end="url(#${markerId})">
        <title>${escapeHtml(edge.label || edge.id)}</title>
      </path>
      <circle class="factory-port-dot ${edge.active ? "active" : ""}" cx="${route.start.x}" cy="${route.start.y}" r="${edge.active ? 4 : 2.5}"></circle>
      <circle class="factory-port-dot ${edge.active ? "active" : ""}" cx="${route.end.x}" cy="${route.end.y}" r="${edge.active ? 4 : 2.5}"></circle>
      ${edge.active ? `<text class="factory-wire-label ${escapeHtml(edge.mode || "context")}" x="${route.label.x}" y="${route.label.y}">${escapeHtml(label)}</text>` : ""}
    `;
  }).join("");
  return `
    <svg class="factory-wire-layer" viewBox="0 0 1600 620" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <marker id="wire-arrow-muted" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" class="wire-arrow-muted"></path>
        </marker>
        <marker id="wire-arrow-active" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" class="wire-arrow-active"></path>
        </marker>
      </defs>
      ${wires}
    </svg>
  `;
}

const FACTORY_LAYOUT = {
  width: 1600,
  height: 620,
  paddingX: 15,
  paddingTop: 40,
  columnWidth: 150,
  columnGap: 53,
  rowHeight: { 1: 150, 2: 150, 3: 80 },
  rowTop: { 1: 40, 2: 280, 3: 520 },
  portGap: 12,
};

function circuitRouteForEdge(fromSlot, toSlot, edge, index) {
  const from = nodeBoundsForSlot(fromSlot);
  const to = nodeBoundsForSlot(toSlot);
  const lane = circuitLaneOffset(index);
  if (edge.loop) return loopCircuitRoute(from, to, index);
  if (Number(fromSlot.row) === Number(toSlot.row)) {
    return sameRowCircuitRoute(from, to, fromSlot, toSlot, edge, index);
  }
  return crossRowCircuitRoute(from, to, fromSlot, toSlot, edge, lane);
}

function nodeBoundsForSlot(slot) {
  const column = Number(slot.column || 1);
  const row = Number(slot.row || 1);
  const width = FACTORY_LAYOUT.columnWidth;
  const height = FACTORY_LAYOUT.rowHeight[row] || 150;
  const left = FACTORY_LAYOUT.paddingX + (column - 1) * (FACTORY_LAYOUT.columnWidth + FACTORY_LAYOUT.columnGap);
  const top = FACTORY_LAYOUT.rowTop[row] || FACTORY_LAYOUT.rowTop[2];
  return {
    row,
    column,
    width,
    height,
    left,
    right: left + width,
    top,
    bottom: top + height,
    centerX: left + width / 2,
    centerY: top + height / 2,
  };
}

function circuitLaneOffset(index) {
  return ((index % 5) - 2) * 9;
}

function topPort(bounds, xOffset = 0) {
  return { x: bounds.centerX + xOffset, y: bounds.top - FACTORY_LAYOUT.portGap };
}

function bottomPort(bounds, xOffset = 0) {
  return { x: bounds.centerX + xOffset, y: bounds.bottom + FACTORY_LAYOUT.portGap };
}

function leftPort(bounds, yOffset = 0) {
  return { x: bounds.left - FACTORY_LAYOUT.portGap, y: bounds.centerY + yOffset };
}

function rightPort(bounds, yOffset = 0) {
  return { x: bounds.right + FACTORY_LAYOUT.portGap, y: bounds.centerY + yOffset };
}

function portPoint(bounds, hint, fallback) {
  const key = hint || fallback;
  if (key === "in") return leftPort(bounds);
  if (key === "out") return rightPort(bounds);
  if (key === "top") return topPort(bounds);
  if (key === "bottom") return bottomPort(bounds);
  if (key === "left") return leftPort(bounds);
  if (key === "right") return rightPort(bounds);
  return fallback === "top" ? topPort(bounds) : fallback === "bottom" ? bottomPort(bounds) : fallback === "left" ? leftPort(bounds) : rightPort(bounds);
}

function stubPoint(point, bounds, hint, lane, directionY = 1) {
  const key = hint || "";
  if (key === "in" || key === "left") return { x: point.x - 24, y: point.y + lane };
  if (key === "out" || key === "right") return { x: point.x + 24, y: point.y + lane };
  if (key === "top") return { x: point.x + lane, y: point.y - 18 };
  if (key === "bottom") return { x: point.x + lane, y: point.y + 18 };
  return { x: point.x + lane, y: point.y + directionY * 18 };
}

function crossRowCircuitRoute(from, to, fromSlot, toSlot, edge, lane) {
  const goingDown = Number(toSlot.row) > Number(fromSlot.row);
  const startFallback = goingDown ? "bottom" : "top";
  const endFallback = goingDown ? "top" : "bottom";
  const startHint = edge.from_port || (edge.produces?.length ? "out" : startFallback);
  const endHint = edge.to_port || (edge.consumes?.length ? "in" : endFallback);
  const start = portPoint(from, startHint, startFallback);
  const end = portPoint(to, endHint, endFallback);
  const startLane = stubPoint(start, from, startHint, lane, goingDown ? 1 : -1);
  const endLane = stubPoint(end, to, endHint, lane, goingDown ? -1 : 1);
  const busY = rowBusY(from.row, to.row, lane);
  return {
    start,
    end,
    label: { x: (start.x + end.x) / 2, y: busY - 8 },
    path: `M ${start.x} ${start.y} V ${startLane.y} H ${startLane.x} V ${busY} H ${endLane.x} V ${endLane.y} H ${end.x} V ${end.y}`,
  };
}

function sameRowCircuitRoute(from, to, fromSlot, toSlot, edge, index) {
  const dir = to.centerX >= from.centerX ? 1 : -1;
  const lane = circuitLaneOffset(index);
  const startFallback = dir > 0 ? "right" : "left";
  const endFallback = dir > 0 ? "left" : "right";
  const startHint = edge.from_port || (edge.produces?.length ? "out" : startFallback);
  const endHint = edge.to_port || (edge.consumes?.length ? "in" : endFallback);
  const start = portPoint(from, startHint, startFallback);
  const end = portPoint(to, endHint, endFallback);
  const busY = sameRowBusY(fromSlot.row, lane);
  const startStub = stubPoint(start, from, startHint, lane, 1);
  const endStub = stubPoint(end, to, endHint, lane, -1);
  return {
    start,
    end,
    label: { x: (startStub.x + endStub.x) / 2, y: busY - 8 },
    path: `M ${start.x} ${start.y} H ${startStub.x} V ${startStub.y} V ${busY} H ${endStub.x} V ${endStub.y} H ${end.x} V ${end.y}`,
  };
}

function loopCircuitRoute(from, to, index) {
  const dir = to.centerX >= from.centerX ? 1 : -1;
  const lane = circuitLaneOffset(index);
  const start = bottomPort(from);
  const end = dir > 0 ? leftPort(to) : rightPort(to);
  const busY = 586 + (index % 2) * 14;
  const endStubX = end.x - dir * 34;
  const startLaneX = start.x + lane;
  const endLaneY = end.y + lane;
  return {
    start,
    end,
    label: { x: (start.x + endStubX) / 2, y: busY - 8 },
    path: `M ${start.x} ${start.y} V ${start.y + 18} H ${startLaneX} V ${busY} H ${endStubX} V ${endLaneY} V ${end.y} H ${end.x}`,
  };
}

function rowBusY(rowA, rowB, lane) {
  const low = Math.min(Number(rowA), Number(rowB));
  if (low <= 1) return 235 + lane;
  return 475 + lane;
}

function sameRowBusY(row, lane) {
  const numericRow = Number(row);
  if (numericRow === 1) return 220 + lane;
  if (numericRow === 2) return 468 + lane;
  return 592 + lane;
}

function renderFactoryGates(frame) {
  const condition = String(frame.condition_result || "").trim();
  if (!condition) return "";
  const tone = condition === "pass" ? "success" : (condition === "fail" ? "danger" : "warning");
  const bounds = nodeBoundsForSlot({ column: 7, row: 2 });
  return `
    <div class="factory-gate ${tone}" style="left:${bounds.left + 28}px; top:${bounds.bottom + 26}px;" title="${escapeHtml(condition)}">
      <span>GATE</span>
      <strong>${escapeHtml(condition)}</strong>
    </div>
  `;
}

function summarizePlayerSlot(slot) {
  if (!slot.active) return "idle";
  const state = slot.element.state || {};
  const eventType = state.last_event_type || state.last_raw_event?.event_type || state.last_event?.event_type;
  const action = state.action || state.decision?.action || state.aggregate?.pass_all_tests;
  if (eventType) return compactLabel(eventType, 28);
  if (action !== undefined) return compactLabel(`action=${action}`, 28);
  return compactLabel(slot.element.label || slot.element.element_id, 28);
}

function shiftPlayerFrame(offset) {
  commitPlayerFrame(Number(maintenanceState.currentFrameIndex || 0) + offset);
}

function playerMarkerFrames(frames) {
  const markers = [];
  let lastGroup = "";
  (frames || []).forEach((raw, index) => {
    const group = playerFrameGroup(raw);
    const marked = raw.is_marker_candidate || index === 0 || index === (frames.length - 1);
    if (!marked) return;
    if (group === lastGroup && group === "executor") return;
    if (group === lastGroup && !["unit_tester", "refiner", "skill_store"].includes(group)) return;
    markers.push({ index, group, label: playerMarkerLabel(raw, index) });
    lastGroup = group;
  });
  return markers;
}

function playerFrameGroup(frame) {
  const group = String(frame.role_group || frame.action_kind || "frame");
  if (group.includes("executor")) return "executor";
  if (group.includes("retriev")) return "retriever";
  if (group.includes("extract")) return "extractor";
  if (group.includes("bundle")) return "bundle_builder";
  if (group.includes("test")) return "unit_tester";
  if (group.includes("refine")) return "refiner";
  if (group.includes("store")) return "skill_store";
  return group || "frame";
}

function playerMarkerLabel(frame, index) {
  return `${index + 1}. ${String(frame.action_kind || frame.name || "frame").replace(/_/g, " ")}`;
}

function renderPlayerMarkerRail(frames, currentIndex) {
  const markers = playerMarkerFrames(frames);
  if (!markers.length) return "";
  const max = Math.max(1, frames.length - 1);
  return `
    <div class="player-marker-rail">
      ${markers.map((marker) => `
        <button
          class="player-marker marker-${escapeHtml(marker.group)} ${marker.index === currentIndex ? "active" : ""}"
          style="left:${(marker.index / max) * 100}%"
          title="${escapeHtml(marker.label)}"
          data-frame-index="${marker.index}"
          onclick="commitPlayerFrame(${marker.index})"
        ></button>
      `).join("")}
    </div>
  `;
}

function shiftPlayerMarker(direction) {
  const frames = maintenanceState.currentPlayer?.frames || [];
  const markers = playerMarkerFrames(frames);
  if (!markers.length) return;
  const current = Number(maintenanceState.currentFrameIndex || 0);
  const next = direction > 0
    ? markers.find((marker) => marker.index > current)
    : [...markers].reverse().find((marker) => marker.index < current);
  if (next) commitPlayerFrame(next.index);
}

function jumpNextNonExecutor() {
  const frames = maintenanceState.currentPlayer?.frames || [];
  const current = Number(maintenanceState.currentFrameIndex || 0);
  const next = playerMarkerFrames(frames).find((marker) => marker.index > current && marker.group !== "executor");
  if (next) commitPlayerFrame(next.index);
}

function resolvePlayerFrame(index) {
  const player = maintenanceState.currentPlayer || {};
  const frames = player.frames || [];
  const frame = { ...(frames[index] || {}) };
  if (player.snapshot_mode !== "delta") {
    return frame;
  }
  const elements = { ...(player.initial_elements || {}) };
  for (let i = 0; i <= index; i += 1) {
    for (const [key, value] of Object.entries(frames[i]?.element_deltas || {})) {
      if (value === null) {
        delete elements[key];
      } else {
        elements[key] = value;
      }
    }
  }
  frame.elements = elements;
  return frame;
}

function selectPlayerFrame(index) {
  commitPlayerFrame(index);
}

function previewPlayerFrame(index) {
  const frames = maintenanceState.currentPlayer?.frames || [];
  if (!frames.length) return;
  maintenanceState.currentFrameIndex = Math.max(0, Math.min(Number(index || 0), frames.length - 1));
  refreshPlayerDynamicView();
}

function commitPlayerFrame(index) {
  previewPlayerFrame(index);
}

function refreshPlayerDynamicView() {
  const frames = maintenanceState.currentPlayer?.frames || [];
  if (!frames.length) return;
  const idx = Math.max(0, Math.min(Number(maintenanceState.currentFrameIndex || 0), frames.length - 1));
  maintenanceState.currentFrameIndex = idx;
  const frame = resolvePlayerFrame(idx);
  const scene = buildPlayerScene(frame);
  if (!maintenanceState.selectedPlayerElementId || !scene.elementsById[maintenanceState.selectedPlayerElementId]) {
    maintenanceState.selectedPlayerElementId = scene.defaultSelectedId;
  }
  const selected = scene.elementsById[maintenanceState.selectedPlayerElementId] || scene.elementsById[scene.defaultSelectedId] || null;
  const board = document.getElementById("player-board-dynamic");
  if (board) board.innerHTML = renderPixelPlayerBoard(scene, frame);
  const inspector = document.getElementById("player-inspector-dynamic");
  if (inspector) inspector.innerHTML = selected ? renderPlayerInspector(selected, frame) : "<div class='timeline-empty'>Select an element.</div>";
  document.querySelectorAll("[data-player-slider]").forEach((slider) => {
    slider.value = String(idx);
  });
  document.querySelectorAll(".player-marker[data-frame-index]").forEach((marker) => {
    marker.classList.toggle("active", Number(marker.dataset.frameIndex) === idx);
  });
  document.querySelectorAll(".player-toolbar .maintenance-stage-title").forEach((el) => {
    el.textContent = frame.name || "Frame";
  });
  document.querySelectorAll(".player-toolbar .maintenance-stage-subtitle").forEach((el) => {
    el.textContent = frame.summary || "";
  });
  document.querySelectorAll(".player-frame-count").forEach((el) => {
    el.textContent = `${idx + 1}/${frames.length}`;
  });
  refreshPlayerOverlayDynamic(scene, frame);
}

function selectPlayerElement(elementId) {
  maintenanceState.selectedPlayerElementId = elementId;
  refreshPlayerDynamicView();
}

function renderPlayerElement(element, frame) {
  const pos = playerElementPosition(element);
  const changed = (frame.changed_elements || []).includes(element.element_id);
  const selected = maintenanceState.selectedPlayerElementId === element.element_id;
  return `
    <button
      class="player-element ${escapeHtml(playerElementKindClass(element.kind))} ${changed ? "changed" : ""} ${selected ? "selected" : ""}"
      style="left:${pos.x}px; top:${pos.y}px"
      onclick="selectPlayerElement('${escapeJs(element.element_id)}')"
    >
      <span class="player-icon">${escapeHtml(playerIcon(element.icon, element.kind))}</span>
      <span class="player-label">${escapeHtml(compactLabel(element.label || element.element_id, 32))}</span>
      <span class="player-meta">${escapeHtml(element.kind || "element")}</span>
    </button>
  `;
}

function playerElementPosition(element) {
  const pos = element.position || {};
  const fallback = hashString(element.element_id || element.label || "node");
  return {
    x: Number.isFinite(Number(pos.x)) ? Number(pos.x) : 80 + (fallback % 820),
    y: Number.isFinite(Number(pos.y)) ? Number(pos.y) : 90 + (Math.floor(fallback / 17) % 360),
  };
}

function hashString(text) {
  let h = 0;
  for (const ch of String(text || "")) h = ((h << 5) - h + ch.charCodeAt(0)) | 0;
  return Math.abs(h);
}

function playerIcon(icon, kind) {
  const key = icon || kind;
  return {
    robot: "R",
    radar: "Q",
    shelf: "S",
    card: "K",
    box: "B",
    clipboard: "T",
    scroll: "L",
    tester: "U",
  }[key] || String(kind || "E").slice(0, 1).toUpperCase();
}

function playerElementKindClass(kind) {
  return `player-kind-${String(kind || "unknown").replace(/[^a-z0-9_-]/gi, "_")}`;
}

function renderPlayerInspector(element, frame) {
  const slotState = element.state?.slot || {};
  const hasDetail = Boolean(slotState.current_detail);
  const represented = element.state?.represented_element || {};
  const representedState = represented.state || element.state || {};
  const lastEvent = representedState.last_raw_event || representedState.last_event || frame.delta?.event || {};
  const openStore = element.element_id === "skill_store"
    ? `<button class="btn chip-btn" onclick="openPlayerOverlay('skill_store')">Open Store</button>`
    : "";
  const openStructured = ["skill", "bundle", "test_result"].includes(element.kind)
    ? `<button class="btn chip-btn" onclick="openPlayerOverlay('${escapeJs(element.kind)}')">Open ${escapeHtml(element.label || element.kind)}</button>`
    : "";
  return `
    <div class="player-inspector-head">
      <div>
        <div class="maintenance-stage-kicker">${escapeHtml(element.kind || "Element")}</div>
        <div class="sequence-role-title">${escapeHtml(element.label || element.element_id)}</div>
        <div class="maintenance-stage-subtitle">${escapeHtml(element.element_id || "")}</div>
      </div>
      <div class="player-controls">
        ${openStore}
        ${openStructured}
        <button class="btn chip-btn" onclick="openPlayerElementModal()">Expand</button>
      </div>
    </div>
    <div class="maintenance-metric-grid compact-metrics">
      ${metricMini("Frame", frame.index ?? 0)}
      ${metricMini("Changed", slotState.changed ?? (frame.changed_elements || []).includes(element.element_id))}
      ${metricMini("Consumed", slotState.consumed ?? "—")}
      ${metricMini("Produced", slotState.produced ?? "—")}
    </div>
    ${hasDetail ? `
      ${renderPlayerRoleStateBoard(element, frame)}
      ${renderLastEventBanner(lastEvent, representedState)}
      <div class="single-json-column">
        ${renderDetailBlock("Last Event Raw", lastEvent || {}, { open: false, key: `player:${element.element_id}:last_event_raw` })}
        ${renderDetailBlock("Frame Delta", frame.delta || {}, { open: false, key: `player:${element.element_id}:frame_delta` })}
      </div>
    ` : `
      <div class="maintenance-missing-detail">
        This transient element has no action in the current frame. Move the timeline to a frame where it is highlighted, or open persistent Skill Store.
      </div>
    `}
  `;
}

function renderLastEventBanner(event, state) {
  const eventType = event?.event_type || state?.last_event_type || "no_event";
  const eventId = event?.event_id || state?.last_event_id || "";
  const ts = event?.ts || event?.timestamp || "";
  return `
    <section class="last-event-banner">
      <div>
        <div class="maintenance-stage-kicker">Last Event / Delta</div>
        <div class="last-event-type">${escapeHtml(eventType)}</div>
      </div>
      <div class="last-event-meta">
        ${eventId ? `<span>${escapeHtml(eventId)}</span>` : ""}
        ${ts ? `<span>${escapeHtml(ts)}</span>` : ""}
      </div>
    </section>
  `;
}

function renderPlayerRoleStateBoard(element, frame) {
  const slotState = element.state?.slot || {};
  const represented = element.state?.represented_element || {};
  const state = represented.state || element.state || {};
  if (element.kind === "role" || String(element.element_id || "").startsWith("role:")) {
    return renderRoleStateBoard(element, state, frame);
  }
  return `
    <section class="role-state-board">
      <div class="maintenance-section-title">Element State Board</div>
      <div class="readable-kv-list">
        <div class="readable-kv-row"><div class="readable-kv-key">Slot</div><div class="readable-kv-value">${escapeHtml(slotState.id || element.element_id || "")}</div></div>
        <div class="readable-kv-row"><div class="readable-kv-key">Status</div><div class="readable-kv-value">${escapeHtml(state.status || (slotState.active ? "active" : "idle"))}</div></div>
        <div class="readable-kv-row"><div class="readable-kv-key">Changed</div><div class="readable-kv-value">${escapeHtml(String(slotState.changed ?? false))}</div></div>
      </div>
    </section>
  `;
}

function renderRoleStateBoard(element, state, frame) {
  const role = String(state.role || element.element_id || "").replace(/^role:/, "");
  const input = state.last_input || {};
  const output = state.last_output || {};
  const stable = state.role_state && typeof state.role_state === "object" ? state.role_state : null;
  const previousInput = stable ? null : findPreviousRoleInputWithMessages(element.element_id);
  return `
    <section class="role-state-board">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Role State Board</div>
          <div class="maintenance-stage-title">${escapeHtml(role || "role")}</div>
          <div class="maintenance-stage-subtitle">Stable view of this role at the selected frame. Raw event I/O is folded below.</div>
        </div>
      </div>
      <div class="maintenance-metric-grid compact-metrics">
        ${metricMini("Turn", stable?.turn_index ?? state.turn_index ?? frame.turn_index ?? "—")}
        ${metricMini("Step", stable?.step_index ?? state.step_index ?? frame.step_index ?? "—")}
        ${metricMini("Loop", stable?.loop_index ?? state.loop_index ?? frame.loop_index ?? "—")}
        ${metricMini("Status", state.status || "—")}
      </div>
      ${stable ? renderStableRoleState(stable) : renderRoleMessageState(role, input, output, previousInput)}
    </section>
  `;
}

function renderStableRoleState(roleState) {
  const visibleMessages = Array.isArray(roleState.visible_messages) ? roleState.visible_messages : [];
  const newMessages = Array.isArray(roleState.new_messages) ? roleState.new_messages : [];
  const toolCalls = Array.isArray(roleState.tool_calls) ? roleState.tool_calls : [];
  const toolResults = Array.isArray(roleState.tool_results) ? roleState.tool_results : [];
  const summaryItems = Array.isArray(roleState.summary_items) ? roleState.summary_items : [];
  return `
    <div class="role-state-summary-grid">
      <section class="role-state-section">
        <div class="maintenance-section-title">Role Summary</div>
        ${summaryItems.length ? renderRoleSummaryRows(summaryItems) : "<div class='maintenance-missing-detail'>No role summary recorded.</div>"}
      </section>
      <section class="role-state-section">
        <div class="maintenance-section-title">Latest Delta Messages</div>
        ${newMessages.length ? newMessages.map((msg, idx) => renderMessageBubble(msg, idx)).join("") : "<div class='maintenance-missing-detail'>No new message in this frame.</div>"}
      </section>
    </div>
    <div class="message-state-board">
      <div class="message-column">
        <div class="maintenance-section-title">Visible Messages</div>
        ${visibleMessages.length ? visibleMessages.map((msg, idx) => renderMessageBubble(msg, idx)).join("") : "<div class='maintenance-missing-detail'>No message state recorded.</div>"}
      </div>
      <div class="message-column">
        <div class="maintenance-section-title">Tool Calls</div>
        ${toolCalls.length ? toolCalls.map(renderToolCallPill).join("") : "<div class='maintenance-missing-detail'>No tool calls in this frame.</div>"}
        <div class="maintenance-section-title">Tool Results</div>
        ${toolResults.length ? toolResults.map(renderToolResultCard).join("") : "<div class='maintenance-missing-detail'>No tool results in this frame.</div>"}
      </div>
    </div>
    ${Object.keys(roleState.metrics || {}).length ? `
      <details class="debug-raw-panel">
        <summary class="maintenance-summary">Role Metrics</summary>
        ${renderReadablePayload(roleState.metrics)}
      </details>
    ` : ""}
  `;
}

function renderRoleSummaryRows(items) {
  return `
    <div class="event-readable-list">
      ${items.map((item) => `
        <div class="event-readable-row">
          <strong>${escapeHtml(item.label || item.key || "item")}</strong>
          <span>${escapeHtml(compactMultiline(String(item.value ?? ""), 1400))}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function findPreviousRoleInputWithMessages(elementId) {
  const current = Number(maintenanceState.currentFrameIndex || 0);
  if (!elementId || current <= 0) return null;
  for (let i = current - 1; i >= 0; i -= 1) {
    const frame = resolvePlayerFrame(i);
    const state = frame.elements?.[elementId]?.state || {};
    const input = state.last_input || {};
    if (normalizeRoleMessages(input).length) return input;
  }
  return null;
}

function renderRoleMessageState(role, input, output, previousInput = null) {
  const inputMessages = normalizeRoleMessages(input);
  const previousMessages = inputMessages.length ? [] : normalizeRoleMessages(previousInput);
  const outputMessages = normalizeRoleOutputMessages(output);
  const toolCalls = normalizeToolCalls(output);
  const readableItems = roleReadableItems(role, input, output);
  return `
    <div class="message-state-board">
      <div class="message-column">
        <div class="maintenance-section-title">Input / Context Messages</div>
        ${inputMessages.length ? inputMessages.map((msg, idx) => renderMessageBubble(msg, idx)).join("") : ""}
        ${previousMessages.length ? `<div class="maintenance-stage-subtitle">No new messages in this event. Showing previous role messages for context.</div>${previousMessages.map((msg, idx) => renderMessageBubble(msg, idx)).join("")}` : ""}
        ${!inputMessages.length && !previousMessages.length ? renderReadableEventItems(readableItems.input, "No input messages in this event.") : ""}
      </div>
      <div class="message-column">
        <div class="maintenance-section-title">Output / Role Result</div>
        ${outputMessages.length ? outputMessages.map((msg, idx) => renderMessageBubble(msg, idx)).join("") : renderReadableEventItems(readableItems.output, "No textual output in this event.")}
        <div class="maintenance-section-title">Tool Calls</div>
        ${toolCalls.length ? toolCalls.map(renderToolCallPill).join("") : "<div class='maintenance-missing-detail'>No tool calls.</div>"}
      </div>
    </div>
  `;
}

function normalizeRoleMessages(payload) {
  if (!payload || typeof payload !== "object") return [];
  const candidates = [payload.messages, payload.user_messages, payload.conversation, payload.prompt_messages];
  for (const value of candidates) {
    if (Array.isArray(value) && value.length) return value.filter(Boolean);
  }
  if (payload.query) return [{ role: "query", content: payload.query }];
  if (payload.prompt) return [{ role: "prompt", content: payload.prompt }];
  return [];
}

function normalizeRoleOutputMessages(output) {
  if (!output || typeof output !== "object") return [];
  const messages = [];
  if (output.assistant_message) messages.push(output.assistant_message);
  if (output.content && !messages.length) messages.push({ role: "assistant", content: output.content });
  if (output.system) messages.push({ role: "system", content: output.system });
  if (output.skill_prompt) messages.push({ role: "skill_prompt", content: output.skill_prompt });
  if (output.turn_instruction) messages.push({ role: "turn_instruction", content: output.turn_instruction });
  if (output.reason) messages.push({ role: "reason", content: output.reason });
  return messages.filter((msg) => msg && (msg.content || msg.text));
}

function normalizeToolCalls(output) {
  if (!output || typeof output !== "object") return [];
  const calls = output.tool_calls || output.assistant_message?.tool_calls || output.actual_tool_calls || output.expected_tool_calls || [];
  return Array.isArray(calls) ? calls : [];
}

function roleReadableItems(role, input, output) {
  const inputItems = [];
  const outputItems = [];
  if (input?.task_id) inputItems.push(["Task", input.task_id]);
  if (input?.query) inputItems.push(["Query", input.query]);
  if (input?.prompt_style) inputItems.push(["Prompt Style", input.prompt_style]);
  if (Array.isArray(input?.turn_prompt_skills)) inputItems.push(["Prompt Skills", input.turn_prompt_skills.map((skill) => skill.name || skill.skill_name || "skill").join(", ") || "none"]);
  if (Array.isArray(input?.bundle_cases)) inputItems.push(["Bundle Cases", `${input.bundle_cases.length} case(s)`]);
  if (Array.isArray(input?.maintenance_results)) inputItems.push(["Maintenance Results", `${input.maintenance_results.length} result(s)`]);

  if (Array.isArray(output?.selected)) outputItems.push(["Selected", output.selected.map((skill) => skill.name || skill.skill_name || "skill").join(", ") || "none"]);
  if (Array.isArray(output?.candidates)) outputItems.push(["Candidates", output.candidates.map((skill) => `${skill.name || "skill"}:${skill.score ?? "?"}${skill.filter_reason ? ` (${skill.filter_reason})` : ""}`).join("\n") || "none"]);
  if (Array.isArray(output?.bundles)) outputItems.push(["Bundles", output.bundles.map((bundle) => `${bundle.skill_name || bundle.bundle_id || "bundle"}: +${bundle.positive || 0}/-${bundle.negative || 0}`).join("\n")]);
  if (output?.aggregate) outputItems.push(["Aggregate", JSON.stringify(output.aggregate)]);
  if (Array.isArray(output?.decisions)) outputItems.push(["Decisions", output.decisions.map((decision) => `${decision.skill_name || "skill"} -> ${decision.action}: ${decision.reason || ""}`).join("\n")]);
  if (output?.store_after) outputItems.push(["Store After", `active=${output.store_after.n_active ?? "?"}, disabled=${output.store_after.n_disabled ?? "?"}, total=${output.store_after.n_total ?? "?"}`]);
  if (!inputItems.length && input && typeof input === "object") inputItems.push(...Object.entries(input).slice(0, 5).map(([k, v]) => [k, summarizeRoleValue(v)]));
  if (!outputItems.length && output && typeof output === "object") outputItems.push(...Object.entries(output).slice(0, 5).map(([k, v]) => [k, summarizeRoleValue(v)]));
  return { input: inputItems, output: outputItems };
}

function summarizeRoleValue(value) {
  if (Array.isArray(value)) return `${value.length} item(s)`;
  if (value && typeof value === "object") return JSON.stringify(value).slice(0, 500);
  return value ?? "";
}

function renderReadableEventItems(items, emptyText) {
  if (!items.length) return `<div class='maintenance-missing-detail'>${escapeHtml(emptyText)}</div>`;
  return `
    <div class="event-readable-list">
      ${items.map(([key, value]) => `
        <div class="event-readable-row">
          <strong>${escapeHtml(key)}</strong>
          <span>${escapeHtml(compactMultiline(String(value ?? ""), 1000))}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderMessageBubble(message, idx) {
  const role = message?.role || `message_${idx + 1}`;
  const content = message?.content || message?.text || "";
  return `
    <article class="message-bubble message-${escapeHtml(String(role).replace(/[^a-z0-9_-]/gi, "_"))}">
      <div class="message-role">${escapeHtml(role)}</div>
      <div class="message-content">${escapeHtml(compactMultiline(content || "(empty)", 1200))}</div>
    </article>
  `;
}

function renderToolCallPill(call) {
  const name = call?.name || call?.function?.name || "tool_call";
  const args = call?.arguments || call?.function?.arguments || {};
  return `
    <div class="tool-call-pill">
      <strong>${escapeHtml(name)}</strong>
      <span>${escapeHtml(typeof args === "string" ? compactLabel(args, 180) : compactLabel(JSON.stringify(args), 180))}</span>
    </div>
  `;
}

function renderToolResultCard(result) {
  return `
    <div class="tool-call-pill tool-result-pill">
      <strong>${escapeHtml(result?.name || result?.tool_name || result?.actual_name || result?.event_type || "tool_result")}</strong>
      <span>${escapeHtml(compactMultiline(summarizeValue(result), 600))}</span>
    </div>
  `;
}

function currentPlayerScene() {
  const frame = resolvePlayerFrame(maintenanceState.currentFrameIndex || 0);
  return { frame, scene: buildPlayerScene(frame) };
}

function openPlayerOverlay(type, payload = {}) {
  maintenanceState.overlayStack.push({ type, payload, frameIndex: maintenanceState.currentFrameIndex || 0 });
  renderMaintenanceDetail(maintenanceState.currentDetail);
}

function openPlayerOverlayFromRegistry(id, type) {
  const payload = maintenanceState.modalPayloadRegistry[id] || {};
  openPlayerOverlay(type, payload);
}

function popPlayerOverlay() {
  maintenanceState.overlayStack.pop();
  renderMaintenanceDetail(maintenanceState.currentDetail);
}

function closePlayerOverlays() {
  maintenanceState.overlayStack = [];
  renderMaintenanceDetail(maintenanceState.currentDetail);
}

function renderPlayerOverlayStack() {
  const overlay = maintenanceState.overlayStack[maintenanceState.overlayStack.length - 1];
  if (!overlay) return "";
  const { frame, scene } = currentPlayerScene();
  const frames = maintenanceState.currentPlayer?.frames || [];
  const idx = Number(maintenanceState.currentFrameIndex || 0);
  return `
    <div class="player-overlay-backdrop" onclick="closePlayerOverlays()">
      <section class="player-overlay-panel" onclick="event.stopPropagation()">
        <div class="player-overlay-head">
          <div>
            <div class="maintenance-stage-kicker">State Overlay</div>
            <div class="floating-detail-title">${escapeHtml(playerOverlayTitle(overlay))}</div>
            <div class="maintenance-stage-subtitle">Frame ${escapeHtml(String((frame.index ?? 0) + 1))}: ${escapeHtml(frame.action_kind || "")}</div>
          </div>
          <div class="player-controls">
            ${maintenanceState.overlayStack.length > 1 ? `<button class="btn chip-btn" onclick="popPlayerOverlay()">Back</button>` : ""}
            <button class="btn chip-btn" onclick="shiftPlayerMarker(-1)">Prev Mark</button>
            <button class="btn chip-btn" onclick="shiftPlayerMarker(1)">Next Mark</button>
            <button class="btn chip-btn" onclick="jumpNextNonExecutor()">Next Role</button>
            <button class="btn chip-btn" onclick="shiftPlayerFrame(-1)">Prev</button>
            <span class="timeline-pill player-frame-count">${escapeHtml(`${idx + 1}/${Math.max(1, frames.length)}`)}</span>
            <button class="btn chip-btn" onclick="shiftPlayerFrame(1)">Next</button>
            <button class="floating-close-btn" onclick="closePlayerOverlays()">×</button>
          </div>
        </div>
        ${frames.length ? `
          <div class="player-overlay-timeline">
            <input class="player-slider" data-player-slider type="range" min="0" max="${frames.length - 1}" value="${idx}" oninput="previewPlayerFrame(Number(this.value))" onchange="commitPlayerFrame(Number(this.value))">
            ${renderPlayerMarkerRail(frames, idx)}
          </div>
        ` : ""}
        <div class="player-overlay-body" id="player-overlay-body-dynamic">
          ${renderPlayerOverlayBody(overlay, scene, frame)}
        </div>
      </section>
    </div>
  `;
}

function refreshPlayerOverlayDynamic(scene, frame) {
  if (!maintenanceState.overlayStack.length) return;
  const overlayBody = document.getElementById("player-overlay-body-dynamic");
  const overlay = maintenanceState.overlayStack[maintenanceState.overlayStack.length - 1];
  if (overlayBody && overlay) {
    const scrollTop = overlayBody.scrollTop;
    overlayBody.innerHTML = renderPlayerOverlayBody(overlay, scene, frame);
    overlayBody.scrollTop = scrollTop;
  }
  document.querySelectorAll(".player-overlay-head .maintenance-stage-subtitle").forEach((el) => {
    el.textContent = `Frame ${String((frame.index ?? 0) + 1)}: ${frame.action_kind || ""}`;
  });
}

function playerOverlayTitle(overlay) {
  return {
    skill_store: "Skill Store Explorer",
    skill: "Skill Detail",
    bundle: "Bundle Detail",
    test_result: "Test Result Detail",
    lineage: "Version Lineage",
    raw: "Raw Debug Payload",
  }[overlay.type] || "Player Detail";
}

function renderPlayerOverlayBody(overlay, scene, frame) {
  if (overlay.type === "skill_store") return renderPlayerSkillStoreOverlay(scene, frame, overlay.payload || {});
  if (overlay.type === "skill") return renderPlayerSkillOverlay(scene, overlay.payload || {});
  if (overlay.type === "bundle") return renderPlayerBundleOverlay(scene, overlay.payload || {});
  if (overlay.type === "test_result") return renderPlayerTestResultOverlay(scene, overlay.payload || {});
  if (overlay.type === "lineage") return renderPlayerLineageOverlay(overlay.payload || {});
  if (overlay.type === "raw") return renderPayloadNavigator(overlay.payload?.raw || {}, "raw");
  return renderPayloadNavigator({ overlay, frame }, "overlay");
}

function slotForScene(scene, slotId) {
  return (scene.slots || []).find((slot) => slot.id === slotId) || null;
}

function matchingSceneElements(scene, kind) {
  const slot = slotForScene(scene, kind);
  if (!slot) return [];
  return Object.values(resolvePlayerFrame(maintenanceState.currentFrameIndex || 0).elements || {}).filter(slot.match);
}

function renderPlayerSkillStoreOverlay(scene, frame, payload) {
  const storeSlot = slotForScene(scene, "skill_store");
  const state = storeSlot?.element?.state || {};
  const represented = state.represented_element?.state || state;
  const skills = represented.skills || represented.store_summary?.skills || [];
  const newNames = new Set(represented.new_skill_names || represented.last_delta?.new_skill_names || []);
  const updatedNames = new Set((frame.changed_elements || [])
    .filter((id) => String(id).startsWith("skill:"))
    .map((id) => String(id).replace(/^skill:/, "")));
  return `
    <div class="store-overlay-layout">
      <aside class="store-shelf-pane">
        <div class="maintenance-stage-title">Skill Library</div>
        <input class="store-search-input" placeholder="Search skill name, keywords, tools..." oninput="filterPlayerStoreSkills(this.value)">
        <div class="store-skill-list" id="player-store-skill-list">
          ${skills.length ? skills.map((skill, idx) => renderStoreSkillRow(skill, idx, { newNames, updatedNames })).join("") : "<div class='maintenance-doc-empty'>No skills in current frame store.</div>"}
        </div>
      </aside>
      <main class="store-summary-pane">
        <div class="maintenance-metric-grid">
          ${metricMini("Total", represented.n_total ?? skills.length)}
          ${metricMini("Active", represented.n_active ?? skills.filter((s) => s.status === "active").length)}
          ${metricMini("Stale", represented.n_stale ?? skills.filter((s) => s.stale).length)}
          ${metricMini("Disabled", represented.n_disabled ?? skills.filter((s) => s.status === "disabled").length)}
        </div>
        <section class="maintenance-stage-card board-neutral">
          <div class="maintenance-stage-title">Recently Changed</div>
          ${renderChipList([...newNames, ...updatedNames])}
        </section>
        <section class="maintenance-stage-card board-neutral">
          <div class="maintenance-stage-title">Store State</div>
          ${renderPayloadNavigator(represented, "skill_store")}
        </section>
      </main>
    </div>
  `;
}

function renderStoreSkillRow(skill, idx, flags) {
  const name = skill.name || skill.skill_name || `skill_${idx}`;
  const highlighted = flags.newNames.has(name) || flags.updatedNames.has(name);
  const haystack = [name, skill.description, skill.status, ...(skill.intent_keywords || []), ...(skill.allowed_tools || [])].join(" ").toLowerCase();
  const overlayId = rememberModalPayload({ skillName: name });
  return `
    <button class="store-skill-row ${highlighted ? "highlighted" : ""}" data-store-search="${escapeHtml(haystack)}" onclick="openPlayerOverlayFromRegistry('${escapeJs(overlayId)}', 'skill')">
      <span class="store-skill-name">${escapeHtml(name)}</span>
      <span class="store-skill-desc">${escapeHtml(compactLabel(skill.description || "No description", 120))}</span>
      <span class="timeline-pill-row">
        <span class="timeline-pill">${escapeHtml(skill.status || "unknown")}</span>
        <span class="timeline-pill">${escapeHtml(skill.version || skill.version_kind || "v?")}</span>
        ${highlighted ? "<span class='timeline-pill'>changed</span>" : ""}
      </span>
    </button>
  `;
}

function filterPlayerStoreSkills(query) {
  const q = String(query || "").toLowerCase().trim();
  document.querySelectorAll("#player-store-skill-list .store-skill-row").forEach((row) => {
    const haystack = row.getAttribute("data-store-search") || "";
    row.style.display = !q || haystack.includes(q) ? "" : "none";
  });
}

function renderPlayerSkillOverlay(scene, payload) {
  const skill = findPlayerSkill(scene, payload.skillName);
  if (!skill) return "<div class='maintenance-doc-empty'>No skill artifact visible in this frame.</div>";
  const lineage = skill.lineage || skill.version_history || skill.history || [];
  return `
    <div class="skill-explorer-like-detail">
      <section class="maintenance-stage-card board-accent">
        <div class="maintenance-stage-head">
          <div>
            <div class="maintenance-stage-kicker">Skill Explorer</div>
            <div class="maintenance-stage-title">${escapeHtml(skill.name || payload.skillName || "Skill")}</div>
            <div class="maintenance-stage-subtitle">${escapeHtml(skill.description || "")}</div>
          </div>
          <div class="player-controls">
            <button class="btn chip-btn" onclick="openPlayerOverlayFromRegistry('${escapeJs(rememberModalPayload({ skillName: skill.name || payload.skillName || "" }))}', 'bundle')">Bundle</button>
            <button class="btn chip-btn" onclick="openPlayerOverlayFromRegistry('${escapeJs(rememberModalPayload({ skillName: skill.name || payload.skillName || "" }))}', 'test_result')">Tests</button>
            <button class="btn chip-btn" onclick="openPlayerOverlayFromRegistry('${escapeJs(rememberModalPayload({ skill }))}', 'lineage')">Lineage</button>
          </div>
        </div>
        <div class="maintenance-metric-grid">
          ${metricMini("Retrieved", skill.retrieved_count ?? skill.retrieval_count ?? 0)}
          ${metricMini("Status", skill.status || "—")}
          ${metricMini("Version", skill.version || "—")}
          ${metricMini("Stale", skill.stale ?? false)}
        </div>
        <div class="skill-code-section">
          <div class="maintenance-section-title">Implementation / Body</div>
          <pre class="maintenance-code-block skill-body-block">${escapeHtml(skill.body || skill.implementation || skill.content || skill.description || "No implementation/body recorded.")}</pre>
        </div>
      </section>
      <section class="maintenance-stage-card board-neutral">
        <div class="maintenance-stage-title">Interface</div>
        ${renderInterfaceContract(skill.interface || {})}
      </section>
      <section class="maintenance-stage-card board-neutral">
        <div class="maintenance-stage-title">Evidence / Metadata</div>
        ${renderReadablePayload({
          dependencies: skill.dependencies || [],
          dependency_pins: skill.dependency_pins || [],
          intent_keywords: skill.intent_keywords || skill.metadata?.intent_keywords || [],
          allowed_tools: skill.allowed_tools || skill.metadata?.allowed_tools || [],
          source_task_ids: skill.source_task_ids || skill.metadata?.source_task_ids || [],
          lineage_count: Array.isArray(lineage) ? lineage.length : 0,
        })}
      </section>
      ${renderDebugRaw("Raw Skill", skill)}
    </div>
  `;
}

function findPlayerSkill(scene, name) {
  const wanted = String(name || "").trim();
  const storeSlot = slotForScene(scene, "skill_store");
  const storeState = storeSlot?.element?.state?.represented_element?.state || storeSlot?.element?.state || {};
  const storeSkills = storeState.skills || storeState.store_summary?.skills || [];
  const topLevelSkills = Object.values(resolvePlayerFrame(maintenanceState.currentFrameIndex || 0).elements || {})
    .filter((el) => String(el.element_id || "").startsWith("skill:") || el.kind === "skill")
    .map((el) => el.state || {});
  const finalArtifacts = maintenanceState.currentDetail?.artifacts || [];
  const mergeSkill = (base) => {
    if (!base) return null;
    const skillName = String(base.name || base.skill_name || wanted || "").trim();
    const full = [...topLevelSkills, ...finalArtifacts].find((item) => String(item.name || item.skill_name || "") === skillName) || {};
    const raw = typeof full.raw === "string" ? safeParseJsonText(full.raw) : {};
    return deepMergeObjects(raw, full, base);
  };
  if (wanted) {
    const fromStore = storeSkills.find((skill) => String(skill.name || skill.skill_name || "") === wanted);
    if (fromStore) return mergeSkill(fromStore);
  }
  const skillElements = topLevelSkills;
  if (wanted) {
    const fromElement = skillElements.find((skill) => String(skill.name || skill.skill_name || "") === wanted);
    if (fromElement) return mergeSkill(fromElement);
  }
  return mergeSkill(skillElements[skillElements.length - 1] || storeSkills[storeSkills.length - 1] || finalArtifacts[finalArtifacts.length - 1]) || null;
}

function safeParseJsonText(text) {
  try {
    return JSON.parse(String(text || ""));
  } catch (_err) {
    return {};
  }
}

function deepMergeObjects(...items) {
  const out = {};
  for (const item of items) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    for (const [key, value] of Object.entries(item)) {
      if (
        value && typeof value === "object" && !Array.isArray(value)
        && out[key] && typeof out[key] === "object" && !Array.isArray(out[key])
      ) {
        out[key] = deepMergeObjects(out[key], value);
      } else if (value !== undefined && value !== "") {
        out[key] = value;
      }
    }
  }
  return out;
}

function renderPlayerBundleOverlay(scene, payload) {
  const bundles = matchingSceneElements(scene, "bundle").map((el) => el.state || {});
  const wanted = String(payload.skillName || "").trim();
  const bundle = (wanted ? bundles.find((item) => String(item.skill_name || item.name || "").includes(wanted)) : null)
    || bundles[bundles.length - 1];
  if (!bundle) return "<div class='maintenance-doc-empty'>No bundle visible in this frame.</div>";
  const cases = bundle.cases || bundle.bundle?.cases || {};
  return `
    <div class="artifact-detail-stack">
      <section class="maintenance-stage-card board-accent">
        <div class="maintenance-stage-title">${escapeHtml(bundle.skill_name || bundle.name || "Bundle")}</div>
        <div class="maintenance-metric-grid">
          ${metricMini("Positive", (cases.positive || []).length)}
          ${metricMini("Negative", (cases.negative || []).length)}
          ${metricMini("Integration", (cases.integration || []).length)}
          ${metricMini("Total", Object.values(cases).reduce((n, arr) => n + (Array.isArray(arr) ? arr.length : 0), 0))}
        </div>
      </section>
      ${renderBundleCaseSection("Positive Cases", cases.positive || [], "positive")}
      ${renderBundleCaseSection("Negative Cases", cases.negative || [], "negative")}
      ${renderBundleCaseSection("Integration-Derived Cases", cases.integration || [], "integration")}
      ${renderDebugRaw("Raw Bundle", bundle)}
    </div>
  `;
}

function renderPlayerTestResultOverlay(scene, payload) {
  const results = matchingSceneElements(scene, "test_result").map((el) => el.state || {});
  const wanted = String(payload.skillName || "").trim();
  const result = (wanted ? results.find((item) => String(item.skill_name || item.name || "").includes(wanted)) : null)
    || results[results.length - 1];
  if (!result) return "<div class='maintenance-doc-empty'>No test result visible in this frame.</div>";
  return `
    <div class="artifact-detail-stack">
      <section class="maintenance-stage-card ${result.aggregate?.pass_all_tests === false || result.pass_all_tests === false ? "board-danger" : "board-accent"}">
        <div class="maintenance-stage-title">${escapeHtml(result.skill_name || result.name || "Test Result")}</div>
        <div class="maintenance-metric-grid">
          ${metricMini("pass_all", result.aggregate?.pass_all_tests ?? result.pass_all_tests ?? "—")}
          ${metricMini("cases", result.aggregate?.n_cases ?? result.n_cases ?? "—")}
          ${metricMini("delta_acc", result.aggregate?.delta_acc ?? result.delta_acc ?? "—")}
          ${metricMini("delta_tokens", result.aggregate?.delta_tokens ?? result.delta_tokens ?? "—")}
          ${metricMini("delta_steps", result.aggregate?.delta_steps ?? result.delta_steps ?? "—")}
        </div>
      </section>
      ${renderReadablePayload(result)}
      ${renderDebugRaw("Raw Test Result", result)}
    </div>
  `;
}

function renderPlayerLineageOverlay(payload) {
  const skill = payload.skill || {};
  const history = skill.lineage || skill.version_history || skill.history || [];
  return `
    <div class="artifact-detail-stack">
      <section class="maintenance-stage-card board-accent">
        <div class="maintenance-stage-title">${escapeHtml(skill.name || "Skill Lineage")}</div>
        <div class="maintenance-stage-subtitle">历史版本和依赖 pin 用于判断 stale / rollback / legacy lock。</div>
      </section>
      <div class="lineage-timeline">
        ${Array.isArray(history) && history.length ? history.map((item, idx) => `
          <button class="lineage-row" onclick="openPlayerOverlayFromRegistry('${escapeJs(rememberModalPayload({ raw: item }))}', 'raw')">
            <span class="lineage-index">${idx + 1}</span>
            <span class="lineage-title">${escapeHtml(item.version || item.name || item.event || `history_${idx + 1}`)}</span>
            <span class="lineage-desc">${escapeHtml(compactLabel(item.reason || item.summary || item.status || "", 140))}</span>
          </button>
        `).join("") : "<div class='maintenance-doc-empty'>No lineage history recorded in current artifact.</div>"}
      </div>
      ${renderDebugRaw("Raw Lineage Source", skill)}
    </div>
  `;
}

function renderPlayerHumanSummary(element, frame) {
  const raw = element.state?.represented_element?.state || element.state || {};
  const event = raw.last_raw_event || raw.last_event || raw;
  const input = event.input || raw.last_input || raw.input || {};
  const output = event.output || raw.last_output || raw.output || raw.audit || {};
  return `
    <div class="player-summary-panel">
      <div class="maintenance-section-title">Frame Meaning</div>
      <div class="readable-kv-list">
        <div class="readable-kv-row"><div class="readable-kv-key">Role</div><div class="readable-kv-value">${escapeHtml(frame.role_group || element.kind || "")}</div></div>
        <div class="readable-kv-row"><div class="readable-kv-key">Consumes</div><div class="readable-kv-value">${renderChipList(frame.consumed_slots || [])}</div></div>
        <div class="readable-kv-row"><div class="readable-kv-key">Produces</div><div class="readable-kv-value">${renderChipList(frame.produced_slots || [])}</div></div>
        ${frame.condition_result ? `<div class="readable-kv-row"><div class="readable-kv-key">Decision</div><div class="readable-kv-value">${escapeHtml(frame.condition_result)}</div></div>` : ""}
      </div>
      <div class="sequence-io-grid single-json-column">
        ${renderTextualPayloadCard("Input", input)}
        ${renderTextualPayloadCard("Output", output)}
      </div>
    </div>
  `;
}

function openPlayerElementModal() {
  const frame = resolvePlayerFrame(maintenanceState.currentFrameIndex || 0);
  const scene = buildPlayerScene(frame);
  const element = scene.elementsById?.[maintenanceState.selectedPlayerElementId];
  if (!element) return;
  openArbitraryModal(element.label || element.element_id || "Player Element", {
    frame: {
      index: frame.index,
      name: frame.name,
      action_kind: frame.action_kind,
      changed_elements: frame.changed_elements || [],
      delta: frame.delta || {},
    },
    element,
  });
}

function renderTaskProblemBar(page, dataflow) {
  const conversation = (dataflow.nodes || []).find((node) => node.kind === "conversation");
  const problem = conversation?.taskProblem || page?.subtitle || page?.title || "";
  return `
    <section class="task-problem-bar">
      <div>
        <div class="maintenance-stage-kicker">Task Problem</div>
        <div class="task-problem-text">${escapeHtml(problem || "No task problem recorded for this round.")}</div>
      </div>
      <button class="btn chip-btn" onclick="pushMaintenanceRoute({view:'executor', experimentId:'${escapeJs(maintenanceState.currentId)}', pageId:'${escapeJs(page?.page_id || '')}'})">Open Conversation</button>
    </section>
  `;
}

function renderCompactRoundMap(page) {
  const graph = buildBoardGraph(page);
  return `
    <div class="compact-flow-strip">
      ${graph.nodes.map((node, idx) => renderCompactNode(node, idx, page)).join("")}
    </div>
  `;
}

function buildSequentialRoleFlow(page) {
  const cards = page?.flow_cards || [];
  return cards
    .filter((card) => !["summary_board", "debug_event"].includes(card.type || ""))
    .map((card, idx) => normalizeSequentialCard(card, idx))
    .filter(Boolean);
}

function normalizeSequentialCard(card, idx) {
  if (String(card.type || "").startsWith("algorithm_")) {
    return normalizeAlgorithmMonitorCard(card, idx);
  }
  const base = {
    id: `seq_${idx}_${card.type || "card"}`,
    index: idx,
    type: card.type || "card",
    title: card.title || card.type || `Step ${idx + 1}`,
    subtitle: card.subtitle || "",
    tone: boardToneClass(card.tone),
    raw: card,
    input: card.detail?.input || {},
    output: card.detail?.output || {},
    metrics: [],
    detailHtml: () => renderFallbackInspector(card),
  };
  if (card.type === "method_case") {
    const assertions = card.assertions || {};
    return {
      ...base,
      role: "Method Test",
      title: card.case_id || "Method Case",
      input: card.given || {},
      output: {
        model_output: card.model_output || {},
        algorithm_output: card.algorithm_output || {},
      },
      metrics: [
        ["Passed", card.passed],
        ["Assertions", `${Object.values(assertions).filter(Boolean).length}/${Object.keys(assertions).length}`],
        ["Role Calls", Object.keys(card.algorithm_output?.role_calls || {}).length],
      ],
      detailHtml: () => renderMethodCaseReport(card, { includeRaw: true }),
    };
  }
  if (card.type === "run") {
    const run = card.run || {};
    return {
      ...base,
      role: "Executor",
      input: card.detail?.input || {},
      output: card.detail?.output || run,
      metrics: [
        ["Official", run.official_valid ?? "—"],
        ["Call F1", run.call_f1 ?? "—"],
        ["Tokens", run.total_tokens ?? "—"],
        ["Errors", (run.call_errors || []).length],
      ],
      detailHtml: () => renderRunCard(card),
    };
  }
  if (card.type === "role_extractor") {
    return {
      ...base,
      role: "Extractor",
      metrics: [
        ["Artifacts", card.artifact_count ?? 0],
        ["Version", card.artifact_preview?.version_kind || "—"],
      ],
      detailHtml: () => renderExtractorCard(card),
    };
  }
  if (card.type === "role_bundle_builder") {
    return {
      ...base,
      role: "Bundle Builder",
      metrics: [
        ["Positive", card.counts?.positive ?? 0],
        ["Negative", card.counts?.negative ?? 0],
        ["Integration", card.counts?.integration ?? 0],
      ],
      detailHtml: () => renderBundleCard(card),
    };
  }
  if (card.type === "maintenance_test") {
    return {
      ...base,
      role: "Unit Utility Test",
      input: {
        skill_name: card.skill_name,
        skill_version: card.skill_version,
        bundle_version: card.bundle_version,
      },
      output: card.detail || card,
      metrics: [
        ["Cases", card.aggregate?.n_cases ?? 0],
        ["Pass All", card.aggregate?.pass_all_tests ?? "—"],
        ["Regressed", card.aggregate?.n_regressed ?? 0],
      ],
      detailHtml: () => renderMaintenanceTestCard(card),
    };
  }
  if (card.type === "role_refiner") {
    return {
      ...base,
      role: "Refiner",
      metrics: [
        ["Action", card.decision?.action || "—"],
        ["Version", card.decision?.version_kind || "—"],
      ],
      detailHtml: () => renderRoleRefinerCard(card),
    };
  }
  if (card.type === "refine_decision") {
    return {
      ...base,
      role: "Refine Decision",
      input: card.detail || {},
      output: card.detail?.raw_decision || card,
      metrics: [
        ["Before", card.version_before ?? "—"],
        ["After", card.version_after ?? "—"],
        ["Regressions", card.failed_count ?? 0],
      ],
      detailHtml: () => renderRefineDecisionCard(card),
    };
  }
  if (card.type === "skill_delta") {
    return {
      ...base,
      role: "Skill Store",
      input: card.detail || {},
      output: card.detail || card,
      metrics: [
        ["New Skills", (card.new_skill_names || []).length],
        ["Skills After", card.n_skills_after ?? 0],
      ],
      detailHtml: () => renderSkillDeltaCard(card),
    };
  }
  return {
    ...base,
    role: card.type || "Role",
    metrics: [["Type", card.type || "card"]],
  };
}

function normalizeAlgorithmMonitorCard(card, idx) {
  const role = card.role || algorithmRoleName(card.type);
  return {
    id: `seq_${idx}_${card.type || "algorithm"}`,
    index: idx,
    type: card.type || "algorithm",
    title: card.title || role,
    subtitle: card.subtitle || "",
    role,
    tone: boardToneClass(card.tone),
    raw: card,
    input: card.detail?.input || {},
    output: card.detail?.output || {},
    debugRaw: card.detail?.debug_raw || {},
    inputSummary: card.input_summary || summarizeValue(card.detail?.input || {}),
    outputSummary: card.output_summary || summarizeValue(card.detail?.output || {}),
    metrics: (card.metrics || []).map((item) => [item.label, item.value, item.help]),
    detailHtml: () => renderAlgorithmMonitorCardDetail(card),
  };
}

function algorithmRoleName(type) {
  const names = {
    algorithm_executor: "Executor",
    algorithm_extractor: "Extractor",
    algorithm_bundle_builder: "Bundle Builder",
    algorithm_replay: "Executor",
    algorithm_refiner: "Refiner",
    algorithm_store: "Skill Store",
  };
  return names[type] || "Role";
}

function renderSequentialRoleFlow(page, flow, options = {}) {
  return `
    <section class="sequence-flow-shell">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">${escapeHtml(options.kicker || "Execution Flow")}</div>
          <div class="maintenance-stage-title">${escapeHtml(options.title || "Sequential Role Cards")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(options.subtitle || "每个卡片表示一次真实记录到的 role/test 输入输出；点击卡片或输入输出块可放大查看。")}</div>
        </div>
        <span class="timeline-pill">${escapeHtml(`${flow.length} cards`)}</span>
      </div>
      <div class="sequence-card-list">
        ${flow.length ? flow.map(renderSequentialCard).join("") : "<div class='timeline-empty'>No role cards recorded.</div>"}
      </div>
    </section>
  `;
}

function renderSequentialCard(card) {
  return `
    <article class="sequence-role-card ${escapeHtml(card.tone)} ${String(card.type || "").startsWith("algorithm_") ? "algorithm-role-card" : ""}">
      <div class="sequence-step-index">${escapeHtml(String(card.index + 1).padStart(2, "0"))}</div>
      <div class="sequence-role-main">
        <div class="sequence-role-head">
          <div>
            <div class="maintenance-stage-kicker">${escapeHtml(card.role || card.type)}</div>
            <div class="sequence-role-title">${escapeHtml(card.title)}</div>
            <div class="maintenance-stage-subtitle">${escapeHtml(card.subtitle || "")}</div>
          </div>
          <button class="btn chip-btn" onclick="openSequentialCardModal('${escapeJs(card.id)}')">Open Detail</button>
        </div>
        <div class="maintenance-metric-grid compact-metrics">
          ${(card.metrics || []).slice(0, 4).map(([key, value, help]) => metricMini(key, value, help)).join("")}
        </div>
        <div class="algorithm-io-summary">
          ${renderClickablePayloadPreview("Input", card.input, card.id, "input", card.inputSummary)}
          ${renderClickablePayloadPreview("Output", card.output, card.id, "output", card.outputSummary)}
        </div>
      </div>
    </article>
  `;
}

function renderClickablePayloadPreview(title, payload, cardId, side, summaryOverride = "") {
  return `
    <button class="sequence-io-preview" onclick="openSequentialPayloadModal('${escapeJs(cardId)}', '${escapeJs(side)}')">
      <span class="maintenance-stage-kicker">${escapeHtml(title)}</span>
      <span class="sequence-preview-text">${escapeHtml(summaryOverride || summarizeValue(payload))}</span>
      <span class="timeline-pill">expand</span>
    </button>
  `;
}

function renderRoundTestResults(page) {
  const tests = (page?.flow_cards || [])
    .map((card, idx) => ({ card, idx }))
    .filter((item) => item.card.type === "maintenance_test" || item.card.type === "method_case");
  if (!tests.length) return "";
  return `
    <section class="sequence-test-results">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Test Results</div>
          <div class="maintenance-stage-title">测试结果</div>
          <div class="maintenance-stage-subtitle">测试页保留单独结果区；role 过程仍在上方顺序卡片中展示。</div>
        </div>
      </div>
      <div class="sequence-test-grid">
        ${tests.map((item) => renderTestResultSummaryCard(item.card, item.idx)).join("")}
      </div>
    </section>
  `;
}

function findSequentialCard(cardId) {
  const page = resolveCurrentPage(maintenanceState.currentDetail);
  return buildSequentialRoleFlow(page).find((card) => card.id === cardId) || null;
}

function openSequentialCardModal(cardId) {
  const card = findSequentialCard(cardId);
  if (!card) return;
  maintenanceState.modalPayload = {
    title: `${card.role || card.type}: ${card.title}`,
    subtitle: card.subtitle || "",
    body: card.detailHtml ? card.detailHtml() : renderDebugRaw("Raw Card", card.raw),
  };
  renderMaintenanceDetail(maintenanceState.currentDetail);
}

function openSequentialPayloadModal(cardId, side) {
  const card = findSequentialCard(cardId);
  if (!card) return;
  const payload = side === "debug" ? (card.debugRaw || card.raw) : (side === "output" ? card.output : card.input);
  maintenanceState.modalPayload = {
    title: `${card.role || card.type} ${side === "debug" ? "Debug Raw" : side === "output" ? "Output" : "Input"}`,
    subtitle: card.title,
    body: renderTextualPayloadCard(side === "debug" ? "Debug Raw" : side === "output" ? "Output" : "Input", payload),
  };
  renderMaintenanceDetail(maintenanceState.currentDetail);
}

function openArbitraryModal(title, payload) {
  maintenanceState.modalPayload = {
    title,
    subtitle: "",
    body: renderTextualPayloadCard(title, payload),
  };
  renderMaintenanceDetail(maintenanceState.currentDetail);
}

function rememberModalPayload(payload) {
  maintenanceState.modalPayloadCounter += 1;
  const id = `modal_payload_${maintenanceState.modalPayloadCounter}`;
  maintenanceState.modalPayloadRegistry[id] = payload;
  return id;
}

function openRememberedModal(id) {
  const row = maintenanceState.modalPayloadRegistry[id];
  if (!row) return;
  maintenanceState.modalPayload = {
    title: row.title || "Detail",
    subtitle: row.subtitle || "",
    body: renderTextualPayloadCard(row.title || "Payload", row.payload),
  };
  renderMaintenanceDetail(maintenanceState.currentDetail);
}

function closeFloatingDetailModal() {
  maintenanceState.modalPayload = null;
  renderMaintenanceDetail(maintenanceState.currentDetail);
}

function renderFloatingDetailModal() {
  const modal = maintenanceState.modalPayload;
  if (!modal) return "";
  return `
    <div class="floating-detail-backdrop" onclick="closeFloatingDetailModal()">
      <section class="floating-detail-panel" onclick="event.stopPropagation()">
        <div class="floating-detail-head">
          <div>
            <div class="maintenance-stage-kicker">Expanded Detail</div>
            <div class="floating-detail-title">${escapeHtml(modal.title || "Detail")}</div>
            <div class="maintenance-stage-subtitle">${escapeHtml(modal.subtitle || "")}</div>
          </div>
          <button class="floating-close-btn" onclick="closeFloatingDetailModal()">×</button>
        </div>
        <div class="floating-detail-body">${modal.body || ""}</div>
      </section>
    </div>
  `;
}

function renderTestResultSummaryCard(card, idx) {
  if (card.type === "method_case") {
    const assertions = card.assertions || {};
    return `
      <button class="sequence-test-summary ${escapeHtml(boardToneClass(card.tone))}" onclick="openSequentialCardModal('seq_${idx}_method_case')">
        <div class="maintenance-stage-kicker">Method Case</div>
        <div class="sequence-role-title">${escapeHtml(card.case_id || "Method Case")}</div>
        <div class="timeline-pill-row">
          <span class="timeline-pill">${escapeHtml(card.passed ? "passed" : "failed")}</span>
          <span class="timeline-pill">${escapeHtml(`${Object.values(assertions).filter(Boolean).length}/${Object.keys(assertions).length} assertions`)}</span>
        </div>
      </button>
    `;
  }
  return `
    <button class="sequence-test-summary ${escapeHtml(boardToneClass(card.tone))}" onclick="openSequentialCardModal('seq_${idx}_maintenance_test')">
      <div class="maintenance-stage-kicker">Unit Utility</div>
      <div class="sequence-role-title">${escapeHtml(card.skill_name || "skill")}</div>
      <div class="timeline-pill-row">
        <span class="timeline-pill">${escapeHtml(`cases=${card.aggregate?.n_cases ?? 0}`)}</span>
        <span class="timeline-pill">${escapeHtml(`pass_all=${card.aggregate?.pass_all_tests ?? "—"}`)}</span>
      </div>
    </button>
  `;
}

function renderAlgorithmMonitorCardDetail(card) {
  return `
    <section class="algorithm-detail-card">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">${escapeHtml(card.role || "Role")}</div>
          <div class="maintenance-stage-title">${escapeHtml(card.title || "")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(card.subtitle || "")}</div>
        </div>
        <span class="timeline-pill">${escapeHtml(card.type || "algorithm")}</span>
      </div>
      <div class="algorithm-detail-summaries">
        <div class="algorithm-summary-box">
          <div class="maintenance-stage-kicker">Input Summary</div>
          <div>${escapeHtml(card.input_summary || summarizeValue(card.detail?.input || {}))}</div>
        </div>
        <div class="algorithm-summary-box">
          <div class="maintenance-stage-kicker">Output Summary</div>
          <div>${escapeHtml(card.output_summary || summarizeValue(card.detail?.output || {}))}</div>
        </div>
      </div>
      <div class="maintenance-metric-grid compact-metrics">
        ${(card.metrics || []).slice(0, 8).map((item) => metricMini(item.label, item.value)).join("")}
      </div>
      <div class="algorithm-detail-actions">
        <button class="btn chip-btn" onclick="openSequentialPayloadModal('${escapeJs(`seq_${findCardIndexInCurrentPage(card)}_${card.type || 'algorithm'}`)}', 'input')">Open Input</button>
        <button class="btn chip-btn" onclick="openSequentialPayloadModal('${escapeJs(`seq_${findCardIndexInCurrentPage(card)}_${card.type || 'algorithm'}`)}', 'output')">Open Output</button>
      </div>
      <details class="maintenance-detail-block" open>
        <summary>Readable Output</summary>
        ${renderReadablePayload(card.detail?.output || {})}
      </details>
      <details class="maintenance-detail-block">
        <summary>Debug Raw</summary>
        ${renderPayloadNavigator(card.detail?.debug_raw || card, "debug_raw")}
      </details>
    </section>
  `;
}

function findCardIndexInCurrentPage(rawCard) {
  const page = resolveCurrentPage(maintenanceState.currentDetail);
  const cards = page?.flow_cards || [];
  const idx = cards.findIndex((item) => item === rawCard || (item.type === rawCard.type && item.title === rawCard.title));
  return Math.max(0, idx);
}

function buildCoreDataflow(page) {
  const cards = page?.flow_cards || [];
  const artifactNodes = [];
  const runCard = cards.find((card) => card.type === "run");
  const methodCase = cards.find((card) => card.type === "method_case");
  if (methodCase) artifactNodes.push(summarizeMethodCase(methodCase));
  if (runCard) artifactNodes.push(summarizeConversation(runCard));
  const extractor = cards.find((card) => card.type === "role_extractor");
  if (extractor) artifactNodes.push(summarizeSkillArtifact(extractor, runCard));
  const bundle = cards.find((card) => card.type === "role_bundle_builder");
  if (bundle) artifactNodes.push(summarizeBundle(bundle));
  const tests = cards.filter((card) => card.type === "maintenance_test");
  if (tests.length) artifactNodes.push(summarizeTestResult(tests[tests.length - 1], tests));
  const refiner = cards.find((card) => card.type === "role_refiner");
  const decision = cards.find((card) => card.type === "refine_decision");
  if (refiner || decision) artifactNodes.push(summarizeRefineDecision(refiner || decision, decision));
  const store = cards.find((card) => card.type === "skill_delta");
  if (store) artifactNodes.push(summarizeStoreDelta(store));
  const debugEvents = cards.filter((card) => card.type === "debug_event");
  if (debugEvents.length) artifactNodes.push(summarizeDebugEvents(debugEvents));
  const nodes = expandDataflowWithRoleNodes(artifactNodes);
  const edges = [];
  artifactNodes.forEach((artifact, idx) => {
    const roleId = roleNodeIdForArtifact(artifact.id);
    if (idx === 0) {
      edges.push({ from: artifact.id, to: roleId, label: "task trace" });
    } else {
      edges.push({ from: artifactNodes[idx - 1].id, to: roleId, label: dataflowEdgeLabel(artifactNodes[idx - 1].kind, artifact.kind) });
    }
    edges.push({ from: roleId, to: artifact.id, label: "produces artifact" });
  });
  return { nodes, edges };
}

function expandDataflowWithRoleNodes(artifactNodes) {
  return artifactNodes.flatMap((artifact) => {
    const roleNode = {
      id: roleNodeIdForArtifact(artifact.id),
      kind: "role",
      nodeClass: "role",
      role: artifact.role,
      title: artifact.role,
      inputSummary: artifact.inputSummary,
      outputSummary: artifact.kind,
      primaryText: `Role ${artifact.role} produces ${artifact.title}.`,
      status: "neutral",
      metrics: [["Artifact", artifact.kind]],
      targetArtifactId: artifact.id,
      rawPayload: artifact.rawPayload,
      detailPayload: artifact.detailPayload,
    };
    return [roleNode, { ...artifact, nodeClass: "artifact" }];
  });
}

function roleNodeIdForArtifact(artifactId) {
  return `role_for_${artifactId}`;
}

function isRoleNode(node) {
  return node?.nodeClass === "role" || String(node?.id || "").startsWith("role_for_");
}

function summarizeConversation(card) {
  const run = card.run || {};
  const detail = run.detail || {};
  const turns = detail.turns || [];
  const firstUser = turns.flatMap((turn) => turn.user_messages || []).map((msg) => msg.content || "").find(Boolean) || card.subtitle || "";
  return {
    id: "core_conversation",
    kind: "conversation",
    role: "Executor",
    title: "Conversation Trace",
    inputSummary: compactMultiline(firstUser || "Task query and current skill context.", 180),
    outputSummary: `${turns.length} turns, ${(detail.tool_calls || []).length} tool calls, ${(run.call_errors || []).length} call errors`,
    primaryText: compactMultiline(firstUser || "No user query recorded.", 260),
    status: run.official_valid ? "success" : ((run.call_errors || []).length ? "danger" : "warning"),
    metrics: [
      ["Official", run.official_valid ?? "—"],
      ["Call F1", run.call_f1 ?? "—"],
      ["Errors", (run.call_errors || []).length],
    ],
    detailPayload: { run, detail },
    taskProblem: firstUser || card.subtitle || "",
    rawPayload: card,
  };
}

function summarizeMethodCase(card) {
  const assertions = card.assertions || {};
  const passedCount = Object.values(assertions).filter(Boolean).length;
  return {
    id: "core_method_case",
    kind: "method_case",
    role: "Method Validator",
    title: card.case_id || "Method Case",
    inputSummary: compactMultiline(card.given?.query || card.subtitle || "Case spec", 180),
    outputSummary: `passed=${card.passed}, assertions=${passedCount}/${Object.keys(assertions).length}`,
    primaryText: compactMultiline(card.given?.query || "", 260),
    status: card.passed ? "success" : "danger",
    metrics: [
      ["Passed", card.passed],
      ["Assertions", `${passedCount}/${Object.keys(assertions).length}`],
      ["Role Calls", Object.keys(card.algorithm_output?.role_calls || {}).length],
    ],
    detailPayload: card.detail || card,
    rawPayload: card,
  };
}

function summarizeSkillArtifact(card, runCard) {
  const preview = card.artifact_preview || {};
  const output = card.detail?.output || {};
  const artifacts = output.artifacts || [];
  const first = artifacts[0] || preview;
  const retrieved = [
    ...((runCard?.run || {}).retrieved_skills || []),
    ...((runCard?.run || {}).prompt_injected_skills || []),
  ];
  const retrievedCount = retrieved.filter((name) => name === (first.name || preview.name)).length;
  return {
    id: "core_skill",
    kind: "skill",
    role: "Extractor",
    title: preview.name || "Skill Artifact",
    inputSummary: compactMultiline(card.user_preview || "Trace evidence from executor.", 180),
    outputSummary: compactMultiline(preview.description || "No skill description recorded.", 220),
    primaryText: first.body || first.implementation || first.content || preview.description || "",
    status: "accent",
    metrics: [
      ["Retrieved", retrievedCount],
      ["Kind", preview.kind || "—"],
      ["Version", preview.version_kind || "—"],
    ],
    detailPayload: { preview, artifact: first, artifacts, retrieved_count: retrievedCount, input: card.detail?.input || {}, output },
    rawPayload: card,
  };
}

function summarizeBundle(card) {
  const counts = card.counts || {};
  return {
    id: "core_bundle",
    kind: "bundle",
    role: "Bundle Builder",
    title: card.subtitle ? `Bundle: ${card.subtitle}` : "Maintenance Bundle",
    inputSummary: compactMultiline(card.user_preview || "Skill artifact plus trace evidence.", 180),
    outputSummary: `positive=${counts.positive ?? 0}, negative=${counts.negative ?? 0}, integration=${counts.integration ?? 0}`,
    primaryText: compactMultiline(card.maintenance_notes || "No maintenance notes recorded.", 320),
    status: "accent",
    metrics: [
      ["Positive", counts.positive ?? 0],
      ["Negative", counts.negative ?? 0],
      ["Integration", counts.integration ?? 0],
    ],
    detailPayload: { cases: card.cases || {}, notes: card.maintenance_notes || "", input: card.detail?.input || {}, output: card.detail?.output || {} },
    rawPayload: card,
  };
}

function summarizeTestResult(card, allTests) {
  const aggregate = card.aggregate || {};
  const utility = aggregate.unit_utility_report || {};
  return {
    id: "core_test_result",
    kind: "test_result",
    role: "Unit Test",
    title: `Test Result: ${card.skill_name || "skill"}`,
    inputSummary: `skill_v${card.skill_version ?? "?"}, bundle_v${card.bundle_version ?? "?"}`,
    outputSummary: `pass_all=${aggregate.pass_all_tests}, improved=${aggregate.n_improved ?? 0}, regressed=${aggregate.n_regressed ?? 0}`,
    primaryText: `Δaccuracy=${utility.delta_accuracy ?? "—"}, Δtokens=${utility.delta_tokens ?? "—"}, Δsteps=${utility.delta_steps ?? "—"}`,
    status: aggregate.pass_all_tests ? "success" : "danger",
    metrics: [
      ["Cases", aggregate.n_cases ?? 0],
      ["Pass", aggregate.pass_all_tests ?? "—"],
      ["ΔAcc", utility.delta_accuracy ?? "—"],
    ],
    detailPayload: { latest: card.detail || {}, card, allTests: allTests.map((item) => item.detail || item) },
    rawPayload: card,
  };
}

function summarizeRefineDecision(card, decisionCard) {
  const decision = card.decision || decisionCard?.detail?.raw_decision || decisionCard || {};
  return {
    id: "core_refine",
    kind: "refine",
    role: card.type === "role_refiner" ? "Refiner" : "Store Decision",
    title: `Refine: ${decision.action || decisionCard?.action || "decision"}`,
    inputSummary: compactMultiline(card.user_preview || "Test failures and current skill/bundle.", 180),
    outputSummary: compactMultiline(decision.reason || decisionCard?.action || "No decision reason recorded.", 240),
    primaryText: compactMultiline(decision.reason || "", 360),
    status: decision.action === "disable" ? "danger" : "warning",
    metrics: [
      ["Action", decision.action || decisionCard?.action || "—"],
      ["Version", decision.version_kind || "—"],
      ["Deps", (decision.pinned_dependencies || []).length],
    ],
    detailPayload: { refiner: card.detail || {}, decision: decisionCard?.detail || decisionCard || {}, parsedDecision: decision },
    rawPayload: { card, decisionCard },
  };
}

function summarizeStoreDelta(card) {
  return {
    id: "core_store",
    kind: "store",
    role: "Skill Store",
    title: "Repository Update",
    inputSummary: "Accepted skill/refine decision.",
    outputSummary: `${(card.new_skill_names || []).length} new skills, ${card.n_skills_after ?? 0} total`,
    primaryText: (card.new_skill_names || []).join(", ") || "No new skills recorded.",
    status: "success",
    metrics: [
      ["New", (card.new_skill_names || []).length],
      ["Total", card.n_skills_after ?? 0],
    ],
    detailPayload: card,
    rawPayload: card,
  };
}

function summarizeDebugEvents(cards) {
  const retrievals = cards.filter((card) => card.event?.event_type === "retrieval");
  const errors = cards.filter((card) => /error|exception/i.test(String(card.event?.event_type || "")));
  return {
    id: "core_debug",
    kind: "debug",
    role: "Debug Timeline",
    title: "Debug Events",
    inputSummary: `${cards.length} loop events`,
    outputSummary: `${retrievals.length} retrievals, ${errors.length} errors/exceptions`,
    primaryText: cards.map((card) => card.event?.event_type || card.title).join(" -> "),
    status: errors.length ? "warning" : "accent",
    metrics: [
      ["Events", cards.length],
      ["Retrievals", retrievals.length],
      ["Errors", errors.length],
    ],
    detailPayload: { cards, events: cards.map((card) => card.event || card.detail?.raw_event || card) },
    rawPayload: cards,
  };
}

function dataflowEdgeLabel(fromKind, toKind) {
  const labels = {
    "conversation:skill": "trace evidence",
    "skill:bundle": "skill contract",
    "bundle:test_result": "bundle cases",
    "test_result:refine": "test report",
    "refine:store": "decision",
    "store:debug": "debug timeline",
  };
  return labels[`${fromKind}:${toKind}`] || "data";
}

function artifactNodesFromDataflow(dataflow) {
  return (dataflow.nodes || []).filter((node) => !isRoleNode(node));
}

function renderCircleDataflow(page, dataflow) {
  const layout = layoutCircleDataflow(dataflow.nodes);
  const artifactCount = artifactNodesFromDataflow(dataflow).length;
  return `
    <div class="circle-dataflow-shell" data-flow-width="${layout.width}" data-flow-height="${layout.height}">
      <div class="circle-dataflow-toolbar">
        <span>Drag nodes; connectors update live. Roles and artifacts are separate nodes.</span>
        <button class="btn chip-btn" onclick="resetCoreNodePositions()">Reset Layout</button>
      </div>
      <svg class="circle-dataflow-svg" viewBox="0 0 ${layout.width} ${layout.height}" style="width:${layout.width}px; height:${layout.height}px;" aria-hidden="true">
        <defs>
          <marker id="core-arrow" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto">
            <path d="M0,0 L10,4 L0,8 Z"></path>
          </marker>
        </defs>
        <circle cx="${layout.cx}" cy="${layout.cy}" r="${layout.r}" class="circle-dataflow-ring"></circle>
        ${dataflow.edges.map((edge) => renderCoreEdge(edge, layout)).join("")}
      </svg>
      <div class="circle-dataflow-center">
        <div class="maintenance-stage-kicker">Dataflow</div>
        <div class="circle-center-title">${escapeHtml(page.title || page.page_id || "Round")}</div>
        <div class="circle-center-subtitle">${escapeHtml(`${artifactCount} artifacts | ${dataflow.nodes.length} nodes`)}</div>
      </div>
      ${layout.nodes.map((node) => renderCoreNodeThumb(node)).join("")}
    </div>
  `;
}

function layoutCircleDataflow(nodes) {
  const width = 1120;
  const height = 720;
  const cx = width / 2;
  const cy = height / 2;
  const roleR = 190;
  const artifactR = 300;
  const start = -Math.PI / 2;
  const saved = loadCoreNodePositions();
  const artifactNodes = nodes.filter((node) => !isRoleNode(node));
  const artifactIndex = new Map(artifactNodes.map((node, idx) => [node.id, idx]));
  const positioned = nodes.map((node, idx) => {
    const artifactId = isRoleNode(node) ? node.targetArtifactId : node.id;
    const logicalIdx = artifactIndex.has(artifactId) ? artifactIndex.get(artifactId) : idx;
    const angle = start + (logicalIdx / Math.max(artifactNodes.length, 1)) * Math.PI * 2;
    const radius = isRoleNode(node) ? roleR : artifactR;
    const savedPosition = saved[node.id];
    const defaultX = cx + Math.cos(angle) * radius;
    const defaultY = cy + Math.sin(angle) * radius;
    return {
      ...node,
      x: Number.isFinite(savedPosition?.x) ? clamp(savedPosition.x, 92, width - 92) : defaultX,
      y: Number.isFinite(savedPosition?.y) ? clamp(savedPosition.y, 84, height - 84) : defaultY,
    };
  });
  return { width, height, cx, cy, r: artifactR, nodes: positioned };
}

function renderCoreEdge(edge, layout) {
  const from = layout.nodes.find((item) => item.id === edge.from);
  const to = layout.nodes.find((item) => item.id === edge.to);
  if (!from || !to) return "";
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  return `
    <path class="core-flow-edge" data-edge-from="${escapeHtml(edge.from)}" data-edge-to="${escapeHtml(edge.to)}" d="${edgePathD(from.x, from.y, to.x, to.y, layout.cx, layout.cy)}" marker-end="url(#core-arrow)"></path>
    <text class="core-flow-label" data-edge-label-from="${escapeHtml(edge.from)}" data-edge-label-to="${escapeHtml(edge.to)}" x="${mx}" y="${my}">${escapeHtml(edge.label)}</text>
  `;
}

function edgePathD(fromX, fromY, toX, toY, cx, cy) {
  return `M ${fromX} ${fromY} Q ${cx} ${cy} ${toX} ${toY}`;
}

function renderCoreNodeThumb(node) {
  return `
    <button class="core-data-node ${isRoleNode(node) ? "core-role-node" : "core-artifact-node"} ${escapeHtml(`core-${node.status}`)}" data-core-node-id="${escapeHtml(node.id)}" data-node-kind="${escapeHtml(node.kind)}" style="left:${node.x}px; top:${node.y}px;" onpointerdown="startDragCoreDataNode(event, '${escapeJs(node.id)}')" onclick="handleCoreNodeClick(event, '${escapeJs(node.id)}')">
      <div class="core-node-top">
        <span class="maintenance-stage-kicker">${escapeHtml(isRoleNode(node) ? "Role" : "Artifact")}</span>
        <span class="core-node-kind">${escapeHtml(node.kind)}</span>
      </div>
      <div class="core-node-title">${escapeHtml(compactLabel(node.title, 44))}</div>
      <div class="core-node-io"><b>In</b> ${escapeHtml(compactLabel(node.inputSummary, 72))}</div>
      <div class="core-node-io"><b>Out</b> ${escapeHtml(compactLabel(node.outputSummary, 78))}</div>
      <div class="timeline-pill-row">${(node.metrics || []).slice(0, 3).map(([k, v]) => `<span class="timeline-pill">${escapeHtml(`${k}: ${v}`)}</span>`).join("")}</div>
    </button>
  `;
}

function handleCoreNodeClick(event, nodeId) {
  if (maintenanceState.suppressCoreNodeClick) {
    event.preventDefault();
    event.stopPropagation();
    return;
  }
  const page = resolveCurrentPage(maintenanceState.currentDetail);
  const dataflow = buildCoreDataflow(page);
  const node = dataflow.nodes.find((item) => item.id === nodeId);
  if (node && isRoleNode(node)) {
    openCoreNodeDetail(node.targetArtifactId || nodeId);
    return;
  }
  openCoreNodeDetail(nodeId);
}

function coreNodePositionStorageKey() {
  return `maintenanceCorePositions:${maintenanceState.currentId || "unknown"}:${maintenanceState.currentPageId || "unknown"}`;
}

function loadCoreNodePositions() {
  try {
    const parsed = JSON.parse(localStorage.getItem(coreNodePositionStorageKey()) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_err) {
    return {};
  }
}

function saveCoreNodePosition(nodeId, x, y) {
  const saved = loadCoreNodePositions();
  saved[nodeId] = { x: Math.round(x), y: Math.round(y) };
  localStorage.setItem(coreNodePositionStorageKey(), JSON.stringify(saved));
}

function resetCoreNodePositions() {
  localStorage.removeItem(coreNodePositionStorageKey());
  maintenanceState.draggingCoreNode = null;
  maintenanceState.suppressCoreNodeClick = false;
  if (maintenanceState.currentDetail) renderMaintenanceDetail(maintenanceState.currentDetail);
}

function startDragCoreDataNode(event, nodeId) {
  if (event.button !== undefined && event.button !== 0) return;
  const nodeEl = event.currentTarget;
  const shell = nodeEl.closest(".circle-dataflow-shell");
  if (!shell) return;
  const rect = shell.getBoundingClientRect();
  const currentX = parseFloat(nodeEl.style.left || "0");
  const currentY = parseFloat(nodeEl.style.top || "0");
  maintenanceState.draggingCoreNode = {
    nodeId,
    nodeEl,
    shell,
    startClientX: event.clientX,
    startClientY: event.clientY,
    startX: Number.isFinite(currentX) ? currentX : event.clientX - rect.left + shell.scrollLeft,
    startY: Number.isFinite(currentY) ? currentY : event.clientY - rect.top + shell.scrollTop,
    moved: false,
  };
  nodeEl.classList.add("core-node-dragging");
  nodeEl.setPointerCapture?.(event.pointerId);
}

function dragCoreDataNode(event) {
  const drag = maintenanceState.draggingCoreNode;
  if (!drag) return;
  const dx = event.clientX - drag.startClientX;
  const dy = event.clientY - drag.startClientY;
  if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
  const width = Number(drag.shell?.dataset?.flowWidth || 1120);
  const height = Number(drag.shell?.dataset?.flowHeight || 720);
  const nextX = clamp(drag.startX + dx, 92, width - 92);
  const nextY = clamp(drag.startY + dy, 84, height - 84);
  drag.nodeEl.style.left = `${nextX}px`;
  drag.nodeEl.style.top = `${nextY}px`;
  updateCoreFlowEdges(drag.shell);
  event.preventDefault();
}

function stopDragCoreDataNode() {
  const drag = maintenanceState.draggingCoreNode;
  if (!drag) return;
  drag.nodeEl.classList.remove("core-node-dragging");
  const x = parseFloat(drag.nodeEl.style.left || "0");
  const y = parseFloat(drag.nodeEl.style.top || "0");
  if (drag.moved && Number.isFinite(x) && Number.isFinite(y)) {
    saveCoreNodePosition(drag.nodeId, x, y);
    maintenanceState.suppressCoreNodeClick = true;
    setTimeout(() => {
      maintenanceState.suppressCoreNodeClick = false;
    }, 0);
  }
  maintenanceState.draggingCoreNode = null;
}

function updateCoreFlowEdges(shell) {
  if (!shell) return;
  const svg = shell.querySelector(".circle-dataflow-svg");
  if (!svg) return;
  const width = Number(shell.dataset.flowWidth || 1120);
  const height = Number(shell.dataset.flowHeight || 720);
  const cx = width / 2;
  const cy = height / 2;
  const positions = {};
  shell.querySelectorAll(".core-data-node[data-core-node-id]").forEach((nodeEl) => {
    const id = nodeEl.dataset.coreNodeId;
    positions[id] = {
      x: parseFloat(nodeEl.style.left || "0"),
      y: parseFloat(nodeEl.style.top || "0"),
    };
  });
  svg.querySelectorAll(".core-flow-edge").forEach((path) => {
    const from = positions[path.dataset.edgeFrom];
    const to = positions[path.dataset.edgeTo];
    if (!from || !to) return;
    path.setAttribute("d", edgePathD(from.x, from.y, to.x, to.y, cx, cy));
  });
  svg.querySelectorAll(".core-flow-label").forEach((label) => {
    const from = positions[label.dataset.edgeLabelFrom];
    const to = positions[label.dataset.edgeLabelTo];
    if (!from || !to) return;
    label.setAttribute("x", String((from.x + to.x) / 2));
    label.setAttribute("y", String((from.y + to.y) / 2));
  });
}

function openCoreNodeDetail(nodeId) {
  const page = resolveCurrentPage(maintenanceState.currentDetail);
  if (!page) return;
  const dataflow = buildCoreDataflow(page);
  const node = dataflow.nodes.find((item) => item.id === nodeId);
  if (node && isRoleNode(node)) {
    openCoreNodeDetail(node.targetArtifactId || nodeId);
    return;
  }
  if (nodeId === "core_conversation") {
    pushMaintenanceRoute({ view: "executor", experimentId: maintenanceState.currentId, pageId: page.page_id });
    return;
  }
  pushMaintenanceRoute({ view: "artifact", experimentId: maintenanceState.currentId, pageId: page.page_id, artifactId: nodeId });
}

function renderCompactNode(node, idx, page) {
  return `
    <button class="compact-flow-node ${escapeHtml(node.tone)}" onclick="selectCompactNode('${escapeJs(node.id)}')" ondblclick="openNodeDetail('${escapeJs(node.id)}')">
      <div class="maintenance-node-step">Step ${idx + 1}</div>
      <div class="compact-flow-icon">${escapeHtml(node.icon)}</div>
      <div class="compact-flow-title">${escapeHtml(compactLabel(node.title, 38))}</div>
      <div class="compact-flow-stats">${node.stats.slice(0, 3).map(([k, v]) => `<span>${escapeHtml(`${k}: ${v}`)}</span>`).join("")}</div>
    </button>
  `;
}

function selectCompactNode(nodeId) {
  maintenanceState.selectedBoardEntityId = nodeId;
  const detail = maintenanceState.currentDetail;
  const page = resolveCurrentPage(detail);
  const graph = buildBoardGraph(page);
  const node = graph.nodes.find((item) => item.id === nodeId);
  const artifacts = graph.artifacts.filter((item) => item.sourceNodeId === nodeId);
  const mount = document.getElementById("maintenance-mini-inspector");
  if (!node || !mount) return;
  mount.innerHTML = `
    <div class="mini-inspector-card">
      <div class="maintenance-stage-kicker">${escapeHtml(node.kind)}</div>
      <div class="maintenance-stage-title">${escapeHtml(node.title)}</div>
      <div class="maintenance-stage-subtitle">${escapeHtml(node.subtitle || node.preview || "")}</div>
      <div class="timeline-pill-row">${node.stats.map(([k, v]) => `<span class="timeline-pill">${escapeHtml(`${k}: ${v}`)}</span>`).join("")}</div>
      <button class="btn chip-btn" onclick="openNodeDetail('${escapeJs(node.id)}')">Open Detail</button>
      ${artifacts.length ? `
        <div>
          <div class="maintenance-section-title">Artifacts</div>
          <div class="structured-stack">
            ${artifacts.map((artifact) => `
              <button class="btn chip-btn" onclick="pushMaintenanceRoute({view:'artifact', experimentId:'${escapeJs(maintenanceState.currentId)}', pageId:'${escapeJs(page.page_id)}', artifactId:'${escapeJs(artifact.id)}'})">${escapeHtml(artifact.title)}</button>
            `).join("")}
          </div>
        </div>
      ` : ""}
    </div>
  `;
}

function openNodeDetail(nodeId) {
  const detail = maintenanceState.currentDetail;
  const page = resolveCurrentPage(detail);
  const graph = buildBoardGraph(page);
  const node = graph.nodes.find((item) => item.id === nodeId);
  if (!node || !page) return;
  if (node.kind === "run") {
    pushMaintenanceRoute({ view: "executor", experimentId: maintenanceState.currentId, pageId: page.page_id });
    return;
  }
  pushMaintenanceRoute({ view: "role", experimentId: maintenanceState.currentId, pageId: page.page_id, cardId: nodeId });
}

function renderExecutorRouteView(detail, page) {
  const runCard = (page?.flow_cards || []).find((card) => card.type === "run");
  if (!runCard) return renderViewChrome(detail, "Executor", "No executor card found.", "<div class='timeline-empty'>No executor trace on this round.</div>");
  const run = runCard.run || {};
  const traceDetail = run.detail || {};
  const timeline = buildExecutionTimeline(traceDetail, run);
  const selectedIndex = getSelectedTurnIndex(timeline);
  const selectedTurn = timeline[selectedIndex] || null;
  const actions = `
    <button class="btn chip-btn" onclick="pushMaintenanceRoute({view:'round', experimentId:'${escapeJs(maintenanceState.currentId)}', pageId:'${escapeJs(page?.page_id || '')}'})">Back To Round</button>
    <button class="btn chip-btn" onclick="pushMaintenanceRoute({view:'metrics', experimentId:'${escapeJs(maintenanceState.currentId)}'})">Metrics</button>
  `;
  return renderViewChrome(
    detail,
    runCard.title || "Executor",
    page?.title || "",
    renderExecutorWorkbench(runCard, timeline, selectedTurn, selectedIndex),
    actions
  );
}

function renderRoleRouteView(detail, page, cardId) {
  const dataflow = buildCoreDataflow(page);
  const mapped = coreNodeForRoleCard(cardId, page, dataflow);
  if (mapped) return renderViewChrome(detail, mapped.title, `${mapped.role} | ${mapped.kind}`, renderCoreNodeDetail(mapped));
  const graph = buildBoardGraph(page);
  const node = graph.nodes.find((item) => item.id === cardId) || graph.nodes.find((item) => !["run", "summary_board"].includes(item.kind));
  if (!node) return renderViewChrome(detail, "Role Detail", "No role card found.", "<div class='timeline-empty'>No role detail.</div>");
  return renderViewChrome(detail, node.title, node.subtitle || node.kind, renderRoleDetailWorkbench(node));
}

function coreNodeForRoleCard(cardId, page, dataflow) {
  const graph = buildBoardGraph(page);
  const node = graph.nodes.find((item) => item.id === cardId);
  const map = {
    role_extractor: "core_skill",
    role_bundle_builder: "core_bundle",
    maintenance_test: "core_test_result",
    role_refiner: "core_refine",
    refine_decision: "core_refine",
    skill_delta: "core_store",
  };
  const id = map[node?.kind];
  return id ? dataflow.nodes.find((item) => item.id === id) : null;
}

function renderArtifactRouteView(detail, page, artifactId) {
  const dataflow = buildCoreDataflow(page);
  const coreNode = dataflow.nodes.find((item) => item.id === artifactId);
  if (coreNode) {
    return renderViewChrome(detail, coreNode.title, `${coreNode.role} | ${coreNode.kind}`, renderCoreNodeDetail(coreNode));
  }
  const graph = buildBoardGraph(page);
  const artifact = graph.artifacts.find((item) => item.id === artifactId) || graph.artifacts[0];
  if (!artifact) return renderViewChrome(detail, "Artifact", "No artifact found.", "<div class='timeline-empty'>No artifact detail.</div>");
  return renderViewChrome(detail, artifact.title, artifact.subtitle || artifact.kind, renderArtifactInspector(artifact));
}

function renderCoreNodeDetail(node) {
  if (node.kind === "method_case") return renderMethodCaseDetail(node);
  if (node.kind === "skill") return renderSkillCoreDetail(node);
  if (node.kind === "bundle") return renderBundleCoreDetail(node);
  if (node.kind === "test_result") return renderTestResultCoreDetail(node);
  if (node.kind === "refine") return renderRefineCoreDetail(node);
  if (node.kind === "store") return renderStoreCoreDetail(node);
  if (node.kind === "debug") return renderDebugEventsCoreDetail(node);
  return `
    <div class="core-detail-layout">
      <section class="core-detail-panel">
        <div class="maintenance-stage-kicker">Input</div>
        <div class="core-detail-text">${escapeHtml(node.inputSummary || "No input summary.")}</div>
      </section>
      <section class="core-detail-panel">
        <div class="maintenance-stage-kicker">Output</div>
        <div class="core-detail-text">${escapeHtml(node.outputSummary || "No output summary.")}</div>
      </section>
      <section class="core-detail-panel wide-card">
        <div class="maintenance-stage-kicker">Primary Artifact Text</div>
        <div class="core-detail-text core-primary-text">${escapeHtml(node.primaryText || "No primary text recorded.")}</div>
      </section>
      <section class="core-detail-panel wide-card">
        <div class="maintenance-stage-kicker">Why It Matters</div>
        <div class="core-detail-text">${escapeHtml(coreNodeMeaning(node))}</div>
      </section>
      <section class="core-detail-panel wide-card">
        <div class="maintenance-stage-kicker">Structured Payload</div>
        ${renderStructuredPayload(node.detailPayload || {})}
        ${renderDetailBlock("Debug Raw", node.rawPayload || {}, { open: false })}
      </section>
    </div>
  `;
}

function renderSkillCoreDetail(node) {
  const payload = node.detailPayload || {};
  const artifact = payload.artifact || payload.preview || {};
  const preview = payload.preview || {};
  const metadata = artifact.metadata || {};
  const iface = artifact.interface || {};
  const deps = artifact.dependencies || preview.dependencies || [];
  const sourceProblems = metadata.source_task_ids || artifact.source_problems || [];
  const implementation = artifact.body || artifact.implementation || artifact.content || node.primaryText || "";
  return `
    <div class="skill-explorer-like-detail">
      <section class="maintenance-stage-card board-accent">
        <div class="maintenance-stage-head">
          <div>
            <div class="maintenance-stage-kicker">Skill Explorer View</div>
            <div class="maintenance-stage-title">${escapeHtml(artifact.name || preview.name || node.title || "Skill")}</div>
            <div class="maintenance-stage-subtitle">${escapeHtml(artifact.description || preview.description || "No description recorded.")}</div>
          </div>
          <span class="timeline-pill">${escapeHtml(artifact.version_kind || preview.version_kind || "version unknown")}</span>
        </div>
        <div class="maintenance-metric-grid">
          ${metricMini("Retrieved", payload.retrieved_count ?? 0)}
          ${metricMini("Kind", artifact.kind || preview.kind || "—")}
          ${metricMini("Dependencies", deps.length)}
          ${metricMini("Intent Keywords", (metadata.intent_keywords || []).length)}
        </div>
        <div class="maintenance-two-col">
          ${infoPanel("Contract", [
            ["Usage", iface.usage || "—"],
            ["Summary", iface.summary || "—"],
            ["Compatibility", iface.compatibility_notes || "—"],
          ])}
          ${infoPanel("Evidence", [
            ["Source Tasks", sourceProblems.join(", ") || "—"],
            ["Allowed Tools", (metadata.allowed_tools || []).join(", ") || "—"],
            ["Domains", (metadata.domains || []).join(", ") || "—"],
          ])}
        </div>
        <div>
          <div class="maintenance-section-title">Dependencies</div>
          ${renderChipList(deps)}
        </div>
        <div class="skill-code-section">
          <div class="maintenance-section-title">Implementation / Body</div>
          <pre class="maintenance-code-block skill-body-block">${escapeHtml(implementation || "No implementation/body recorded.")}</pre>
        </div>
      </section>
      <section class="maintenance-stage-card board-neutral">
        <div class="maintenance-stage-title">Interface</div>
        ${renderInterfaceContract(iface)}
      </section>
      <section class="maintenance-stage-card board-neutral">
        <div class="maintenance-stage-title">Role Input / Output</div>
        <div class="single-json-column">
          ${renderTextualPayloadCard("Extractor Input", payload.input)}
          ${renderTextualPayloadCard("Extractor Output", payload.output)}
        </div>
        ${renderDebugRaw("Debug Raw Skill Payload", node.rawPayload || payload)}
      </section>
    </div>
  `;
}

function renderInterfaceContract(iface) {
  if (!iface || !Object.keys(iface).length) return "<div class='maintenance-doc-empty'>No interface object recorded.</div>";
  return `
    <div class="interface-contract-grid">
      ${renderTextualPayloadCard("Input Contract", iface.input_contract || {})}
      ${renderTextualPayloadCard("Invocation Contract", iface.invocation_contract || {})}
      ${renderTextualPayloadCard("Output Contract", iface.output_contract || {})}
      ${renderTextualPayloadCard("Full Interface Notes", {
        usage: iface.usage,
        summary: iface.summary,
        compatibility_notes: iface.compatibility_notes,
      })}
    </div>
  `;
}

function renderBundleCoreDetail(node) {
  const payload = node.detailPayload || {};
  const cases = payload.cases || {};
  const positive = cases.positive || [];
  const negative = cases.negative || [];
  const integration = cases.integration || [];
  return `
    <div class="artifact-detail-stack">
      <section class="maintenance-stage-card board-accent">
        <div class="maintenance-stage-head">
          <div>
            <div class="maintenance-stage-kicker">Bundle</div>
            <div class="maintenance-stage-title">${escapeHtml(node.title || "Maintenance Bundle")}</div>
            <div class="maintenance-stage-subtitle">Long-lived unit-like cases bound to the skill.</div>
          </div>
        </div>
        <div class="maintenance-metric-grid">
          ${metricMini("Positive", positive.length)}
          ${metricMini("Negative", negative.length)}
          ${metricMini("Integration", integration.length)}
          ${metricMini("Total", positive.length + negative.length + integration.length)}
        </div>
        <div class="maintenance-note-box">${escapeHtml(payload.notes || "No maintenance notes recorded.")}</div>
      </section>
      ${renderBundleCaseSection("Positive Cases", positive, "positive")}
      ${renderBundleCaseSection("Negative Cases", negative, "negative")}
      ${renderBundleCaseSection("Integration-Derived Cases", integration, "integration")}
      <section class="maintenance-stage-card board-neutral">
        <div class="maintenance-stage-title">Builder Input / Output</div>
        <div class="single-json-column">
          ${renderTextualPayloadCard("Bundle Builder Input", payload.input)}
          ${renderTextualPayloadCard("Bundle Builder Output", payload.output)}
        </div>
        ${renderDebugRaw("Debug Raw Bundle Payload", node.rawPayload || payload)}
      </section>
    </div>
  `;
}

function renderBundleCaseSection(title, cases, polarity) {
  return `
    <section class="maintenance-stage-card ${polarity === "negative" ? "board-warning" : "board-neutral"}">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">${escapeHtml(polarity)}</div>
          <div class="maintenance-stage-title">${escapeHtml(title)}</div>
        </div>
        <span class="timeline-pill">${escapeHtml(`${cases.length} cases`)}</span>
      </div>
      <div class="bundle-case-list">
        ${cases.length ? cases.map((item, idx) => renderBundleCaseCard(item, idx)).join("") : "<div class='maintenance-doc-empty'>No cases in this bucket.</div>"}
      </div>
    </section>
  `;
}

function renderBundleCaseCard(item, idx) {
  const context = item.context || {};
  const fragment = context.task_fragment || {};
  const question = extractQuestionText(item) || item.prompt || "No question text recorded.";
  const expected = item.expected || {};
  const inputArtifacts = fragment.input_artifacts || item.input_artifacts || {};
  const focusClass = maintenanceState.route?.focusId === item.case_id ? "focused-case-card" : "";
  return `
    <article class="bundle-case-card ${focusClass}" id="${escapeHtml(domIdForCase(item.case_id || `case-${idx}`))}">
      <div class="bundle-case-head">
        <div>
          <div class="maintenance-stage-kicker">Case ${idx + 1}</div>
          <div class="bundle-case-title">${escapeHtml(item.case_id || "case")}</div>
        </div>
        <div class="timeline-pill-row">
          <span class="timeline-pill">${escapeHtml(item.polarity || "unknown")}</span>
          <button class="btn chip-btn" onclick="jumpToBundleCase('${escapeJs(item.case_id || "")}')">Permalink</button>
        </div>
      </div>
      <div class="bundle-question-box">${escapeHtml(question)}</div>
      <div class="case-section-grid">
        ${infoPanel("Scope", [
          ["Source Task", context.source_task_id || "—"],
          ["Focus Tools", (context.focus_tools || []).join(", ") || "—"],
          ["Focus Turns", (context.focus_turns || []).join(", ") || "—"],
          ["Source", item.source || "—"],
        ])}
        ${infoPanel("Contrast", [
          ["With Skill", item.contrast_protocol?.with_skill ?? "—"],
          ["Without Skill", item.contrast_protocol?.without_skill ?? "—"],
          ["Tags", (item.tags || []).join(", ") || "—"],
        ])}
      </div>
      <div class="case-section-grid">
        ${renderToolCallList("Expected Tool Calls", expected.tool_calls || [])}
        ${renderForbiddenCallList("Forbidden Calls", expected.forbidden_calls || [])}
      </div>
      <details class="maintenance-raw-details">
        <summary>Input Artifacts / Fixtures</summary>
        ${renderInputArtifacts(inputArtifacts)}
      </details>
      ${fragment.expected ? `
        <details class="maintenance-raw-details">
          <summary>Task Fragment Expected Calls</summary>
          <div class="chip-grid">${(fragment.expected || []).map((value) => `<span class="dep-chip">${escapeHtml(String(value))}</span>`).join("")}</div>
        </details>
      ` : ""}
      ${renderDebugRaw("Raw Case", item)}
    </article>
  `;
}

function renderTestResultCoreDetail(node) {
  const payload = node.detailPayload || {};
  const card = payload.card || {};
  const aggregate = card.aggregate || payload.latest?.aggregate || {};
  const utility = aggregate.unit_utility_report || {};
  const runs = card.unit_case_runs || payload.latest?.unit_case_runs || [];
  const failures = card.integration_failures || payload.latest?.integration_failures || [];
  return `
    <div class="artifact-detail-stack">
      <section class="maintenance-stage-card ${aggregate.pass_all_tests ? "board-success" : "board-danger"}">
        <div class="maintenance-stage-head">
          <div>
            <div class="maintenance-stage-kicker">Unit Utility Report</div>
            <div class="maintenance-stage-title">${escapeHtml(card.skill_name || node.title || "Test Result")}</div>
            <div class="maintenance-stage-subtitle">${escapeHtml(`skill_v${card.skill_version ?? "?"} | bundle_v${card.bundle_version ?? "?"}`)}</div>
          </div>
          <span class="timeline-pill ${aggregate.pass_all_tests ? "success-pill" : "danger-pill"}">${escapeHtml(`pass_all=${aggregate.pass_all_tests}`)}</span>
        </div>
        <div class="maintenance-metric-grid">
          ${metricMini("cases", aggregate.n_cases ?? runs.length)}
          ${metricMini("comparable", aggregate.n_comparable_cases ?? "—")}
          ${metricMini("improved", aggregate.n_improved ?? "—")}
          ${metricMini("regressed", aggregate.n_regressed ?? "—")}
          ${metricMini("pass_all", aggregate.pass_all_tests ?? "—")}
          ${metricMini("delta_acc", utility.delta_accuracy ?? "—")}
          ${metricMini("delta_tokens", utility.delta_tokens ?? "—")}
          ${metricMini("delta_steps", utility.delta_steps ?? "—")}
        </div>
      </section>
      <section class="maintenance-stage-card board-neutral">
        <div class="maintenance-stage-title">Case Runs</div>
        <div class="test-case-run-list">
          ${runs.length ? groupCaseRuns(runs).map(renderCaseRunGroup).join("") : "<div class='maintenance-doc-empty'>No unit case runs recorded.</div>"}
        </div>
      </section>
      <section class="maintenance-stage-card ${failures.length ? "board-warning" : "board-success"}">
        <div class="maintenance-stage-head">
          <div>
            <div class="maintenance-stage-title">Integration Failures</div>
            <div class="maintenance-stage-subtitle">Failures observed while testing the skill with current bundle evidence.</div>
          </div>
          <span class="timeline-pill">${escapeHtml(String(failures.length))}</span>
        </div>
        <div class="test-failure-list">
          ${failures.length ? failures.map(renderIntegrationFailureCard).join("") : "<div class='maintenance-doc-empty'>No integration failures recorded.</div>"}
        </div>
      </section>
      ${renderDebugRaw("Debug Raw Test Result", node.rawPayload || payload)}
    </div>
  `;
}

function jumpToBundleCase(caseId) {
  const page = resolveCurrentPage(maintenanceState.currentDetail);
  if (!page) return;
  pushMaintenanceRoute({
    view: "artifact",
    experimentId: maintenanceState.currentId,
    pageId: page.page_id,
    artifactId: "core_bundle",
    focusId: caseId || "",
  });
  setTimeout(() => {
    const el = document.getElementById(domIdForCase(caseId));
    el?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, 50);
}

function renderRefineCoreDetail(node) {
  const payload = node.detailPayload || {};
  const parsed = payload.parsedDecision || {};
  const refiner = payload.refiner || {};
  const refinerInput = refiner.input || {};
  const refinerOutput = refiner.output || {};
  return `
    <div class="artifact-detail-stack">
      <section class="maintenance-stage-card ${parsed.action === "disable" ? "board-danger" : "board-warning"}">
        <div class="maintenance-stage-head">
          <div>
            <div class="maintenance-stage-kicker">Semantic Refiner</div>
            <div class="maintenance-stage-title">${escapeHtml(parsed.action || "decision")}</div>
            <div class="maintenance-stage-subtitle">Decision produced from test results, integration failures, and current artifact contract.</div>
          </div>
          <span class="timeline-pill">${escapeHtml(parsed.version_kind || "—")}</span>
        </div>
        <div class="maintenance-metric-grid">
          ${metricMini("Action", parsed.action || "—")}
          ${metricMini("Version", parsed.version_kind || "—")}
          ${metricMini("Pinned Deps", (parsed.pinned_dependencies || []).length)}
          ${metricMini("Migration", parsed.migration_reason ? "yes" : "no")}
        </div>
        <div class="refine-reason-box">${escapeHtml(parsed.reason || "No reason recorded.")}</div>
      </section>
      <section class="maintenance-stage-card board-neutral">
        <div class="maintenance-stage-title">Refiner Input</div>
        <div class="single-json-column">
          ${renderTextualPayloadCard("System Prompt", refinerInput.system || refiner.system || "")}
          ${renderTextualPayloadCard("User Prompt", refinerInput.user || refiner.user || "")}
        </div>
        ${renderTextualPayloadCard("Metadata", refinerInput.metadata || {})}
      </section>
      <section class="maintenance-stage-card board-neutral">
        <div class="maintenance-stage-title">Refiner Output</div>
        <div class="single-json-column">
          ${renderTextualPayloadCard("Parsed Decision", parsed)}
          ${renderTextualPayloadCard("Raw Output", refinerOutput)}
        </div>
      </section>
      ${renderDebugRaw("Debug Raw Refiner Payload", node.rawPayload || payload)}
    </div>
  `;
}

function renderStoreCoreDetail(node) {
  const payload = node.detailPayload || {};
  return `
    <div class="artifact-detail-stack">
      <section class="maintenance-stage-card board-success">
        <div class="maintenance-stage-head">
          <div>
            <div class="maintenance-stage-kicker">Skill Store</div>
            <div class="maintenance-stage-title">Repository Update</div>
            <div class="maintenance-stage-subtitle">${escapeHtml(payload.subtitle || node.outputSummary || "")}</div>
          </div>
        </div>
        <div class="maintenance-metric-grid">
          ${metricMini("New Skills", (payload.new_skill_names || []).length)}
          ${metricMini("Skills After", payload.n_skills_after ?? 0)}
        </div>
        <div class="maintenance-two-col">
          <div>
            <div class="maintenance-section-title">New Skills</div>
            ${renderChipList(payload.new_skill_names || [])}
          </div>
          <div>
            <div class="maintenance-section-title">Store After This Round</div>
            ${renderChipList(payload.skill_names_after || [])}
          </div>
        </div>
      </section>
      ${renderDebugRaw("Debug Raw Store Payload", payload)}
    </div>
  `;
}

function renderDebugEventsCoreDetail(node) {
  const events = node.detailPayload?.events || [];
  return `
    <div class="artifact-detail-stack">
      <section class="maintenance-stage-card board-accent">
        <div class="maintenance-stage-head">
          <div>
            <div class="maintenance-stage-kicker">Debug Timeline</div>
            <div class="maintenance-stage-title">Full Loop Debug Events</div>
            <div class="maintenance-stage-subtitle">Retrieval, executor, test, refine, and store events captured during this loop.</div>
          </div>
          <span class="timeline-pill">${escapeHtml(`${events.length} events`)}</span>
        </div>
        <div class="maintenance-metric-grid">
          ${metricMini("Events", events.length)}
          ${metricMini("Retrievals", events.filter((event) => event.event_type === "retrieval").length)}
          ${metricMini("Executor Steps", events.filter((event) => event.event_type === "executor_step").length)}
          ${metricMini("Tool Calls", events.filter((event) => event.event_type === "tool_call").length)}
        </div>
      </section>
      <section class="maintenance-stage-card board-neutral">
        <div class="debug-event-list">
          ${events.length ? events.map(renderDebugEventCard).join("") : "<div class='maintenance-doc-empty'>No debug events recorded.</div>"}
        </div>
      </section>
    </div>
  `;
}

function renderDebugEventCard(event) {
  if (event.event_type === "retrieval") return renderRetrievalDebugEvent(event);
  return `
    <article class="debug-event-card">
      <div class="bundle-case-head">
        <div>
          <div class="maintenance-stage-kicker">${escapeHtml(event.event_id || "event")}</div>
          <div class="bundle-case-title">${escapeHtml(String(event.event_type || "debug_event"))}</div>
        </div>
        <span class="timeline-pill">${escapeHtml(event.phase || "")}</span>
      </div>
      <div class="single-json-column">
        ${renderTextualPayloadCard("Input", event.input || {})}
        ${renderTextualPayloadCard("Output", event.output || {})}
      </div>
      ${Object.keys(event.metrics || {}).length ? renderTextualPayloadCard("Metrics", event.metrics) : ""}
      ${renderDebugRaw("Raw Debug Event", event)}
    </article>
  `;
}

function renderRetrievalDebugEvent(event) {
  const output = event.output || {};
  const candidates = output.candidates || [];
  return `
    <article class="debug-event-card retrieval-debug-card">
      <div class="bundle-case-head">
        <div>
          <div class="maintenance-stage-kicker">${escapeHtml(event.event_id || "retrieval")}</div>
          <div class="bundle-case-title">Retrieval: ${escapeHtml(event.trigger || event.phase || "")}</div>
        </div>
        <span class="timeline-pill">${escapeHtml(`${candidates.length} candidates`)}</span>
      </div>
      <div class="bundle-question-box">${escapeHtml(event.input?.query || output.query || "")}</div>
      <div class="maintenance-metric-grid compact-metrics">
        ${metricMini("Store Total", output.store_summary?.n_total ?? "—")}
        ${metricMini("Active", output.store_summary?.n_active ?? "—")}
        ${metricMini("Stale", output.store_summary?.n_stale ?? "—")}
        ${metricMini("Disabled", output.store_summary?.n_disabled ?? "—")}
        ${metricMini("Selected", (output.selected || []).length)}
      </div>
      ${renderRetrievalCandidateTable(candidates)}
      ${renderTextualPayloadCard("Selected", output.selected || [])}
      ${renderDebugRaw("Raw Retrieval Event", event)}
    </article>
  `;
}

function renderRetrievalCandidateTable(candidates) {
  if (!candidates.length) return "<div class='maintenance-doc-empty'>No candidates.</div>";
  return `
    <div class="retrieval-candidate-table">
      <div class="retrieval-candidate-row retrieval-head">
        <span>Name</span><span>Score</span><span>Rank</span><span>Selected</span><span>Filter</span>
      </div>
      ${candidates.map((item) => `
        <div class="retrieval-candidate-row ${item.selected ? "candidate-selected" : ""}">
          <span>${escapeHtml(item.name || "")}</span>
          <span>${escapeHtml(String(item.score ?? "—"))}</span>
          <span>${escapeHtml(String(item.rank ?? "—"))}</span>
          <span>${escapeHtml(String(item.selected ?? false))}</span>
          <span>${escapeHtml(item.filter_reason || (item.predicate_passed ? "passed" : "filtered"))}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function coreNodeMeaning(node) {
  const docs = {
    conversation: "这是维护链路的证据来源：后续 skill、bundle 和 refine 都应该能追溯到这里的对话与工具调用。",
    skill: "这是 extractor 从 trace 中提取出的可复用维护资产，后续 bundle 和测试围绕它构造。",
    bundle: "这是该 skill 的长期测试资产，用来验证 skill 是否真的有独立价值。",
    test_result: "这是 with/without skill 的局部 utility 证据，用来判断 skill 是否应该保留或 refine。",
    refine: "这是根据测试结果做出的语义维护决策，影响 skill 版本和后续 store 状态。",
    store: "这是 repository 状态变化，说明本轮最终沉淀了哪些 skill。",
  };
  return docs[node.kind] || "该节点是维护链路中的中间数据产物。";
}

function renderMetricsView(detail) {
  const roundMetrics = (detail.pages || []).map((page) => `
    <section class="metrics-round-block">
      <h3>${escapeHtml(page.title || page.page_id)}</h3>
      <div class="maintenance-hero-grid">${(page.summary_metrics || []).map((card) => `
        <div class="timeline-summary-card ${escapeHtml(card.tone || "neutral")}" title="${escapeHtml(metricHelp(card.label || ""))}">
          <div class="timeline-summary-label">${escapeHtml(card.label || "")}</div>
          <div class="timeline-summary-value">${escapeHtml(String(card.value ?? ""))}</div>
        </div>
      `).join("")}</div>
    </section>
  `).join("");
  return renderViewChrome(detail, "Metrics", "Experiment and round metrics.", `
    <section class="metrics-round-block">
      <h3>Experiment</h3>
      <div class="maintenance-hero-grid">${(detail.overview_metrics || []).map((card) => `
        <div class="timeline-summary-card ${escapeHtml(card.tone || "neutral")}" title="${escapeHtml(metricHelp(card.label || ""))}">
          <div class="timeline-summary-label">${escapeHtml(card.label || "")}</div>
          <div class="timeline-summary-value">${escapeHtml(String(card.value ?? ""))}</div>
        </div>
      `).join("")}</div>
    </section>
    ${roundMetrics}
  `);
}

function renderDocsView(detail) {
  const docs = detail.docs || [];
  const activeDoc = docs.find((item) => item.id === maintenanceState.currentDocId) || docs[0];
  return renderViewChrome(detail, "Documentation", "Experiment documentation and rendered diagrams.", `
    <div class="docs-route-layout">
      <div class="inline-chip-row">${docs.map((doc) => `<button class="btn chip-btn ${doc.id === activeDoc?.id ? "active-toggle" : ""}" onclick="maintenanceState.currentDocId='${escapeJs(doc.id)}'; renderMaintenanceDetail(maintenanceState.currentDetail)">${escapeHtml(doc.title || doc.id)}</button>`).join("")}</div>
      <div class="docs-route-body">${activeDoc ? renderMaintenanceDoc(activeDoc) : "<div class='timeline-empty'>No docs attached.</div>"}</div>
    </div>
  `);
}

function renderMaintenanceDocs(docs) {
  const normalizedDocs = (docs && docs.length)
    ? docs
    : (maintenanceState.currentDetail?.readme_text ? [{
        id: "readme_text",
        title: "README",
        kind: "experiment",
        path: "",
        text: maintenanceState.currentDetail.readme_text,
      }] : []);
  const nav = document.getElementById("maintenance-doc-nav");
  const mount = document.getElementById("maintenance-doc-panel");
  if (!mount || !nav) return;
  if (!normalizedDocs.length) {
    nav.innerHTML = "";
    mount.innerHTML = "<div class='maintenance-doc-empty'>No documentation attached to this experiment.</div>";
    return;
  }
  const activeDoc = normalizedDocs.find((item) => item.id === maintenanceState.currentDocId) || normalizedDocs[0];
  maintenanceState.currentDocId = activeDoc.id;
  nav.innerHTML = normalizedDocs.map((item) => `
    <button class="btn chip-btn ${item.id === activeDoc.id ? "active-toggle" : ""}" onclick="selectMaintenanceDoc('${escapeJs(item.id)}')">
      ${escapeHtml(item.title || item.id)}
    </button>
  `).join("");
  mount.innerHTML = renderMaintenanceDoc(activeDoc);
}

function renderMaintenanceDoc(doc) {
  const markdownText = doc?.text || "";
  const blocks = parseDocBlocks(markdownText);
  return `
    <section class="maintenance-doc-sheet">
      <div class="maintenance-doc-meta">
        <div>
          <div class="maintenance-stage-kicker">Document</div>
          <div class="maintenance-stage-title">${escapeHtml(doc?.title || "Documentation")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(doc?.path || "")}</div>
        </div>
        <div class="timeline-pill-row">
          <span class="timeline-pill">${escapeHtml(doc?.kind || "reference")}</span>
          <span class="timeline-pill">${escapeHtml(`blocks=${blocks.length}`)}</span>
        </div>
      </div>
      ${blocks.map(renderDocBlock).join("")}
    </section>
  `;
}

function renderOverviewCards(cards) {
  document.getElementById("maintenance-summary-cards").innerHTML = cards.map((card) => `
    <div class="timeline-summary-card ${escapeHtml(card.tone || "neutral")}" title="${escapeHtml(metricHelp(card.label || ""))}">
      <div class="timeline-summary-label">${escapeHtml(card.label || "")}</div>
      <div class="timeline-summary-value">${escapeHtml(String(card.value ?? ""))}</div>
    </div>
  `).join("");
}

function renderPageNav(pages) {
  const mount = document.getElementById("maintenance-page-nav");
  mount.innerHTML = pages.map((page) => `
    <button class="btn chip-btn ${page.page_id === maintenanceState.currentPageId ? "active-toggle" : ""}" onclick="selectMaintenancePage('${escapeJs(page.page_id)}')">
      ${escapeHtml(page.label || page.title || page.page_id)}
    </button>
  `).join("");
}

function renderCurrentPage(page) {
  const pages = maintenanceState.currentDetail?.pages || [];
  const currentIndex = Math.max(0, pages.findIndex((item) => item.page_id === page?.page_id));
  document.getElementById("maintenance-page-title").innerHTML = `${escapeHtml(page?.title || "")} ${paneToggleButton("inspector", "Inspector")}`;
  document.getElementById("maintenance-page-tone").textContent = page?.status_tone || "";
  document.getElementById("maintenance-page-progress").textContent = page ? `${currentIndex + 1} / ${pages.length}` : "";
  const prevBtn = document.getElementById("maintenance-prev-page");
  const nextBtn = document.getElementById("maintenance-next-page");
  if (prevBtn) prevBtn.disabled = currentIndex <= 0;
  if (nextBtn) nextBtn.disabled = currentIndex >= pages.length - 1;
  document.getElementById("maintenance-page-metrics").innerHTML = (page?.summary_metrics || []).map((card) => `
    <div class="timeline-summary-card ${escapeHtml(card.tone || "neutral")}" title="${escapeHtml(metricHelp(card.label || ""))}">
      <div class="timeline-summary-label">${escapeHtml(card.label || "")}</div>
      <div class="timeline-summary-value">${escapeHtml(String(card.value ?? ""))}</div>
    </div>
  `).join("");
  document.getElementById("maintenance-board").innerHTML = page
    ? renderBoardPage(page, currentIndex, pages.length)
    : "<div class='timeline-empty'>No page selected.</div>";
}

function renderBoardPage(page, currentIndex, totalPages) {
  const graph = buildBoardGraph(page);
  const entities = [...graph.nodes, ...graph.artifacts];
  const selected = entities.find((item) => item.id === maintenanceState.selectedBoardEntityId) || graph.nodes[0] || graph.artifacts[0] || null;
  if (!maintenanceState.selectedBoardEntityId && selected) {
    maintenanceState.selectedBoardEntityId = selected.id;
  }
  const map = layoutBoardGraph(graph);
  return `
    <div class="maintenance-page-workbench">
      <section class="maintenance-round-rail" aria-label="round flow map">
        <div class="maintenance-round-header">
          <div>
            <div class="maintenance-stage-kicker">Flow Map</div>
            <div class="maintenance-stage-title">${escapeHtml(page.title || `Round ${currentIndex + 1}`)}</div>
            <div class="maintenance-stage-subtitle">${escapeHtml(`Round ${currentIndex + 1} of ${totalPages} | nodes=${graph.nodes.length} | artifacts=${graph.artifacts.length}`)}</div>
          </div>
          <div class="timeline-pill-row maintenance-map-legend">
            <span class="timeline-pill">role</span>
            <span class="timeline-pill">artifact</span>
            <span class="timeline-pill">click for detail</span>
          </div>
        </div>
        <div class="maintenance-map-scroll">
          <div class="maintenance-map-canvas" style="width:${map.width}px; height:${map.height}px;">
            <svg class="maintenance-map-links" width="${map.width}" height="${map.height}" viewBox="0 0 ${map.width} ${map.height}" aria-hidden="true">
              <defs>
                <marker id="maintenance-arrowhead" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto">
                  <path d="M0,0 L10,4 L0,8 Z"></path>
                </marker>
              </defs>
              ${map.links.map(renderBoardLink).join("")}
            </svg>
            <div class="maintenance-map-lane maintenance-map-role-lane" style="left:18px; top:${map.roleLaneY - 26}px; width:${map.width - 36}px;">Roles</div>
            <div class="maintenance-map-lane maintenance-map-artifact-lane" style="left:18px; top:${map.artifactLaneY - 26}px; width:${map.width - 36}px;">Artifacts</div>
            ${map.nodes.map((node) => renderBoardNode(node, map)).join("")}
            ${map.artifacts.length ? map.artifacts.map((artifact) => renderBoardArtifact(artifact, map)).join("") : renderEmptyArtifactNotice(map)}
          </div>
        </div>
      </section>
      <div class="maintenance-splitter" title="Drag to resize inspector" onpointerdown="startMaintenanceInspectorResize(event)" ondblclick="resetMaintenanceInspectorWidth(event)"></div>
      <aside class="maintenance-inspector">
        ${selected ? renderBoardInspector(selected) : "<div class='maintenance-doc-empty'>No node selected.</div>"}
      </aside>
    </div>
  `;
}

function buildBoardGraph(page) {
  const cards = page?.flow_cards || [];
  const nodes = cards.map((card, idx) => boardNodeFromCard(card, idx));
  const edges = nodes.slice(1).map((node, idx) => ({
    from: nodes[idx].id,
    to: node.id,
    label: edgeLabelForNode(node),
  }));
  const artifacts = cards.flatMap((card, idx) => boardArtifactsFromCard(card, idx));
  const artifactEdges = artifacts.map((artifact) => ({
    from: artifact.sourceNodeId,
    to: artifact.id,
    label: artifact.kind || "artifact",
    artifact: true,
  }));
  return { nodes, edges: [...edges, ...artifactEdges], artifacts };
}

function layoutBoardGraph(graph) {
  const nodeW = 176;
  const nodeH = 132;
  const artifactW = 164;
  const artifactH = 106;
  const colGap = graph.nodes.length > 7 ? 28 : 42;
  const marginX = 28;
  const roleY = 46;
  const artifactY = 220;
  const width = Math.max(760, marginX * 2 + graph.nodes.length * nodeW + Math.max(0, graph.nodes.length - 1) * colGap);
  const height = graph.artifacts.length ? 350 : 240;
  const nodes = graph.nodes.map((node, idx) => ({
    ...node,
    x: marginX + idx * (nodeW + colGap),
    y: roleY,
    w: nodeW,
    h: nodeH,
  }));
  const nodeById = new Map(nodes.map((item) => [item.id, item]));
  const artifacts = graph.artifacts.map((artifact, idx) => {
    const source = nodeById.get(artifact.sourceNodeId);
    const x = source ? source.x + Math.max(0, (nodeW - artifactW) / 2) : marginX + idx * (artifactW + 24);
    const y = artifactY + (idx % 2) * 18;
    return { ...artifact, x, y, w: artifactW, h: artifactH };
  });
  const artifactById = new Map(artifacts.map((item) => [item.id, item]));
  const links = graph.edges.map((edge) => {
    const from = nodeById.get(edge.from) || artifactById.get(edge.from);
    const to = nodeById.get(edge.to) || artifactById.get(edge.to);
    if (!from || !to) return null;
    if (edge.artifact) {
      return {
        ...edge,
        x1: from.x + from.w / 2,
        y1: from.y + from.h,
        x2: to.x + to.w / 2,
        y2: to.y,
        kind: "artifact",
      };
    }
    return {
      ...edge,
      x1: from.x + from.w,
      y1: from.y + from.h / 2,
      x2: to.x,
      y2: to.y + to.h / 2,
      kind: "flow",
    };
  }).filter(Boolean);
  return {
    width,
    height,
    roleLaneY: roleY,
    artifactLaneY: artifactY,
    nodes,
    artifacts,
    links,
  };
}

function boardNodeFromCard(card, idx) {
  const base = {
    id: `node_${idx}_${card.type || "card"}`,
    card,
    cardIndex: idx,
    kind: card.type || "card",
    title: card.title || card.type || `card_${idx}`,
    subtitle: card.subtitle || "",
    tone: boardToneClass(card.tone),
    icon: "•",
    stats: [],
    preview: "",
    inspector: () => renderFallbackInspector(card),
  };
  if (card.type === "run") {
    const run = card.run || {};
    return {
      ...base,
      icon: "▶",
      stats: [
        ["Official", run.official_valid ?? "—"],
        ["Call F1", run.call_f1 ?? "—"],
        ["Tokens", run.total_tokens ?? "—"],
        ["Steps", run.n_model_steps ?? "—"],
      ],
      preview: `Retrieved ${(run.retrieved_skills || []).length} skills; ${(run.call_errors || []).length} call errors.`,
      inspector: () => renderRunCard(card),
    };
  }
  if (card.type === "role_extractor") {
    return {
      ...base,
      icon: "✦",
      stats: [
        ["Artifacts", card.artifact_count ?? 0],
        ["Version Kind", card.artifact_preview?.version_kind || "—"],
      ],
      preview: card.artifact_preview?.name || "No extracted artifact",
      inspector: () => renderExtractorCard(card),
    };
  }
  if (card.type === "role_bundle_builder") {
    return {
      ...base,
      icon: "▣",
      stats: [
        ["Positive", card.counts?.positive ?? 0],
        ["Negative", card.counts?.negative ?? 0],
        ["Integration", card.counts?.integration ?? 0],
      ],
      preview: card.maintenance_notes || "Bundle maintenance notes unavailable",
      inspector: () => renderBundleCard(card),
    };
  }
  if (card.type === "role_refiner") {
    return {
      ...base,
      icon: "⟳",
      stats: [
        ["Action", card.decision?.action || "—"],
        ["Version Kind", card.decision?.version_kind || "—"],
      ],
      preview: card.decision?.reason || "No refine reason recorded",
      inspector: () => renderRoleRefinerCard(card),
    };
  }
  if (card.type === "maintenance_test") {
    return {
      ...base,
      icon: "✓",
      stats: [
        ["cases", card.aggregate?.n_cases ?? 0],
        ["pass_all", card.aggregate?.pass_all_tests ?? "—"],
        ["regressed", card.aggregate?.n_regressed ?? 0],
      ],
      preview: `${card.skill_name || "skill"} unit utility result`,
      inspector: () => renderMaintenanceTestCard(card),
    };
  }
  if (card.type === "method_case") {
    const assertions = card.assertions || {};
    const passedCount = Object.values(assertions).filter(Boolean).length;
    return {
      ...base,
      icon: "◇",
      stats: [
        ["Passed", card.passed],
        ["Assertions", `${passedCount}/${Object.keys(assertions).length}`],
        ["Role Calls", Object.keys(card.algorithm_output?.role_calls || {}).length],
      ],
      preview: card.subtitle || "Method validation case",
      inspector: () => renderMethodCaseCard(card),
    };
  }
  if (card.type === "refine_decision") {
    return {
      ...base,
      icon: "⚑",
      stats: [
        ["Before", card.version_before ?? "—"],
        ["After", card.version_after ?? "—"],
        ["Regressions", card.failed_count ?? 0],
      ],
      preview: card.action || "No action",
      inspector: () => renderRefineDecisionCard(card),
    };
  }
  if (card.type === "skill_delta") {
    return {
      ...base,
      icon: "▤",
      stats: [
        ["New Skills", (card.new_skill_names || []).length],
        ["Skills After", card.n_skills_after ?? 0],
      ],
      preview: (card.new_skill_names || []).join(", ") || "No new skills",
      inspector: () => renderSkillDeltaCard(card),
    };
  }
  if (card.type === "summary_board") {
    const metrics = card.metrics || {};
    return {
      ...base,
      icon: "◎",
      stats: [
        ["success_rate", metrics.success_rate ?? "—"],
        ["official_valid_rate", metrics.official_valid_rate ?? "—"],
      ],
      preview: card.subtitle || "Aggregate summary",
      inspector: () => renderSummaryBoard(card),
    };
  }
  return base;
}

function boardArtifactsFromCard(card, idx) {
  const prefix = `artifact_${idx}`;
  const sourceNodeId = `node_${idx}_${card.type || "card"}`;
  if (card.type === "run") {
    return [{
      id: `${prefix}_trace`,
      sourceNodeId,
      kind: "trace",
      title: `Trace: ${card.title || "Executor"}`,
      subtitle: card.subtitle || "Executor trace and outputs",
      tone: boardToneClass(card.tone),
      payload: card.detail || {},
      summary: [
        ["Retrieved", (card.run?.retrieved_skills || []).length],
        ["Injected", (card.run?.prompt_injected_skills || []).length],
        ["Errors", (card.run?.call_errors || []).length],
      ],
    }];
  }
  if (card.type === "role_extractor") {
    return [{
      id: `${prefix}_extractor`,
      sourceNodeId,
      kind: "skill_artifact",
      title: card.artifact_preview?.name || "Candidate Skill",
      subtitle: card.artifact_preview?.description || "Extracted candidate artifact",
      tone: "board-accent",
      payload: card.detail?.output || {},
      summary: [
        ["Artifacts", card.artifact_count ?? 0],
        ["Dependencies", (card.artifact_preview?.dependencies || []).join(", ") || "none"],
      ],
    }];
  }
  if (card.type === "role_bundle_builder") {
    return [{
      id: `${prefix}_bundle`,
      sourceNodeId,
      kind: "bundle",
      title: `Bundle: ${card.subtitle || "artifact"}`,
      subtitle: card.maintenance_notes || "Distilled maintenance bundle",
      tone: "board-accent",
      payload: card.detail?.output || {},
      summary: [
        ["Positive", card.counts?.positive ?? 0],
        ["Negative", card.counts?.negative ?? 0],
        ["Integration", card.counts?.integration ?? 0],
      ],
    }];
  }
  if (card.type === "maintenance_test") {
    return [{
      id: `${prefix}_test`,
      sourceNodeId,
      kind: "test_result",
      title: `Test Result: ${card.skill_name || "skill"}`,
      subtitle: `skill_v${card.skill_version ?? "?"} | bundle_v${card.bundle_version ?? "?"}`,
      tone: boardToneClass(card.tone),
      payload: card.detail || {},
      summary: [
        ["pass_all", card.aggregate?.pass_all_tests ?? "—"],
        ["delta_acc", card.aggregate?.unit_utility_report?.delta_accuracy ?? "—"],
        ["regressed", card.aggregate?.n_regressed ?? 0],
      ],
    }];
  }
  if (card.type === "method_case") {
    return [{
      id: `${prefix}_method_case`,
      sourceNodeId,
      kind: "method_case",
      title: card.case_id || "Method Case",
      subtitle: card.subtitle || "Case spec, model output, algorithm output, assertions",
      tone: boardToneClass(card.tone),
      payload: card.detail || card,
      summary: [
        ["Passed", card.passed],
        ["Assertions", `${Object.values(card.assertions || {}).filter(Boolean).length}/${Object.keys(card.assertions || {}).length}`],
      ],
    }];
  }
  if (card.type === "refine_decision" || card.type === "role_refiner") {
    return [{
      id: `${prefix}_decision`,
      sourceNodeId,
      kind: "decision",
      title: `Decision: ${card.skill_name || card.subtitle || "refine"}`,
      subtitle: card.action || card.decision?.action || "No action",
      tone: boardToneClass(card.tone),
      payload: card.detail || {},
      summary: [
        ["Action", card.action || card.decision?.action || "—"],
        ["After", card.version_after ?? "—"],
      ],
    }];
  }
  if (card.type === "skill_delta") {
    return [{
      id: `${prefix}_store`,
      sourceNodeId,
      kind: "store_state",
      title: "Skill Store Delta",
      subtitle: card.subtitle || "Post-round store state",
      tone: "board-accent",
      payload: card,
      summary: [
        ["New Skills", (card.new_skill_names || []).length],
        ["Skills After", card.n_skills_after ?? 0],
      ],
    }];
  }
  if (card.type === "summary_board") {
    return [{
      id: `${prefix}_summary`,
      sourceNodeId,
      kind: "summary",
      title: card.title || "Summary",
      subtitle: card.subtitle || "Aggregate metrics",
      tone: boardToneClass(card.tone),
      payload: card.metrics || {},
      summary: Object.entries(card.metrics || {}).slice(0, 4),
    }];
  }
  return [];
}

function edgeLabelForNode(node) {
  const labels = {
    run: "trace",
    role_extractor: "artifacts",
    role_bundle_builder: "bundles",
    maintenance_test: "test results",
    role_refiner: "refine proposal",
    refine_decision: "store decision",
    skill_delta: "store state",
    summary_board: "summary",
  };
  return labels[node.kind] || "data";
}

function renderBoardNode(node) {
  const selected = node.id === maintenanceState.selectedBoardEntityId;
  return `
    <button class="maintenance-node-card ${escapeHtml(node.tone)} ${selected ? "node-selected" : ""}"
      style="left:${node.x}px; top:${node.y}px; width:${node.w}px; height:${node.h}px;"
      title="${escapeHtml(`${node.title}\n${node.subtitle || node.kind}\n${node.preview || ""}`)}"
      onclick="selectBoardEntity('${escapeJs(node.id)}')">
      <div class="maintenance-node-topline">
        <span class="maintenance-node-step">${escapeHtml(`Step ${node.cardIndex + 1}`)}</span>
        <span class="maintenance-node-icon">${escapeHtml(node.icon)}</span>
      </div>
      <div class="maintenance-node-title">${escapeHtml(compactLabel(node.title, 44))}</div>
      <div class="maintenance-node-subtitle">${escapeHtml(compactLabel(node.subtitle || node.kind, 52))}</div>
      <div class="maintenance-node-stats">
        ${node.stats.slice(0, 3).map(([key, value]) => `<span class="timeline-pill" title="${escapeHtml(metricHelp(String(key)))}">${escapeHtml(compactLabel(`${key}: ${value}`, 24))}</span>`).join("")}
      </div>
      <div class="maintenance-node-preview">${escapeHtml(compactLabel(node.preview || "Open inspector for full detail.", 74))}</div>
    </button>
  `;
}

function renderBoardLink(link) {
  const selected = link.from === maintenanceState.selectedBoardEntityId || link.to === maintenanceState.selectedBoardEntityId;
  if (link.kind === "artifact") {
    const midY = link.y1 + Math.max(24, (link.y2 - link.y1) * 0.44);
    return `
      <path class="maintenance-link maintenance-link-artifact ${selected ? "selected" : ""}" d="M ${link.x1} ${link.y1} C ${link.x1} ${midY}, ${link.x2} ${midY}, ${link.x2} ${link.y2}" marker-end="url(#maintenance-arrowhead)"></path>
      <text class="maintenance-link-label" x="${(link.x1 + link.x2) / 2}" y="${midY - 4}">${escapeHtml(compactLabel(link.label || "artifact", 16))}</text>
    `;
  }
  return `
    <path class="maintenance-link maintenance-link-flow ${selected ? "selected" : ""}" d="M ${link.x1} ${link.y1} C ${link.x1 + 34} ${link.y1}, ${link.x2 - 34} ${link.y2}, ${link.x2} ${link.y2}" marker-end="url(#maintenance-arrowhead)"></path>
    <text class="maintenance-link-label" x="${(link.x1 + link.x2) / 2}" y="${link.y1 - 10}">${escapeHtml(compactLabel(link.label || "data", 18))}</text>
  `;
}

function renderEmptyArtifactNotice(map) {
  return `
    <div class="maintenance-doc-empty maintenance-map-empty" style="left:28px; top:${map.artifactLaneY}px; width:360px;">
      No derived artifacts recorded on this page.
    </div>
  `;
}

function renderBoardArtifact(artifact) {
  const selected = artifact.id === maintenanceState.selectedBoardEntityId;
  return `
    <button class="maintenance-artifact-tile ${escapeHtml(artifact.tone || "board-neutral")} ${selected ? "node-selected" : ""}"
      style="left:${artifact.x}px; top:${artifact.y}px; width:${artifact.w}px; height:${artifact.h}px;"
      title="${escapeHtml(`${artifact.title}\n${artifact.subtitle || ""}`)}"
      onclick="selectBoardEntity('${escapeJs(artifact.id)}')">
      <div class="maintenance-stage-kicker">${escapeHtml(artifact.kind || "artifact")}</div>
      <div class="maintenance-artifact-title">${escapeHtml(compactLabel(artifact.title || "Artifact", 44))}</div>
      <div class="maintenance-artifact-subtitle">${escapeHtml(compactLabel(artifact.subtitle || "", 58))}</div>
      <div class="maintenance-node-stats">
        ${(artifact.summary || []).slice(0, 2).map(([key, value]) => `<span class="timeline-pill">${escapeHtml(compactLabel(`${key}: ${value}`, 26))}</span>`).join("")}
      </div>
    </button>
  `;
}

function renderBoardInspector(node) {
  const statRows = node.stats || node.summary || [];
  const content = typeof node.inspector === "function"
    ? node.inspector()
    : renderArtifactInspector(node);
  return `
    <div class="maintenance-inspector-shell">
      <div class="maintenance-inspector-head">
        <div>
          <div class="maintenance-stage-kicker">Inspector</div>
          <div class="maintenance-stage-title">${escapeHtml(node.title || "Detail")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(node.subtitle || node.kind || "")}</div>
        </div>
        ${paneToggleButton("inspector", "Inspector")}
        <div class="timeline-pill-row">
          ${statRows.map(([key, value]) => `<span class="timeline-pill">${escapeHtml(`${key}: ${value}`)}</span>`).join("")}
        </div>
      </div>
      ${content}
    </div>
  `;
}

function renderArtifactInspector(artifact) {
  return `
    <section class="maintenance-stage-card ${escapeHtml(artifact.tone || "board-neutral")}">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">${escapeHtml(artifact.kind || "artifact")}</div>
          <div class="maintenance-stage-title">${escapeHtml(artifact.title || "Artifact")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(artifact.subtitle || "")}</div>
        </div>
      </div>
      <div class="maintenance-metric-grid">
        ${(artifact.summary || []).map(([label, value]) => metricMini(label, value)).join("")}
      </div>
      ${renderDetailBlock("Artifact Payload", artifact.payload || {}, { open: maintenanceState.showVerbose })}
    </section>
  `;
}

function renderFallbackInspector(card) {
  return `
    <section class="maintenance-stage-card board-neutral">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Card</div>
          <div class="maintenance-stage-title">${escapeHtml(card.title || card.type || "card")}</div>
        </div>
      </div>
      ${renderDetailBlock("Raw Card Payload", card || {}, { open: true })}
    </section>
  `;
}

function selectBoardEntity(entityId) {
  maintenanceState.selectedBoardEntityId = entityId;
  if (maintenanceState.currentDetail) {
    renderMaintenanceDetail(maintenanceState.currentDetail);
  }
}

function getDefaultSelectedTurnIndex(timeline) {
  if (!timeline.length) return -1;
  const errorIndex = timeline.findIndex((turn) => {
    const errors = turn.callErrors || [];
    const scoreRows = turn.scoreRows || [];
    return errors.length > 0 || scoreRows.some((row) => /error|fail|extra|missing/i.test(String(row.label || "") + String(row.value || "")));
  });
  return errorIndex >= 0 ? errorIndex : 0;
}

function getSelectedTurnIndex(timeline) {
  if (!timeline.length) {
    maintenanceState.selectedTurnIndex = -1;
    return -1;
  }
  if (maintenanceState.selectedTurnIndex < 0 || maintenanceState.selectedTurnIndex >= timeline.length) {
    maintenanceState.selectedTurnIndex = getDefaultSelectedTurnIndex(timeline);
  }
  return maintenanceState.selectedTurnIndex;
}

function selectExecutionTurn(index) {
  maintenanceState.selectedTurnIndex = index;
  if (maintenanceState.currentDetail) {
    renderMaintenanceDetail(maintenanceState.currentDetail);
  }
}

function renderExecutorWorkbench(runCard, timeline, selectedTurn, selectedIndex) {
  const run = runCard.run || {};
  const traceDetail = run.detail || {};
  return `
    <section class="maintenance-stage-card ${escapeHtml(boardToneClass(runCard.tone))}">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Executor Detail</div>
          <div class="maintenance-stage-title">${escapeHtml(runCard.title || "Executor")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(runCard.subtitle || "")}</div>
        </div>
        <div class="timeline-pill-row">
          ${(runCard.pills || []).map((pill) => `<span class="timeline-pill">${escapeHtml(String(pill))}</span>`).join("")}
        </div>
      </div>
      <div class="maintenance-metric-grid">
        ${metricMini("Official", run.official_valid ?? "—")}
        ${metricMini("Call F1", run.call_f1 ?? "—")}
        ${metricMini("Tokens", run.total_tokens ?? "—")}
        ${metricMini("Elapsed", run.elapsed_s ?? "—")}
        ${metricMini("Steps", run.n_model_steps ?? "—")}
        ${metricMini("Errors", (run.call_errors || []).length)}
      </div>
      <div class="maintenance-two-col">
        <div>
          <div class="maintenance-section-title">Retrieved Skills</div>
          ${renderChipList(run.retrieved_skills || [])}
        </div>
        <div>
          <div class="maintenance-section-title">Prompt Injected Skills</div>
          ${renderChipList(run.prompt_injected_skills || [])}
        </div>
      </div>
      <div class="maintenance-executor-layout">
        <div class="maintenance-turn-rail">
          <div class="maintenance-section-title">Turns</div>
          ${renderExecutionTurnRail(timeline, selectedIndex)}
        </div>
        <div class="maintenance-turn-detail">
          ${selectedTurn ? renderSelectedExecutionTurn(selectedTurn) : "<div class='maintenance-doc-empty'>No turn selected.</div>"}
        </div>
      </div>
      ${renderDetailBlock("Executor Inputs", (runCard.detail || {}).input || {}, { open: maintenanceState.showVerbose })}
      ${renderDetailBlock("Executor Outputs", (runCard.detail || {}).output || {}, { open: false })}
      ${renderDetailBlock("Raw Trace JSON", traceDetail.raw_trace || "", { open: false })}
    </section>
  `;
}

function renderExecutionTurnRail(timeline, selectedIndex) {
  if (!timeline.length) {
    return "<div class='maintenance-doc-empty'>No turn timeline available.</div>";
  }
  return `
    <div class="maintenance-turn-rail-list">
      ${timeline.map((turn, idx) => {
        const active = idx === selectedIndex ? "active" : "";
        const errorCount = (turn.callErrors || []).length;
        const callCount = (turn.toolCalls || []).length;
        const skillCount = (turn.retrievedSkills || []).length + (turn.promptInjectedSkills || []).length;
        return `
          <button class="maintenance-turn-thumb ${active} ${errorCount ? "turn-has-error" : ""}" onclick="selectExecutionTurn(${idx})">
            <div class="maintenance-turn-thumb-top">
              <span class="maintenance-node-step">Turn ${idx + 1}</span>
              ${errorCount ? `<span class="timeline-pill">errors ${errorCount}</span>` : ""}
            </div>
            <div class="maintenance-turn-thumb-title">${escapeHtml(compactLabel(turn.userSummary || turn.title || `Turn ${idx + 1}`, 72))}</div>
            <div class="compact-flow-stats">
              <span>${escapeHtml(`calls: ${callCount}`)}</span>
              <span>${escapeHtml(`skills: ${skillCount}`)}</span>
              <span>${escapeHtml(`score rows: ${(turn.scoreRows || []).length}`)}</span>
            </div>
          </button>
        `;
      }).join("")}
    </div>
  `;
}

function renderSelectedExecutionTurn(turn) {
  return `
    <div class="maintenance-selected-turn">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Selected Turn</div>
          <div class="maintenance-stage-title">${escapeHtml(turn.title || "Turn")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(turn.userSummary || "")}</div>
        </div>
        <div class="timeline-pill-row">
          <span class="timeline-pill">${escapeHtml(`tool calls ${(turn.toolCalls || []).length}`)}</span>
          <span class="timeline-pill">${escapeHtml(`errors ${(turn.callErrors || []).length}`)}</span>
        </div>
      </div>
      ${renderExecutionTurn(turn)}
    </div>
  `;
}

function selectRoleTab(tab) {
  maintenanceState.selectedRoleTab = tab;
  if (maintenanceState.currentDetail) {
    renderMaintenanceDetail(maintenanceState.currentDetail);
  }
}

function renderRoleDetailWorkbench(node) {
  const card = node.card || {};
  const activeTab = maintenanceState.selectedRoleTab || "summary";
  const tabs = [
    ["summary", "Summary"],
    ["input", "Input"],
    ["output", "Parsed Output"],
    ["raw", "Raw"],
  ];
  return `
    <section class="maintenance-stage-card ${escapeHtml(node.tone || "board-neutral")}">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Role Detail</div>
          <div class="maintenance-stage-title">${escapeHtml(node.title || "Role")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(node.subtitle || node.kind || "")}</div>
        </div>
      </div>
      <div class="maintenance-tab-row">
        ${tabs.map(([key, label]) => `<button class="btn chip-btn ${activeTab === key ? "active-toggle" : ""}" onclick="selectRoleTab('${escapeJs(key)}')">${escapeHtml(label)}</button>`).join("")}
      </div>
      <div class="maintenance-role-tab-panel">
        ${renderRoleTabPanel(card, activeTab, node)}
      </div>
    </section>
  `;
}

function renderRoleTabPanel(card, tab, node) {
  if (tab === "input") {
    return renderDetailBlock("Role Input", card.detail?.input || {}, { open: true });
  }
  if (tab === "output") {
    return renderDetailBlock("Parsed Output", card.detail?.output || {}, { open: true });
  }
  if (tab === "raw") {
    return renderDetailBlock("Raw Role Detail", card.detail || card || {}, { open: true });
  }
  return renderRoleSummary(card, node);
}

function renderRoleSummary(card, node) {
  if (card.type === "role_extractor") return renderExtractorCard(card, { suppressIO: true });
  if (card.type === "role_bundle_builder") return renderBundleCard(card, { suppressIO: true });
  if (card.type === "role_refiner") return renderRoleRefinerCard(card, { suppressIO: true });
  if (card.type === "maintenance_test") return renderMaintenanceTestCard(card, { suppressRaw: true });
  if (card.type === "refine_decision") return renderRefineDecisionCard(card, { suppressRaw: true });
  if (card.type === "skill_delta") return renderSkillDeltaCard(card);
  return node.inspector ? node.inspector() : renderFallbackInspector(card);
}

function renderRunCard(card) {
  const run = card.run || {};
  const detail = card.detail || {};
  return `
    <section class="maintenance-stage-card ${escapeHtml(boardToneClass(card.tone))}">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Executor</div>
          <div class="maintenance-stage-title">${escapeHtml(card.title || "Executor")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(card.subtitle || "")}</div>
        </div>
        <div class="timeline-pill-row">
          ${(card.pills || []).map((pill) => `<span class="timeline-pill">${escapeHtml(String(pill))}</span>`).join("")}
        </div>
      </div>
      <div class="maintenance-metric-grid">
        ${metricMini("Official", run.official_valid ?? "—")}
        ${metricMini("Call F1", run.call_f1 ?? "—")}
        ${metricMini("Tokens", run.total_tokens ?? "—")}
        ${metricMini("Elapsed", run.elapsed_s ?? "—")}
        ${metricMini("Steps", run.n_model_steps ?? "—")}
        ${metricMini("Errors", (run.call_errors || []).length)}
      </div>
      <div class="maintenance-two-col">
        <div>
          <div class="maintenance-section-title">Retrieved Skills</div>
          ${renderChipList(run.retrieved_skills || [])}
        </div>
        <div>
          <div class="maintenance-section-title">Prompt Injected Skills</div>
          ${renderChipList(run.prompt_injected_skills || [])}
        </div>
      </div>
      ${renderDetailBlock("Executor Inputs", detail.input || {}, { open: maintenanceState.showVerbose })}
      ${renderExecutorTraceBlock(run.detail || {}, run)}
      ${renderDetailBlock("Executor Outputs", detail.output || {}, { open: false })}
    </section>
  `;
}

function renderExtractorCard(card, options = {}) {
  const preview = card.artifact_preview || {};
  return `
    <section class="maintenance-stage-card board-accent">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Role</div>
          <div class="maintenance-stage-title">Extractor</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(preview.name || "No artifact")}</div>
        </div>
        <span class="timeline-pill">${escapeHtml(`artifacts=${card.artifact_count || 0}`)}</span>
      </div>
      <div class="maintenance-two-col">
        ${infoPanel("Detected Skill", [
          ["Name", preview.name || "—"],
          ["Kind", preview.kind || "—"],
          ["Version Kind", preview.version_kind || "—"],
        ])}
        ${infoPanel("Information Flow", [
          ["Input", "executor result trace"],
          ["Output", "candidate skill artifact"],
          ["Dependencies", (preview.dependencies || []).join(", ") || "none"],
        ])}
      </div>
      <div class="maintenance-note-box">${escapeHtml(preview.description || "No description")}</div>
      ${options.suppressIO ? "" : renderRoleIO(card)}
    </section>
  `;
}

function renderBundleCard(card, options = {}) {
  return `
    <section class="maintenance-stage-card board-accent">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Role</div>
          <div class="maintenance-stage-title">Bundle Builder</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(card.subtitle || "")}</div>
        </div>
      </div>
      <div class="maintenance-metric-grid">
        ${metricMini("Positive", card.counts?.positive ?? 0)}
        ${metricMini("Negative", card.counts?.negative ?? 0)}
        ${metricMini("Integration", card.counts?.integration ?? 0)}
        ${metricMini("Source Runs", card.metadata?.n_source_results ?? "—")}
        ${metricMini("Replay Runs", card.metadata?.n_replay_results ?? "—")}
        ${metricMini("Failures", card.metadata?.n_integration_failures ?? "—")}
      </div>
      <div class="maintenance-two-col">
        ${caseScopePanel("Positive Cases", card.cases?.positive || [])}
        ${caseScopePanel("Negative Cases", card.cases?.negative || [])}
      </div>
      <div class="maintenance-note-box">${escapeHtml(card.maintenance_notes || "No maintenance notes")}</div>
      ${options.suppressIO ? "" : renderRoleIO(card)}
    </section>
  `;
}

function renderRoleRefinerCard(card, options = {}) {
  const decision = card.decision || {};
  return `
    <section class="maintenance-stage-card ${escapeHtml(boardToneClass(card.tone))}">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Role</div>
          <div class="maintenance-stage-title">Refiner</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(card.subtitle || "")}</div>
        </div>
        <span class="timeline-pill">${escapeHtml(decision.action || "—")}</span>
      </div>
      <div class="maintenance-two-col">
        ${infoPanel("Decision", [
          ["Action", decision.action || "—"],
          ["Version Kind", decision.version_kind || "—"],
          ["Pinned Deps", (decision.pinned_dependencies || []).join(", ") || "none"],
        ])}
        ${infoPanel("Effect", [
          ["Artifact", card.artifact_preview?.name || "—"],
          ["Bundle Positive", card.bundle_preview?.positive ?? 0],
          ["Bundle Negative", card.bundle_preview?.negative ?? 0],
        ])}
      </div>
      <div class="maintenance-note-box">${escapeHtml(decision.reason || "No reason")}</div>
      ${options.suppressIO ? "" : renderRoleIO(card)}
    </section>
  `;
}

function renderMaintenanceTestCard(card, options = {}) {
  const detail = card.detail || {};
  const caseRuns = card.unit_case_runs || detail.unit_case_runs || [];
  return `
    <section class="maintenance-stage-card ${escapeHtml(boardToneClass(card.tone))}">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Unit Test</div>
          <div class="maintenance-stage-title">${escapeHtml(card.skill_name || "")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(`skill_v${card.skill_version ?? "?"} | bundle_v${card.bundle_version ?? "?"}`)}</div>
        </div>
        <span class="timeline-pill">${escapeHtml(`pass_all=${card.aggregate?.pass_all_tests}`)}</span>
      </div>
      <div class="maintenance-metric-grid">
        ${(card.breakdown || []).map((item) => metricMini(item.label, item.value ?? "—")).join("")}
      </div>
      <div class="maintenance-two-col">
        ${infoPanel("Counterfactual", buildCounterfactualRows(card.counterfactual || {}))}
        ${infoPanel("Unit Cases", [
          ["Total", caseRuns.length],
          ["Failures", caseRuns.filter((item) => item.passed === false).length],
          ["Integration Failures", (card.integration_failures || []).length],
        ])}
      </div>
      ${renderUnitCaseRunExplorer(caseRuns)}
      ${options.suppressRaw ? "" : renderDetailBlock("Detailed Counterfactual", detail.counterfactual || {}, { open: false })}
      ${options.suppressRaw ? "" : renderDetailBlock("Detailed Integration Failures", detail.integration_failures || [], { open: false })}
    </section>
  `;
}

function renderUnitCaseRunExplorer(caseRuns) {
  if (!caseRuns.length) {
    return "<div class='maintenance-missing-detail'>No per-case test run details were recorded.</div>";
  }
  const grouped = new Map();
  for (const run of caseRuns) {
    const key = run.case_id || "unknown_case";
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(run);
  }
  return `
    <div class="unit-case-run-stack">
      ${[...grouped.entries()].map(([caseId, runs]) => renderUnitCaseGroup(caseId, runs)).join("")}
    </div>
  `;
}

function renderUnitCaseGroup(caseId, runs) {
  const first = runs[0] || {};
  const snapshot = first.bundle_case_snapshot || {};
  return `
    <details class="unit-case-group" open>
      <summary>
        <span>${escapeHtml(caseId)}</span>
        <span class="timeline-pill">${escapeHtml(`${runs.length} variants`)}</span>
        <span class="timeline-pill">${escapeHtml(snapshot.polarity || first.metadata?.polarity || "case")}</span>
      </summary>
      <div class="unit-case-body">
        <div class="unit-case-oracle">
          <div class="maintenance-stage-kicker">Case Scope</div>
          <div class="task-problem-text">${escapeHtml(snapshot.prompt || extractQuestionText(snapshot) || "No prompt recorded.")}</div>
          <div class="chip-grid">
            ${(snapshot.tags || []).map((tag) => `<span class="dep-chip">${escapeHtml(tag)}</span>`).join("")}
          </div>
        </div>
        <div class="unit-variant-grid">
          ${runs.map(renderUnitVariantCard).join("")}
        </div>
      </div>
    </details>
  `;
}

function renderUnitVariantCard(run) {
  const traceSummary = run.trace_summary || run.actual_output?.trace_summary || {};
  const calls = run.tool_calls || [];
  const modalId = rememberModalPayload({
    title: "Unit Case Variant",
    subtitle: `${run.case_id || ""} | ${run.variant || ""}`,
    payload: run,
  });
  const hasStructured = Boolean(
    Object.keys(run.input_payload || {}).length ||
    Object.keys(run.actual_output || {}).length ||
    calls.length
  );
  return `
    <article class="unit-variant-card ${run.passed ? "board-success" : "board-warning"}">
      <div class="sequence-role-head">
        <div>
          <div class="maintenance-stage-kicker">${escapeHtml(run.variant || "variant")}</div>
          <div class="sequence-role-title">${escapeHtml(run.passed ? "Passed" : "Failed")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(run.failure_summary || traceSummary.error || "")}</div>
        </div>
        <button class="btn chip-btn" onclick="openRememberedModal('${escapeJs(modalId)}')">Open</button>
      </div>
      <div class="maintenance-metric-grid compact-metrics">
        ${metricMini("Validity", run.validity ?? "—")}
        ${metricMini("Accuracy", run.accuracy ?? "—")}
        ${metricMini("Tokens", run.tokens ?? traceSummary.total_tokens ?? "—")}
        ${metricMini("Steps", run.steps ?? traceSummary.n_model_steps ?? "—")}
        ${metricMini("Tool Calls", calls.length || traceSummary.n_tool_calls || 0)}
        ${metricMini("Errors", (traceSummary.call_errors || run.metadata?.call_errors || []).length)}
      </div>
      <div class="unit-call-list">
        ${calls.length ? calls.slice(0, 6).map((call, idx) => renderMiniToolCall(call, idx)).join("") : `<div class="empty-inline">${hasStructured ? "No tool calls recorded." : "Legacy log: detailed output unavailable."}</div>`}
      </div>
      ${hasStructured ? `
        <div class="sequence-io-grid single-json-column">
          ${renderClickableUnitPayload("Input", run.input_payload || {}, run)}
          ${renderClickableUnitPayload("Expected", run.expected_behavior || {}, run)}
          ${renderClickableUnitPayload("Actual", run.actual_output || {}, run)}
        </div>
      ` : `
        <div class="maintenance-missing-detail">
          ${escapeHtml(run.io_unavailable_reason || "This historical test result only stores pass/fail metrics. Rerun with the current logger to capture per-case input/output.")}
        </div>
      `}
    </article>
  `;
}

function renderMiniToolCall(call, idx) {
  return `
    <div class="mini-tool-call">
      <span class="readable-list-index">${idx + 1}</span>
      <span class="mini-tool-name">${escapeHtml(call.name || call.actual_name || "tool")}</span>
      <span class="mini-tool-args">${escapeHtml(summarizeValue(call.arguments || call.actual_arguments || {}))}</span>
    </div>
  `;
}

function renderClickableUnitPayload(title, payload, run) {
  const id = rememberModalPayload({ title, payload, subtitle: `${run.case_id || ""} | ${run.variant || ""}` });
  return `
    <button class="sequence-io-preview" onclick="openRememberedModal('${escapeJs(id)}')">
      <span class="maintenance-stage-kicker">${escapeHtml(title)}</span>
      <span class="sequence-preview-text">${escapeHtml(summarizeValue(payload))}</span>
      <span class="timeline-pill">tree</span>
    </button>
  `;
}

function renderMethodCaseCard(card) {
  return renderMethodCaseReport(card, { includeRaw: true });
}

function renderMethodCaseDetail(node) {
  const card = node.rawPayload || node.detailPayload?.raw_result || {};
  return renderMethodCaseReport(card, { includeRaw: true });
}

function renderMethodCaseReport(card, options = {}) {
  const vm = card.view_model || {};
  const assertions = card.assertions || {};
  const given = card.given || {};
  const retrieval = vm.retrieval_summary || {};
  const role = vm.role_summary || {};
  const artifact = vm.artifact_summary || {};
  const model = card.model_output || {};
  const algorithm = card.algorithm_output || {};
  return `
    <div class="method-report-stack">
      <section class="maintenance-stage-card ${escapeHtml(boardToneClass(card.tone))} method-case-card">
        <div class="maintenance-stage-head">
          <div>
            <div class="maintenance-stage-kicker">Method Validation Report</div>
            <div class="maintenance-stage-title">${escapeHtml(card.case_id || "Method Case")}</div>
            <div class="maintenance-stage-subtitle">${escapeHtml(given.query || card.subtitle || "")}</div>
          </div>
          <span class="timeline-pill ${card.passed ? "success-pill" : "danger-pill"}">${escapeHtml(card.passed ? "passed" : "failed")}</span>
        </div>
        <div class="maintenance-metric-grid">
          ${metricMini("Passed", card.passed)}
          ${metricMini("Assertions", `${Object.values(assertions).filter(Boolean).length}/${Object.keys(assertions).length}`)}
          ${metricMini("Selected Skills", (retrieval.selected || []).length)}
          ${metricMini("Role Calls", Object.keys(algorithm.role_calls || {}).length)}
          ${metricMini("Audit Rows", (model.audit_rows || []).length)}
          ${metricMini("Resolved Version", artifact.resolved_version ?? "—")}
        </div>
      </section>

      <div class="method-report-grid">
        <section class="method-report-panel method-case-given">
          <div class="method-case-section-head">
            <div>
              <div class="maintenance-stage-kicker">Given By Test Case</div>
              <div class="maintenance-section-title">Case Spec / Setup</div>
            </div>
            <span class="timeline-pill">fixed input</span>
          </div>
          <div class="method-query-box">${escapeHtml(given.query || "No query recorded.")}</div>
          <div class="maintenance-two-col">
            ${infoPanel("Setup", [
              ["Initial Skills", (given.skills_before || []).length],
              ["Expected Fields", Object.keys(given.expected || {}).join(", ") || "none"],
              ["Case ID", card.case_id || "—"],
            ])}
            ${renderMethodSkillSetup(given.skills_before || [])}
          </div>
          ${renderTextualPayloadCard("Expected Behavior", given.expected || {})}
        </section>

        <section class="method-report-panel method-case-model">
          <div class="method-case-section-head">
            <div>
              <div class="maintenance-stage-kicker">Model Output</div>
              <div class="maintenance-section-title">Role I/O</div>
            </div>
            <span class="timeline-pill">LLM</span>
          </div>
          ${renderMethodRoleCall("Stale Resolver", model.role_io?.stale_resolver || {})}
          <div class="refine-reason-box">${escapeHtml(role.stale_resolver_reason || "No model reason recorded.")}</div>
        </section>

        <section class="method-report-panel method-case-algorithm">
          <div class="method-case-section-head">
            <div>
              <div class="maintenance-stage-kicker">Algorithm Output</div>
              <div class="maintenance-section-title">Retrieval / Resolution / Tests</div>
            </div>
            <span class="timeline-pill">system</span>
          </div>
          ${renderMethodRetrievalSummary(retrieval)}
          ${renderMethodResolvedArtifact(artifact)}
          ${renderTextualPayloadCard("Post-resolution Test Result", vm.test_summary || algorithm.test_result || {})}
        </section>

        <section class="method-report-panel method-case-assertions">
          <div class="method-case-section-head">
            <div>
              <div class="maintenance-stage-kicker">Assertions</div>
              <div class="maintenance-section-title">Pass / Fail Checks</div>
            </div>
            <span class="timeline-pill">oracle</span>
          </div>
          ${renderMethodAssertions(assertions)}
        </section>
      </div>

      ${options.includeRaw ? renderDebugRaw("Debug Raw Method Case", card.detail?.raw_result || card) : ""}
    </div>
  `;
}

function renderMethodSkillSetup(skills) {
  return `
    <div class="method-skill-mini-list">
      <div class="maintenance-section-title">Setup Skills</div>
      ${skills.length ? skills.map((skill) => `
        <article class="method-skill-mini">
          <div class="bundle-case-head">
            <div>
              <div class="bundle-case-title">${escapeHtml(skill.name || "skill")}</div>
              <div class="maintenance-stage-subtitle">${escapeHtml(compactMultiline(skill.description || skill.body || "", 140))}</div>
            </div>
            <span class="timeline-pill">${escapeHtml(`v${skill.version ?? "?"} ${skill.status || ""}`)}</span>
          </div>
          <div class="core-detail-text">${escapeHtml(compactMultiline(skill.body || "", 220))}</div>
        </article>
      `).join("") : "<div class='maintenance-doc-empty'>No setup skills recorded.</div>"}
    </div>
  `;
}

function renderMethodRoleCall(title, io) {
  return `
    <div class="method-role-call">
      <div class="bundle-case-head">
        <div>
          <div class="bundle-case-title">${escapeHtml(title)}</div>
          <div class="maintenance-stage-subtitle">真实 role 调用输入输出</div>
        </div>
        <span class="timeline-pill">${escapeHtml(io.parsed_output?.action || "output")}</span>
      </div>
      <div class="single-json-column">
        ${renderTextualPayloadCard("System Prompt", io.system || "")}
        ${renderTextualPayloadCard("User Prompt", io.user || "")}
      </div>
      <div class="single-json-column">
        ${renderTextualPayloadCard("Parsed Output", io.parsed_output || {})}
        ${renderTextualPayloadCard("Raw Response", io.raw_response || "")}
      </div>
    </div>
  `;
}

function renderMethodRetrievalSummary(retrieval) {
  const candidates = retrieval.candidates || [];
  return `
    <div class="method-retrieval-panel">
      <div class="maintenance-two-col">
        ${infoPanel("Store Summary", Object.entries(retrieval.store_summary || {}))}
        ${infoPanel("Selected Skills", (retrieval.selected || []).map((item) => [
          item.name || "skill",
          `rank=${item.rank ?? "?"}, score=${item.score ?? "?"}`,
        ]))}
      </div>
      <div class="retrieval-candidate-table method-candidate-table">
        <div class="retrieval-candidate-row retrieval-head">
          <span>Skill</span><span>Score</span><span>Rank</span><span>Status</span><span>Why Visible</span>
        </div>
        ${candidates.length ? candidates.map((item) => `
          <div class="retrieval-candidate-row ${item.selected ? "candidate-selected" : ""}">
            <span>${escapeHtml(`${item.name || "skill"}@v${item.version ?? "?"}`)}</span>
            <span>${escapeHtml(item.score ?? "—")}</span>
            <span>${escapeHtml(item.rank ?? "—")}</span>
            <span>${escapeHtml(item.stale ? "stale" : (item.status || "active"))}</span>
            <span>${escapeHtml(item.filter_reason || (item.predicate_passed ? "predicate passed" : "predicate failed"))}</span>
          </div>
        `).join("") : "<div class='maintenance-doc-empty'>No retrieval candidates recorded.</div>"}
      </div>
    </div>
  `;
}

function renderMethodResolvedArtifact(artifact) {
  return `
    <div class="method-resolved-artifact">
      <div class="maintenance-section-title">Resolved Skill Artifact</div>
      <div class="maintenance-metric-grid compact-metrics">
        ${metricMini("Name", artifact.resolved_name || "—")}
        ${metricMini("Version", artifact.resolved_version ?? "—")}
        ${metricMini("Status", artifact.resolved_status || "—")}
        ${metricMini("Version Kind", artifact.resolved_version_kind || "—")}
      </div>
      <pre class="maintenance-code-block skill-body-block">${escapeHtml(artifact.resolved_body || "No resolved body recorded.")}</pre>
      ${renderTextualPayloadCard("Resolved Interface", artifact.resolved_interface || {})}
    </div>
  `;
}

function renderMethodAssertions(assertions) {
  const rows = Object.entries(assertions || {});
  if (!rows.length) return "<div class='maintenance-doc-empty'>No assertions recorded.</div>";
  return `
    <div class="method-assertion-list">
      ${rows.map(([key, value]) => `
        <div class="method-assertion-row ${value ? "assertion-pass" : "assertion-fail"}">
          <span class="method-assertion-status">${escapeHtml(value ? "PASS" : "FAIL")}</span>
          <span class="method-assertion-name">${escapeHtml(key)}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function methodCaseSection(title, payload, kind) {
  return `
    <section class="method-case-section method-case-${escapeHtml(kind)}">
      <div class="method-case-section-head">
        <div class="maintenance-section-title">${escapeHtml(title)}</div>
        <span class="timeline-pill">${escapeHtml(kind)}</span>
      </div>
      ${kind === "assertions" ? infoPanel("Assertion Results", payload.rows || []) : renderReadablePayload(payload)}
      <details class="maintenance-raw-details">
        <summary>Raw ${escapeHtml(title)}</summary>
        ${renderJsonTree(payload.raw || payload, title, 0)}
      </details>
    </section>
  `;
}

function renderRefineDecisionCard(card, options = {}) {
  const detail = card.detail || {};
  return `
    <section class="maintenance-stage-card ${escapeHtml(boardToneClass(card.tone))}">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Decision</div>
          <div class="maintenance-stage-title">${escapeHtml(card.skill_name || "")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(card.action || "")}</div>
        </div>
      </div>
      <div class="maintenance-metric-grid">
        ${metricMini("Before", card.version_before ?? "—")}
        ${metricMini("After", card.version_after ?? "—")}
        ${metricMini("Regressions", card.failed_count ?? 0)}
        ${metricMini("Helped", card.helped_count ?? 0)}
        ${metricMini("Counterfactual", card.used_counterfactual_evidence ?? "—")}
      </div>
      <div class="maintenance-two-col">
        ${infoPanel("Regression Tasks", (card.regression_task_ids || []).map((item) => ["Task", item]))}
        ${infoPanel("Counterfactual Tasks", (card.counterfactual_task_ids || []).map((item) => ["Task", item]))}
      </div>
      ${options.suppressRaw ? "" : renderDetailBlock("Raw Decision Payload", detail.raw_decision || {}, { open: false })}
    </section>
  `;
}

function renderSkillDeltaCard(card) {
  return `
    <section class="maintenance-stage-card board-accent">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Store</div>
          <div class="maintenance-stage-title">Skill Store Update</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(card.subtitle || "")}</div>
        </div>
      </div>
      <div class="maintenance-metric-grid">
        ${metricMini("New Skills", (card.new_skill_names || []).length)}
        ${metricMini("Skills After", card.n_skills_after ?? 0)}
      </div>
      <div>
        <div class="maintenance-section-title">Added Skills</div>
        ${renderChipList(card.new_skill_names || [])}
      </div>
    </section>
  `;
}

function renderSummaryBoard(card) {
  const metrics = card.metrics || {};
  const preferred = [
    "success_rate", "official_valid_rate", "avg_score", "avg_call_precision",
    "avg_call_recall", "avg_turn_success_rate", "avg_relaxed_turn_success_rate",
    "avg_total_tokens", "avg_elapsed_s", "avg_model_steps",
  ];
  const rows = preferred.filter((key) => key in metrics).map((key) => metricMini(key, metrics[key])).join("");
  return `
    <section class="maintenance-stage-card ${escapeHtml(boardToneClass(card.tone))}">
      <div class="maintenance-stage-head">
        <div>
          <div class="maintenance-stage-kicker">Summary</div>
          <div class="maintenance-stage-title">${escapeHtml(card.title || "")}</div>
          <div class="maintenance-stage-subtitle">${escapeHtml(card.subtitle || "")}</div>
        </div>
      </div>
      <div class="maintenance-metric-grid">${rows}</div>
    </section>
  `;
}

function infoPanel(title, rows) {
  const content = rows.length
    ? rows.map(([key, value]) => `
        <div class="maintenance-info-row">
          <span class="maintenance-info-key">${escapeHtml(String(key))}</span>
          <span class="maintenance-info-value">${escapeHtml(String(value ?? "—"))}</span>
        </div>
      `).join("")
    : "<div class='empty-inline'>None</div>";
  return `
    <div class="maintenance-info-panel">
      <div class="maintenance-section-title">${escapeHtml(title)}</div>
      <div class="structured-stack">${content}</div>
    </div>
  `;
}

function caseScopePanel(title, cases) {
  return `
    <div class="maintenance-info-panel">
      <div class="maintenance-section-title">${escapeHtml(title)}</div>
      <div class="structured-stack">
        ${cases.slice(0, 3).map((item) => `
          <div class="maintenance-list-row">
            <div class="maintenance-list-title">${escapeHtml(item.case_id || "case")}</div>
            <div class="maintenance-list-detail">${escapeHtml(item.source || "")}</div>
          </div>
        `).join("") || "<div class='empty-inline'>None</div>"}
      </div>
    </div>
  `;
}

function buildCounterfactualRows(cf) {
  const withoutMap = cf.without_skill_valid_by_task || {};
  const withMap = cf.with_skill_valid_by_task || {};
  const taskIds = Array.from(new Set([...Object.keys(withoutMap), ...Object.keys(withMap)]));
  return taskIds.map((taskId) => [taskId, `without=${withoutMap[taskId]} | with=${withMap[taskId]}`]);
}

function renderChipList(items) {
  if (!items.length) return "<span class='empty-inline'>None</span>";
  return `<div class="chip-grid">${items.map((item) => `<span class="dep-chip">${escapeHtml(String(item))}</span>`).join("")}</div>`;
}

function metricMini(label, value) {
  return `
    <div class="maintenance-mini-card" title="${escapeHtml(metricHelp(label))}">
      <div class="maintenance-mini-label">${escapeHtml(String(label))}</div>
      <div class="maintenance-mini-value">${escapeHtml(String(value ?? "—"))}</div>
    </div>
  `;
}

function metricHelp(label) {
  const docs = {
    "Official": "BFCL 官方验证器是否判定本轮正确。",
    "Call F1": "expected calls 与 actual calls 的调用级 F1。",
    "Tokens": "本轮总 token 消耗。",
    "Elapsed": "本轮端到端耗时（秒）。",
    "Steps": "本轮模型响应步数。",
    "Errors": "被 scorer 标出的调用错误数量。",
    "cases": "本次 unit maintenance 实际执行的 bundle case 数量。",
    "comparable": "能做 with/without 对照的 case 数量。",
    "improved": "加 skill 后改善的 case 数量。",
    "regressed": "加 skill 后退化的 case 数量。",
    "pass_all": "该 bundle 当前是否全部通过。",
    "delta_acc": "with_skill 相对 without_skill 的局部 utility 精度差。",
    "delta_tokens": "with_skill 相对 without_skill 的 token 差。",
    "delta_steps": "with_skill 相对 without_skill 的 step 差。",
    "Before": "变更前版本。",
    "After": "变更后版本。",
    "Regressions": "当前决策摘要记录到的回归计数。",
    "Helped": "当前决策摘要记录到的帮助计数。",
    "Counterfactual": "该决策是否引用了 with/without 证据。",
    "Positive": "bundle 中正例数量。",
    "Negative": "bundle 中反例数量。",
    "Integration": "bundle 中 integration case 数量。",
    "Source Runs": "bundle builder 输入里的 source 运行数。",
    "Replay Runs": "bundle builder 输入里的 replay 运行数。",
    "Failures": "bundle builder 输入里的 integration failure 数量。",
    "New Skills": "该轮新增技能数。",
    "Skills After": "该轮后 skill store 中技能总数。",
    "Total": "总数。",
    "Integration Failures": "带 skill 仍失败并被记录的样例数。",
  };
  return docs[label] || `${label} 的具体语义需要结合当前卡片上下文理解。`;
}

function renderRoleIO(card) {
  return `
    <div class="single-json-column">
      ${renderDetailBlock("Role Input", card.detail?.input || {}, { open: maintenanceState.showVerbose })}
      ${renderDetailBlock("Role Output", card.detail?.output || {}, { open: false })}
    </div>
  `;
}

function renderDetailBlock(title, payload, options = {}) {
  const key = options.key || `detail:${title}`;
  const hasRemembered = Object.prototype.hasOwnProperty.call(maintenanceState.detailOpenState, key);
  const shouldOpen = hasRemembered ? maintenanceState.detailOpenState[key] : Boolean(options.open);
  const openAttr = shouldOpen ? " open" : "";
  return `
    <section class="trace-block maintenance-detail-block">
      <div class="maintenance-detail-summary">${escapeHtml(title)}</div>
      <details class="maintenance-raw-details json-navigator-panel"${openAttr} ontoggle="rememberDetailOpenState('${escapeJs(key)}', this.open)">
        <summary>JSON Navigator</summary>
        ${renderJsonTree(payload, title || "root", 0)}
      </details>
    </section>
  `;
}

function rememberDetailOpenState(key, open) {
  maintenanceState.detailOpenState[key] = Boolean(open);
}

function renderStructuredPayload(payload) {
  if (payload === null || payload === undefined || payload === "") {
    return "<div class='maintenance-missing-detail'>No payload recorded.</div>";
  }
  if (typeof payload === "string") {
    return `<div class="maintenance-note-box">${escapeHtml(compactMultiline(payload, 900))}</div>`;
  }
  if (Array.isArray(payload)) {
    if (!payload.length) return "<div class='empty-inline'>Empty list</div>";
    return `
      <div class="maintenance-structured-list">
        ${payload.slice(0, 12).map((item, idx) => renderStructuredListItem(item, idx)).join("")}
        ${payload.length > 12 ? `<div class="maintenance-list-more">+ ${payload.length - 12} more items in raw payload</div>` : ""}
      </div>
    `;
  }
  if (typeof payload === "object") {
    const entries = Object.entries(payload);
    if (!entries.length) return "<div class='empty-inline'>Empty object</div>";
    return `
      <div class="maintenance-structured-grid">
        ${entries.slice(0, 18).map(([key, value]) => renderStructuredKv(key, value)).join("")}
        ${entries.length > 18 ? `<div class="maintenance-list-more">+ ${entries.length - 18} more fields in raw payload</div>` : ""}
      </div>
    `;
  }
  return `<div class="maintenance-note-box">${escapeHtml(String(payload))}</div>`;
}

function renderStructuredListItem(item, idx) {
  if (item && typeof item === "object" && !Array.isArray(item)) {
    const title = item.role || item.name || item.type || item.case_id || item.task_id || item.id || `Item ${idx + 1}`;
    const fields = Object.entries(item)
      .filter(([key]) => !["raw", "raw_trace", "input", "output"].includes(key))
      .slice(0, 5);
    return `
      <div class="maintenance-structured-item">
        <div class="maintenance-list-title">${escapeHtml(String(title))}</div>
        <div class="maintenance-structured-fields">
          ${fields.map(([key, value]) => renderStructuredKv(key, value)).join("")}
        </div>
      </div>
    `;
  }
  return `
    <div class="maintenance-structured-item">
      <div class="maintenance-list-title">Item ${idx + 1}</div>
      <div class="maintenance-list-detail">${escapeHtml(summarizeValue(item))}</div>
    </div>
  `;
}

function renderStructuredKv(key, value) {
  return `
    <div class="maintenance-structured-kv">
      <span class="maintenance-structured-key">${escapeHtml(String(key))}</span>
      <span class="maintenance-structured-value">${escapeHtml(summarizeValue(value))}</span>
    </div>
  `;
}

function renderTextualPayloadCard(title, payload) {
  return `
    <div class="textual-payload-card json-payload-card">
      <div class="maintenance-section-title">${escapeHtml(title)}</div>
      ${renderPayloadNavigator(payload, title)}
    </div>
  `;
}

function renderPayloadNavigator(payload, title = "payload") {
  if (payload === null || payload === undefined || payload === "") {
    return "<div class='empty-inline'>None</div>";
  }
  const textPreview = typeof payload === "string" ? payload : "";
  return `
    ${textPreview ? `<pre class="textual-pre json-text-preview">${escapeHtml(textPreview)}</pre>` : ""}
    <details class="json-navigator-panel" open>
      <summary>JSON Tree</summary>
      ${renderJsonTree(payload, title, 0)}
    </details>
  `;
}

function renderReadablePayload(payload) {
  if (payload === null || payload === undefined || payload === "") {
    return "<div class='empty-inline'>None</div>";
  }
  if (typeof payload === "string") {
    return `<pre class="textual-pre">${escapeHtml(payload)}</pre>`;
  }
  if (typeof payload === "number" || typeof payload === "boolean") {
    return `<div class="maintenance-note-box">${escapeHtml(String(payload))}</div>`;
  }
  if (Array.isArray(payload)) {
    if (!payload.length) return "<div class='empty-inline'>Empty list</div>";
    return `
      <div class="readable-list">
        ${payload.slice(0, 20).map((item, idx) => `
          <div class="readable-list-item">
            <span class="readable-list-index">${idx + 1}</span>
            <span>${renderReadableInline(item)}</span>
          </div>
        `).join("")}
        ${payload.length > 20 ? `<div class="maintenance-list-more">+ ${payload.length - 20} more in Debug Raw</div>` : ""}
      </div>
    `;
  }
  if (typeof payload === "object") {
    const entries = Object.entries(payload).filter(([, value]) => value !== undefined && value !== "");
    if (!entries.length) return "<div class='empty-inline'>Empty object</div>";
    return `
      <div class="readable-kv-list">
        ${entries.slice(0, 24).map(([key, value]) => `
          <div class="readable-kv-row">
            <div class="readable-kv-key">${escapeHtml(key)}</div>
            <div class="readable-kv-value">${renderReadableInline(value)}</div>
          </div>
        `).join("")}
        ${entries.length > 24 ? `<div class="maintenance-list-more">+ ${entries.length - 24} more fields in Debug Raw</div>` : ""}
      </div>
    `;
  }
  return `<div class="maintenance-note-box">${escapeHtml(String(payload))}</div>`;
}

function renderReadableInline(value) {
  if (value === null) return "<span class='empty-inline'>null</span>";
  if (value === undefined) return "<span class='empty-inline'>undefined</span>";
  if (typeof value === "string") {
    return value.length > 220 || value.includes("\n")
      ? `<pre class="textual-pre inline-pre">${escapeHtml(value)}</pre>`
      : `<span>${escapeHtml(value)}</span>`;
  }
  if (typeof value === "number" || typeof value === "boolean") return `<span>${escapeHtml(String(value))}</span>`;
  if (Array.isArray(value)) {
    if (!value.length) return "<span class='empty-inline'>[]</span>";
    return `<div class="chip-grid">${value.slice(0, 12).map((item) => `<span class="dep-chip">${escapeHtml(summarizeValue(item))}</span>`).join("")}${value.length > 12 ? `<span class="dep-chip">+${value.length - 12}</span>` : ""}</div>`;
  }
  if (typeof value === "object") {
    return renderReadablePayload(value);
  }
  return `<span>${escapeHtml(String(value))}</span>`;
}

function renderDebugRaw(title, payload) {
  return `
    <details class="maintenance-raw-details debug-raw-panel">
      <summary>${escapeHtml(title || "Debug Raw")}</summary>
      ${renderJsonTree(payload, "root", 0)}
    </details>
  `;
}

function renderJsonTree(value, key = "root", depth = 0) {
  if (value === null || value === undefined || typeof value !== "object") {
    return `
      <div class="json-tree-leaf depth-${Math.min(depth, 6)}">
        <span class="json-tree-key">${escapeHtml(String(key))}</span>
        <span class="json-tree-value ${jsonValueClass(value)}">${escapeHtml(formatJsonScalar(value))}</span>
      </div>
    `;
  }
  const isArray = Array.isArray(value);
  const entries = isArray ? value.map((item, idx) => [idx, item]) : Object.entries(value);
  const summary = `${key} ${isArray ? `[${entries.length}]` : `{${entries.length}}`}`;
  const open = depth < 2 ? " open" : "";
  return `
    <details class="json-tree-node depth-${Math.min(depth, 6)}"${open}>
      <summary>
        <span class="json-tree-key">${escapeHtml(String(summary))}</span>
      </summary>
      <div class="json-tree-children">
        ${entries.length ? entries.map(([childKey, childValue]) => renderJsonTree(childValue, childKey, depth + 1)).join("") : "<div class='empty-inline'>empty</div>"}
      </div>
    </details>
  `;
}

function formatJsonScalar(value) {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (typeof value === "string") return value;
  return String(value);
}

function jsonValueClass(value) {
  if (value === null || value === undefined) return "json-null";
  if (typeof value === "string") return "json-string";
  if (typeof value === "number") return "json-number";
  if (typeof value === "boolean") return "json-boolean";
  return "";
}

function extractQuestionText(item) {
  const question = item?.question || item?.context?.question || item?.context?.task_fragment?.question;
  if (Array.isArray(question)) {
    return question.map((msg) => msg?.content || "").filter(Boolean).join("\n");
  }
  if (typeof question === "string") return question;
  return "";
}

function renderToolCallList(title, calls) {
  return `
    <div class="tool-call-summary-panel">
      <div class="maintenance-section-title">${escapeHtml(title)}</div>
      ${calls.length ? calls.map((call, idx) => renderExpectedToolCall(call, idx)).join("") : "<div class='empty-inline'>None</div>"}
    </div>
  `;
}

function renderExpectedToolCall(call, idx) {
  return `
    <div class="expected-tool-call-card">
      <div class="execution-tool-head">
        <div>
          <span class="execution-call-index">#${idx + 1}</span>
          <span class="execution-tool-name">${escapeHtml(call.name || "unknown_tool")}</span>
        </div>
      </div>
      ${renderArgumentTable(call.arguments || {})}
    </div>
  `;
}

function renderForbiddenCallList(title, calls) {
  return `
    <div class="tool-call-summary-panel forbidden-call-panel">
      <div class="maintenance-section-title">${escapeHtml(title)}</div>
      ${calls.length ? calls.map((call) => `<span class="forbidden-call-chip">${escapeHtml(call.name || String(call))}</span>`).join("") : "<div class='empty-inline'>None</div>"}
    </div>
  `;
}

function renderInputArtifacts(artifacts) {
  const files = [];
  collectFileArtifacts(artifacts, "", files);
  if (!files.length) return renderReadablePayload(artifacts);
  return `
    <div class="fixture-file-list">
      ${files.map((file) => `
        <div class="fixture-file-card">
          <div class="fixture-file-path">${escapeHtml(file.path)}</div>
          <pre class="textual-pre">${escapeHtml(file.content || "")}</pre>
        </div>
      `).join("")}
    </div>
  `;
}

function collectFileArtifacts(value, path, out) {
  if (!value || typeof value !== "object") return;
  if (value.type === "file") {
    out.push({ path: path || "file", content: value.content || "" });
    return;
  }
  const contents = value.contents && typeof value.contents === "object" ? value.contents : value;
  Object.entries(contents || {}).forEach(([key, child]) => {
    const nextPath = path ? `${path}/${key}` : key;
    collectFileArtifacts(child, nextPath, out);
  });
}

function groupCaseRuns(runs) {
  const byCase = new Map();
  (runs || []).forEach((run) => {
    const id = run.case_id || "case";
    if (!byCase.has(id)) byCase.set(id, []);
    byCase.get(id).push(run);
  });
  return [...byCase.entries()].map(([caseId, items]) => ({ caseId, items }));
}

function renderCaseRunGroup(group) {
  return `
    <article class="case-run-group">
      <div class="bundle-case-head">
        <div>
          <div class="maintenance-stage-kicker">Unit Case</div>
          <div class="bundle-case-title">${escapeHtml(group.caseId)}</div>
        </div>
        <button class="btn chip-btn" onclick="jumpToBundleCase('${escapeJs(group.caseId)}')">Open Bundle Case</button>
      </div>
      <div class="case-run-variant-grid">
        ${group.items.map(renderCaseRunVariant).join("")}
      </div>
    </article>
  `;
}

function renderCaseRunVariant(run) {
  const callErrors = run.metadata?.call_errors || [];
  return `
    <div class="case-run-variant ${run.passed ? "variant-pass" : "variant-fail"}">
      <div class="case-run-variant-head">
        <span class="timeline-pill">${escapeHtml(run.variant || "variant")}</span>
        <span class="timeline-pill ${run.passed ? "success-pill" : "danger-pill"}">${escapeHtml(run.passed ? "passed" : "failed")}</span>
      </div>
      <div class="maintenance-metric-grid compact-metrics">
        ${metricMini("Validity", run.validity ?? "—")}
        ${metricMini("Accuracy", run.accuracy ?? "—")}
        ${metricMini("Tokens", run.tokens ?? "—")}
        ${metricMini("Steps", run.steps ?? "—")}
        ${metricMini("Call F1", run.metadata?.call_f1 ?? "—")}
        ${metricMini("Errors", callErrors.length)}
      </div>
      ${run.failure_summary ? `<div class="maintenance-note-box danger-row">${escapeHtml(run.failure_summary)}</div>` : ""}
      ${callErrors.length ? `
        <details class="maintenance-raw-details">
          <summary>Call Error Explanations</summary>
          ${callErrors.map((error) => renderCallErrorExplanation({ raw: error, detail: `turn ${error.turn_index ?? "?"}` }, { compact: true })).join("")}
        </details>
      ` : ""}
      ${renderCaseRunTrace(run)}
    </div>
  `;
}

function renderCaseRunTrace(run) {
  const trace = run.trace || {};
  const turns = normalizeTraceTurns(trace, run.metadata?.call_errors || []);
  if (!turns.length && !Object.keys(trace || {}).length) return "";
  return `
    <details class="maintenance-raw-details">
      <summary>Replay Trace (${escapeHtml(run.variant || "variant")})</summary>
      ${turns.length ? `
        <div class="execution-turn-list compact-trace">
          ${turns.map(renderExecutionTurn).join("")}
        </div>
      ` : "<div class='empty-inline'>No structured turns recorded.</div>"}
      ${renderDebugRaw("Raw Replay Trace", trace)}
    </details>
  `;
}

function normalizeTraceTurns(trace, callErrors) {
  const rawTurns = trace?.turns || [];
  const allCalls = trace?.tool_calls || [];
  const errors = (callErrors || []).map((error) => ({ raw: error, detail: `turn ${error.turn_index ?? "?"}` }));
  return (rawTurns || []).map((turn, idx) => {
    const turnIndex = turn.turn_index ?? idx;
    const calls = (turn.tool_calls || allCalls.filter((call) => Number(call.turn_index ?? -1) === Number(turnIndex)) || []);
    return {
      turnIndex,
      userMessages: turn.user_messages || [],
      calls,
      errors: errors.filter((error) => Number(error.raw?.turn_index ?? -1) === Number(turnIndex)),
      retrievedSkills: trace.retrieved_skills || [],
      promptInjectedSkills: trace.prompt_injected_skills || [],
    };
  });
}

function renderIntegrationFailureCard(failure) {
  const metrics = failure.metrics || {};
  const callErrors = metrics.call_errors || [];
  return `
    <article class="integration-failure-card">
      <div class="bundle-case-head">
        <div>
          <div class="maintenance-stage-kicker">Integration Failure</div>
          <div class="bundle-case-title">${escapeHtml(failure.case_id || failure.task_id || "failure")}</div>
        </div>
        <div class="timeline-pill-row">
          <span class="timeline-pill danger-pill">${escapeHtml(`${callErrors.length} call errors`)}</span>
          ${failure.case_id ? `<button class="btn chip-btn" onclick="jumpToBundleCase('${escapeJs(failure.case_id)}')">Open Bundle Case</button>` : ""}
        </div>
      </div>
      <div class="maintenance-metric-grid compact-metrics">
        ${metricMini("Official", metrics.official_valid ?? "—")}
        ${metricMini("Task Success", metrics.task_success ?? "—")}
        ${metricMini("Call F1", metrics.call_f1 ?? "—")}
        ${metricMini("Tokens", metrics.total_tokens ?? "—")}
        ${metricMini("Steps", metrics.n_model_steps ?? "—")}
        ${metricMini("Elapsed", metrics.elapsed_s ?? "—")}
      </div>
      ${failure.error ? `<div class="maintenance-note-box danger-row">${escapeHtml(failure.error)}</div>` : ""}
      ${callErrors.length ? `
        <div class="execution-diagnosis">
          <div class="maintenance-section-title">Call Error Explanations</div>
          ${callErrors.map((error) => renderCallErrorExplanation({ raw: error, detail: `turn ${error.turn_index ?? "?"}` }, { compact: true })).join("")}
        </div>
      ` : ""}
      ${renderDebugRaw("Raw Failure", failure)}
    </article>
  `;
}

function summarizeValue(value) {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (typeof value === "string") return compactMultiline(value, 180);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const preview = value.slice(0, 3).map((item) => summarizeValue(item)).join("; ");
    return `[${value.length}] ${preview}${value.length > 3 ? " ..." : ""}`;
  }
  if (typeof value === "object") {
    const keys = Object.keys(value);
    const preview = keys.slice(0, 5).map((key) => `${key}: ${summarizeValue(value[key])}`).join("; ");
    return `{${keys.length}} ${preview}${keys.length > 5 ? " ..." : ""}`;
  }
  return String(value);
}

function compactMultiline(value, maxLen) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (text.length <= maxLen) return text;
  return `${text.slice(0, Math.max(0, maxLen - 1))}…`;
}

function renderExecutorTraceBlock(detail, run = {}) {
  if (!detail || !detail.available) {
    return `
      <details class="trace-block maintenance-detail-block">
        <summary>Executor Trace</summary>
        <div class="maintenance-missing-detail">This result file only stores summary metrics; full executor trace is not available in the current artifact.</div>
      </details>
    `;
  }
  const timeline = buildExecutionTimeline(detail, run);
  const skillEvents = detail.skill_events || [];
  return `
    <section class="trace-block maintenance-detail-block execution-trace-block">
      <div class="maintenance-detail-summary">Execution Timeline</div>
      <div class="maintenance-metric-grid">
        ${metricMini("Turns", timeline.length)}
        ${metricMini("Tool Calls", (detail.tool_calls || []).length)}
        ${metricMini("Messages", detail.summary?.n_messages ?? 0)}
        ${metricMini("Skill Events", skillEvents.length)}
        ${metricMini("Errors", (run.call_errors || []).length)}
        ${metricMini("Call F1", run.call_f1 ?? "—")}
      </div>
      <div class="execution-timeline">
        ${timeline.map((turn) => renderExecutionTurn(turn)).join("")}
      </div>
      ${renderDetailBlock("Raw Trace JSON", detail.raw_trace || "", { open: false })}
    </section>
  `;
}

function buildExecutionTimeline(detail, run) {
  const turns = detail.turns || [];
  const toolCalls = detail.tool_calls || [];
  const callErrors = run.call_errors || [];
  const turnIndexes = new Set();
  turns.forEach((turn) => turnIndexes.add(Number(turn.turn_index ?? 0)));
  toolCalls.forEach((call) => turnIndexes.add(Number(call.turn_index ?? 0)));
  callErrors.forEach((error) => turnIndexes.add(Number(error.raw?.turn_index ?? parseTurnFromDetail(error.detail) ?? 0)));
  const sorted = [...turnIndexes].sort((a, b) => a - b);
  return sorted.map((turnIndex) => {
    const turn = turns.find((item) => Number(item.turn_index ?? 0) === turnIndex) || {};
    const callsFromTurn = Array.isArray(turn.tool_calls) ? turn.tool_calls : [];
    const calls = callsFromTurn.length
      ? callsFromTurn
      : toolCalls.filter((call) => Number(call.turn_index ?? 0) === turnIndex);
    const errors = callErrors.filter((error) => Number(error.raw?.turn_index ?? parseTurnFromDetail(error.detail) ?? 0) === turnIndex);
    return {
      turnIndex,
      userMessages: turn.user_messages || [],
      calls,
      errors,
      retrievedSkills: run.retrieved_skills || [],
      promptInjectedSkills: run.prompt_injected_skills || [],
    };
  });
}

function parseTurnFromDetail(detail) {
  const match = String(detail || "").match(/turn\s+(\d+)/i);
  return match ? Number(match[1]) : undefined;
}

function renderExecutionTurn(turn) {
  return `
    <section class="execution-turn-card">
      <div class="execution-turn-head">
        <div>
          <div class="maintenance-stage-kicker">Turn ${turn.turnIndex}</div>
          <div class="execution-user-text">${escapeHtml(turnUserText(turn.userMessages))}</div>
        </div>
        <div class="timeline-pill-row">
          <span class="timeline-pill">${escapeHtml(`${turn.calls.length} calls`)}</span>
          <span class="timeline-pill ${turn.errors.length ? "danger-pill" : ""}">${escapeHtml(`${turn.errors.length} errors`)}</span>
        </div>
      </div>
      <div class="execution-skill-context">
        <div>
          <div class="maintenance-section-title">Retrieved Skills</div>
          ${renderChipList(turn.retrievedSkills)}
        </div>
        <div>
          <div class="maintenance-section-title">Prompt Injected</div>
          ${renderChipList(turn.promptInjectedSkills)}
        </div>
      </div>
      <div class="execution-call-stack">
        ${turn.calls.length ? turn.calls.map((call, idx) => renderToolCallCard(call, idx, linkedErrorsForCall(call, turn.errors))).join("") : "<div class='empty-inline'>No tool calls in this turn</div>"}
      </div>
      ${turn.errors.length ? `
        <div class="execution-diagnosis">
          <div class="maintenance-section-title">Scorer Diagnosis</div>
          ${turn.errors.map((error) => renderCallErrorExplanation(error)).join("")}
        </div>
      ` : ""}
    </section>
  `;
}

function turnUserText(messages) {
  const text = (messages || []).map((msg) => msg?.content || "").join(" ").trim();
  return text || "No user message recorded for this turn.";
}

function linkedErrorsForCall(call, errors) {
  return (errors || []).filter((error) => {
    const raw = error.raw || {};
    if (raw.type === "extra_call") {
      return raw.actual_name === call.name && sameLooseJson(raw.actual_arguments || {}, call.arguments || {});
    }
    if (raw.type === "argument_mismatch") {
      return raw.name === call.name;
    }
    return false;
  });
}

function sameLooseJson(a, b) {
  try {
    return JSON.stringify(a || {}) === JSON.stringify(b || {});
  } catch (_err) {
    return false;
  }
}

function renderToolCallCard(call, idx, errors) {
  const hasError = errors.length > 0 || call.error;
  return `
    <article class="execution-tool-card ${hasError ? "tool-card-error" : ""}">
      <div class="execution-tool-head">
        <div>
          <span class="execution-call-index">#${idx + 1}</span>
          <span class="execution-tool-name">${escapeHtml(call.name || "unknown_tool")}</span>
        </div>
        <span class="timeline-pill ${hasError ? "danger-pill" : "success-pill"}">${escapeHtml(hasError ? "needs review" : "ok")}</span>
      </div>
      <div class="execution-tool-grid">
        <div>
          <div class="maintenance-section-title">Arguments</div>
          ${renderArgumentTable(call.arguments || {})}
        </div>
        <div>
          <div class="maintenance-section-title">Result</div>
          ${renderToolResult(call.result, call.error)}
        </div>
      </div>
      ${errors.length ? `<div class="execution-linked-errors">${errors.map((error) => renderCallErrorExplanation(error, { compact: true })).join("")}</div>` : ""}
      <details class="maintenance-raw-details">
        <summary>Raw tool call</summary>
        <pre class="maintenance-code-block">${escapeHtml(formatPayload(call))}</pre>
      </details>
    </article>
  `;
}

function renderArgumentTable(args) {
  const entries = Object.entries(args || {});
  if (!entries.length) return "<div class='empty-inline'>No arguments</div>";
  return `
    <div class="execution-arg-table">
      ${entries.map(([key, value]) => `
        <div class="execution-arg-row">
          <div class="execution-arg-key">${escapeHtml(key)}</div>
          <div class="execution-arg-value">${renderReadableValue(value)}</div>
        </div>
      `).join("")}
    </div>
  `;
}

function renderToolResult(result, error) {
  if (error) {
    return `<div class="maintenance-note-box danger-row">${escapeHtml(String(error))}</div>`;
  }
  if (result === null || result === undefined) return "<div class='empty-inline'>No result recorded</div>";
  if (typeof result === "object" && !Array.isArray(result)) {
    const entries = Object.entries(result);
    if (!entries.length) return "<div class='empty-inline'>Empty result</div>";
    return `
      <div class="execution-result-list">
        ${entries.map(([key, value]) => `
          <div class="execution-result-item">
            <div class="execution-arg-key">${escapeHtml(key)}</div>
            <div class="execution-arg-value">${renderReadableValue(value)}</div>
          </div>
        `).join("")}
      </div>
    `;
  }
  return `<div class="execution-result-item">${renderReadableValue(result)}</div>`;
}

function renderReadableValue(value) {
  if (value === null) return "<span class='empty-inline'>null</span>";
  if (value === undefined) return "<span class='empty-inline'>undefined</span>";
  if (typeof value === "string") {
    if (value.includes("\n") || value.length > 120) {
      return `<pre class="execution-value-block">${escapeHtml(value)}</pre>`;
    }
    return `<span>${escapeHtml(value)}</span>`;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return `<span>${escapeHtml(String(value))}</span>`;
  }
  if (Array.isArray(value)) {
    if (!value.length) return "<span class='empty-inline'>[]</span>";
    return `
      <div class="execution-array-list">
        ${value.slice(0, 20).map((item) => `<span class="execution-array-item">${escapeHtml(summarizeValue(item))}</span>`).join("")}
        ${value.length > 20 ? `<span class="execution-array-item">+ ${value.length - 20} more</span>` : ""}
      </div>
    `;
  }
  return `<pre class="execution-value-block">${escapeHtml(formatPayload(value))}</pre>`;
}

function renderCallErrorExplanation(error, options = {}) {
  const raw = error.raw || {};
  const compact = Boolean(options.compact);
  const explanation = callErrorExplanation(raw, error);
  return `
    <div class="execution-error-card ${compact ? "compact-error" : ""}">
      <div class="execution-error-title">${escapeHtml(explanation.title)}</div>
      <div class="execution-error-body">${escapeHtml(explanation.body)}</div>
      ${explanation.comparison ? renderExpectedActualComparison(explanation.comparison) : ""}
      <details class="maintenance-raw-details">
        <summary>Raw error</summary>
        <pre class="maintenance-code-block">${escapeHtml(formatPayload(error))}</pre>
      </details>
    </div>
  `;
}

function callErrorExplanation(raw, fallback) {
  const turn = raw.turn_index ?? parseTurnFromDetail(fallback.detail) ?? "?";
  if (raw.type === "extra_call") {
    return {
      title: `Extra tool call: ${raw.actual_name || "unknown"}`,
      body: `Turn ${turn} 中模型实际调用了 ${raw.actual_name || "unknown"}(${formatInlineArgs(raw.actual_arguments || {})})，但 expected calls 中没有对应调用，所以这是多余调用，会降低 precision / call_f1。`,
      comparison: { actual: raw.actual_arguments || {}, expected: null },
    };
  }
  if (raw.type === "missing_call") {
    return {
      title: `Missing tool call: ${raw.expected_name || "unknown"}`,
      body: `Turn ${turn} 中官方期望调用 ${raw.expected_name || "unknown"}，但模型没有调用它，所以这是漏调，会降低 recall / call_f1。`,
      comparison: { actual: null, expected: raw.expected_arguments || {} },
    };
  }
  if (raw.type === "argument_mismatch") {
    return {
      title: `Argument mismatch: ${raw.name || "unknown"}`,
      body: `Turn ${turn} 中模型调用了正确工具 ${raw.name || "unknown"}，但参数与 expected arguments 不一致。`,
      comparison: { actual: raw.actual_arguments || {}, expected: raw.expected_arguments || {} },
    };
  }
  return {
    title: fallback.label || raw.type || "Call error",
    body: `Turn ${turn} 中 scorer 记录了调用问题。当前 viewer 暂无该类型的专门解释，请查看 raw error。`,
    comparison: null,
  };
}

function formatInlineArgs(args) {
  const entries = Object.entries(args || {});
  if (!entries.length) return "{}";
  return entries.map(([key, value]) => `${key}=${summarizeValue(value)}`).join(", ");
}

function renderExpectedActualComparison(comparison) {
  return `
    <div class="execution-comparison">
      <div>
        <div class="maintenance-section-title">Expected</div>
        ${comparison.expected === null ? "<div class='empty-inline'>No expected call</div>" : renderArgumentTable(comparison.expected)}
      </div>
      <div>
        <div class="maintenance-section-title">Actual</div>
        ${comparison.actual === null ? "<div class='empty-inline'>No actual call</div>" : renderArgumentTable(comparison.actual)}
      </div>
    </div>
  `;
}

function formatPayload(payload) {
  if (typeof payload === "string") return payload;
  return JSON.stringify(payload ?? {}, null, 2);
}

function parseDocBlocks(markdownText) {
  const lines = String(markdownText || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let paragraph = [];
  let inCode = false;
  let codeLang = "";
  let codeLines = [];

  function flushParagraph() {
    if (!paragraph.length) return;
    const text = paragraph.join("\n").trim();
    if (text) blocks.push({ type: "markdown", text });
    paragraph = [];
  }

  function flushCode() {
    const text = codeLines.join("\n");
    blocks.push({
      type: codeLang === "mermaid" ? "mermaid" : "code",
      lang: codeLang,
      text,
    });
    codeLang = "";
    codeLines = [];
  }

  for (const line of lines) {
    const fence = line.match(/^```([A-Za-z0-9_-]+)?\s*$/);
    if (fence) {
      if (inCode) {
        flushCode();
        inCode = false;
      } else {
        flushParagraph();
        inCode = true;
        codeLang = String(fence[1] || "").trim().toLowerCase();
      }
      continue;
    }
    if (inCode) codeLines.push(line);
    else paragraph.push(line);
  }
  if (inCode) flushCode();
  flushParagraph();
  return blocks;
}

function renderDocBlock(block) {
  if (block.type === "mermaid") return renderMermaidBlock(block.text || "");
  if (block.type === "code") {
    return `
      <details class="trace-block maintenance-detail-block">
        <summary>${escapeHtml(block.lang || "code")}</summary>
        <pre class="maintenance-code-block">${escapeHtml(block.text || "")}</pre>
      </details>
    `;
  }
  return renderDocMarkdown(block.text || "");
}

function renderMermaidBlock(source) {
  const firstLine = String(source || "").split("\n").map((line) => line.trim()).find(Boolean) || "";
  if (firstLine === "sequenceDiagram") {
    return renderMermaidSequenceCard(source);
  }
  return `
    <section class="maintenance-mermaid-fallback">
      <div class="maintenance-stage-kicker">Mermaid</div>
      <div class="maintenance-stage-title">Unsupported Diagram Type</div>
      <div class="maintenance-stage-subtitle">${escapeHtml(firstLine || "unknown")}</div>
      <div class="maintenance-missing-detail">The current viewer renders sequence diagrams as visual cards. Other Mermaid diagram types are shown as source for now.</div>
      <details class="trace-block maintenance-detail-block">
        <summary>Diagram Source</summary>
        <pre class="maintenance-code-block">${escapeHtml(source)}</pre>
      </details>
    </section>
  `;
}

function renderDocMarkdown(text) {
  const lines = String(text || "").split("\n");
  const html = [];
  let listItems = [];

  function flushList() {
    if (!listItems.length) return;
    html.push(`<ul class="maintenance-doc-list">${listItems.join("")}</ul>`);
    listItems = [];
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      continue;
    }
    if (trimmed.startsWith("### ")) {
      flushList();
      html.push(`<h5 class="maintenance-doc-h3">${inlineMarkdown(trimmed.slice(4))}</h5>`);
      continue;
    }
    if (trimmed.startsWith("## ")) {
      flushList();
      html.push(`<h4 class="maintenance-doc-h2">${inlineMarkdown(trimmed.slice(3))}</h4>`);
      continue;
    }
    if (trimmed.startsWith("# ")) {
      flushList();
      html.push(`<h3 class="maintenance-doc-h1">${inlineMarkdown(trimmed.slice(2))}</h3>`);
      continue;
    }
    if (/^[-*]\s+/.test(trimmed)) {
      listItems.push(`<li>${inlineMarkdown(trimmed.replace(/^[-*]\s+/, ""))}</li>`);
      continue;
    }
    if (/^\d+\.\s+/.test(trimmed)) {
      listItems.push(`<li>${inlineMarkdown(trimmed.replace(/^\d+\.\s+/, ""))}</li>`);
      continue;
    }
    flushList();
    html.push(`<p class="maintenance-doc-p">${inlineMarkdown(trimmed)}</p>`);
  }
  flushList();
  return `<div class="maintenance-doc-markdown">${html.join("")}</div>`;
}

function inlineMarkdown(text) {
  let html = escapeHtml(String(text || ""));
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_m, label, href) => {
    return `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`;
  });
  return html;
}

function renderMermaidSequenceCard(source) {
  const parsed = parseSequenceDiagram(source);
  if (!parsed.ok) {
    return `
      <details class="trace-block maintenance-detail-block" open>
        <summary>Sequence Diagram Parse Error</summary>
        <div class="maintenance-missing-detail">${escapeHtml(parsed.error || "Failed to parse sequence diagram")}</div>
        <pre class="maintenance-code-block">${escapeHtml(source)}</pre>
      </details>
    `;
  }
  return `
    <section class="maintenance-seq-card">
      <div class="maintenance-seq-head">
        <div>
          <div class="maintenance-stage-kicker">Sequence Diagram</div>
          <div class="maintenance-stage-title">${escapeHtml(parsed.title || "Execution Timeline")}</div>
        </div>
        <span class="timeline-pill">${escapeHtml(`participants=${parsed.participants.length}`)}</span>
      </div>
      <div class="maintenance-seq-grid" style="grid-template-columns: repeat(${parsed.participants.length}, minmax(140px, 1fr));">
        ${parsed.participants.map((item) => `<div class="maintenance-seq-participant">${escapeHtml(item.label)}</div>`).join("")}
      </div>
      <div class="maintenance-seq-events">
        ${parsed.events.map((event) => renderSequenceEvent(event)).join("")}
      </div>
      <details class="trace-block maintenance-detail-block">
        <summary>Diagram Source</summary>
        <pre class="maintenance-code-block">${escapeHtml(source)}</pre>
      </details>
    </section>
  `;
}

function parseSequenceDiagram(source) {
  const lines = String(source || "").split("\n").map((line) => line.trim()).filter(Boolean);
  if (!lines.length || lines[0] !== "sequenceDiagram") {
    return { ok: false, error: "Only Mermaid sequenceDiagram blocks are supported right now." };
  }
  const participants = [];
  const participantMap = new Map();
  const events = [];
  const groupStack = [];

  function ensureParticipant(id, label) {
    const key = String(id || "").trim();
    if (!key) return;
    if (!participantMap.has(key)) {
      const item = { id: key, label: String(label || key).trim() || key };
      participantMap.set(key, item);
      participants.push(item);
    }
  }

  for (let i = 1; i < lines.length; i += 1) {
    const line = lines[i];
    const participantMatch = line.match(/^participant\s+([A-Za-z0-9_]+)(?:\s+as\s+(.+))?$/);
    if (participantMatch) {
      ensureParticipant(participantMatch[1], participantMatch[2] || participantMatch[1]);
      continue;
    }
    const groupMatch = line.match(/^(loop|alt|else|opt)\s+(.+)$/);
    if (groupMatch) {
      const kind = groupMatch[1];
      const label = groupMatch[2];
      if (kind === "else") {
        events.push({ type: "group-divider", kind, label, depth: Math.max(groupStack.length - 1, 0) });
      } else {
        events.push({ type: "group-start", kind, label, depth: groupStack.length });
        groupStack.push(kind);
      }
      continue;
    }
    if (line === "end") {
      const kind = groupStack.pop() || "group";
      events.push({ type: "group-end", kind, depth: groupStack.length });
      continue;
    }
    const arrowMatch = line.match(/^([A-Za-z0-9_]+)\s*(-{1,2}>{1,2}|-->>|->>|-->|->)\s*([A-Za-z0-9_]+)\s*:\s*(.+)$/);
    if (arrowMatch) {
      const from = arrowMatch[1];
      const arrow = arrowMatch[2];
      const to = arrowMatch[3];
      const label = arrowMatch[4];
      ensureParticipant(from, from);
      ensureParticipant(to, to);
      events.push({ type: "message", from, to, arrow, label, depth: groupStack.length });
      continue;
    }
    events.push({ type: "note", label: line, depth: groupStack.length });
  }

  if (!participants.length) {
    return { ok: false, error: "No participants found in sequence diagram." };
  }
  return {
    ok: true,
    title: inferSequenceTitle(events),
    participants,
    events,
  };
}

function inferSequenceTitle(events) {
  const firstGroup = events.find((item) => item.type === "group-start");
  if (firstGroup) return `${capitalize(firstGroup.kind)}: ${firstGroup.label}`;
  const firstMessage = events.find((item) => item.type === "message");
  return firstMessage ? firstMessage.label : "Execution Timeline";
}

function renderSequenceEvent(event) {
  if (event.type === "group-start") {
    return `<div class="maintenance-seq-group maintenance-seq-group-start depth-${event.depth}"><span>${escapeHtml(capitalize(event.kind))}: ${escapeHtml(event.label)}</span></div>`;
  }
  if (event.type === "group-divider") {
    return `<div class="maintenance-seq-group maintenance-seq-group-divider depth-${event.depth}"><span>${escapeHtml(capitalize(event.kind))}: ${escapeHtml(event.label)}</span></div>`;
  }
  if (event.type === "group-end") {
    return `<div class="maintenance-seq-group maintenance-seq-group-end depth-${event.depth}"><span>End</span></div>`;
  }
  if (event.type === "note") {
    return `<div class="maintenance-seq-note depth-${event.depth}">${escapeHtml(event.label)}</div>`;
  }
  const dashed = String(event.arrow || "").includes("--");
  return `
    <div class="maintenance-seq-event depth-${event.depth}">
      <div class="maintenance-seq-event-meta">
        <span class="maintenance-seq-from">${escapeHtml(event.from)}</span>
        <span class="maintenance-seq-arrow ${dashed ? "dashed" : "solid"}">${escapeHtml(event.arrow)}</span>
        <span class="maintenance-seq-to">${escapeHtml(event.to)}</span>
      </div>
      <div class="maintenance-seq-label">${escapeHtml(event.label)}</div>
    </div>
  `;
}

function capitalize(value) {
  const text = String(value || "");
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function boardToneClass(tone) {
  const mapping = {
    success: "board-success",
    danger: "board-danger",
    warning: "board-warning",
    accent: "board-accent",
    neutral: "board-neutral",
  };
  return mapping[tone] || "board-neutral";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeJs(value) {
  return String(value ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/'/g, "\\'");
}

function compactLabel(value, maxLen) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!maxLen || text.length <= maxLen) return text;
  return `${text.slice(0, Math.max(0, maxLen - 1))}…`;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function domIdForCase(caseId) {
  return `case-${String(caseId || "").replace(/[^A-Za-z0-9_-]+/g, "-")}`;
}
