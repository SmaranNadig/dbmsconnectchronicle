// Data editor: browse tables, add / edit / delete rows and documents in the lab
// (schema lab and MongoDB database <mongo_db>_lab). See DATA_EDITOR_PLAN.md.

const $ = (id) => document.getElementById(id);
const fmt = new Intl.NumberFormat("en");
const num = (v) => (v === null || v === undefined ? "–" : fmt.format(v));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pretty = (v) => JSON.stringify(v, null, 2);

async function api(path, params, body) {
  const url = new URL(`/api/v1/${path}`, location.origin);
  Object.entries(params || {}).forEach(([k, v]) => v !== undefined && v !== null && url.searchParams.set(k, v));
  const token = $("lab-token").value;
  const r = await fetch(url, body === undefined ? {} : {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", ...(token ? { "X-Review-Token": token } : {}) },
  });
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail;
    throw new Error(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `${r.status} ${r.statusText}`);
  }
  return r.json();
}

const ok = (html) => `<div class="callout good-box">${html}</div>`;
const bad = (msg) => `<pre class="error-box">${esc(msg)}</pre>`;
function cell(v) {
  if (v === null || v === undefined) return `<span class="null">NULL</span>`;
  const s = typeof v === "object" ? JSON.stringify(v) : String(v);
  return s.length > 80 ? `<span title="${esc(s)}">${esc(s.slice(0, 77))}…</span>` : esc(s);
}
const asInput = (v) => (v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v));

// ---------------------------------------------------------------- status
async function loadStatus() {
  try {
    const s = await api("lab/status");
    if (!s.built) { $("lab-kpis").innerHTML = `<p class="note">The lab has not been built yet.</p>`; return; }
    const t = s.tables;
    $("lab-size").textContent = `${num(t.neurons)} neurons and ${num(t.connections)} connections`;
    $("lab-token-l").hidden = !s.review_token_required;
    const docs = Object.values(s.mongo.collections).reduce((a, b) => a + b, 0);
    $("lab-kpis").innerHTML = [
      [num(t.datasets), "datasets"], [num(t.releases), "releases"], [num(t.neurons), "neurons"],
      [num(t.connections), "connections"], [num(docs), "MongoDB documents"], [num(t.changes), "changes logged"],
    ].map(([v, l]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l} in the lab</div></div>`).join("");
  } catch (e) {
    $("lab-kpis").innerHTML = bad(`Could not load the lab: ${e.message}`);
  }
}

$("lab-reset").addEventListener("click", async () => {
  if (!confirm("Reset the lab? Every change made in schema lab and the MongoDB lab is discarded and the copy of the real data is rebuilt. The real tables are not touched.")) return;
  $("lab-reset-meta").textContent = "rebuilding from the real data…";
  try {
    const r = await api("lab/reset", null, {});
    $("lab-reset-meta").textContent = `rebuilt in ${r.seconds} s`;
    await Promise.all([loadStatus(), loadTables(), loadCollections()]);
    ed.table = null; $("ed-rows").innerHTML = ""; $("ed-title").textContent = "Pick a table";
  } catch (e) {
    $("lab-reset-meta").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
});

// ---------------------------------------------------------------- modes
function setEditorMode(mode) {
  document.querySelectorAll("#editor-mode button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  $("ed-tables").hidden = mode !== "tables";
  $("ed-docs").hidden = mode !== "docs";
  $("ed-log").hidden = mode !== "log";
  if (mode === "log") loadLog();
  if (mode === "docs" && !docs.loaded) loadCollections();
}
document.querySelectorAll("#editor-mode button").forEach((b) => b.addEventListener("click", () => setEditorMode(b.dataset.mode)));

// ---------------------------------------------------------------- SQL tables
const ed = { schema: "lab", table: null, offset: 0, limit: 25, sort: null, desc: false, data: null };

async function loadTables() {
  try {
    const t = await api("lab/tables", { schema: ed.schema });
    $("ed-table-list").innerHTML = t.tables.map((x) => `<button type="button" class="tbl-btn ${x.name === ed.table ? "on" : ""}" data-table="${esc(x.name)}">
      <span>${esc(x.name)}</span><small>${x.kind === "table" ? "" : `${esc(x.kind)} · `}${x.rows !== undefined ? num(x.rows) : `~${num(x.estimated_rows)}`} rows</small></button>`).join("")
      || `<p class="note">No tables.</p>`;
    $("ed-table-list").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => openTable(b.dataset.table)));
  } catch (e) {
    $("ed-table-list").innerHTML = bad(e.message);
  }
}
$("ed-schema").addEventListener("change", (ev) => {
  ed.schema = ev.target.value; ed.table = null;
  $("ed-title").textContent = "Pick a table"; $("ed-rows").innerHTML = ""; $("ed-meta").textContent = "";
  $("ed-add").hidden = true; $("ed-form").hidden = true; $("ed-result").innerHTML = ""; $("ed-structure-body").innerHTML = "";
  loadTables();
});

function openTable(name) {
  Object.assign(ed, { table: name, offset: 0, sort: null, desc: false });
  $("ed-form").hidden = true; $("ed-result").innerHTML = "";
  $("ed-table-list").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.table === name));
  loadRows();
}

async function loadRows() {
  $("ed-meta").textContent = "loading…";
  try {
    const d = await api(`lab/tables/${ed.schema}/${encodeURIComponent(ed.table)}`,
      { limit: ed.limit, offset: ed.offset, sort: ed.sort, desc: ed.desc });
    ed.data = d;
    $("ed-title").innerHTML = `<code>${esc(ed.schema)}.${esc(ed.table)}</code> ${d.editable ? `<span class="badge">editable</span>` : `<span class="badge muted">read-only</span>`}`;
    $("ed-add").hidden = !d.editable;
    $("ed-structure-body").innerHTML = structure(d);
    const shown = d.rows.length ? `rows ${num(ed.offset + 1)}–${num(ed.offset + d.rows.length)}` : "no rows";
    $("ed-meta").innerHTML = `${shown}${d.total !== null ? ` of ${num(d.total)}` : ""} · <code>${esc(d.sql)}</code>`;
    const cols = d.columns.map((c) => c.name);
    const head = cols.map((c) => `<th><button type="button" class="sort" data-col="${esc(c)}">${esc(c)}${d.primary_key.includes(c) ? " 🔑" : ""}${ed.sort === c ? (ed.desc ? " ▾" : " ▴") : ""}</button></th>`).join("");
    const body = d.rows.map((r, i) => `<tr>${d.editable ? `<td class="actions"><button type="button" class="ghost small" data-edit="${i}">Edit</button><button type="button" class="ghost small danger" data-del="${i}">Delete</button></td>` : ""}${
      cols.map((c) => `<td>${cell(r[c])}</td>`).join("")}</tr>`).join("");
    $("ed-rows").innerHTML = `<div class="scroll"><table><thead><tr>${d.editable ? "<th></th>" : ""}${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    $("ed-rows").querySelectorAll("button.sort").forEach((b) => b.addEventListener("click", () => {
      ed.desc = ed.sort === b.dataset.col ? !ed.desc : false; ed.sort = b.dataset.col; ed.offset = 0; loadRows();
    }));
    $("ed-rows").querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => showForm(d.rows[b.dataset.edit])));
    $("ed-rows").querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => deleteRow(d.rows[b.dataset.del])));
    $("ed-prev").disabled = ed.offset === 0;
    $("ed-next").disabled = d.rows.length < ed.limit;
  } catch (e) {
    $("ed-meta").textContent = "";
    $("ed-rows").innerHTML = bad(e.message);
  }
}
$("ed-prev").addEventListener("click", () => { ed.offset = Math.max(0, ed.offset - ed.limit); loadRows(); });
$("ed-next").addEventListener("click", () => { ed.offset += ed.limit; loadRows(); });

function structure(d) {
  const cols = d.columns.map((c) => `<tr><td><code>${esc(c.name)}</code>${c.primary_key ? " 🔑" : ""}</td><td>${esc(c.type)}</td>
    <td>${c.nullable ? "NULL allowed" : "NOT NULL"}</td><td>${c.identity ? `identity (${c.identity === "a" ? "ALWAYS" : "BY DEFAULT"})` : c.default ? `<code>${esc(c.default)}</code>` : ""}</td></tr>`).join("");
  const cons = d.constraints.map((c) => `<tr><td><code>${esc(c.name)}</code></td><td>${esc(c.type)}</td><td><code>${esc(c.definition)}</code></td></tr>`).join("");
  return `<div class="tbl-wrap"><table><thead><tr><th>Column</th><th>Type</th><th>Null</th><th>Default</th></tr></thead><tbody>${cols}</tbody></table></div>
    ${cons ? `<div class="tbl-wrap"><table><thead><tr><th>Constraint</th><th>Type</th><th>Definition</th></tr></thead><tbody>${cons}</tbody></table></div>` : ""}`;
}

function showForm(row) {
  const d = ed.data;
  if (!d) return;
  const editing = !!row;
  const fields = d.columns.filter((c) => !c.generated && !(c.identity === "a"));
  $("ed-form").hidden = false;
  $("ed-form").innerHTML = `<h4>${editing ? "Edit row" : `Add a row to <code>lab.${esc(ed.table)}</code>`}</h4>
    <p class="note">${editing ? "Change the values and save. An empty field becomes NULL." : "An empty field uses the column's default (or identity); otherwise it is NULL."}
      Values are checked by PostgreSQL itself.</p>
    <div class="form-grid">${fields.map((c) => `<label class="stack"><span><code>${esc(c.name)}</code> <small>${esc(c.type)}${c.nullable ? "" : " · required"}${c.primary_key ? " · key" : ""}</small></span>
      <input data-col="${esc(c.name)}" value="${esc(editing ? asInput(row[c.name]) : "")}" placeholder="${esc(c.identity ? "automatic" : c.default || (c.nullable ? "NULL" : ""))}" spellcheck="false"></label>`).join("")}</div>
    <div class="row"><button type="button" id="ed-save">${editing ? "Save changes" : "Insert row"}</button><button type="button" class="ghost" id="ed-cancel">Cancel</button></div>`;
  $("ed-cancel").addEventListener("click", () => { $("ed-form").hidden = true; });
  $("ed-save").addEventListener("click", async () => {
    const values = {};
    $("ed-form").querySelectorAll("input[data-col]").forEach((i) => {
      const before = editing ? asInput(row[i.dataset.col]) : "";
      if (!editing || i.value !== before) values[i.dataset.col] = i.value;
    });
    const key = editing ? Object.fromEntries(d.primary_key.map((k) => [k, row[k]])) : {};
    await write(editing ? "update" : "insert", { key, values });
  });
  $("ed-form").scrollIntoView({ block: "nearest" });
}
$("ed-add").addEventListener("click", () => showForm(null));

async function deleteRow(row) {
  const key = Object.fromEntries(ed.data.primary_key.map((k) => [k, row[k]]));
  if (!confirm(`Delete the row with ${Object.entries(key).map(([k, v]) => `${k} = ${v}`).join(", ")} from lab.${ed.table}?`)) return;
  await write("delete", { key, values: {} });
}

async function write(action, body) {
  $("ed-result").innerHTML = "";
  try {
    const r = await api(`lab/tables/lab/${encodeURIComponent(ed.table)}/${action}`, null, body);
    $("ed-form").hidden = true;
    $("ed-result").innerHTML = ok(`<b>${action === "insert" ? "Inserted" : action === "update" ? "Updated" : "Deleted"} ${r.rowcount} row${r.rowcount === 1 ? "" : "s"}</b> in ${r.elapsed_ms} ms. Statement that ran:<pre class="sqlshown">${esc(r.sql)}</pre>`);
    await Promise.all([loadRows(), loadTables(), loadStatus()]);
  } catch (e) {
    $("ed-result").innerHTML = `<p class="note">PostgreSQL refused the change, so nothing was saved:</p>${bad(e.message)}`;
  }
}

// ---------------------------------------------------------------- MongoDB
const docs = { loaded: false, collection: "neuron_source_record", skip: 0, limit: 10, list: [] };

async function loadCollections() {
  try {
    const c = await api("lab/mongo/collections");
    docs.loaded = true;
    if (!c.collections.some((x) => x.name === docs.collection)) docs.collection = c.collections[0]?.name || null;
    $("ed-coll-list").innerHTML = `<p class="note">Database <code>${esc(c.database)}</code></p>` + c.collections.map((x) =>
      `<button type="button" class="tbl-btn ${x.name === docs.collection ? "on" : ""}" data-coll="${esc(x.name)}"><span>${esc(x.name)}</span><small>${num(x.documents)} documents</small></button>`).join("");
    $("ed-coll-list").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      docs.collection = b.dataset.coll; docs.skip = 0; loadCollections(); findDocs();
    }));
    const cur = c.collections.find((x) => x.name === docs.collection);
    $("ed-coll-info").innerHTML = cur ? `<details><summary>Indexes (${cur.indexes.length})</summary><pre class="json">${esc(pretty(cur.indexes.map((i) => ({ name: i.name, key: i.key, unique: !!i.unique }))))}</pre></details>
      <details><summary>Validator</summary><pre class="json">${esc(cur.validator ? pretty(cur.validator) : "none")}</pre></details>` : "";
    if (cur && !docs.list.length) findDocs();
  } catch (e) {
    $("ed-coll-list").innerHTML = bad(e.message);
  }
}

function docFilter() {
  const t = $("ed-doc-filter").value.trim();
  if (!t) return {};
  try { return JSON.parse(t); } catch (e) { throw new Error(`Filter is not valid JSON: ${e.message}`); }
}

async function mongo(body) {
  return api("query/mongo", null, { target: "lab", collection: docs.collection, ...body });
}

async function findDocs() {
  if (!docs.collection) return;
  $("ed-doc-meta").textContent = "loading…";
  try {
    const filter = docFilter();
    const [r, c] = await Promise.all([mongo({ op: "find", filter, limit: docs.limit, skip: docs.skip }), mongo({ op: "count", filter })]);
    docs.list = r.documents;
    $("ed-doc-meta").innerHTML = `${docs.list.length ? `documents ${num(docs.skip + 1)}–${num(docs.skip + docs.list.length)}` : "no documents"} of ${num(c.count)} · <code>db.${esc(docs.collection)}.find(${esc(JSON.stringify(filter))}).skip(${docs.skip}).limit(${docs.limit})</code>`;
    $("ed-doc-list").innerHTML = docs.list.map((d, i) => `<details class="doc"><summary><code>${esc(JSON.stringify(d._id))}</code> ${d.dataset ? `· ${esc(d.dataset)}` : ""} ${d.source_neuron_id ? `· ${esc(d.source_neuron_id)}` : ""}
      <span class="doc-actions"><button type="button" class="ghost small" data-edit="${i}">Edit</button><button type="button" class="ghost small danger" data-del="${i}">Delete</button></span></summary>
      <pre class="json">${esc(pretty(d))}</pre></details>`).join("");
    $("ed-doc-list").querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", (ev) => { ev.preventDefault(); docForm(docs.list[b.dataset.edit]); }));
    $("ed-doc-list").querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", (ev) => { ev.preventDefault(); deleteDoc(docs.list[b.dataset.del]); }));
    $("ed-doc-prev").disabled = docs.skip === 0;
    $("ed-doc-next").disabled = docs.skip + docs.list.length >= c.count;
  } catch (e) {
    $("ed-doc-meta").textContent = "";
    $("ed-doc-list").innerHTML = bad(e.message);
  }
}
$("ed-doc-find").addEventListener("click", () => { docs.skip = 0; findDocs(); });
$("ed-doc-filter").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { docs.skip = 0; findDocs(); } });
$("ed-doc-prev").addEventListener("click", () => { docs.skip = Math.max(0, docs.skip - docs.limit); findDocs(); });
$("ed-doc-next").addEventListener("click", () => { docs.skip += docs.limit; findDocs(); });

const NEW_DOC = { dataset: "evaluator", source_neuron_id: "my-doc-1", dataset_neuron_id: 900000001, releases: {}, note: "added in the Data editor" };
function docForm(doc) {
  const editing = !!doc;
  const shown = editing ? Object.fromEntries(Object.entries(doc).filter(([k]) => k !== "_id")) : (docs.collection === "neuron_source_record" ? NEW_DOC : {});
  $("ed-doc-form").hidden = false;
  $("ed-doc-form").innerHTML = `<h4>${editing ? `Edit document <code>${esc(JSON.stringify(doc._id))}</code> (replaceOne)` : `Insert a document into <code>${esc(docs.collection)}</code> (insertOne)`}</h4>
    <p class="note">JSON. The collection's validator and unique indexes are checked by MongoDB${editing ? "; <code>_id</code> stays the same" : ""}.</p>
    <textarea id="ed-doc-text" rows="12" spellcheck="false">${esc(pretty(shown))}</textarea>
    <div class="row"><button type="button" id="ed-doc-save">${editing ? "Save document" : "Insert document"}</button><button type="button" class="ghost" id="ed-doc-cancel">Cancel</button></div>`;
  $("ed-doc-cancel").addEventListener("click", () => { $("ed-doc-form").hidden = true; });
  $("ed-doc-save").addEventListener("click", async () => {
    let body;
    try { body = JSON.parse($("ed-doc-text").value); } catch (e) { $("ed-doc-result").innerHTML = bad(`Not valid JSON: ${e.message}`); return; }
    await docWrite(editing ? { op: "replaceOne", filter: { _id: doc._id }, document: body } : { op: "insertOne", document: body });
  });
  $("ed-doc-form").scrollIntoView({ block: "nearest" });
}
$("ed-doc-new").addEventListener("click", () => docForm(null));

async function deleteDoc(doc) {
  if (!confirm(`Delete document ${JSON.stringify(doc._id)} from ${docs.collection}?`)) return;
  await docWrite({ op: "deleteOne", filter: { _id: doc._id } });
}

async function docWrite(body) {
  $("ed-doc-result").innerHTML = "";
  try {
    const r = await mongo(body);
    $("ed-doc-form").hidden = true;
    const out = Object.fromEntries(Object.entries(r).filter(([k]) => !["database", "collection", "elapsed_ms"].includes(k)));
    $("ed-doc-result").innerHTML = ok(`<b>${esc(body.op)}</b> on <code>${esc(r.database)}.${esc(r.collection)}</code> in ${r.elapsed_ms} ms:<pre class="sqlshown">${esc(pretty(out))}</pre>`);
    await Promise.all([findDocs(), loadCollections(), loadStatus()]);
  } catch (e) {
    $("ed-doc-result").innerHTML = `<p class="note">MongoDB refused the change, so nothing was saved:</p>${bad(e.message)}`;
  }
}

// ---------------------------------------------------------------- change log
async function loadLog() {
  try {
    const rows = await api("lab/changes", { limit: 100 });
    $("ed-log-rows").innerHTML = rows.length ? `<div class="scroll"><table><thead><tr><th>#</th><th>When</th><th>Table</th><th>Operation</th><th>By</th><th>Before</th><th>After</th></tr></thead><tbody>${
      rows.map((r) => `<tr><td>${r.change_id}</td><td>${esc(new Date(r.changed_at).toLocaleString())}</td><td><code>${esc(r.table_name)}</code></td><td>${esc(r.operation)}</td><td>${esc(r.changed_by)}</td><td>${cell(r.old_row)}</td><td>${cell(r.new_row)}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="note">No changes yet. Add, edit or delete something, here or with the Lab target of the query console.</p>`;
  } catch (e) {
    $("ed-log-rows").innerHTML = bad(e.message);
  }
}
$("ed-log-refresh").addEventListener("click", loadLog);

// ---------------------------------------------------------------- start
let started = false;
function maybeStart() {
  if (started || location.hash.slice(1).split("?")[0] !== "editor") return;
  started = true;
  loadStatus();
  loadTables();
}
window.addEventListener("hashchange", () => {
  maybeStart();
  if (started && location.hash.startsWith("#editor")) loadStatus();
});
maybeStart();
