/* drp editor frontend - vanilla JS, no build step. */

"use strict";

// ---------- state ----------

const state = {
  project: null,       // /api/project payload
  view: "",            // "" = all clips, timeline token, "unattached", "media"
  clips: [],           // current table rows
  selected: new Set(), // selected tokens
  detailToken: null,   // token shown in the details panel
  search: "",
};

// ---------- helpers ----------

const $ = (sel) => document.querySelector(sel);

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

function toast(message, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 6000 : 3200);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return res.json();
}

const postJSON = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

// ---------- project lifecycle ----------

async function openByPath() {
  const path = $("#path-input").value.trim();
  if (!path) return toast("Enter a path to a .drp file first", "error");
  await withBusy(async () => {
    state.project = await postJSON("/api/open", { path });
    onProjectOpened();
  });
}

async function uploadFile(file) {
  const form = new FormData();
  form.append("file", file);
  await withBusy(async () => {
    state.project = await api("/api/upload", { method: "POST", body: form });
    onProjectOpened();
  });
}

async function withBusy(fn) {
  document.body.style.cursor = "progress";
  try {
    await fn();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    document.body.style.cursor = "";
  }
}

function onProjectOpened() {
  state.view = "";
  state.selected.clear();
  state.detailToken = null;
  state.search = "";
  $("#search-input").value = "";
  $("#layout").classList.remove("hidden");
  $("#empty-state").classList.add("hidden");
  closeDetails();
  renderAll();
  loadClips();
  toast(`Opened "${state.project.name}"`, "ok");
}

async function refreshProject() {
  state.project = await api("/api/project");
  renderAll();
}

// ---------- rendering: sidebar + status ----------

function renderAll() {
  renderProjectCard();
  renderValidation();
  renderNav();
  renderStatusBar();
  const hasProject = Boolean(state.project);
  $("#save-btn").disabled = !hasProject;
  $("#download-btn").classList.toggle("disabled", !hasProject);
  $("#undo-btn").disabled = !hasProject || state.project.patches.length === 0;
}

function renderProjectCard() {
  const p = state.project;
  $("#project-card").innerHTML = `
    <div class="project-name">${esc(p.name)}</div>
    <div class="project-path">${esc(p.source_path || "(uploaded)")}</div>
    <div class="stat-row"><span>Timelines</span><span>${p.timelines.length}</span></div>
    <div class="stat-row"><span>Clips</span><span>${p.clip_count}</span></div>
    <div class="stat-row"><span>Media items</span><span>${p.media.length}</span></div>
    <div class="stat-row"><span>Archive members</span><span>${p.archive_members.length}</span></div>`;
}

function renderValidation() {
  const issues = state.project.validation;
  const card = $("#validation-card");
  if (!issues.length) {
    card.innerHTML = `<h3>Validation</h3>
      <div class="validation-ok">&#10003; No issues found</div>`;
    return;
  }
  const rows = issues
    .map(
      (i) => `<div class="issue ${i.severity}" title="${esc(i.message)}">
        <span class="dot"></span>
        <span><span class="code">${esc(i.code)}</span><br>${esc(i.message)}</span>
      </div>`
    )
    .join("");
  card.innerHTML = `<h3>Validation &mdash; ${issues.length} issue(s)</h3>${rows}`;
}

function renderNav() {
  const p = state.project;
  const item = (view, label, count, removable = null) => `
    <div class="nav-item ${state.view === view ? "active" : ""}" data-view="${esc(view)}">
      <span>${esc(label)}</span>
      <span class="count">${count}</span>
      ${removable ? `<button class="btn danger small row-remove" data-remove="${esc(removable)}" title="Remove timeline">&times;</button>` : ""}
    </div>`;

  let html = `<div class="nav-group-label">Clips</div>`;
  html += item("", "All clips", p.clip_count);
  if (p.unattached_count > 0) html += item("unattached", "Unattached", p.unattached_count);
  html += `<div class="nav-group-label">Timelines</div>`;
  for (const t of p.timelines) {
    html += item(t.token, t.name, t.clip_count, t.token);
  }
  html += `<div class="nav-group-label">Pool</div>`;
  html += item("media", "Media pool", p.media.length);
  $("#nav-card").innerHTML = html;

  for (const el of document.querySelectorAll("#nav-card .nav-item")) {
    el.addEventListener("click", (ev) => {
      if (ev.target.closest("[data-remove]")) return;
      switchView(el.dataset.view);
    });
  }
  for (const btn of document.querySelectorAll("#nav-card [data-remove]")) {
    btn.addEventListener("click", () => removeTokens([btn.dataset.remove], "timeline"));
  }
}

function renderStatusBar() {
  const p = state.project;
  const patches = p.patches.length;
  $("#patch-status").textContent = patches
    ? `${patches} unsaved change${patches === 1 ? "" : "s"}`
    : "No unsaved changes";
  $("#version-chips").innerHTML = p.versions
    .map((v) => `<span class="version-chip" title="${esc(v)}">${esc(v.split(/[\\/]/).pop())}</span>`)
    .join("");
}

// ---------- views: clips / media ----------

function switchView(view) {
  state.view = view;
  state.selected.clear();
  closeDetails();
  renderNav();
  if (view === "media") {
    $("#view-title").textContent = "Media pool";
    renderMediaTable();
  } else {
    const names = { "": "All clips", unattached: "Unattached clips" };
    const timeline = state.project.timelines.find((t) => t.token === view);
    $("#view-title").textContent = names[view] ?? (timeline ? timeline.name : "Clips");
    loadClips();
  }
}

async function loadClips() {
  const params = new URLSearchParams();
  if (state.view && state.view !== "media") params.set("timeline", state.view);
  if (state.search) params.set("q", state.search);
  try {
    state.clips = await api(`/api/clips?${params}`);
  } catch (err) {
    toast(err.message, "error");
    state.clips = [];
  }
  renderClipTable();
}

function renderClipTable() {
  const rows = state.clips;
  if (!rows.length) {
    $("#table-container").innerHTML = `<div class="empty-table">No clips here.</div>`;
    updateSelectionUI();
    return;
  }
  const timelineName = (uuid) => {
    const t = state.project.timelines.find((t) => t.uuid === uuid);
    return t ? t.name : "";
  };
  const body = rows
    .map(
      (c) => `<tr data-token="${esc(c.token)}"
                  class="${state.selected.has(c.token) ? "selected" : ""} ${state.detailToken === c.token ? "detail-open" : ""}">
        <td class="checkbox-col"><input type="checkbox" ${state.selected.has(c.token) ? "checked" : ""}></td>
        <td>${esc(c.name)}</td>
        <td class="uuid">${esc(c.uuid)}</td>
        <td>${esc(timelineName(c.timeline_uuid))}</td>
        <td>${c.has_blob ? '<span class="badge blob">blob</span>' : ""}</td>
      </tr>`
    )
    .join("");
  $("#table-container").innerHTML = `
    <table>
      <thead><tr>
        <th class="checkbox-col"><input type="checkbox" id="select-all"></th>
        <th>Name</th><th>UUID</th><th>Timeline</th><th></th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`;
  bindTableEvents("clip");
}

function renderMediaTable() {
  const rows = state.project.media;
  if (!rows.length) {
    $("#table-container").innerHTML = `<div class="empty-table">Media pool is empty.</div>`;
    updateSelectionUI();
    return;
  }
  const body = rows
    .map(
      (m) => `<tr data-token="${esc(m.token)}"
                  class="${state.selected.has(m.token) ? "selected" : ""} ${state.detailToken === m.token ? "detail-open" : ""}">
        <td class="checkbox-col"><input type="checkbox" ${state.selected.has(m.token) ? "checked" : ""}></td>
        <td>${esc(m.name)}</td>
        <td class="uuid">${esc(m.uuid)}</td>
        <td class="path">${esc(m.file_path)}</td>
      </tr>`
    )
    .join("");
  $("#table-container").innerHTML = `
    <table>
      <thead><tr>
        <th class="checkbox-col"><input type="checkbox" id="select-all"></th>
        <th>Name</th><th>UUID</th><th>File path</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`;
  bindTableEvents("media");
}

function bindTableEvents() {
  const selectAll = $("#select-all");
  if (selectAll) {
    selectAll.addEventListener("change", () => {
      state.selected.clear();
      if (selectAll.checked) {
        for (const tr of document.querySelectorAll("tbody tr")) state.selected.add(tr.dataset.token);
      }
      rerenderCurrentTable();
    });
  }
  for (const tr of document.querySelectorAll("tbody tr")) {
    const token = tr.dataset.token;
    tr.querySelector("input[type=checkbox]").addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (state.selected.has(token)) state.selected.delete(token);
      else state.selected.add(token);
      tr.classList.toggle("selected");
      updateSelectionUI();
    });
    tr.addEventListener("click", () => showDetails(token));
  }
  updateSelectionUI();
}

function rerenderCurrentTable() {
  if (state.view === "media") renderMediaTable();
  else renderClipTable();
}

function updateSelectionUI() {
  const n = state.selected.size;
  $("#selection-info").textContent = n ? `${n} selected` : "";
  $("#remove-btn").disabled = n === 0;
}

// ---------- details panel ----------

async function showDetails(token) {
  try {
    const detail = await api(`/api/object/${encodeURIComponent(token)}`);
    state.detailToken = token;
    renderDetails(detail);
    rerenderCurrentTable();
  } catch (err) {
    toast(err.message, "error");
  }
}

function renderDetails(d) {
  const panel = $("#details");
  panel.classList.remove("hidden");

  const editableField = (prop, value) => `
    <div class="field">
      <label>${esc(prop)}</label>
      <input data-prop="${esc(prop)}" value="${esc(value)}" spellcheck="false">
      <div class="hint">Enter to apply</div>
    </div>`;
  const readonlyField = (label, value) =>
    value ? `<div class="field"><label>${esc(label)}</label><div class="value">${esc(value)}</div></div>` : "";

  let fields = "";
  for (const prop of d.editable) fields += editableField(prop, d[prop.replace("file_path", "file_path")] ?? d[prop]);
  fields += readonlyField("uuid", d.uuid);
  if (d.type === "clip") {
    fields += readonlyField("source media", d.source);
    fields += readonlyField("timeline", d.timeline_uuid);
  }
  if (d.type === "media" && !d.editable.includes("file_path")) {
    fields += readonlyField("file path", d.file_path);
  }

  let blob = "";
  if (d.type === "clip" && d.blob) {
    if (d.blob.error) {
      blob = `<div class="blob-section"><h3 class="nav-group-label">FieldsBlob</h3>
        <div class="issue error"><span class="dot"></span><span>${esc(d.blob.error)}</span></div></div>`;
    } else {
      const known = Object.entries(d.blob.known_fields)
        .map(([k, v]) => `<div class="blob-field-row"><span>${esc(k)}</span><span>${esc(String(v))}</span></div>`)
        .join("");
      blob = `<div class="blob-section">
        <h3 class="nav-group-label" style="padding-left:0">FieldsBlob &mdash; ${d.blob.size} bytes</h3>
        ${known ? `<div class="blob-fields">${known}</div>` : `<div class="hint muted">No known fields mapped. Load a signature database to decode.</div>`}
        <pre class="hexdump">${esc(d.blob.hex_dump)}${d.blob.truncated ? "\n… truncated …" : ""}</pre>
      </div>`;
    }
  }

  panel.innerHTML = `
    <div class="details-header">
      <h2>${esc(d.name)}</h2>
      <button class="close-btn" title="Close">&times;</button>
    </div>
    <span class="type-tag">${esc(d.type)}</span>
    ${fields}
    ${blob}
    <div class="details-actions">
      <button class="btn danger" id="detail-remove">Remove ${esc(d.type)}</button>
    </div>`;

  panel.querySelector(".close-btn").addEventListener("click", closeDetails);
  panel.querySelector("#detail-remove").addEventListener("click", () => removeTokens([d.token], d.type));
  for (const input of panel.querySelectorAll("input[data-prop]")) {
    input.addEventListener("keydown", async (ev) => {
      if (ev.key !== "Enter") return;
      await withBusy(async () => {
        const result = await postJSON("/api/set-property", {
          token: d.token,
          property: input.dataset.prop,
          value: input.value,
        });
        toast(`${input.dataset.prop} updated`, "ok");
        await refreshProject();
        await reloadAfterMutation(result.patch.object_id || d.token);
      });
    });
  }
}

function closeDetails() {
  state.detailToken = null;
  $("#details").classList.add("hidden");
  $("#details").innerHTML = "";
}

// ---------- mutations ----------

async function removeTokens(tokens, kind) {
  if (!tokens.length) return;
  const label = tokens.length === 1 ? `this ${kind ?? "element"}` : `${tokens.length} elements`;
  if (!confirm(`Remove ${label}? You can undo afterwards.`)) return;
  await withBusy(async () => {
    await postJSON("/api/remove", { tokens });
    toast(`Removed ${label}`, "ok");
    state.selected.clear();
    if (tokens.includes(state.detailToken)) closeDetails();
    if (tokens.includes(state.view)) state.view = "";
    await refreshProject();
    await reloadAfterMutation();
  });
}

async function undo() {
  await withBusy(async () => {
    const result = await postJSON("/api/undo", {});
    if (!result.undone) return toast("Nothing to undo");
    toast(`Undid change to ${result.undone.property.replace("__removed__", "removed element")}`, "ok");
    closeDetails();
    await refreshProject();
    await reloadAfterMutation();
  });
}

async function reloadAfterMutation(detailToken = null) {
  if (state.view === "media") {
    renderMediaTable();
  } else {
    // The current timeline may have been removed.
    const stillThere =
      !state.view ||
      state.view === "unattached" ||
      state.project.timelines.some((t) => t.token === state.view);
    if (!stillThere) state.view = "";
    await loadClips();
  }
  renderNav();
  if (detailToken) await showDetails(detailToken).catch(() => closeDetails());
}

async function saveVersion() {
  await withBusy(async () => {
    const result = await postJSON("/api/save-version", {});
    toast(`Saved ${result.path.split(/[\\/]/).pop()}`, "ok");
    await refreshProject();
  });
}

// ---------- wiring ----------

$("#open-btn").addEventListener("click", openByPath);
$("#path-input").addEventListener("keydown", (ev) => ev.key === "Enter" && openByPath());
$("#upload-btn").addEventListener("click", () => $("#file-input").click());
$("#file-input").addEventListener("change", () => {
  const file = $("#file-input").files[0];
  if (file) uploadFile(file);
  $("#file-input").value = "";
});
$("#undo-btn").addEventListener("click", undo);
$("#save-btn").addEventListener("click", saveVersion);
$("#remove-btn").addEventListener("click", () => removeTokens([...state.selected]));

let searchTimer = null;
$("#search-input").addEventListener("input", (ev) => {
  state.search = ev.target.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => state.view !== "media" && loadClips(), 180);
});

document.addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "z" && state.project) {
    const tag = document.activeElement?.tagName;
    if (tag !== "INPUT" && tag !== "TEXTAREA") {
      ev.preventDefault();
      undo();
    }
  }
});

// drag & drop anywhere
const dropZone = $("#drop-zone");
for (const target of [document.body]) {
  target.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    dropZone.classList.add("dragover");
  });
  target.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  target.addEventListener("drop", (ev) => {
    ev.preventDefault();
    dropZone.classList.remove("dragover");
    const file = ev.dataTransfer.files[0];
    if (file) uploadFile(file);
  });
}

// If the server was launched with a project preloaded, show it.
api("/api/project")
  .then((p) => {
    state.project = p;
    onProjectOpened();
  })
  .catch(() => {
    /* no project open yet - stay on the empty state */
  });
