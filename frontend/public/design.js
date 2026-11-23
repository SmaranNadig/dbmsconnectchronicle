// Design: data flow diagrams, normal forms, anomalies and file maintenance,
// the query catalogue and the data dictionary (GET/POST /api/v1/design/*).

const $ = (id) => document.getElementById(id);
const fmt = new Intl.NumberFormat("en");
const num = (v) => (v === null || v === undefined ? "–" : fmt.format(v));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const status = (ok, label) => `<span class="status ${ok === true ? "good" : ok === false ? "bad" : "partial"}">${esc(label)}</span>`;
const bad = (msg) => `<pre class="error-box">${esc(msg)}</pre>`;
const bytes = (n) => {
  if (n === null || n === undefined) return "–";
  const u = ["B", "kB", "MB", "GB"]; let i = 0; let v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return i === 0 ? `${v} B` : `${v.toFixed(1)} ${u[i]}`;
};
const when = (t) => (t ? String(t).replace("T", " ").slice(0, 19) : "never");
const keyText = (k) => "(" + k.join(", ") + ")";
function cell(v) {
  if (v === null || v === undefined) return `<span class="null">NULL</span>`;
  const s = typeof v === "object" ? JSON.stringify(v) : String(v);
  return s.length > 80 ? `<span title="${esc(s)}">${esc(s.slice(0, 77))}…</span>` : esc(s);
}
const table = (head, rows) => `<div class="tbl-wrap"><table><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
  <tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;

async function api(path, body) {
  const token = $("lab-token")?.value;
  const r = await fetch(`/api/v1/design/${path}`, body === undefined ? {} : {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", ...(token ? { "X-Review-Token": token } : {}) },
  });
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail;
    throw new Error(typeof detail === "string" ? detail : `${r.status} ${r.statusText}`);
  }
  return r.json();
}

// ---------------------------------------------------------------- modes
const loaded = {};
const loaders = { dfd: loadDfd, nf: loadNf, files: loadFiles, queries: loadQueries, dict: loadDict };
function show(mode) {
  document.querySelectorAll("#design-mode button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  document.querySelectorAll(".design-pane").forEach((p) => { p.hidden = p.id !== `dz-${mode}`; });
  if (!loaded[mode]) { loaded[mode] = true; loaders[mode](); }
}
$("design-mode").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) show(b.dataset.mode); });

// ------------------------------------------------------------ data flow
let dfdData = null;
async function loadDfd() {
  try {
    dfdData = await api("dfd");
    const c = dfdData.check;
    $("dz-dfd-check").innerHTML = `${status(c.ok, c.ok ? "every notation rule holds" : `${c.errors.length} rule violation(s)`)}
      <span class="note">${dfdData.diagrams.length} diagrams · ${Object.keys(dfdData.externals).length} external entities ·
      ${Object.keys(dfdData.stores).length} data stores · rules checked: ${Object.keys(c.rules).length}</span>`
      + (c.errors.length ? table(["Rule", "Diagram", "Problem"], c.errors.map((v) => [esc(v.rule), esc(v.diagram), esc(v.message)])) : "");
    $("dz-dfd-list").innerHTML = dfdData.diagrams.map((d, i) =>
      `<button type="button" class="${i === 0 ? "on" : ""}" data-id="${esc(d.id)}">${esc(d.level === 0 ? "Context (level 0)" : `Level ${d.level}${d.level > 1 ? `: process ${d.parent}` : ""}`)}</button>`).join("");
    $("dz-dfd-rules").innerHTML = table(["Rule", "Meaning"], Object.entries(c.rules).map(([k, v]) => [`<b>${esc(k)}</b>`, esc(v)]));
    $("dz-dfd-stores").innerHTML = table(["Element", "Name", "Holds / role"], [
      ...Object.entries(dfdData.externals).map(([k, x]) => [`<b>${esc(k)}</b>`, esc(x.name), esc(x.description || "")]),
      ...Object.entries(dfdData.stores).map(([k, x]) => [`<b>${esc(k)}</b>`, esc(x.name), esc(x.holds || "")])]);
    showDiagram(dfdData.diagrams[0].id);
  } catch (e) { $("dz-dfd-check").innerHTML = bad(e.message); }
}
function showDiagram(id) {
  const d = dfdData.diagrams.find((x) => x.id === id);
  document.querySelectorAll("#dz-dfd-list button").forEach((b) => b.classList.toggle("on", b.dataset.id === id));
  $("dz-dfd-img").innerHTML = `<a href="/api/v1/design/dfd/${encodeURIComponent(id)}.svg" target="_blank" rel="noopener" title="Open full size">
    <img src="/api/v1/design/dfd/${encodeURIComponent(id)}.svg" alt="${esc(d.title)}" class="dfd-img"></a>`;
  $("dz-dfd-procs").innerHTML = table(["Process", "Name", "Implemented by"], d.processes.map((p) => [`<b>${esc(p.id)}</b>`, esc(p.name), p.by ? `<code>${esc(p.by)}</code>` : ""]))
    + `<p class="note">${d.flows.length} data flows.</p>`;
}
$("dz-dfd-list").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) showDiagram(b.dataset.id); });

// ---------------------------------------------------------- normal forms
let nfData = null;
async function loadNf() {
  try {
    nfData = await api("normalisation");
    renderNf();
  } catch (e) { $("dz-nf-summary").innerHTML = bad(e.message); }
}
function renderNf() {
  const s = nfData.analysis.summary;
  const ver = nfData.verification;
  const kpis = [[s.relations, "relations analysed"], ...["BCNF", "3NF", "2NF", "1NF"].map((f) => [s.by_highest_form[f] || 0, `highest form ${f}`]),
    [s.controlled_redundancy.length, "kept redundancy, each protected"]];
  $("dz-nf-summary").innerHTML = `<div class="kpis">${kpis.map(([v, l]) => `<div class="kpi"><div class="v">${num(v)}</div><div class="l">${esc(l)}</div></div>`).join("")}</div>
    <p class="note">${ver ? `${status(ver.ok, ver.ok ? "every key and dependency holds on the loaded data" : "a check failed")}
      · verified ${esc(nfData.verified_at)}${nfData.verification_seconds ? ` in ${nfData.verification_seconds} s` : ""}
      · ${ver.coverage.live_tables} live tables, ${ver.coverage.missing.length} not described` : "not verified yet"}</p>`;
  const verBy = Object.fromEntries((ver?.relations || []).map((r) => [r.relation, r]));
  $("dz-nf-rel").innerHTML = table(["Relation", "Candidate keys", "1NF", "2NF", "3NF", "BCNF", "Data check"],
    nfData.analysis.relations.map((r) => {
      const v = verBy[r.relation];
      const vOk = v ? v.dependencies.every((d) => d.status !== "violated") && (v.protections || []).every((p) => p.exists !== false) : null;
      return [`<a href="#" data-rel="${esc(r.relation)}"><code>${esc(r.relation)}</code></a>`, esc(r.candidate_keys.map(keyText).join(" ")),
        ...["1NF", "2NF", "3NF", "BCNF"].map((f) => (r.satisfies[f] ? "✓" : `<span class="bad-text">✗</span>`)),
        v ? status(vOk, vOk ? "holds" : "violated") : "–"];
    }));
  showRelation(nfData.analysis.relations[0].relation);
  $("dz-nf-walk").innerHTML = nfData.walkthrough.map((w) => `<div class="panel"><h4>${esc(w.form)}: ${esc(w.title)}</h4>
    ${table(["Relation", "Attributes", "Dependencies", "Keys", "Highest form"], w.relations.map((r) => [
      `<b>${esc(r.name)}</b>`, esc(r.attrs.join(", ")), esc((r.fds || []).join("; ")), esc((r.keys || []).map(keyText).join(" ")),
      esc(r.highest_form || "not a relation")]))}
    ${w.relations.some((r) => r.note) ? `<p class="note">${w.relations.filter((r) => r.note).map((r) => esc(r.note)).join(" ")}</p>` : ""}
    ${w.sample && w.form === "UNF" ? `<p class="note"><b>Real rows:</b> ${esc(w.sample.neuron)} in release ${esc(w.sample.release)}, read from the loaded data
      (${num(w.sample.partners_total)} partners; the ${w.sample.partners_shown} strongest are shown). In 1NF every partner row repeats the
      neuron's facts, so they are stored ${num(w.sample.partners_total)} times for this neuron alone.</p>` : ""}
    ${w.relations.filter((r) => r.rows?.length).map((r) => `<p class="note"><code>${esc(r.name)}</code></p>`
      + table(r.attrs, r.rows.map((row) => row.map(cell)))).join("")}
    ${w.sample?.note ? `<p class="note"><i>Note:</i> ${esc(w.sample.note)}.</p>` : ""}
    ${w.decision ? `<p class="note"><b>Decision:</b> ${esc(w.decision)}</p>` : ""}</div>`).join("");
}
function showRelation(name) {
  const r = nfData.analysis.relations.find((x) => x.relation === name);
  const v = (nfData.verification?.relations || []).find((x) => x.relation === name);
  $("dz-nf-detail").innerHTML = `<h4><code>${esc(r.relation)}</code> · highest form ${esc(r.highest_form)}</h4>
    <p class="note">${esc(r.purpose)}</p>
    <p class="note"><b>Attributes:</b> ${esc(r.attributes.join(", "))}</p>
    ${table(["Functional dependency"], r.fds.map((f) => [`<code>${esc(f)}</code>`]))}
    <p class="note"><b>Minimal cover:</b> ${r.minimal_cover.map((f) => `<code>${esc(f)}</code>`).join(" · ")}</p>
    <p class="note"><b>Candidate keys:</b> ${esc(r.candidate_keys.map(keyText).join(" "))} · <b>prime:</b> ${esc(r.prime.join(", "))}</p>
    ${r.first_nf?.non_scalar && Object.keys(r.first_nf.non_scalar).length ? `<p class="note"><b>Atomicity (1NF):</b> ${Object.entries(r.first_nf.non_scalar).map(([k, t]) => `<code>${esc(k)}</code>: ${esc(t)}`).join("; ")}</p>` : ""}
    ${r.violations.length ? table(["Form", "Dependency", "Why it is not met"], r.violations.map((x) => [esc(x.form), `<code>${esc(x.fd)}</code>`, esc(x.reason)])) : ""}
    ${r.redundancy.length ? `<p class="note"><b>Redundancy kept on purpose, and what keeps it consistent:</b></p>` + table(["Dependency", "Protected by", "Why"], r.redundancy.map((x) => [`<code>${esc(x.fd)}</code>`, x.enforced_by.map((c) => `<code>${esc(c)}</code>`).join(" "), esc(x.why)])) : ""}
    ${v ? `<p class="note"><b>Checked on the loaded data:</b></p>` + table(["Dependency", "Status", "How"], v.dependencies.map((d) => [`<code>${esc(d.fd)}</code>`, status(d.status !== "violated", d.status), esc(d.by ? `${d.by}: ${d.note || ""}` : d.note || "")])) : ""}`;
}
$("dz-nf-rel").addEventListener("click", (e) => { const a = e.target.closest("a[data-rel]"); if (a) { e.preventDefault(); showRelation(a.dataset.rel); $("dz-nf-detail").scrollIntoView({ block: "nearest" }); } });
$("dz-nf-verify").addEventListener("click", async () => {
  $("dz-nf-verify-meta").textContent = "checking every key and dependency on the loaded data (about 20 s)…";
  try {
    const r = await api("normalisation/verify", {});
    Object.assign(nfData, r);
    renderNf();
    $("dz-nf-verify-meta").textContent = `done in ${r.verification_seconds} s`;
  } catch (e) { $("dz-nf-verify-meta").textContent = e.message; }
});

// ------------------------------------------------- anomalies and files
async function loadFiles() {
  try {
    const r = await api("files");
    $("dz-files-list").innerHTML = `<p class="note">Read ${esc(r.read_at)} from <code>pg_class</code> and <code>pg_stat_user_tables</code>.</p>`
      + `<div class="scroll">` + table(["Table", "File", "Heap", "Indexes", "Pages", "Live rows", "Dead rows", "Seq / index scans", "Last vacuum", "Last analyze"],
        r.tables.map((t) => [`<code>${esc(t.schema)}.${esc(t.table)}</code>`, `<code>${esc(t.file)}</code>`, bytes(t.heap_bytes), bytes(t.index_bytes),
          num(t.pages), num(t.live_tuples), num(t.dead_tuples), `${num(t.seq_scan)} / ${num(t.idx_scan)}`,
          esc(when(t.last_vacuum || t.last_autovacuum)), esc(when(t.last_analyze || t.last_autoanalyze))])) + `</div>`;
  } catch (e) { $("dz-files-list").innerHTML = bad(e.message); }
}
$("dz-anom-run").addEventListener("click", async () => {
  $("dz-anom").innerHTML = `<p class="note">running…</p>`;
  try {
    const a = await api("anomalies", {});
    const red = a.redundancy;
    const pair = (title, x) => `<div class="panel"><h4>${esc(title)}</h4><div class="grid2 tight">
      ${["unnormalised", "normalised"].map((k) => `<div><p class="note"><b>${k === "unnormalised" ? "One wide table" : "Normalised lab tables"}</b> ${status(k === "normalised", k === "normalised" ? "no anomaly" : "anomaly")} <code>${esc(x[k].ok ? x[k].status : "rejected")}</code></p>
        <pre class="code">${esc(x[k].sql)}</pre><p class="note">${esc(x[k].result)}${x[k].error ? `<br><code>${esc(x[k].error)}</code>` : ""}</p></div>`).join("")}</div></div>`;
    $("dz-anom").innerHTML = `<p class="note">A copy of the ${num(red.unnormalised_rows)} lab connections with the neuron facts repeated on every row
      (<code>connection_flat</code>). Neuron <code>${esc(red.neuron.source_neuron_id)}</code> (${esc(red.neuron.cell_type)}) is stored
      ${num(red.neuron.copies)} times there and once in <code>lab.neuron</code>; ${num(red.repeated_neuron_facts)} neuron facts are repeated in all.
      Everything below ran in one transaction and was rolled back.</p>`
      + pair("Update anomaly", a.update) + pair("Insertion anomaly", a.insert) + pair("Deletion anomaly", a.delete);
  } catch (e) { $("dz-anom").innerHTML = bad(e.message); }
});
document.querySelectorAll("#dz-maint button").forEach((b) => b.addEventListener("click", async () => {
  const op = b.dataset.op;
  $("dz-maint-out").innerHTML = `<p class="note">running ${esc(op)} on the lab tables…</p>`;
  try {
    const r = await api(`maintenance/${op}`, {});
    $("dz-maint-out").innerHTML = `<pre class="code">${esc(r.statements.map((s) => `${s.sql};   -- ${s.ms} ms`).join("\n"))}</pre>`
      + table(["Table", "Pages", "Live rows", "Dead rows", "Indexes", "Last vacuum", "Last analyze"], r.tables.map((t) => [
        `<code>lab.${esc(t.table)}</code>`, `${num(t.before.pages)} → ${num(t.after.pages)}`, `${num(t.before.live_tuples)} → ${num(t.after.live_tuples)}`,
        `${num(t.before.dead_tuples)} → <b>${num(t.after.dead_tuples)}</b>`, `${bytes(t.before.index_bytes)} → ${bytes(t.after.index_bytes)}`,
        esc(when(t.after.last_vacuum)), esc(when(t.after.last_analyze))]));
    loadFiles();
  } catch (e) { $("dz-maint-out").innerHTML = bad(e.message); }
}));

// ------------------------------------------------------------- queries
let qData = null;
async function loadQueries() {
  try {
    qData = await api("queries");
    const s = qData.summary;
    $("dz-q-summary").innerHTML = s ? `${status(s.passed === s.queries, `${s.passed} of ${s.queries} passed every check`)}
      <span class="note">${s.sql} SQL, ${s.mongo} MongoDB · last full run ${esc(qData.ran_at)}</span>` : `<span class="note">not run yet</span>`;
    $("dz-q-cat").innerHTML = `<option value="">All categories</option>` + qData.categories.map((c) => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join("");
    renderQueries();
  } catch (e) { $("dz-q-summary").innerHTML = bad(e.message); }
}
const targetText = { read: "read-only", trial: "trial on real tables, rolled back", lab: "lab, rolled back" };
function codeOf(q) {
  if (q.sql) return q.sql.trim();
  const { op, ...rest } = q.mongo;
  return `db.neuron_source_record.${op}(${JSON.stringify(rest, null, 2)})`;
}
function resultHtml(r) {
  if (!r) return `<p class="note">Not run yet.</p>`;
  const checks = `<p class="note">${r.checks.map((c) => status(c.ok, c.check)).join(" ")}</p>`;
  if (r.error) return checks + `<pre class="error-box">${esc(r.error)}</pre>`;
  const res = r.result || {}; const p = r.plan || {};
  let body = "";
  if (res.columns?.length) body = `<p class="note">${num(res.row_count)} row(s), ${res.elapsed_ms} ms</p>` + table(res.columns, res.rows.slice(0, 8).map((row) => row.map(cell)));
  else if (res.documents) body = `<p class="note">${num(res.row_count)} document(s), ${res.elapsed_ms} ms</p><pre class="json">${esc(JSON.stringify(res.documents.slice(0, 3), null, 2))}</pre>`;
  else if (res.statements) body = `<p class="note">${res.statements.map((s) => esc(s.status)).join(" · ")}</p>`;
  let plan = "";
  if (p.nodes) plan = `<p class="note"><b>Plan:</b> ${esc(p.top_node)} · indexes ${p.indexes.map((x) => `<code>${esc(x)}</code>`).join(" ") || "none"} · sequential scans ${p.seq_scans.map((x) => `<code>${esc(x)}</code>`).join(" ") || "none"} · planning ${p.planning_ms} ms, execution ${p.execution_ms} ms</p>`;
  else if (p.stages) plan = `<p class="note"><b>Plan:</b> ${esc([...p.stages].reverse().join(" → "))} · indexes ${p.indexes.map((x) => `<code>${esc(x)}</code>`).join(" ") || "none"} · ${num(p.keys_examined)} keys, ${num(p.docs_examined)} documents examined</p>`;
  return checks + plan + body;
}
function renderQueries() {
  const cat = $("dz-q-cat").value;
  $("dz-q-list").innerHTML = qData.queries.filter((q) => !cat || q.category === cat).map((q) => `
    <details class="panel" data-id="${esc(q.id)}"><summary>${q.last ? status(q.last.ok, "") : ""} <b>${esc(q.question)}</b>
      <span class="pill">${q.mongo ? "MongoDB" : "SQL"}</span> <span class="pill">${esc(targetText[q.target || "read"] || q.target)}</span></summary>
      <pre class="code">${esc(codeOf(q))}</pre>
      <p class="note"><b>Why this form:</b> ${esc(q.why)}</p>
      <div class="row"><button type="button" class="ghost" data-run="${esc(q.id)}">Run now</button><span class="note" data-meta="${esc(q.id)}">${q.last ? `last run ${esc(qData.ran_at)}` : ""}</span></div>
      <div data-out="${esc(q.id)}">${resultHtml(q.last)}</div></details>`).join("");
}
$("dz-q-cat").addEventListener("change", renderQueries);
$("dz-q-list").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-run]"); if (!b) return;
  const id = b.dataset.run; const meta = document.querySelector(`[data-meta="${id}"]`);
  meta.textContent = "running…";
  try {
    const r = await api(`queries/${encodeURIComponent(id)}/run`, {});
    document.querySelector(`[data-out="${id}"]`).innerHTML = resultHtml(r);
    meta.textContent = `ran now in ${r.seconds} s`;
  } catch (err) { meta.textContent = err.message; }
});

// ---------------------------------------------------------- dictionary
let dictData = null;
async function loadDict() {
  try {
    dictData = await api("dictionary");
    const s = dictData.summary;
    $("dz-dict-summary").innerHTML = `<div class="kpis">${[[s.tables, "tables"], [s.columns, "columns"], [s.foreign_keys, "foreign keys"], [s.checks, "checks"],
      [s.indexes, "indexes"], [s.triggers, "triggers"], [s.views, "views"], [s.functions, "functions"]].map(([v, l]) => `<div class="kpi"><div class="v">${num(v)}</div><div class="l">${l}</div></div>`).join("")}</div>
      <p class="note">Read from the catalog ${esc(dictData.read_at)}.</p>`;
    $("dz-dict-table").innerHTML = dictData.postgres.tables.map((t) => `<option value="${esc(t.schema)}.${esc(t.name)}">${esc(t.schema)}.${esc(t.name)}</option>`).join("")
      + `<option value="_views">Views</option><option value="_functions">Functions</option><option value="_mongo">MongoDB collections</option>`;
    renderDict();
  } catch (e) { $("dz-dict-summary").innerHTML = bad(e.message); }
}
function renderDict() {
  const v = $("dz-dict-table").value; const pg = dictData.postgres;
  if (v === "_views") { $("dz-dict-out").innerHTML = table(["View", "Kind", "Description"], pg.views.map((x) => [`<code>${esc(x.schema)}.${esc(x.name)}</code>`, x.materialized ? "materialized" : "view", esc(x.description || "")])); return; }
  if (v === "_functions") { $("dz-dict-out").innerHTML = table(["Function", "Returns", "Language", "Volatility"], pg.functions.map((f) => [`<code>${esc(f.schema)}.${esc(f.name)}(${esc(f.arguments)})</code>`, cell(f.returns), esc(f.language), esc(f.volatility)])); return; }
  if (v === "_mongo") {
    $("dz-dict-out").innerHTML = dictData.mongo.map((m) => `<h4><code>${esc(m.database)}.${esc(m.name)}</code> · ${num(m.documents)} documents</h4>`
      + table(["Index", "Keys", "Unique"], m.indexes.map((i) => [`<code>${esc(i.name)}</code>`, `<code>${esc(JSON.stringify(i.keys))}</code>`, i.unique ? "yes" : ""]))
      + (m.validator ? `<p class="note"><b>Validator</b></p><pre class="json">${esc(JSON.stringify(m.validator, null, 2))}</pre>` : `<p class="note">No server-side validator: documents keep every original source column.</p>`)).join("");
    return;
  }
  const t = pg.tables.find((x) => `${x.schema}.${x.name}` === v);
  $("dz-dict-out").innerHTML = `<h4><code>${esc(v)}</code></h4><p class="note">${esc(t.description || "")} · ${num(t.approx_rows)} rows · ${bytes(t.total_bytes)} with indexes</p>`
    + table(["#", "Column", "Type", "Null", "Key", "Default", "Description"], t.columns.map((c) => [c.position, `<code>${esc(c.name)}</code>`, esc(c.type), c.not_null ? "" : "yes", esc(c.key), c.generated ? "generated" : c.identity ? "identity" : c.default_expr ? cell(c.default_expr) : "", esc(c.description || "")]))
    + table(["Constraint", "Kind", "Definition"], t.constraints.map((k) => [`<code>${esc(k.name)}</code>`, esc(k.kind), `<code>${esc(k.definition)}</code>`]))
    + (t.indexes.length ? table(["Index", "Method", "Size", "Definition"], t.indexes.map((i) => [`<code>${esc(i.name)}</code>`, esc(i.method), bytes(i.bytes), `<code>${esc(i.definition)}</code>`])) : "")
    + (t.triggers.length ? table(["Trigger", "Definition"], t.triggers.map((g) => [`<code>${esc(g.name)}</code>`, `<code>${esc(g.definition)}</code>`])) : "");
}
$("dz-dict-table").addEventListener("change", renderDict);

// ---------------------------------------------------------------- start
let started = false;
function maybeStart() {
  if (started || location.hash.slice(1).split("?")[0] !== "design") return;
  started = true;
  show("dfd");
}
window.addEventListener("hashchange", maybeStart);
maybeStart();
