const docsState = {
  docs: [],
  activeDocId: "",
  activeSectionId: "",
  loadError: "",
};

window.docsState = docsState;
window.addEventListener("error", (event) => {
  showDocsFatalError(event.error?.stack || event.message || "Unknown frontend error");
});
window.addEventListener("unhandledrejection", (event) => {
  showDocsFatalError(event.reason?.stack || event.reason?.message || String(event.reason || "Unknown promise rejection"));
});
document.addEventListener("DOMContentLoaded", loadMaintenanceDocs);

async function loadMaintenanceDocs() {
  try {
    const res = await fetch("/api/maintenance/docs");
    const payload = await res.json();
    if (payload.error) throw new Error(payload.error);
    docsState.docs = payload.docs || [];
    docsState.activeDocId = docsState.docs[0]?.id || "";
    docsState.activeSectionId = "";
    docsState.loadError = "";
  } catch (err) {
    docsState.docs = [];
    docsState.loadError = String(err?.message || err || "Failed to load docs");
  }
  try {
    renderDocsApp();
  } catch (err) {
    showDocsFatalError(err?.stack || err?.message || String(err));
  }
}

function showDocsFatalError(message) {
  const title = document.getElementById("docs-title");
  const chips = document.getElementById("docs-section-chips");
  const content = document.getElementById("docs-content");
  if (title) title.textContent = "Documentation Render Error";
  if (chips) chips.innerHTML = "";
  if (content) {
    content.innerHTML = `
      <section class="gradio-doc-card">
        <div class="maintenance-stage-kicker">Frontend Error</div>
        <div class="maintenance-missing-detail">文档数据已加载失败或前端渲染异常。下面是可复制的错误信息。</div>
        <pre class="maintenance-code-block">${escapeHtml(message)}</pre>
      </section>
    `;
  }
}

function renderDocsApp() {
  renderDocsNav();
  renderDocsContent();
}

function renderDocsNav() {
  const mount = document.getElementById("docs-nav");
  if (!mount) return;
  if (docsState.loadError) {
    mount.innerHTML = `<div class="maintenance-missing-detail">${escapeHtml(docsState.loadError)}</div>`;
    return;
  }
  mount.innerHTML = (docsState.docs || []).map((doc) => `
    <button class="docs-nav-item ${doc.id === docsState.activeDocId ? "active" : ""}" onclick="selectDoc('${escapeJs(doc.id)}')">
      <span>${escapeHtml(doc.title || doc.id)}</span>
      <small>${escapeHtml(doc.kind || "reference")}</small>
    </button>
  `).join("");
}

function selectDoc(docId) {
  docsState.activeDocId = docId;
  docsState.activeSectionId = "";
  renderDocsApp();
}

function selectSection(sectionId) {
  docsState.activeSectionId = sectionId;
  renderDocsContent();
  const target = document.getElementById(sectionId);
  if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
}

function activeDoc() {
  return docsState.docs.find((doc) => doc.id === docsState.activeDocId) || docsState.docs[0] || null;
}

function renderDocsContent() {
  const doc = activeDoc();
  const title = document.getElementById("docs-title");
  const chips = document.getElementById("docs-section-chips");
  const content = document.getElementById("docs-content");
  if (!title || !chips || !content) return;
  if (!doc) {
    title.textContent = "No docs";
    chips.innerHTML = "";
    content.innerHTML = "<div class='timeline-empty'>No documentation found.</div>";
    return;
  }
  const sections = splitMarkdownSections(doc.text || "");
  const safeSections = sections.length ? sections : [{
    id: "doc-section-0",
    title: doc.title || "Document",
    level: 1,
    text: doc.text || "",
    lines: String(doc.text || "").split("\n"),
  }];
  title.textContent = doc.title || doc.id;
  chips.innerHTML = safeSections.map((section, idx) => `
    <button class="btn chip-btn ${section.id === docsState.activeSectionId || (!docsState.activeSectionId && idx === 0) ? "active-toggle" : ""}" onclick="selectSection('${escapeJs(section.id)}')">
      ${escapeHtml(section.title)}
    </button>
  `).join("");
  content.innerHTML = `
    <section class="docs-hero-card">
      <div>
        <div class="maintenance-stage-kicker">${escapeHtml(doc.kind || "reference")}</div>
        <div class="maintenance-stage-title">${escapeHtml(doc.title || doc.id)}</div>
        <div class="maintenance-stage-subtitle">${escapeHtml(doc.path || "")}</div>
      </div>
      <span class="timeline-pill">${escapeHtml(`${safeSections.length} sections`)}</span>
    </section>
    ${safeSections.map(renderDocSection).join("")}
  `;
}

function splitMarkdownSections(text) {
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  const sections = [];
  let current = { title: "Overview", level: 1, lines: [] };
  for (const line of lines) {
    const match = line.match(/^(#{1,2})\s+(.+?)\s*$/);
    if (match && current.lines.length) {
      sections.push(current);
      current = { title: match[2], level: match[1].length, lines: [line] };
      continue;
    }
    if (match && !current.lines.length) {
      current.title = match[2];
      current.level = match[1].length;
    }
    current.lines.push(line);
  }
  if (current.lines.length) sections.push(current);
  return sections.map((section, idx) => ({
    ...section,
    id: `doc-section-${idx}`,
    text: section.lines.join("\n").trim(),
  }));
}

function renderDocSection(section) {
  const blocks = parseDocBlocks(section.text || "");
  const safeBlocks = blocks.length ? blocks : [{ type: "markdown", text: section.text || "" }];
  return `
    <article id="${escapeHtml(section.id)}" class="gradio-doc-section">
      <div class="gradio-doc-section-head">
        <div>
          <div class="maintenance-stage-kicker">Section</div>
          <div class="maintenance-stage-title">${escapeHtml(section.title)}</div>
        </div>
        <span class="timeline-pill">${escapeHtml(`blocks=${safeBlocks.length}`)}</span>
      </div>
      <div class="gradio-block-grid">
        ${safeBlocks.map(renderDocBlockSafe).join("")}
      </div>
    </article>
  `;
}

function renderDocBlockSafe(block) {
  try {
    return renderDocBlock(block);
  } catch (err) {
    return `
      <section class="gradio-doc-card">
        <div class="maintenance-stage-kicker">Block Render Error</div>
        <pre class="maintenance-code-block">${escapeHtml(err?.stack || err?.message || String(err))}</pre>
        <details class="maintenance-raw-details">
          <summary>Raw Block</summary>
          <pre class="maintenance-code-block">${escapeHtml(JSON.stringify(block, null, 2))}</pre>
        </details>
      </section>
    `;
  }
}

function parseDocBlocks(markdownText) {
  const lines = String(markdownText || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let paragraph = [];
  let inCode = false;
  let codeLang = "";
  let codeLines = [];

  function flushParagraph() {
    const text = paragraph.join("\n").trim();
    if (text) blocks.push({ type: "markdown", text });
    paragraph = [];
  }

  function flushCode() {
    blocks.push({ type: codeLang === "mermaid" ? "mermaid" : "code", lang: codeLang, text: codeLines.join("\n") });
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
      <details class="gradio-doc-card code-card" open>
        <summary>${escapeHtml(block.lang || "code")}</summary>
        <pre class="maintenance-code-block">${escapeHtml(block.text || "")}</pre>
      </details>
    `;
  }
  return `<section class="gradio-doc-card">${renderDocMarkdown(block.text || "")}</section>`;
}

function renderMermaidBlock(source) {
  const firstLine = String(source || "").split("\n").map((line) => line.trim()).find(Boolean) || "";
  if (firstLine !== "sequenceDiagram") {
    return `
      <details class="gradio-doc-card code-card" open>
        <summary>Mermaid Source</summary>
        <pre class="maintenance-code-block">${escapeHtml(source)}</pre>
      </details>
    `;
  }
  const parsed = parseSequenceDiagram(source);
  if (!parsed.ok) {
    return `<pre class="maintenance-code-block">${escapeHtml(source)}</pre>`;
  }
  return `
    <section class="gradio-doc-card sequence-doc-card">
      <div class="maintenance-stage-kicker">Sequence Diagram</div>
      <div class="sequence-doc-lanes">${parsed.participants.map((item) => `<span>${escapeHtml(item.label)}</span>`).join("")}</div>
      <div class="sequence-doc-steps">
        ${parsed.messages.map((msg, idx) => `
          <div class="sequence-doc-step">
            <span class="readable-list-index">${idx + 1}</span>
            <strong>${escapeHtml(msg.from)}</strong>
            <span>${escapeHtml(msg.arrow)}</span>
            <strong>${escapeHtml(msg.to)}</strong>
            <em>${escapeHtml(msg.text)}</em>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function parseSequenceDiagram(source) {
  const participants = [];
  const messages = [];
  for (const raw of String(source || "").split("\n")) {
    const line = raw.trim();
    if (!line || line === "sequenceDiagram") continue;
    const p = line.match(/^participant\s+([A-Za-z0-9_]+)\s+as\s+(.+)$/);
    if (p) {
      participants.push({ id: p[1], label: p[2] });
      continue;
    }
    const m = line.match(/^([A-Za-z0-9_]+)\s*([-.]+>>?)\s*([A-Za-z0-9_]+)\s*:\s*(.*)$/);
    if (m) {
      messages.push({ from: m[1], arrow: m[2], to: m[3], text: m[4] });
    }
  }
  return { ok: true, participants, messages };
}

function renderDocMarkdown(text) {
  const lines = String(text || "").split("\n");
  const html = [];
  let listItems = [];
  let table = [];

  function flushList() {
    if (!listItems.length) return;
    html.push(`<ul class="maintenance-doc-list">${listItems.join("")}</ul>`);
    listItems = [];
  }

  function flushTable() {
    if (!table.length) return;
    html.push(renderMarkdownTable(table));
    table = [];
  }

  for (const rawLine of lines) {
    const trimmed = rawLine.trim();
    if (!trimmed) {
      flushList();
      flushTable();
      continue;
    }
    if (isMarkdownTableRow(trimmed)) {
      table.push(trimmed);
      continue;
    }
    flushTable();
    if (trimmed.startsWith("### ")) {
      flushList();
      html.push(`<h5 class="maintenance-doc-h3">${inlineMarkdown(trimmed.slice(4))}</h5>`);
    } else if (trimmed.startsWith("## ")) {
      flushList();
      html.push(`<h4 class="maintenance-doc-h2">${inlineMarkdown(trimmed.slice(3))}</h4>`);
    } else if (trimmed.startsWith("# ")) {
      flushList();
      html.push(`<h3 class="maintenance-doc-h1">${inlineMarkdown(trimmed.slice(2))}</h3>`);
    } else if (/^[-*]\s+/.test(trimmed)) {
      listItems.push(`<li>${inlineMarkdown(trimmed.replace(/^[-*]\s+/, ""))}</li>`);
    } else if (/^\d+\.\s+/.test(trimmed)) {
      listItems.push(`<li>${inlineMarkdown(trimmed.replace(/^\d+\.\s+/, ""))}</li>`);
    } else {
      flushList();
      html.push(`<p class="maintenance-doc-p">${inlineMarkdown(trimmed)}</p>`);
    }
  }
  flushList();
  flushTable();
  return `<div class="maintenance-doc-markdown">${html.join("")}</div>`;
}

function isMarkdownTableRow(line) {
  if (!line.includes("|")) return false;
  if (/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(line)) return true;
  const cells = line.replace(/^\|/, "").replace(/\|$/, "").split("|");
  return cells.length >= 2 && cells.every((cell) => cell.trim().length > 0);
}

function renderMarkdownTable(rows) {
  const cleanRows = rows.filter((row) => !/^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$/.test(row));
  if (!cleanRows.length) return "";
  const cells = cleanRows.map((row) => row.replace(/^\|/, "").replace(/\|$/, "").split("|").map((item) => item.trim()));
  const [head, ...body] = cells;
  return `
    <div class="doc-table-wrap">
      <table class="doc-table">
        <thead><tr>${head.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("")}</tr></thead>
        <tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${inlineMarkdown(cell)}</td>`).join("")}</tr>`).join("")}</tbody>
      </table>
    </div>
  `;
}

function inlineMarkdown(text) {
  let html = escapeHtml(String(text || ""));
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_m, label, href) => {
    return `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`;
  });
  return html;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeJs(value) {
  return String(value ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/'/g, "\\'")
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r");
}
