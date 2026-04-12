/* ── Skill Explorer — Client-side Logic ─────────────────────────────── */

let allSkills = [];
let currentSkill = null;
let currentLib = "";

// ── Init ──────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  await loadLibraries();
  document.getElementById("search").addEventListener("input", renderList);
  document.getElementById("sort-by").addEventListener("change", renderList);
  document.getElementById("btn-run-test").addEventListener("click", runTest);
  document.getElementById("btn-run-custom").addEventListener("click", runCustom);
  document.getElementById("lib-select").addEventListener("change", switchLibrary);
});

// ── Libraries ─────────────────────────────────────────────────────────

async function loadLibraries() {
  const res = await fetch("/api/libraries");
  const libs = await res.json();
  const select = document.getElementById("lib-select");
  select.innerHTML = libs.map(l =>
    `<option value="${l.id}">${l.name} (${l.skill_count} skills)</option>`
  ).join("");
  if (libs.length > 0) {
    currentLib = libs[0].id;
    select.value = currentLib;
    await switchLibrary();
  }
}

async function switchLibrary() {
  currentLib = document.getElementById("lib-select").value;
  currentSkill = null;
  document.getElementById("detail-placeholder").style.display = "flex";
  document.getElementById("detail-content").style.display = "none";
  await Promise.all([loadStats(), loadSkills(), loadGraph()]);
}

// ── Load Stats ────────────────────────────────────────────────────────

async function loadStats() {
  const res = await fetch(`/api/stats?lib=${currentLib}`);
  const s = await res.json();
  const bar = document.getElementById("stats-bar");
  bar.innerHTML = `
    <div class="stat-chip"><strong>${s.total_skills}</strong> Skills</div>
    <div class="stat-chip"><strong>${s.used_skills}</strong> Used</div>
    <div class="stat-chip"><strong>${s.skills_with_deps}</strong> Composite</div>
    <div class="stat-chip"><strong>${s.total_usage}</strong> Total Uses</div>
    <div class="stat-chip"><strong>${(s.avg_success_rate * 100).toFixed(0)}%</strong> Avg Success</div>
  `;
}

// ── Load & Render Skill List ──────────────────────────────────────────

async function loadSkills() {
  const res = await fetch(`/api/skills?lib=${currentLib}`);
  allSkills = await res.json();
  renderList();
}

function renderList() {
  const q = document.getElementById("search").value.toLowerCase();
  const sort = document.getElementById("sort-by").value;

  let filtered = allSkills.filter(s =>
    !q || s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q)
  );

  filtered.sort((a, b) => {
    if (sort === "usage") return b.usage_count - a.usage_count;
    if (sort === "success") {
      const ra = a.usage_count ? a.success_count / a.usage_count : -1;
      const rb = b.usage_count ? b.success_count / b.usage_count : -1;
      return rb - ra;
    }
    if (sort === "deps") return b.dependencies.length - a.dependencies.length;
    return a.name.localeCompare(b.name);
  });

  const ul = document.getElementById("skill-list");
  ul.innerHTML = filtered.map(s => {
    const rate = s.usage_count ? `${((s.success_count / s.usage_count) * 100).toFixed(0)}%` : "—";
    const deps = s.dependencies.length ? `📦${s.dependencies.length}` : "";
    const active = currentSkill === s.name ? "active" : "";
    return `
      <li class="${active}" onclick="selectSkill('${s.name}')">
        <div class="skill-name">${s.name}</div>
        <div class="skill-meta">v${s.version} · 📊${s.usage_count} uses · ✅${rate} ${deps}</div>
        <div class="skill-desc">${s.description}</div>
      </li>`;
  }).join("");
}

// ── Select & Show Skill Detail ────────────────────────────────────────

async function selectSkill(name) {
  currentSkill = name;
  renderList(); // update active state

  const res = await fetch(`/api/skills/${name}?lib=${currentLib}`);
  const s = await res.json();

  document.getElementById("detail-placeholder").style.display = "none";
  document.getElementById("detail-content").style.display = "block";

  document.getElementById("detail-name").textContent = s.name;
  document.getElementById("detail-version").textContent = `v${s.version}`;
  document.getElementById("detail-desc").textContent = s.description || "";

  // Meta
  document.getElementById("meta-usage").textContent = s.usage_count || 0;
  document.getElementById("meta-success").textContent = s.success_count || 0;
  const rate = s.usage_count ? `${((s.success_count / s.usage_count) * 100).toFixed(0)}%` : "—";
  const rateEl = document.getElementById("meta-rate");
  rateEl.textContent = rate;
  rateEl.style.color = rate === "—" ? "" : 
    (s.success_count / s.usage_count >= 0.8 ? "var(--green)" : 
     s.success_count / s.usage_count >= 0.5 ? "var(--yellow)" : "var(--red)");
  document.getElementById("meta-deps").textContent = (s.dependencies || []).length;

  // Dependencies
  const depsSection = document.getElementById("deps-section");
  const deps = s.dependencies || [];
  if (deps.length) {
    depsSection.style.display = "block";
    document.getElementById("deps-list").innerHTML = deps.map(d =>
      `<span class="dep-chip" onclick="selectSkill('${d}')">${d}</span>`
    ).join("");
  } else {
    depsSection.style.display = "none";
  }

  // Source problem
  const srcSection = document.getElementById("source-section");
  const problems = s.source_problems || [];
  if (problems.length) {
    srcSection.style.display = "block";
    document.getElementById("source-problem").textContent = problems[0];
  } else {
    srcSection.style.display = "none";
  }

  // Code
  const codeEl = document.getElementById("detail-code");
  codeEl.textContent = s.code || "";
  hljs.highlightElement(codeEl);

  // Test code
  const testSection = document.getElementById("test-section");
  const testEl = document.getElementById("detail-test");
  if (s.test_code) {
    testSection.style.display = "block";
    testEl.textContent = s.test_code;
    hljs.highlightElement(testEl);
  } else {
    testSection.style.display = "none";
  }

  // Reset output & custom code
  document.getElementById("run-output").textContent = "";
  document.getElementById("run-output").className = "run-output";
  document.getElementById("custom-code").value = "";
}

// ── Run Skill ─────────────────────────────────────────────────────────

async function runTest() {
  if (!currentSkill) return;
  await runCode("");
}

async function runCustom() {
  if (!currentSkill) return;
  const code = document.getElementById("custom-code").value;
  await runCode(code);
}

async function runCode(code) {
  const btn = code ? document.getElementById("btn-run-custom") : document.getElementById("btn-run-test");
  btn.textContent = "⏳ Running...";
  btn.disabled = true;

  try {
    const res = await fetch(`/api/skills/${currentSkill}/run?lib=${currentLib}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    const result = await res.json();

    const outputSection = document.getElementById("output-section");
    const outputEl = document.getElementById("run-output");
    outputSection.style.display = "block";
    outputEl.textContent = result.output;
    outputEl.className = "run-output " + (result.success ? "success" : "error");
    outputSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    const outputSection = document.getElementById("output-section");
    const outputEl = document.getElementById("run-output");
    outputSection.style.display = "block";
    outputEl.textContent = `Network error: ${e.message}`;
    outputEl.className = "run-output error";
    outputSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } finally {
    btn.textContent = "▶ Run";
    btn.disabled = false;
  }
}

// ── Interactive Dependency Graph (SVG) ─────────────────────────────────

async function loadGraph() {
  const res = await fetch(`/api/graph?lib=${currentLib}`);
  const { nodes, edges } = await res.json();

  // Also fetch full details for tooltip
  const detailsRes = await fetch(`/api/skills?lib=${currentLib}`);
  const allDetails = await detailsRes.json();
  const detailMap = {};
  allDetails.forEach(s => detailMap[s.name] = s);

  const container = document.getElementById("graph-container");
  const svg = document.getElementById("graph-svg");
  const tooltip = document.getElementById("graph-tooltip");
  const W = container.offsetWidth;
  const H = 500;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);

  // Only show connected nodes
  const connected = new Set();
  edges.forEach(e => { connected.add(e.source); connected.add(e.target); });
  const graphNodes = nodes.filter(n => connected.has(n.id));
  const isolatedNodes = nodes.filter(n => !connected.has(n.id));

  if (graphNodes.length === 0) {
    svg.innerHTML = `<text x="${W/2}" y="${H/2}" fill="#8b949e" text-anchor="middle" font-size="14">No dependency edges found.</text>`;
    return;
  }

  // Layout: force-directed simulation
  const pos = {};
  graphNodes.forEach((n, i) => {
    const angle = (2 * Math.PI * i) / graphNodes.length;
    pos[n.id] = {
      x: W / 2 + W * 0.3 * Math.cos(angle),
      y: H / 2 + H * 0.3 * Math.sin(angle),
      vx: 0, vy: 0,
    };
  });

  // Run simulation
  for (let iter = 0; iter < 200; iter++) {
    const dt = 0.3;
    // Repulsion
    for (let i = 0; i < graphNodes.length; i++) {
      for (let j = i + 1; j < graphNodes.length; j++) {
        const a = pos[graphNodes[i].id], b = pos[graphNodes[j].id];
        const dx = a.x - b.x, dy = a.y - b.y;
        const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
        const force = 8000 / (dist * dist);
        const fx = (dx / dist) * force * dt;
        const fy = (dy / dist) * force * dt;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
    }
    // Attraction along edges
    edges.forEach(e => {
      const a = pos[e.source], b = pos[e.target];
      if (!a || !b) return;
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const force = (dist - 160) * 0.005 * dt;
      a.vx += (dx / dist) * force;
      a.vy += (dy / dist) * force;
      b.vx -= (dx / dist) * force;
      b.vy -= (dy / dist) * force;
    });
    // Center gravity + damping
    graphNodes.forEach(n => {
      const p = pos[n.id];
      p.vx += (W / 2 - p.x) * 0.003 * dt;
      p.vy += (H / 2 - p.y) * 0.003 * dt;
      p.vx *= 0.9; p.vy *= 0.9;
      p.x += p.vx; p.y += p.vy;
      p.x = Math.max(100, Math.min(W - 100, p.x));
      p.y = Math.max(50, Math.min(H - 50, p.y));
    });
  }

  // Build SVG
  let svgContent = '<defs>';
  svgContent += '<marker id="arrowhead" viewBox="0 0 10 7" refX="10" refY="3.5" markerWidth="8" markerHeight="6" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="#58a6ff"/></marker>';
  svgContent += '</defs>';

  // Edges
  edges.forEach(e => {
    const a = pos[e.source], b = pos[e.target];
    if (!a || !b) return;
    const nodeR = 12;
    const dx = b.x - a.x, dy = b.y - a.y;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const tx = b.x - (dx / dist) * (nodeR + 4);
    const ty = b.y - (dy / dist) * (nodeR + 4);
    svgContent += `<line x1="${a.x}" y1="${a.y}" x2="${tx}" y2="${ty}" 
      stroke="#30363d" stroke-width="2" marker-end="url(#arrowhead)"
      class="graph-edge" data-source="${e.source}" data-target="${e.target}"/>`;
  });

  // Nodes
  graphNodes.forEach(n => {
    const p = pos[n.id];
    const r = 10 + Math.min(n.usage, 12) * 0.8;
    const rate = n.usage ? n.success / n.usage : 0.5;
    const color = rate >= 0.8 ? "#3fb950" : rate >= 0.5 ? "#d29922" : "#f85149";
    const shortName = n.id.length > 22 ? n.id.substring(0, 20) + "…" : n.id;

    svgContent += `<g class="graph-node" data-name="${n.id}" style="cursor:pointer">
      <circle cx="${p.x}" cy="${p.y}" r="${r}" fill="${color}" stroke="#e6edf3" stroke-width="1.5" 
        opacity="0.9"/>
      <text x="${p.x}" y="${p.y + r + 16}" fill="#e6edf3" text-anchor="middle" 
        font-size="11" font-family="monospace" 
        style="paint-order: stroke; stroke: #0d1117; stroke-width: 3px; stroke-linecap: round; stroke-linejoin: round;">${shortName}</text>
    </g>`;
  });

  // Isolated count
  if (isolatedNodes.length) {
    svgContent += `<text x="${W - 20}" y="${H - 15}" fill="#8b949e" text-anchor="end" font-size="12">
      + ${isolatedNodes.length} isolated skills (no dependencies)</text>`;
  }

  svg.innerHTML = svgContent;

  // Interactive: hover tooltip + click
  svg.querySelectorAll(".graph-node").forEach(g => {
    const name = g.dataset.name;
    const circle = g.querySelector("circle");

    g.addEventListener("mouseenter", (evt) => {
      circle.setAttribute("stroke-width", "3");
      circle.setAttribute("stroke", "#58a6ff");
      // Highlight connected edges
      svg.querySelectorAll(".graph-edge").forEach(edge => {
        if (edge.dataset.source === name || edge.dataset.target === name) {
          edge.setAttribute("stroke", "#58a6ff");
          edge.setAttribute("stroke-width", "3");
        }
      });

      const d = detailMap[name] || {};
      const rate = d.usage_count ? `${((d.success_count / d.usage_count) * 100).toFixed(0)}%` : "—";
      tooltip.innerHTML = `
        <div class="tt-name">${name}</div>
        <div class="tt-desc">${d.description || ""}</div>
        <div class="tt-stats">📊 ${d.usage_count || 0} uses · ✅ ${rate} · 📦 ${(d.dependencies || []).length} deps</div>
      `;
      tooltip.classList.add("visible");

      const rect = container.getBoundingClientRect();
      tooltip.style.left = (evt.clientX - rect.left + 15) + "px";
      tooltip.style.top = (evt.clientY - rect.top - 10) + "px";
    });

    g.addEventListener("mousemove", (evt) => {
      const rect = container.getBoundingClientRect();
      const x = evt.clientX - rect.left + 15;
      const y = evt.clientY - rect.top - 10;
      tooltip.style.left = Math.min(x, W - 300) + "px";
      tooltip.style.top = Math.min(y, H - 80) + "px";
    });

    g.addEventListener("mouseleave", () => {
      circle.setAttribute("stroke-width", "1.5");
      circle.setAttribute("stroke", "#e6edf3");
      svg.querySelectorAll(".graph-edge").forEach(edge => {
        edge.setAttribute("stroke", "#30363d");
        edge.setAttribute("stroke-width", "2");
      });
      tooltip.classList.remove("visible");
    });

    g.addEventListener("click", () => {
      selectSkill(name);
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  });
}
