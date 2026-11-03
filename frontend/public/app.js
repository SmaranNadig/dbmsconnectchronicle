// ConnectomeChronicle dashboard. Plain ES module, no build step: everything is
// fetched from the FastAPI backend (proxied at /api by server.js).

const $ = (id) => document.getElementById(id);
const fmt = new Intl.NumberFormat("en");
const num = (v) => (v === null || v === undefined ? "–" : fmt.format(v));
const pct = (v, d = 1) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(d)}%`);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const bytes = (b) => {
  if (b == null) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; let v = Number(b);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i ? 2 : 1)} ${u[i]}`;
};
const ms = (v) => (v < 10 ? v.toFixed(2) : num(Math.round(v)));

async function api(path, params, body) {
  const url = new URL(`/api/v1/${path}`, location.origin);
  Object.entries(params || {}).forEach(([k, v]) => v !== undefined && url.searchParams.set(k, v));
  const r = await fetch(url, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail;
    throw new Error(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `${r.status} ${r.statusText}`);
  }
  return r.json();
}

function table(cols, rows) {
  // cols: [{key|fn, label, num?, mono?}]
  const head = cols.map((c) => `<th class="${c.num ? "n" : ""}">${esc(c.label)}</th>`).join("");
  const body = rows.map((r) => `<tr>${cols.map((c) => {
    const v = c.fn ? c.fn(r) : esc(r[c.key]);
    return `<td class="${c.num ? "n" : ""} ${c.mono ? "mono" : ""}">${v ?? "–"}</td>`;
  }).join("")}</tr>`).join("");
  return `<div class="tbl-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

// Horizontal bars, one hue; labels and values are text, never colour alone.
function bars(items, { max, quiet = () => false, value = (i) => num(i.value), tip } = {}) {
  const m = max ?? Math.max(...items.map((i) => i.value), 1);
  return `<div class="bars">${items.map((i, k) => `
    <div class="lab">${esc(i.label)}</div>
    <div class="track"><div class="bar ${quiet(i) ? "quiet" : ""}" data-tip="${esc(tip ? tip(i) : "")}"
      style="width:${Math.max(0.3, (i.value / m) * 72)}%"></div><span class="val">${value(i, k)}</span></div>`).join("")}</div>`;
}

const status = (ok, label) => `<span class="status ${ok === true ? "good" : ok === false ? "bad" : "partial"}">${esc(label)}</span>`;
const fail = (el, e) => { $(el).innerHTML = `<p class="err">Could not load: ${esc(e.message)}</p>`; };
const kpi = (v, l) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`;

const tipEl = $("tip");
document.addEventListener("mousemove", (ev) => {
  const t = ev.target.closest("[data-tip]");
  if (!t || !t.dataset.tip) { tipEl.hidden = true; return; }
  tipEl.textContent = t.dataset.tip;
  tipEl.hidden = false;
  tipEl.style.left = `${Math.min(ev.clientX + 12, innerWidth - 310)}px`;
  tipEl.style.top = `${ev.clientY + 12}px`;
});

// ---------------------------------------------------------------- syntax colouring (SQL, Python, YAML)
const SQL_KW = "select|from|where|join|left|inner|on|using|and|or|not|in|is|null|as|group|by|order|having|limit|offset|union|all|distinct|case|when|then|else|end|with|lateral|exists|insert|into|values|update|set|delete|create|table|view|materialized|index|unique|primary|key|foreign|references|check|constraint|exclude|gist|btree|default|function|returns|language|begin|declare|return|if|raise|exception|trigger|before|after|for|each|row|execute|procedure|alter|add|column|schema|grant|revoke|role|do|loop|perform|stable|immutable|generated|always|identity|cascade|restrict|replace|desc|asc|true|false|bigint|integer|smallint|text|jsonb|numeric|boolean|timestamptz|int4range|tstzrange|char|date|refresh|extension|comment|over|partition|filter|coalesce|count|sum|avg|min|max";
const PY_KW = "def|class|return|if|elif|else|for|while|in|not|and|or|import|from|as|with|try|except|finally|raise|yield|lambda|None|True|False|is|pass|continue|break|global";
function highlight(text, lang) {
  const kw = lang === "sql" ? SQL_KW : lang === "py" ? PY_KW : null;
  const parts = lang === "sql"
    ? [/--[^\n]*/, /'(?:[^']|'')*'/, /\$\$/]
    : lang === "py" ? [/#[^\n]*/, /"""[\s\S]*?"""|'''[\s\S]*?'''/, /"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/]
    : [/#[^\n]*/];
  const re = new RegExp(`(${parts[0].source})|(${parts.slice(1).map((p) => p.source).join("|") || "(?!)"})${kw ? `|\\b(${kw})\\b` : ""}`, lang === "sql" ? "gi" : "g");
  let out = ""; let last = 0;
  for (const m of text.matchAll(re)) {
    out += esc(text.slice(last, m.index));
    out += m[1] ? `<span class="cm">${esc(m[0])}</span>` : m[2] ? `<span class="st">${esc(m[0])}</span>` : `<span class="kw">${esc(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}
function codeBlock(text, lang) {
  const n = text.split("\n").length;
  return `<div class="code-grid"><pre class="gutter">${Array.from({ length: n }, (_, i) => i + 1).join("\n")}</pre><pre class="code">${highlight(text, lang)}</pre></div>`;
}

// ---------------------------------------------------------------- shared state
const ctx = { facts: {} };
let EXAMPLES = { sql: [], mongo: [] };

// ================================================================ overview
async function loadOverview() {
  try {
    const h = await api("health");
    $("health").innerHTML = status(true, `live · schema revision ${h.schema_revision}`);
  } catch (e) { $("health").innerHTML = status(false, "API not reachable"); }
  let o;
  try { o = await api("overview"); } catch (e) { fail("kpis", e); return; }
  ctx.overview = o;
  const st = o.storage;
  const mongoTotal = Object.values(o.mongo_documents).filter((v) => typeof v === "number").reduce((a, b) => a + b, 0);
  $("n-tables").textContent = st.tables;
  $("kpis").innerHTML = [
    kpi(`${o.datasets.length} · ${o.releases.length}`, "datasets · releases loaded"),
    kpi(num(st.source_neurons), "neurons (one identity each)"),
    kpi(num(st.connection_rows_stored), "connection rows (bitemporal)"),
    kpi(num(st.annotation_rows), "annotation rows"),
    kpi(num(mongoTotal), "MongoDB documents"),
    kpi(bytes(st.database_bytes), "PostgreSQL database size"),
  ].join("");
  const rows = o.datasets.map((d) => {
    const rs = o.releases.filter((r) => r.dataset === d.code);
    return { ...d, rs, latest: rs[rs.length - 1] };
  });
  $("datasets").innerHTML = table([
    { fn: (d) => `<b>${esc(d.name)}</b><div class="note">${esc(d.organism)}</div>`, label: "Dataset" },
    { fn: (d) => esc(d.organization), label: "Published by" },
    { fn: (d) => d.rs.map((r) => esc(r.version_label)).join(" → "), label: "Releases loaded" },
    { fn: (d) => num(d.latest?.neurons), label: "Neurons (latest)", num: true },
    { fn: (d) => num(d.latest?.connections), label: "Connections (latest)", num: true },
    { fn: (d) => `<a href="${esc(d.source_url)}" target="_blank" rel="noopener">source</a>`, label: "Link" },
  ], rows);
}

// ================================================================ console
function setMode(mode) {
  document.querySelectorAll("#console .seg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  $("sql-pane").hidden = mode !== "sql";
  $("mongo-pane").hidden = mode !== "mongo";
}
document.querySelectorAll("#console .seg button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

function renderCell(v) {
  if (v === null || v === undefined) return `<span class="null">NULL</span>`;
  if (typeof v === "object") return `<code>${esc(JSON.stringify(v))}</code>`;
  if (typeof v === "string" && v.includes("\n")) return `<pre class="cell">${esc(v)}</pre>`;
  return esc(v);
}

async function runSql(explain) {
  const sql = $("sql-text").value;
  $("sql-meta").textContent = "running…";
  $("sql-out").innerHTML = "";
  try {
    const r = await api("query/sql", null, { sql, explain: explain || null });
    $("sql-meta").innerHTML = `${status(true, `${num(r.row_count)} row${r.row_count === 1 ? "" : "s"}${r.truncated ? " (first 500)" : ""}`)} · ${ms(r.elapsed_ms)} ms · ${esc(r.executed_as)}`;
    if (r.explain) {
      $("sql-out").innerHTML = `<pre class="plan">${esc(r.rows.map((x) => x[0]).join("\n"))}</pre>`;
    } else {
      $("sql-out").innerHTML = `<div class="scroll">${table(r.columns.map((c, i) => ({ fn: (row) => renderCell(row[i]), label: c,
        num: r.rows.length > 0 && r.rows.every((row) => row[i] === null || typeof row[i] === "number") })), r.rows)}</div>`;
    }
  } catch (e) {
    $("sql-meta").innerHTML = status(false, "rejected");
    $("sql-out").innerHTML = `<pre class="error-box">${esc(e.message)}</pre>`;
  }
}
$("sql-run").addEventListener("click", () => runSql());
$("sql-plan").addEventListener("click", () => runSql("plan"));
$("sql-analyze").addEventListener("click", () => runSql("analyze"));
$("sql-text").addEventListener("keydown", (ev) => { if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") runSql(); });

const CONCEPT_NAME = {};
function fillExamples() {
  const groups = {};
  EXAMPLES.sql.forEach((e) => (groups[e.concept] ||= []).push(e));
  $("sql-example").innerHTML = `<option value="">— pick an example or write your own —</option>` + Object.entries(groups).map(([c, list]) =>
    `<optgroup label="${esc(CONCEPT_NAME[c] || c)}">${list.map((e) => `<option value="${e.id}">${esc(e.title)}</option>`).join("")}</optgroup>`).join("");
  $("mongo-example").innerHTML = `<option value="">— pick an example or write your own —</option>` +
    EXAMPLES.mongo.map((e) => `<option value="${e.id}">${esc(e.title)}</option>`).join("");
}
function loadSqlExample(id, run = true) {
  const e = EXAMPLES.sql.find((x) => x.id === id);
  if (!e) return;
  setMode("sql");
  $("sql-example").value = id;
  $("sql-text").value = e.sql;
  if (run) { $("console").scrollIntoView({ behavior: "smooth" }); runSql(e.explain); }
}
$("sql-example").addEventListener("change", (ev) => loadSqlExample(ev.target.value, false));

const pretty = (v) => (v == null ? "" : JSON.stringify(v, null, 2));
function mongoOpChanged() {
  const op = $("mongo-op").value;
  $("mongo-filter-l").hidden = op === "aggregate";
  $("mongo-proj-l").hidden = op !== "find";
  $("mongo-pipe-l").hidden = op !== "aggregate";
}
$("mongo-op").addEventListener("change", mongoOpChanged);
function loadMongoExample(id, run = true) {
  const e = EXAMPLES.mongo.find((x) => x.id === id);
  if (!e) return;
  setMode("mongo");
  $("mongo-example").value = id;
  $("mongo-op").value = e.op;
  $("mongo-filter").value = pretty(e.filter);
  $("mongo-proj").value = pretty(e.projection);
  $("mongo-pipe").value = pretty(e.pipeline);
  $("mongo-limit").value = e.limit || 5;
  mongoOpChanged();
  if (run) { $("console").scrollIntoView({ behavior: "smooth" }); runMongo(); }
}
$("mongo-example").addEventListener("change", (ev) => loadMongoExample(ev.target.value, false));

async function runMongo() {
  const parse = (id, label) => {
    const t = $(id).value.trim();
    if (!t) return null;
    try { return JSON.parse(t); } catch (e) { throw new Error(`${label} is not valid JSON: ${e.message}`); }
  };
  $("mongo-meta").textContent = "running…";
  $("mongo-out").innerHTML = "";
  try {
    const op = $("mongo-op").value;
    const body = { op, limit: Number($("mongo-limit").value) || 5 };
    if (op !== "aggregate") body.filter = parse("mongo-filter", "Filter");
    if (op === "find") body.projection = parse("mongo-proj", "Projection");
    if (op === "aggregate") body.pipeline = parse("mongo-pipe", "Pipeline");
    const r = await api("query/mongo", null, body);
    $("mongo-meta").innerHTML = `${status(true, `${num(r.count)} document${r.count === 1 ? "" : "s"}`)} · ${ms(r.elapsed_ms)} ms`;
    $("mongo-out").innerHTML = r.documents.map((d, i) => `<details ${i < 2 ? "open" : ""}><summary>${esc(d._id !== undefined ? JSON.stringify(d._id) : `result ${i + 1}`)}</summary><pre class="json">${esc(pretty(d))}</pre></details>`).join("") || `<p class="note">No documents.</p>`;
  } catch (e) {
    $("mongo-meta").innerHTML = status(false, "rejected");
    $("mongo-out").innerHTML = `<pre class="error-box">${esc(e.message)}</pre>`;
  }
}
$("mongo-run").addEventListener("click", runMongo);

// ================================================================ concepts
const CONCEPTS = [
  { id: "er", title: "ER model → relational schema",
    what: "An entity–relationship design becomes tables, primary keys and foreign keys.",
    here: "Our team's ER diagram (8 entities) is implemented as normalised tables. Schema <code>er</code> has one view per ER entity, with exactly the diagram's attribute names.",
    evidence: (c) => `${c.tables} tables · ${c.erViews} ER views`,
    extra: `<div class="er-imgs"><a href="er-diagram.jpeg" target="_blank" rel="noopener"><img src="er-diagram.jpeg" alt="Team ER diagram" loading="lazy"></a>
      <a href="er-mapping.jpeg" target="_blank" rel="noopener"><img src="er-mapping.jpeg" alt="ER to relational mapping" loading="lazy"></a></div>` },
  { id: "relational", title: "Relational queries",
    what: "Joins, aggregation (GROUP BY / HAVING), subqueries and set operations.",
    here: "Every API answer is one parameterised SQL query. Comparing releases is a set difference between two database states.",
    evidence: (c) => `${num(c.connections)} connection rows to query` },
  { id: "keys", title: "Keys, constraints and triggers",
    what: "The DBMS itself refuses data that breaks a rule, whatever program writes it.",
    here: "Composite foreign keys stop an edge from joining neurons of two datasets. GiST exclusion constraints forbid two overlapping versions of a fact. A trigger makes history append-only.",
    evidence: (c) => c.constraintSummary,
    action: `<button type="button" id="run-integrity">Run the 9 violation tests ▸</button>` },
  { id: "txn", title: "Transactions (ACID)",
    what: "A unit of work either completes fully or leaves no trace (atomicity).",
    here: "Each release loads in one transaction (staging tables → validation → merge → refresh materialized view). Failed runs stay in <code>ingestion_run</code> with their error, but none of their data does.",
    evidence: (c) => `${c.failedRuns} failed runs recorded · ${num(c.partialFacts)} facts left behind by them` },
  { id: "temporal", title: "Temporal (bitemporal) data",
    what: "Valid time: when a fact is true. Transaction time: when the database recorded it.",
    here: "Each fact has <code>valid_rel</code> (the releases it holds in) and <code>tx</code> (when it was believed). A fact that does not change is stored once for many releases.",
    evidence: (c) => `${pct(c.saved)} fewer rows than one full copy per release` },
  { id: "index", title: "Indexing and query plans",
    what: "Indexes let the planner avoid reading whole tables. EXPLAIN shows what it chose.",
    here: "B-tree indexes on neuron ids, GiST on (id, valid range, transaction range), partial indexes on current rows. Compare the index scan with the full scan in the examples.",
    evidence: (c) => `${c.btree} B-tree · ${c.gist} GiST indexes` },
  { id: "views", title: "Views, materialized views, functions",
    what: "Stored queries and procedures kept inside the DBMS.",
    here: "ER views, the diff-detail view and region labels. The materialized view <code>release_stats</code> is refreshed inside each load. SQL and PL/pgSQL functions handle time travel, diff and lineage.",
    evidence: (c) => `${c.views} views · ${c.matviews} materialized · ${c.functions} functions` },
  { id: "provenance", title: "Provenance (data lineage)",
    what: "For any value: where did it come from, and how was it produced?",
    here: "Every fact points to a provenance row, then the source file (with SHA-256), line number, ingestion run and pipeline version.",
    evidence: (c) => `${c.sourceFiles} source files with checksums` },
  { id: "diff", title: "Version comparison (ConnectomeDiff)",
    what: "Set-based difference between two states of the database.",
    here: "Neurons, annotations and connections that were added, removed or modified between releases, stored with weight deltas. Comparing two datasets is a separate mode.",
    evidence: (c) => `${c.diffRuns} diff runs stored` },
  { id: "matching", title: "Entity resolution",
    what: "Deciding when records in two sources describe the same real-world thing.",
    here: "FlyWire neurons are matched to hemibrain cell types, each with a confidence and the evidence. Nothing is approved automatically, and unmatched neurons are kept with the reason.",
    evidence: (c) => `${num(c.mappings)} mappings recorded` },
  { id: "nosql", title: "NoSQL document store",
    what: "Schema-flexible documents for data whose shape varies.",
    here: "The original source rows of each neuron, whose columns differ by dataset and release, are stored in MongoDB and linked to PostgreSQL by key.",
    evidence: (c) => `${num(c.mongoDocs)} documents`, link: "#nosql" },
  { id: "distributed", title: "Distributed database",
    what: "Fragmentation, replication and query routing across several servers.",
    here: "Three partitioning strategies on 3 PostgreSQL shards, each deployment checked row by row against the coordinator.",
    evidence: (c) => c.cutSummary, link: "#distributed" },
];
CONCEPTS.forEach((c) => { CONCEPT_NAME[c.id] = c.title; });

function renderConcepts() {
  const c = ctx.facts;
  $("concept-cards").innerHTML = CONCEPTS.map((k, i) => {
    const tries = EXAMPLES.sql.filter((e) => e.concept === k.id);
    let ev = "";
    try { ev = k.evidence(c) || ""; } catch { ev = ""; }
    const known = ev && !/undefined|NaN|–/.test(ev);
    return `<article class="concept" id="c-${k.id}">
      <h4><span class="idx">${i + 1}</span>${esc(k.title)}</h4>
      <p class="what">${esc(k.what)}</p>
      <p>${k.here}</p>
      ${known ? `<p class="evidence">${esc(ev)}</p>` : ""}
      ${k.extra || ""}
      <div class="tries">${tries.map((e) => `<button type="button" class="ghost try" data-example="${e.id}">Try: ${esc(e.title)}</button>`).join("")}
        ${k.action || ""}${k.link ? `<a class="more" href="${k.link}">See the implementation ↓</a>` : ""}</div>
    </article>`;
  }).join("");
  document.querySelectorAll("button.try").forEach((b) => b.addEventListener("click", () => loadSqlExample(b.dataset.example)));
  $("run-integrity")?.addEventListener("click", runIntegrity);
}

async function runIntegrity() {
  const panel = $("integrity");
  panel.hidden = false;
  $("integrity-out").innerHTML = `<p class="note">sending 9 invalid statements…</p>`;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const r = await api("demo/integrity", null, {});
    const ok = r.filter((x) => x.rejected).length;
    $("integrity-out").innerHTML = `<p>${status(ok === r.length, `${ok} of ${r.length} rejected by PostgreSQL`)}</p>` +
      r.map((x) => `<div class="violation">
        <div class="v-head"><span class="pill">${esc(x.rule)}</span> ${esc(x.what)}</div>
        <pre class="code small">${highlight(x.sql, "sql")}</pre>
        <div class="${x.rejected ? "v-err" : "v-bad"}">${x.rejected ? `✕ ERROR ${esc(x.sqlstate)}: ${esc(x.message)}` : `⚠ ${esc(x.message)}`}
          ${x.constraint ? `<span class="note">· constraint <code>${esc(x.constraint)}</code></span>` : ""}</div>
      </div>`).join("");
  } catch (e) { fail("integrity-out", e); }
}

// ================================================================ SQL implementation
let CATALOG = null;
let catTab = "relations";
const CAT_COLS = {
  relations: [{ key: "schema", label: "Schema" }, { key: "name", label: "Name", mono: true }, { key: "kind", label: "Kind" },
    { fn: (r) => num(r.columns), label: "Columns", num: true }, { fn: (r) => num(r.approx_rows), label: "Rows (approx.)", num: true },
    { fn: (r) => bytes(r.bytes), label: "Size", num: true }],
  constraints: [{ key: "table", label: "Table", mono: true }, { key: "type", label: "Type" }, { key: "name", label: "Name", mono: true },
    { fn: (r) => `<code>${esc(r.definition)}</code>`, label: "Definition" }],
  indexes: [{ key: "table", label: "Table", mono: true }, { key: "name", label: "Index", mono: true }, { key: "method", label: "Type" },
    { fn: (r) => bytes(r.bytes), label: "Size", num: true }, { fn: (r) => `<code>${esc(r.definition.replace(/^CREATE (UNIQUE )?INDEX \S+ ON /, ""))}</code>`, label: "Definition" }],
  triggers: [{ key: "table", label: "Table", mono: true }, { key: "name", label: "Trigger", mono: true }, { key: "function", label: "Function", mono: true },
    { fn: (r) => `<code>${esc(r.definition)}</code>`, label: "Definition" }],
  functions: [{ key: "schema", label: "Schema" }, { key: "name", label: "Function", mono: true }, { fn: (r) => `<code>${esc(r.arguments)}</code>`, label: "Arguments" },
    { fn: (r) => `<code>${esc(r.returns)}</code>`, label: "Returns" }, { key: "language", label: "Language" }],
};
const CAT_LABEL = { relations: "Tables & views", constraints: "Constraints", indexes: "Indexes", triggers: "Triggers", functions: "Functions" };
function renderCatalog() {
  if (!CATALOG) return;
  $("cat-tabs").innerHTML = Object.keys(CAT_COLS).map((k) => `<button type="button" class="${k === catTab ? "on" : ""}" data-cat="${k}">${CAT_LABEL[k]} (${CATALOG[k].length})</button>`).join("");
  $("cat-tabs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { catTab = b.dataset.cat; renderCatalog(); }));
  const q = $("cat-filter").value.trim().toLowerCase();
  const rows = CATALOG[catTab].filter((r) => !q || JSON.stringify(r).toLowerCase().includes(q));
  $("cat-out").innerHTML = table(CAT_COLS[catTab], rows) + (rows.length ? "" : `<p class="note">No match.</p>`);
}
$("cat-filter").addEventListener("input", renderCatalog);

async function showSource(path) {
  $("code-title").textContent = path;
  $("code-view").innerHTML = `<p class="note">loading…</p>`;
  document.querySelectorAll("#sql-files button").forEach((b) => b.classList.toggle("on", b.dataset.source === path));
  try {
    const f = await api("source/file", { path });
    const lang = path.endsWith(".sql") ? "sql" : path.endsWith(".py") ? "py" : "yaml";
    $("code-view").innerHTML = codeBlock(f.text, lang);
    $("code-view").scrollTop = 0;
  } catch (e) { fail("code-view", e); }
}
document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-source]");
  if (b) {
    if (!b.closest("#sql-files")) $("sql").scrollIntoView({ behavior: "smooth" });
    showSource(b.dataset.source);
  }
  const m = ev.target.closest("[data-mongo-example]");
  if (m) loadMongoExample(m.dataset.mongoExample);
  const s = ev.target.closest("[data-sql-example]");
  if (s) loadSqlExample(s.dataset.sqlExample);
});

const MIGRATION_ABOUT = {
  "0001": "core schema: tables, keys, constraints, append-only trigger",
  "0002": "temporal queries: time travel, history, lineage functions",
  "0003": "ConnectomeDiff: set-based release comparison",
  "0004": "entity resolution: identities, type crosswalk, review",
  "0005": "MongoDB sync log (cross-store bookkeeping)",
  "0006": "brain regions and per-region synapse counts",
  "0007": "ER-diagram views (schema er)",
  "0008": "distribution: shards, strategies, deployments",
  "0009": "materialized view release_stats",
  "0010": "read-only role for this console",
};
async function loadSql() {
  try {
    const files = await api("source");
    const btn = (f, label) => `<button type="button" class="file" data-source="${esc(f.path)}"><b>${esc(label)}</b><span>${esc(f.about)} · ${num(f.lines)} lines</span></button>`;
    const sql = files.filter((f) => f.path.endsWith(".sql"));
    const other = files.filter((f) => !f.path.endsWith(".sql"));
    $("sql-files").innerHTML = `<div class="flabel">Migrations (DDL)</div>` + sql.map((f) => {
      const n = f.path.split("/").pop().slice(0, 4);
      return btn({ ...f, about: MIGRATION_ABOUT[n] || f.about }, f.path.split("/").pop());
    }).join("") + `<div class="flabel">Application code that uses the databases</div>` + other.map((f) => btn(f, f.path)).join("");
    showSource(sql[0].path);
  } catch (e) { fail("sql-files", e); }
}

// ================================================================ NoSQL implementation
async function loadMongo() {
  let m;
  try { m = await api("mongo/overview"); } catch (e) { fail("mongo-kpis", e); return; }
  $("mongo-kpis").innerHTML = [
    kpi(num(m.documents), "documents (one per neuron)"),
    kpi(bytes(m.data_bytes), `data · ${bytes(m.storage_bytes)} on disk (compressed)`),
    kpi(bytes(m.avg_document_bytes), "average document"),
    kpi(m.indexes.length, `indexes · ${bytes(m.index_bytes)}`),
    kpi(`v${esc(m.server_version)}`, `MongoDB · database <code>${esc(m.database)}</code>`),
  ].join("");
  $("mongo-indexes").innerHTML = table([
    { key: "name", label: "Index", mono: true },
    { fn: (i) => `<code>${esc(i.keys.map(([k, d]) => `${k}: ${d}`).join(", "))}</code>`, label: "Keys" },
    { fn: (i) => (i.unique || i.name === "_id_" ? "yes" : "no"), label: "Unique" },
  ], m.indexes);
  $("mongo-syncs").innerHTML = table([
    { key: "dataset", label: "Dataset" }, { key: "release", label: "Release" },
    { fn: (s) => num(s.documents_written), label: "Documents", num: true },
    { fn: (s) => num(s.records_written), label: "Source rows", num: true },
    { fn: (s) => `${s.seconds} s`, label: "Took", num: true },
  ], m.syncs);
  const pg = m.sample_postgres_row || {};
  $("cross-store").innerHTML = `<div><h4 class="store">PostgreSQL · <code>cc.dataset_neuron</code></h4>
      ${table([{ key: "dataset_neuron_id", label: "dataset_neuron_id", num: true }, { key: "source_neuron_id", label: "source_neuron_id", mono: true },
        { fn: (r) => `<mark>${esc(r.metadata_doc_id)}</mark>`, label: "metadata_doc_id", mono: true }], [pg])}
      <p class="note">Clean, typed facts live in their own tables (annotation, connection…), keyed by <code>dataset_neuron_id</code>.</p>
      <button type="button" class="ghost" data-sql-example="cross-store">Run in SQL console</button></div>
    <div><h4 class="store">MongoDB · <code>neuron_source_record</code></h4>
      <details open><summary><mark>"_id": ${esc(JSON.stringify(m.sample._id))}</mark>, ${Object.keys(m.sample.releases || {}).length} releases</summary>
      <pre class="json tall">${esc(pretty(m.sample))}</pre></details></div>`;
}

// ================================================================ distributed
const STRATEGY_LABEL = { hash: "Hash (by neuron id)", anatomical: "Anatomical (by brain region)", community: "Community (graph clusters)" };
async function loadDistribution() {
  let d;
  try { d = await api("distribution"); } catch (e) { fail("strategies", e); return; }
  const deps = d.deployments;
  const live = d.strategies.filter((s) => deps[s.code]);
  ctx.facts.cutSummary = live.map((s) => `${s.code} ${pct(deps[s.code].cut_fraction, 0)}`).join(" · ") + " of edges cut";
  $("strategies").innerHTML = `<div class="grid2"><div>${table([
    { fn: (s) => `<b>${esc(STRATEGY_LABEL[s.code])}</b><div class="note">${esc(s.description)}</div>`, label: "Strategy" },
    { fn: (s) => pct(deps[s.code].cut_fraction), label: "Edges cut", num: true },
    { fn: (s) => `${deps[s.code].storage_overhead}×`, label: "Storage vs 1 copy", num: true },
    { fn: (s) => pct(deps[s.code].max_share), label: "Largest shard", num: true },
    { fn: (s) => status(deps[s.code].verified, deps[s.code].verified ? "verified" : "not verified"), label: "Copy check" },
  ], live)}</div><div><h4 class="chart-t">Edges stored on two shards (lower is better)</h4>${bars(live.map((s) => ({ label: s.code, value: deps[s.code].cut_fraction })),
    { max: 1, value: (i) => pct(i.value), tip: (i) => `${i.label}: ${pct(i.value, 2)} of connections cross shards` })}
    <p class="note">Random hashing over 3 shards cuts about two thirds of all edges by construction. Grouping by brain region or graph community cuts far fewer, so most queries need one shard.</p></div></div>`;
  $("r-release").innerHTML = (ctx.overview?.releases || []).map((r) => `<option value="${r.release_id}" ${r.dataset === "hemibrain" && r.version_label === "v1.2" ? "selected" : ""}>${esc(r.dataset)} ${esc(r.version_label)}</option>`).join("");
}
function routeKindChanged() {
  const k = $("r-kind").value;
  $("r-neuron-l").hidden = !["neighbours_out", "two_hop"].includes(k);
  $("r-region-l").hidden = k !== "region";
}
$("r-kind").addEventListener("change", routeKindChanged);
$("route-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const rel = (ctx.overview?.releases || []).find((r) => String(r.release_id) === $("r-release").value);
  const body = { strategy: $("r-strategy").value, kind: $("r-kind").value, release: Number($("r-release").value),
    dataset: rel?.dataset, source_id: $("r-neuron").value.trim(), region: $("r-region").value };
  $("route-out").innerHTML = `<p class="note">routing… (the first query per strategy loads the placement map and is slower)</p>`;
  try {
    const r = await api("distributed/query", null, body);
    const shards = [1, 2, 3].map((k) => `<div class="shard ${r.shard_ids.includes(k) ? "hit" : ""}"><b>shard ${k}</b><span>${r.shard_ids.includes(k) ? "queried" : "skipped"}</span></div>`).join("");
    $("route-out").innerHTML = `<div class="route">
      <div class="shards"><div class="shard coord"><b>router</b><span>${esc(r.strategy)}</span></div><div class="arrow">→</div>${shards}</div>
      <div class="kpis">${kpi(`${r.shards_touched} of 3`, "shards touched")}${kpi(num(r.rows), "rows returned")}
        ${kpi(`${ms(r.shard_ms)} ms`, "on the shards")}${kpi(`${ms(r.coordinator_ms)} ms`, "same query, single server")}
        ${kpi(status(r.matches_coordinator, r.matches_coordinator ? "identical" : "DIFFERENT"), "answer vs single server")}</div>
      <h4>SQL sent to each queried shard <span class="note">(%s = parameters bound by the router)</span></h4><pre class="code small">${highlight(r.shard_sql, "sql")}</pre>
      <details><summary>First ${r.sample.length} rows</summary><pre class="json">${esc(r.sample.map((x) => JSON.stringify(x)).join("\n"))}</pre></details>
      <p class="note">Timings from one run on one machine (all shards share it); repeat a query to see warm-cache times. The benchmark below uses repeated runs.</p></div>`;
  } catch (e) { $("route-out").innerHTML = `<pre class="error-box">${esc(e.message)}</pre>`; }
});

// ================================================================ results
const QUERY_LABEL = {
  neighbours_out: "Outgoing partners of a neuron", neighbours_in: "Incoming partners of a neuron",
  two_hop: "Partners of partners (2 hops)", region: "All connections of one brain region", summary: "Totals for a whole release",
};
const TARGETS = ["coordinator", "hash", "anatomical", "community"];
const TARGET_LABEL = { coordinator: "single server", hash: "hash", anatomical: "anatomical", community: "community" };
async function loadBench() {
  let b;
  try { b = await api("benchmarks/latest"); } catch (e) { fail("bench-head", e); return; }
  const c = b.correctness;
  $("bench-head").innerHTML = `<p class="note">${status(c.mismatches === 0, `${num(c.queries_compared)} routed answers compared, ${num(c.mismatches)} mismatches`)}
    · grey = single server, blue = 3 shards · label shows shards touched · ${b.parameters.repeat} timed passes after a warm-up ·
    all shards share one machine, so this measures placement and routing, not network cost.</p>`;
  const kinds = Object.keys(QUERY_LABEL).filter((k) => b.results[k]);
  $("bench-charts").innerHTML = kinds.map((k) => {
    const items = TARGETS.filter((t) => b.results[k][t]).map((t) => ({ label: TARGET_LABEL[t], t, value: b.results[k][t].p50_ms, r: b.results[k][t] }));
    return `<div><h4>${esc(QUERY_LABEL[k])}</h4>${bars(items, {
      quiet: (i) => i.t === "coordinator",
      value: (i) => `${ms(i.value)} ms <small>· ${i.r.mean_shards_touched} shard${i.r.mean_shards_touched === 1 ? "" : "s"}</small>`,
      tip: (i) => `${i.label}: p50 ${i.r.p50_ms} ms, p95 ${i.r.p95_ms} ms, ${num(i.r.mean_rows)} rows, ${i.r.executions} runs`,
    })}</div>`;
  }).join("");
}

const TEST_AREA = {
  test_temporal_store: "temporal storage, integrity, rollback, provenance", test_connectome_diff: "ConnectomeDiff",
  test_entity_resolution: "entity resolution", test_api: "REST API",
  test_metadata_regions_er: "MongoDB, brain regions, ER views", test_distributed: "partitioning, shards, router",
  test_console: "query consoles and integrity demo",
};
async function loadTests() {
  let t;
  try { t = await api("tests/latest"); } catch (e) { fail("tests-head", e); return; }
  const passed = t.tests - t.failures - t.skipped;
  $("tests-head").innerHTML = `<div class="testline"><span class="big">${num(passed)} / ${num(t.tests)}</span>
    ${status(t.failures === 0, t.failures === 0 ? "automated tests passing" : `${t.failures} failing`)}
    <span class="note">${t.seconds.toFixed(0)} s · last run ${esc(new Date(t.timestamp).toLocaleString())} · run on small, labelled test fixtures in separate test databases</span></div>
    <p class="note">${Object.entries(t.files).map(([f, x]) => `${esc(TEST_AREA[f] || f)} ${x.passed}/${x.passed + x.failed + x.skipped}`).join(" · ")}</p>`;
}

const REQUIREMENTS = [
  ["1 Ingestion and schema integration", "done", "source adapters, schema-mapping registry, original ids, files and lines kept"],
  ["2 Cross-dataset entity resolution", "partial", "identities and type-crosswalk candidates with confidence; embedding / GraphSAGE matcher not started"],
  ["3 Temporal, version-aware storage", "done", "valid + transaction time, exclusion constraints, append-only trigger, any release / any time"],
  ["4 Provenance and lineage", "done", "dataset → release → file (SHA-256) → line → run → pipeline version, for every fact"],
  ["5 ConnectomeDiff", "partial", "added / removed / modified with weight deltas, persisted; Isolation Forest anomalies not started"],
  ["6 Distributed storage and query", "done", "3 shards, 3 partitioning strategies, verified copies, query router, benchmark"],
  ["7 API and visualisation", "partial", "REST API, this dashboard and query consoles; React dashboard not started"],
  ["8 Integrity, consistency, reproducibility", "done", "constraints and trigger, one transaction per load, versions recorded on every output"],
  ["Heterogeneous metadata (MongoDB)", "done", "one document per neuron, sync log, cross-store consistency check"],
  ["Team ER diagram", "done", "every entity and attribute queryable in schema er"],
];
function loadRequirements() {
  $("requirements").innerHTML = table([
    { fn: (r) => esc(r[0]), label: "Requirement" },
    { fn: (r) => status(r[1] === "done" ? true : null, r[1] === "done" ? "done" : "partly done"), label: "Status" },
    { fn: (r) => esc(r[2]), label: "What is in place / what is missing" },
  ], REQUIREMENTS);
}

// ================================================================ start
async function factsFromCatalog() {
  CATALOG = await api("catalog");
  const by = (list, f) => list.filter(f).length;
  const types = ["PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "EXCLUDE"];
  const short = { "PRIMARY KEY": "primary keys", "FOREIGN KEY": "foreign keys", UNIQUE: "unique", CHECK: "check", EXCLUDE: "exclusion" };
  Object.assign(ctx.facts, {
    tables: by(CATALOG.relations, (r) => r.kind === "table" && r.schema === "cc"),
    erViews: by(CATALOG.relations, (r) => r.schema === "er"),
    views: by(CATALOG.relations, (r) => r.kind === "view"),
    matviews: by(CATALOG.relations, (r) => r.kind === "materialized view"),
    functions: CATALOG.functions.length,
    btree: by(CATALOG.indexes, (i) => i.method === "btree"),
    gist: by(CATALOG.indexes, (i) => i.method === "gist"),
    constraintSummary: types.map((t) => `${by(CATALOG.constraints, (c) => c.type === t)} ${short[t]}`).join(" · ")
      + ` · ${CATALOG.triggers.length} triggers`,
  });
  renderCatalog();
}

(async function start() {
  try { EXAMPLES = await (await fetch("examples.json")).json(); } catch { /* the consoles work without examples */ }
  fillExamples();
  renderConcepts();
  loadSqlExample("join-releases", false);
  loadMongoExample("m-one", false);
  setMode("sql");
  routeKindChanged();
  loadRequirements();
  await loadOverview();
  const o = ctx.overview;
  if (o) {
    Object.assign(ctx.facts, {
      connections: o.storage.connection_rows_stored, saved: 1 - o.storage.connection_rows_stored / o.storage.rows_if_snapshots,
      failedRuns: o.ingestion_runs.filter((r) => r.status === "failed").length, sourceFiles: o.source_files.length,
      mongoDocs: Object.values(o.mongo_documents).filter((v) => typeof v === "number").reduce((a, b) => a + b, 0),
    });
  }
  await Promise.allSettled([
    factsFromCatalog(),
    api("diffs").then((d) => { ctx.facts.diffRuns = d.filter((x) => x.status === "succeeded").length; }),
    api("mappings/summary").then((m) => { ctx.facts.mappings = m.by_status.reduce((a, r) => a + r.n, 0); }),
    // facts written by failed runs: each fact references a provenance row of its run, so count those
    api("query/sql", null, { sql: `SELECT count(*) FROM provenance p JOIN ingestion_run r ON r.run_id = p.ingestion_run_id
                                    WHERE r.status = 'failed'` }).then((r) => { ctx.facts.partialFacts = r.rows[0][0]; }),
    loadDistribution(),
  ]);
  renderConcepts();
  loadSql();
  loadMongo();
  loadBench();
  loadTests();
}());
