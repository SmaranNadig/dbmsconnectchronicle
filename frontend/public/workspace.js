const $ = (id) => document.getElementById(id);
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const number = (value) => new Intl.NumberFormat("en").format(value ?? 0);
const pretty = (value) => JSON.stringify(value, null, 2);
const state = {
  context: null,
  neuron: null,
  detail: null,
  exploreOffset: 0,
  eventOffset: 0,
  qualityOffset: 0,
  matchingOffset: 0,
  diff: null,
  diffs: [],
  models: [],
  searchGeneration: 0,
  selectionGeneration: 0,
};
const empty = (message) => `<div class="empty">${escape(message)}</div>`;
const badge = (value, kind = value) =>
  `<span class="badge ${escape(kind)}">${escape(value)}</span>`;
const metric = (value, label) =>
  `<div class="kpi"><div class="v">${escape(value)}</div><div class="l">${escape(label)}</div></div>`;
const label = (id) =>
  String(id).length > 17
    ? `${String(id).slice(0, 7)}…${String(id).slice(-5)}`
    : String(id);

async function api(path, params = {}, body, headers = {}) {
  const url = new URL(`/api/v1/${path}`, location.origin);
  for (const [key, value] of Object.entries(params))
    if (value !== null && value !== undefined && value !== "")
      url.searchParams.set(key, value);
  const response = await fetch(
    url,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json", ...headers },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    let data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : pretty(data.detail) || `${response.status} ${response.statusText}`,
    );
  }
  return response.json();
}
function guard(action, target) {
  return async (event) => {
    try {
      await action(event);
    } catch (error) {
      const message = `<p class="err" role="alert">${escape(error.message)}</p>`;
      if (target === "job-history" && view() !== "methodology")
        evidence("Operation could not complete", message);
      else $(target).innerHTML = message;
    }
  };
}
function grid(columns, rows) {
  return `<div class="tbl-wrap"><table><thead><tr>${columns.map(([name]) => `<th>${escape(name)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map(([, render]) => `<td>${render(row)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function iso(id) {
  const value = $(id).value;
  if (
    state.exactKnowledge?.field === id &&
    state.exactKnowledge.value === value
  )
    return state.exactKnowledge.timestamp;
  return value ? new Date(value).toISOString() : undefined;
}
function tokenHeader() {
  return { "X-Review-Token": $("m-token").value };
}
function releases(dataset) {
  return state.context.releases.filter((r) => r.dataset === dataset);
}
function fillSelect(id, items, value) {
  $(id).innerHTML = items
    .map(([v, text]) => `<option value="${escape(v)}">${escape(text)}</option>`)
    .join("");
  if (value !== undefined && items.some(([v]) => String(v) === String(value)))
    $(id).value = value;
}

function view() {
  return location.hash.slice(1).split("?")[0] || "overview";
}
function navigate(name, params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(
      ([, v]) => v !== null && v !== undefined && v !== "",
    ),
  ).toString();
  location.hash = name + (query ? `?${query}` : "");
}
function route(event) {
  let active = view();
  if (!document.querySelector(`main>section[id="${CSS.escape(active)}"]`))
    active = "overview";
  document
    .querySelectorAll("main>section")
    .forEach((section) => (section.hidden = section.id !== active));
  document.querySelectorAll(".tabs a").forEach((a) => {
    const selected = a.hash === `#${active}`;
    a.classList.toggle("active", selected);
    if (selected) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const lab = [
    "console",
    "editor",
    "concepts",
    "sql",
    "nosql",
    "distributed",
    "results",
  ].includes(active);
  document.querySelector(".nav-more").classList.toggle("active", lab);
  if (event?.type === "hashchange") document.querySelector(".nav-more").open = false;
  if (!state.context) return;
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  if (active === "explorer") {
    const ds = params.get("dataset");
    if (ds && releases(ds).length) {
      $("e-dataset").value = ds;
      fillReleases("e");
    }
    if (
      params.get("release") &&
      releases($("e-dataset").value).some(
        (r) => r.version_label === params.get("release"),
      )
    )
      $("e-release").value = params.get("release");
    if (params.has("as_of")) {
      const date = new Date(params.get("as_of"));
      if (!Number.isNaN(date.getTime()))
        $("e-asof").value = new Date(
          date.getTime() - date.getTimezoneOffset() * 60000,
        )
          .toISOString()
          .slice(0, 23);
      state.exactKnowledge = {
        field: "e-asof",
        value: $("e-asof").value,
        timestamp: params.get("as_of"),
      };
    }
    const id = params.get("neuron");
    if (
      id &&
      (id !== String(state.neuron) ||
        state.renderedRelease !== $("e-release").value ||
        state.renderedAsOf !== iso("e-asof"))
    )
      selectNeuron(Number(id)).catch(
        (e) => ($("e-graph").innerHTML = empty(e.message)),
      );
    else if (!state.searched)
      search().catch((e) => ($("e-results").innerHTML = empty(e.message)));
  }
  if (
    active === "compare" &&
    params.get("diff") &&
    params.get("diff") !== String(state.diff?.diff_run_id)
  )
    loadDiff(Number(params.get("diff"))).catch(
      (e) => ($("c-job").innerHTML = empty(e.message)),
    );
  if (active === "quality")
    loadModels().catch((e) => ($("q-results").innerHTML = empty(e.message)));
  if (active === "matching")
    loadMatching().catch((e) => ($("m-results").innerHTML = empty(e.message)));
  if (active === "methodology")
    loadJobs().catch((e) => ($("job-history").innerHTML = empty(e.message)));
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
route();

function fillRegions() {
  const rel = releases($("e-dataset").value).find(
    (r) => r.version_label === $("e-release").value,
  );
  fillSelect("e-region", [
    ["", "All regions"],
    ...state.context.regions
      .filter((r) => r.release_id === rel?.release_id)
      .map((r) => [r.code, r.code]),
  ]);
}
function fillReleases(prefix) {
  const rs = releases($(`${prefix}-dataset`).value);
  if (prefix === "e") {
    fillSelect(
      "e-release",
      rs.map((r) => [r.version_label, r.version_label]),
      rs.at(-1)?.version_label,
    );
    fillRegions();
  } else {
    fillSelect(
      "c-before",
      rs.map((r) => [r.version_label, r.version_label]),
      rs.at(-2)?.version_label,
    );
    fillSelect(
      "c-after",
      rs.map((r) => [r.version_label, r.version_label]),
      rs.at(-1)?.version_label,
    );
  }
}
$("e-dataset").addEventListener("change", () => {
  fillReleases("e");
  state.exploreOffset = 0;
  state.neuron = null;
  state.detail = null;
  state.selectionGeneration++;
  $("e-source-records").disabled = true;
  search().catch((error) => ($("e-results").innerHTML = empty(error.message)));
});
$("c-dataset").addEventListener("change", () => fillReleases("c"));
$("e-release").addEventListener("change", () => {
  fillRegions();
  if (state.neuron)
    selectNeuron(state.neuron).catch(
      (e) => ($("e-graph").innerHTML = empty(e.message)),
    );
});
$("explore-form").addEventListener(
  "submit",
  guard(async (event) => {
    event.preventDefault();
    state.exploreOffset = 0;
    await search();
  }, "e-results"),
);
async function search() {
  state.searched = true;
  const generation = ++state.searchGeneration;
  $("e-results").innerHTML = empty("Searching the selected release…");
  const rows = await api("neurons", {
    dataset: $("e-dataset").value,
    release: $("e-release").value,
    as_of: iso("e-asof"),
    source_id: $("e-source").value.trim(),
    cell_type: $("e-type").value.trim(),
    region: $("e-region").value,
    limit: 26,
    offset: state.exploreOffset,
  });
  if (generation !== state.searchGeneration) return;
  $("e-count").textContent =
    `${state.exploreOffset + 1}–${state.exploreOffset + Math.min(rows.length, 25)}`;
  $("e-results").innerHTML = rows.length
    ? rows
        .slice(0, 25)
        .map(
          (row) =>
            `<button type="button" class="search-item ${row.dataset_neuron_id === state.neuron ? "selected" : ""}" data-neuron="${row.dataset_neuron_id}"><code>${escape(row.source_neuron_id)}</code><span>${escape(row.cell_type || "Type not annotated")} · ${escape(row.release)}</span></button>`,
        )
        .join("")
    : empty("No neurons match this release and knowledge time.");
  $("e-prev").disabled = state.exploreOffset === 0;
  $("e-next").disabled = rows.length < 26;
  if (rows.length) await selectNeuron(rows[0].dataset_neuron_id);
  else {
    state.neuron = null;
    state.detail = null;
    $("e-count").textContent = "0 results";
    $("e-selected").innerHTML = "<h3>No selected neuron</h3>";
    $("e-graph").innerHTML = empty("Change the filters to find a neuron.");
    $("e-timeline").innerHTML = "";
    $("e-connections").innerHTML = "";
    $("e-graph-meta").textContent = "";
    $("e-source-records").disabled = true;
  }
}
$("e-prev").addEventListener(
  "click",
  guard(async () => {
    state.exploreOffset = Math.max(0, state.exploreOffset - 25);
    await search();
  }, "e-results"),
);
$("e-next").addEventListener(
  "click",
  guard(async () => {
    state.exploreOffset += 25;
    await search();
  }, "e-results"),
);
function evidence(title, body) {
  $("evidence-title").textContent = title;
  $("evidence-body").innerHTML = body;
  if (!$("evidence-dialog").open) $("evidence-dialog").showModal();
}
$("evidence-close").addEventListener("click", () =>
  $("evidence-dialog").close(),
);
$("evidence-dialog").addEventListener("click", (event) => {
  if (event.target === $("evidence-dialog")) $("evidence-dialog").close();
});
function sourceSummary(value) {
  const leaves = [];
  function walk(item, path = "") {
    if (Array.isArray(item)) {
      item.slice(0, 8).forEach((v, i) => walk(v, `${path}[${i}]`));
    } else if (item && typeof item === "object") {
      for (const [k, v] of Object.entries(item))
        walk(v, path ? `${path}.${k}` : k);
    } else if (
      item !== null &&
      /file|line|sha256|version|dataset|release|transformation|source_url|cosine|score|coverage|type|side|reason/.test(
        path,
      )
    )
      leaves.push([path, String(item)]);
  }
  walk(value);
  const priority = (key) =>
    /archive_path|member_path/.test(key)
      ? 0
      : /source_lines|source_line/.test(key)
        ? 1
        : /sha256/.test(key)
          ? 2
          : /transformation|pipeline_version/.test(key)
            ? 3
            : /dataset.code|version_label/.test(key)
              ? 4
              : 9;
  leaves.sort((a, b) => priority(a[0]) - priority(b[0]));
  return `<div class="evidence-summary">${leaves
    .slice(0, 20)
    .map(
      ([k, v]) =>
        `<div><small>${escape(k)}</small><code>${escape(v)}</code></div>`,
    )
    .join(
      "",
    )}</div><details><summary>Full structured evidence</summary><pre class="json">${escape(pretty(value))}</pre></details>`;
}
async function showProvenance(table, id) {
  const data = await api(`provenance/${table}/${id}`);
  evidence("Source provenance", sourceSummary(data));
}
async function selectNeuron(id) {
  const generation = ++state.selectionGeneration;
  state.neuron = id;
  $("e-graph").innerHTML = empty("Reconstructing the neighbourhood…");
  const rel = $("e-release").value,
    asof = iso("e-asof");
  const [detail, graph, connections] = await Promise.all([
    api(`neurons/${id}`, { as_of: asof }),
    api(`workspace/neurons/${id}/graph`, {
      release: rel,
      as_of: asof,
      hops: $("e-hops").value,
      min_weight: $("e-weight").value,
      direction: $("e-direction").value,
    }),
    api(`neurons/${id}/connections`, {
      release: rel,
      as_of: asof,
      min_weight: $("e-weight").value,
      limit: 50,
    }),
  ]);
  if (generation !== state.selectionGeneration) return;
  state.detail = detail;
  state.renderedRelease = rel;
  state.renderedAsOf = asof;
  if (detail.dataset !== $("e-dataset").value) {
    $("e-dataset").value = detail.dataset;
    fillReleases("e");
  }
  const current = detail.states.find((s) => s.release === rel);
  $("e-selected").innerHTML =
    `<h3>Neuron <code>${escape(detail.source_neuron_id)}</code></h3><p class="note">${escape(detail.dataset)} / ${escape(rel)} · ${escape(current?.annotations?.cell_type || "Unannotated type")} · ${current?.present ? "present" : "absent at this knowledge time"}</p>`;
  $("e-source-records").disabled = false;
  drawGraph("e-graph", graph, {
    onNode: (node) =>
      selectNeuron(node).catch(
        (e) => ($("e-graph").innerHTML = empty(e.message)),
      ),
  });
  $("e-graph-meta").textContent =
    `${graph.nodes.length} nodes · ${graph.edges.length} directed connections · ${graph.truncated ? "View capped: showing strongest connections." : "Within the requested view limits."} Region filters use release-derived aggregates.`;
  $("e-timeline").innerHTML = detail.states
    .map(
      (s) =>
        `<button type="button" class="timeline-card ${s.present ? "" : "absent"} ${s.release === rel ? "selected" : ""}" data-release="${escape(s.release)}"><b>${escape(s.release)}</b><span>${s.present ? "Present" : "Absent"}</span><span>${escape(s.annotations?.cell_type || "No cell type")}</span></button>`,
    )
    .join("");
  $("e-connections").innerHTML = connections.length
    ? grid(
        [
          ["Direction", (r) => escape(r.direction)],
          [
            "Partner",
            (r) =>
              `<button class="link-button" data-neuron="${r.partner_neuron_id}">${escape(r.partner_source_id)}</button>`,
          ],
          ["Synapses", (r) => number(r.synapse_count)],
          [
            "Evidence",
            (r) =>
              `<button class="link-button" data-provenance="connection:${r.connection_id}">Trace source ↗</button>`,
          ],
        ],
        connections,
      )
    : empty("No connections meet these filters.");
  document
    .querySelectorAll(".search-item")
    .forEach((e) =>
      e.classList.toggle("selected", Number(e.dataset.neuron) === id),
    );
  if (view() === "explorer")
    history.replaceState(
      null,
      "",
      `#explorer?${new URLSearchParams({ dataset: detail.dataset, release: rel, neuron: id, ...(asof ? { as_of: asof } : {}) })}`,
    );
}
for (const id of ["e-hops", "e-direction", "e-weight", "e-asof"])
  $(id).addEventListener(
    "change",
    guard(async () => {
      if (state.neuron) await selectNeuron(state.neuron);
    }, "e-graph"),
  );
$("e-source-records").addEventListener(
  "click",
  guard(async () => {
    const data = await api(`neurons/${state.neuron}/provenance`);
    const entries =
      state.detail.source_records?.filter(
        (r) => r.release === $("e-release").value,
      ) || [];
    evidence(
      "Neuron source evidence",
      sourceSummary(data) +
        `<h3>Original rows for ${escape($("e-release").value)}</h3><p class="note">Published source rows from the latest metadata sync, separate from the selected knowledge time.</p>` +
        (entries.length
          ? `<pre class="json">${escape(pretty(entries))}</pre>`
          : empty("Original rows have not been synced for this release.")),
    );
  }, "e-connections"),
);

function drawGraph(target, graph, { positions, onNode, selectedEvent } = {}) {
  const all = positions || graph.nodes.map((n) => n.id),
    root = graph.root;
  const rest = all.filter((id) => id !== root),
    xy = new Map([[root, [450, 270]]]);
  rest.forEach((id, i) => {
    const ring = rest.length > 24 && i >= 24 ? 1 : 0,
      count = ring ? rest.length - 24 : Math.min(rest.length, 24),
      index = ring ? i - 24 : i;
    const angle = (index / count) * Math.PI * 2 - Math.PI / 2;
    const radius = ring ? 230 : rest.length > 24 ? 145 : 205;
    xy.set(id, [
      450 + Math.cos(angle) * radius * 1.48,
      270 + Math.sin(angle) * radius,
    ]);
  });
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]));
  const paths = graph.edges
    .map((e) => {
      const a = xy.get(e.source),
        b = xy.get(e.target);
      if (!a || !b) return "";
      const [x, y] = a,
        [u, v] = b,
        dx = u - x,
        dy = v - y,
        len = Math.hypot(dx, dy) || 1;
      const sx = x + (dx / len) * 18,
        sy = y + (dy / len) * 18,
        ex = u - (dx / len) * 22,
        ey = v - (dy / len) * 22;
      const path =
        e.source === e.target
          ? `M${x - 15},${y - 12} C${x - 70},${y - 85} ${x + 70},${y - 85} ${x + 15},${y - 12}`
          : `M${sx},${sy} Q${(x + u) / 2 - dy * 0.07},${(y + v) / 2 + dx * 0.07} ${ex},${ey}`;
      const selected =
        selectedEvent &&
        e.source === selectedEvent.dataset_neuron_id &&
        e.target === selectedEvent.post_neuron_id;
      return `<path class="graph-edge ${selected ? escape(selectedEvent.change_type) : ""}" d="${path}" stroke-width="${Math.min(5, 1 + Math.log1p(e.weight) * 0.5)}" marker-end="url(#${target}-arrow)"><title>${escape(`${nodes.get(e.source)?.label || e.source} → ${nodes.get(e.target)?.label || e.target}: ${e.weight} synapses`)}</title></path>${graph.edges.length <= 35 ? `<text class="edge-label" x="${(x + u) / 2 - dy * 0.04}" y="${(y + v) / 2 + dx * 0.04 - 6}">${e.weight}</text>` : ""}`;
    })
    .join("");
  const marks = all
    .map((id) => {
      const node = nodes.get(id),
        [x, y] = xy.get(id);
      if (!node) return "";
      return `<g class="graph-node ${id === root ? "root" : ""} ${node.present ? "" : "absent"}" transform="translate(${x},${y})" role="button" tabindex="0" data-graph-node="${id}" aria-label="Explore neuron ${escape(node.label)}"><title>${escape(node.label)} · ${escape(node.annotations?.cell_type || "unannotated")} · ${node.present ? "present" : "absent"}</title><circle r="${id === root ? 22 : 13}"/><text text-anchor="middle" y="${id === root ? 44 : 30}">${escape(label(node.label))}</text><text class="type" text-anchor="middle" y="${id === root ? 60 : 44}">${escape(node.annotations?.cell_type || (!node.present ? "absent" : ""))}</text></g>`;
    })
    .join("");
  $(target).innerHTML =
    `<svg viewBox="0 0 900 565" role="group" aria-label="Directed connection neighbourhood"><defs><marker id="${target}-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z"/></marker></defs>${paths}${marks}</svg>`;
  if (onNode)
    $(target)
      .querySelectorAll("[data-graph-node]")
      .forEach((el) => {
        el.addEventListener("click", () =>
          onNode(Number(el.dataset.graphNode)),
        );
        el.addEventListener("keydown", (event) => {
          if (["Enter", " "].includes(event.key)) {
            event.preventDefault();
            onNode(Number(el.dataset.graphNode));
          }
        });
      });
}

async function job(kind, request, target) {
  const node = $(target);
  node.classList.remove("error");
  let data = await api("workspace/jobs", {}, { kind, request }, tokenHeader());
  while (true) {
    node.innerHTML = `<b>${escape(data.kind)} / ${escape(data.status)}</b> · ${escape(data.message)} ${data.status === "queued" ? `<button class="ghost" data-cancel-job="${data.job_id}">Cancel queued job</button>` : ""}<progress value="${data.progress}" max="100" aria-label="Job progress"></progress>`;
    if (data.status === "succeeded") return data.result;
    if (["failed", "cancelled"].includes(data.status)) {
      node.classList.add("error");
      throw new Error(data.message);
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
    data = await api(`workspace/jobs/${data.job_id}`);
  }
}
async function loadDiffs() {
  state.diffs = await api("diffs");
  const list = state.diffs.map((r) => [
    r.diff_run_id,
    `${r.dataset} ${r.release_a} → ${r.release_b} / run ${r.diff_run_id}`,
  ]);
  fillSelect(
    "c-saved",
    [["", "Select a saved comparison"], ...list],
    state.diff?.diff_run_id,
  );
  fillSelect("q-diff", list, state.diff?.diff_run_id);
}
$("c-refresh").addEventListener("click", guard(loadDiffs, "c-job"));
$("compare-form").addEventListener(
  "submit",
  guard(async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    try {
      const result = await job(
        "diff",
        {
          dataset: $("c-dataset").value,
          release_a: $("c-before").value,
          release_b: $("c-after").value,
          as_of: iso("c-asof"),
        },
        "c-job",
      );
      await loadDiffs();
      await loadDiff(result.diff_run_id);
      navigate("compare", { diff: result.diff_run_id });
    } finally {
      button.disabled = false;
    }
  }, "c-job"),
);
$("c-saved").addEventListener(
  "change",
  guard(async () => {
    if ($("c-saved").value) {
      await loadDiff(Number($("c-saved").value));
      navigate("compare", { diff: $("c-saved").value });
    }
  }, "c-job"),
);
async function loadDiff(id) {
  state.diff = await api(`diffs/${id}`);
  state.eventOffset = 0;
  $("c-workspace").hidden = false;
  $("c-detail").hidden = true;
  const meta = state.diffs.find((d) => d.diff_run_id === id);
  if (meta) {
    $("c-dataset").value = meta.dataset;
    fillReleases("c");
    $("c-before").value = meta.release_a;
    $("c-after").value = meta.release_b;
  }
  $("c-saved").value = id;
  $("c-summary").innerHTML =
    ["neuron", "connection", "annotation"]
      .map((type) =>
        metric(
          number(
            ["added", "removed", "modified"].reduce(
              (sum, k) => sum + (state.diff.stats[`${type}_${k}`] || 0),
              0,
            ),
          ),
          `${type} changes`,
        ),
      )
      .join("") +
    metric(new Date(state.diff.as_of).toLocaleString(), "Knowledge snapshot");
  await loadEvents();
}
function eventFilters() {
  return {
    entity_type: $("c-entity").value,
    change_type: $("c-change").value,
    q: $("c-query").value.trim(),
  };
}
async function loadEvents() {
  if (!state.diff) return;
  const data = await api(`workspace/diffs/${state.diff.diff_run_id}/events`, {
    ...eventFilters(),
    offset: state.eventOffset,
    limit: 25,
  });
  $("c-meta").textContent =
    `${number(data.total)} matching changes · ${state.eventOffset + 1}–${state.eventOffset + data.rows.length}. Export includes these filters; maximum 10,000 rows.`;
  $("c-events").innerHTML = data.rows.length
    ? grid(
        [
          ["Entity", (r) => escape(r.entity_type)],
          ["Change", (r) => badge(r.change_type)],
          [
            "Source neuron",
            (r) =>
              `<code>${escape(r.neuron_source_id)}</code>${r.post_source_id ? ` → <code>${escape(r.post_source_id)}</code>` : ""}`,
          ],
          [
            "Before → after",
            (r) =>
              escape(
                r.entity_type === "connection"
                  ? `${r.old_weight ?? "absent"} → ${r.new_weight ?? "absent"}`
                  : r.entity_type === "annotation"
                    ? `${r.old_value ?? "absent"} → ${r.new_value ?? "absent"}`
                    : "Existence changed",
              ),
          ],
          [
            "Δ synapses",
            (r) =>
              r.entity_type === "connection"
                ? `${r.weight_delta > 0 ? "+" : ""}${r.weight_delta}`
                : "—",
          ],
          [
            "Inspect",
            (r) =>
              `<button class="link-button" data-event="${r.diff_event_id}">Explain & trace ↗</button>`,
          ],
        ],
        data.rows,
      )
    : empty("No changes match these filters.");
  $("c-prev").disabled = state.eventOffset === 0;
  $("c-next").disabled = state.eventOffset + 25 >= data.total;
}
$("c-filter").addEventListener(
  "click",
  guard(async () => {
    state.eventOffset = 0;
    await loadEvents();
  }, "c-events"),
);
$("c-prev").addEventListener(
  "click",
  guard(async () => {
    state.eventOffset = Math.max(0, state.eventOffset - 25);
    await loadEvents();
  }, "c-events"),
);
$("c-next").addEventListener(
  "click",
  guard(async () => {
    state.eventOffset += 25;
    await loadEvents();
  }, "c-events"),
);
async function inspectEvent(id) {
  const detail = await api(`workspace/events/${id}`);
  if (!state.diff || state.diff.diff_run_id !== detail.diff_run_id) {
    await loadDiffs();
    await loadDiff(detail.diff_run_id);
  }
  $("c-detail").hidden = false;
  $("c-detail").innerHTML =
    `${badge(detail.change_type)} <b>${escape(detail.entity_type)} event ${id}</b><p>${escape(detail.explanation)}</p><div class="event-actions"><button class="ghost" data-event-evidence="${id}">Open source provenance ↗</button><button class="ghost" data-explore-event="${detail.dataset_neuron_id}" data-dataset="${escape(detail.dataset)}" data-release="${escape(detail.release_b)}">Explore this neuron →</button></div>`;
  const [before, after] = await Promise.all([
    api(`workspace/neurons/${detail.dataset_neuron_id}/graph`, {
      release: detail.release_a,
      as_of: state.diff.as_of,
      max_nodes: 24,
      max_edges: 40,
    }),
    api(`workspace/neurons/${detail.dataset_neuron_id}/graph`, {
      release: detail.release_b,
      as_of: state.diff.as_of,
      max_nodes: 24,
      max_edges: 40,
    }),
  ]);
  if (detail.entity_type === "connection")
    for (const [graph, weight] of [
      [before, detail.old_weight],
      [after, detail.new_weight],
    ]) {
      if (!graph.nodes.some((n) => n.id === detail.post_neuron_id))
        graph.nodes.push({
          id: detail.post_neuron_id,
          label: detail.post_source_id,
          present: weight !== null,
          annotations: {},
        });
      if (
        weight !== null &&
        !graph.edges.some(
          (e) =>
            e.source === detail.dataset_neuron_id &&
            e.target === detail.post_neuron_id,
        )
      )
        graph.edges.push({
          source: detail.dataset_neuron_id,
          target: detail.post_neuron_id,
          weight,
        });
    }
  const positions = [
    ...new Set([...before.nodes, ...after.nodes].map((n) => n.id)),
  ];
  const onNode = (node) => {
    navigate("explorer", {
      dataset: detail.dataset,
      release: detail.release_b,
      neuron: node,
      as_of: state.diff.as_of,
    });
  };
  drawGraph("c-graph-before", before, {
    positions,
    onNode,
    selectedEvent: detail,
  });
  drawGraph("c-graph-after", after, {
    positions,
    onNode,
    selectedEvent: detail,
  });
  $("c-before-title").textContent = `Before / ${detail.release_a}`;
  $("c-after-title").textContent = `After / ${detail.release_b}`;
  $("c-detail").scrollIntoView({ behavior: "smooth", block: "start" });
}
async function exportDiff(format) {
  if (!state.diff) return;
  const params = new URLSearchParams({ ...eventFilters(), format });
  const response = await fetch(
    `/api/v1/workspace/diffs/${state.diff.diff_run_id}/export?${params}`,
  );
  if (!response.ok) throw new Error("Export failed");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = `connectomediff-${state.diff.diff_run_id}.${format}`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  if (response.headers.get("X-Export-Truncated") === "true")
    $("c-meta").textContent =
      "Export capped at 10,000 rows. Narrow the filters or use the API for further pages.";
}
$("c-export").addEventListener(
  "click",
  guard(() => exportDiff("csv"), "c-events"),
);
$("c-export-json").addEventListener(
  "click",
  guard(() => exportDiff("json"), "c-events"),
);
$("c-score").addEventListener("click", () => {
  fillSelect(
    "q-diff",
    state.diffs.map((r) => [
      r.diff_run_id,
      `${r.dataset} ${r.release_a} → ${r.release_b}`,
    ]),
    state.diff.diff_run_id,
  );
  navigate("quality");
});

async function loadModels(selected) {
  state.models = await api("workspace/models");
  fillSelect(
    "q-model",
    [
      ["", "Select a model run"],
      ...state.models.map((m) => [
        m.model_run_id,
        `${m.model_kind} / run ${m.model_run_id} / diff ${m.diff_run_ids.join(", ")}`,
      ]),
    ],
    selected || $("q-model").value,
  );
  if (selected) {
    $("q-model").value = selected;
    await loadScores();
  }
}
$("quality-form").addEventListener(
  "submit",
  guard(async (event) => {
    event.preventDefault();
    if (!$("q-diff").value)
      throw new Error("Create a release comparison first.");
    const button = event.submitter;
    button.disabled = true;
    try {
      const result = await job(
        "anomalies",
        { diff_id: Number($("q-diff").value) },
        "q-job",
      );
      await loadModels(result.model_run_id);
    } finally {
      button.disabled = false;
    }
  }, "q-job"),
);
async function loadScores() {
  const id = Number($("q-model").value);
  if (!id) return;
  const model = state.models.find((m) => m.model_run_id === id),
    metrics = model.metrics;
  const data = await api(`workspace/models/${id}/results`, {
    flagged_only: $("q-flagged").checked,
    offset: state.qualityOffset,
    limit: 25,
  });
  $("q-summary").innerHTML =
    metric(number(metrics.rows), "Connection changes scored") +
    metric(number(metrics.model_flagged), "Model flagged") +
    metric(number(metrics.baseline_flagged), "Threshold baseline flagged") +
    metric(
      `${((metrics.baseline_agreement || 0) * 100).toFixed(0)}%`,
      "Agreement with baseline",
    );
  $("q-method").innerHTML =
    `<b>${escape(model.model_kind === "threshold" ? "Small-sample threshold baseline" : "Isolation Forest / deterministic seed 42")}</b><p>${escape(model.parameters.fallback || "Fit on a deterministic reservoir of at most 20,000 changed connections; all changes are scored in batches. Features: log old/new weights, log absolute delta, relative delta, added/removed indicators.")}</p><p>${escape(metrics.evaluation_status)}. No labelled accuracy is claimed. Scores are review priorities; the listed features are observations, not causal attributions.</p>`;
  $("q-meta").textContent =
    `${number(data.total)} matching results · model ${id} · ${model.feature_schema.join(", ")}`;
  $("q-results").innerHTML = data.rows.length
    ? grid(
        [
          ["Rank", (r) => r.score_rank],
          [
            "Connection",
            (r) =>
              `<code>${escape(r.neuron_source_id)} → ${escape(r.post_source_id)}</code><div>${r.old_weight ?? 0} → ${r.new_weight ?? 0} synapses</div>`,
          ],
          ["Score", (r) => r.score.toFixed(4)],
          [
            "Model / baseline",
            (r) =>
              `${badge(r.is_flagged ? "flagged" : "not flagged")}<br>${badge(r.baseline_flagged ? "baseline flagged" : "baseline not flagged")}`,
          ],
          [
            "Explanation",
            (r) =>
              `<p class="score-explanation">${escape(r.explanation)}</p><button class="link-button" data-quality-event="${r.diff_event_id}">Inspect source change ↗</button>`,
          ],
        ],
        data.rows,
      )
    : empty("No results match this filter.");
  $("q-prev").disabled = state.qualityOffset === 0;
  $("q-next").disabled = state.qualityOffset + 25 >= data.total;
}
$("q-model").addEventListener(
  "change",
  guard(async () => {
    state.qualityOffset = 0;
    await loadScores();
  }, "q-results"),
);
$("q-flagged").addEventListener(
  "change",
  guard(async () => {
    state.qualityOffset = 0;
    await loadScores();
  }, "q-results"),
);
$("q-prev").addEventListener(
  "click",
  guard(async () => {
    state.qualityOffset = Math.max(0, state.qualityOffset - 25);
    await loadScores();
  }, "q-results"),
);
$("q-next").addEventListener(
  "click",
  guard(async () => {
    state.qualityOffset += 25;
    await loadScores();
  }, "q-results"),
);

async function loadMatching() {
  const data = await api("workspace/matching", {
    status: $("m-status").value,
    offset: state.matchingOffset,
    limit: 51,
  });
  state.matching = data.rows;
  $("m-token-label").hidden = !data.review_token_required;
  $("m-results").innerHTML = data.rows.length
    ? grid(
        [
          [
            "Source / target",
            (r) =>
              `<code>${escape(r.dataset)}:${escape(r.source_neuron_id)}</code><div>${escape(r.target_label || "Unresolved")}</div>`,
          ],
          ["Status", (r) => badge(r.review_status)],
          [
            "Type heuristic",
            (r) =>
              r.confidence === null ? "—" : Number(r.confidence).toFixed(3),
          ],
          [
            "Evidence rank",
            (r) =>
              r.score_details.rank_score == null
                ? "Not enough evidence"
                : r.score_details.rank_score.toFixed(3),
          ],
          [
            "Evidence & review",
            (r) =>
              `<button class="link-button" data-mapping="${r.mapping_id}">Inspect correspondence ↗</button>`,
          ],
        ],
        data.rows.slice(0, 50),
      )
    : empty(
        "No correspondence candidates match. Load hemibrain and FlyWire and run the type crosswalk to propose candidates.",
      );
  $("m-prev").disabled = state.matchingOffset === 0;
  $("m-next").disabled = data.rows.length < 51;
}
$("m-status").addEventListener(
  "change",
  guard(async () => {
    state.matchingOffset = 0;
    await loadMatching();
  }, "m-results"),
);
$("m-prev").addEventListener(
  "click",
  guard(async () => {
    state.matchingOffset = Math.max(0, state.matchingOffset - 50);
    await loadMatching();
  }, "m-results"),
);
$("m-next").addEventListener(
  "click",
  guard(async () => {
    state.matchingOffset += 50;
    await loadMatching();
  }, "m-results"),
);
$("m-rank").addEventListener(
  "click",
  guard(async () => {
    const button = $("m-rank");
    button.disabled = true;
    try {
      const result = await job("matching", { limit: 5000 }, "m-job");
      $("m-evaluation").innerHTML =
        `<b>${number(result.scored_candidates)} candidates scored</b><p>${escape(result.evaluation_status)}</p><p>${result.reviewed_queries ? `Reviewed queries: ${result.reviewed_queries}; approved top-1 retrieval ${(100 * result.approved_top1_retrieval).toFixed(1)}%; type-only baseline ${(100 * result.type_baseline_top1_retrieval).toFixed(1)}%.` : "No approved reference matches. No accuracy estimate is available."}</p><p>${escape(result.model)}. Processing cap: ${result.candidate_cap} candidates.</p>`;
      await loadMatching();
    } finally {
      button.disabled = false;
    }
  }, "m-job"),
);
function inspectMapping(id) {
  const row = state.matching.find((r) => r.mapping_id === id);
  if (!row) return;
  evidence(
    "Correspondence evidence",
    `<p><code>${escape(row.dataset)}:${escape(row.source_neuron_id)}</code> → ${escape(row.target_label || "Unresolved")}</p><p>${escape(row.score_details.explanation || "Type-and-side candidate; heuristic score is not a calibrated probability.")}</p>${sourceSummary({ type_crosswalk: row.evidence, ranking: row.score_details })}${row.review_status === "proposed" ? `<p class="note">Approval records this canonical correspondence and your reviewer name. It does not establish same-individual identity across specimens.</p><div class="row"><button data-review-mapping="${id}" data-decision="approved">Approve correspondence</button><button class="ghost" data-review-mapping="${id}" data-decision="rejected">Reject</button></div><p id="review-message" class="note" aria-live="polite"></p>` : `<p>${badge(row.review_status)} · ${escape(row.reviewed_by || "No review recorded")}</p>`}`,
  );
}

document.addEventListener(
  "click",
  guard(async (event) => {
    const el = event.target.closest("button");
    if (!el) return;
    if (el.dataset.neuron) await selectNeuron(Number(el.dataset.neuron));
    if (el.dataset.release) {
      $("e-release").value = el.dataset.release;
      fillRegions();
      await selectNeuron(state.neuron);
    }
    if (el.dataset.provenance) {
      const [table, id] = el.dataset.provenance.split(":");
      await showProvenance(table, id);
    }
    if (el.dataset.event) await inspectEvent(Number(el.dataset.event));
    if (el.dataset.eventEvidence) {
      const detail = await api(`workspace/events/${el.dataset.eventEvidence}`);
      evidence(
        "Change provenance",
        `<p>${escape(detail.explanation)}</p>${Object.entries(detail.provenance)
          .map(
            ([side, trace]) =>
              `<h3>${escape(side)} source</h3>${sourceSummary(trace)}`,
          )
          .join("")}`,
      );
    }
    if (el.dataset.exploreEvent)
      navigate("explorer", {
        dataset: el.dataset.dataset,
        release: el.dataset.release,
        neuron: el.dataset.exploreEvent,
        as_of: state.diff?.as_of,
      });
    if (el.dataset.qualityEvent) {
      navigate("compare");
      await inspectEvent(Number(el.dataset.qualityEvent));
      history.replaceState(null, "", `#compare?diff=${state.diff.diff_run_id}`);
    }
    if (el.dataset.mapping) inspectMapping(Number(el.dataset.mapping));
    if (el.dataset.reviewMapping) {
      const reviewer = $("m-reviewer").value.trim();
      if (!reviewer) {
        $("review-message").textContent =
          "Enter your reviewer name in the Correspondences tab before reviewing.";
        return;
      }
      el.disabled = true;
      try {
        await api(
          `mappings/${el.dataset.reviewMapping}/review`,
          {},
          { reviewer, decision: el.dataset.decision },
          tokenHeader(),
        );
        $("evidence-dialog").close();
        await loadMatching();
      } catch (e) {
        el.disabled = false;
        $("review-message").textContent = e.message;
      }
    }
    if (el.dataset.cancelJob) {
      await api(`workspace/jobs/${el.dataset.cancelJob}/cancel`, {}, {});
      el.disabled = true;
    }
  }, "job-history"),
);

$("deploy-run").addEventListener(
  "click",
  guard(async () => {
    const button = $("deploy-run");
    button.disabled = true;
    try {
      const result = await job(
        "deploy",
        { strategy: $("deploy-strategy").value },
        "deploy-job",
      );
      $("deploy-job").innerHTML =
        `Generation ${result.batch_id} activated. Full binary content verified with SHA-256 on all three shards. Reload the tab to update deployment totals.`;
    } finally {
      button.disabled = false;
    }
  }, "deploy-job"),
);
async function loadJobs() {
  const rows = await api("workspace/jobs");
  $("job-history").innerHTML = rows.length
    ? grid(
        [
          ["Operation", (r) => escape(r.kind)],
          ["Status", (r) => badge(r.status)],
          ["Progress", (r) => `${r.progress}%`],
          ["Message", (r) => escape(r.message)],
          ["Created", (r) => escape(new Date(r.created_at).toLocaleString())],
          [
            "Action",
            (r) =>
              r.status === "queued"
                ? `<button class="link-button" data-cancel-job="${r.job_id}">Cancel</button>`
                : "—",
          ],
        ],
        rows,
      )
    : empty("No operations have been submitted yet.");
}
$("jobs-refresh").addEventListener("click", guard(loadJobs, "job-history"));

async function start() {
  state.context = await api("workspace/context");
  const datasets = [
    ...new Map(
      state.context.releases.map((r) => [r.dataset, r.name]),
    ).entries(),
  ];
  const preferred = datasets.reduce(
    (best, [ds]) => (releases(ds).length > releases(best).length ? ds : best),
    datasets[0]?.[0],
  );
  fillSelect("e-dataset", datasets, preferred);
  fillSelect("c-dataset", datasets, preferred);
  fillReleases("e");
  fillReleases("c");
  $("m-token-label").hidden = !state.context.review_token_required;
  await loadDiffs();
  const guide = [
    [
      "Overview",
      "What is connected, what is loaded, and whether data is synthetic or public.",
    ],
    [
      "Neuron explorer",
      "Search a release, follow a bounded graph, inspect history and trace source records.",
    ],
    [
      "Compare releases",
      "Inspect persisted added/removed/modified events and before/after neighbourhoods.",
    ],
    [
      "Change quality",
      "Compare a threshold baseline with Isolation Forest and inspect recorded score features.",
    ],
    [
      "Correspondences",
      "Rank type-and-side candidates with connectivity/region evidence; approve or reject manually.",
    ],
    [
      "Query console",
      "Read-only PostgreSQL and MongoDB queries, with EXPLAIN support.",
    ],
    [
      "DBMS concepts",
      "Live evidence for keys, constraints, transactions, temporal storage and lineage.",
    ],
    [
      "SQL / NoSQL implementation",
      "Read the schema, live catalog, document model, indexes and metadata sync evidence.",
    ],
    [
      "Distributed",
      "Rebuild verified shard generations and compare routed answers against the coordinator.",
    ],
    [
      "Results",
      "Saved test and benchmark reports with timestamps and data-identity labels.",
    ],
    [
      "How it works",
      "Methodology, interpretation limits and durable operation status.",
    ],
  ];
  $("tab-guide").innerHTML = grid(
    [
      ["Tab", (r) => escape(r[0])],
      ["Purpose", (r) => escape(r[1])],
    ],
    guide,
  );
  if (!datasets.length)
    $("e-results").innerHTML = empty(
      "No releases loaded. Run the demo setup or ingest public releases.",
    );
  route();
}
start().catch((error) => {
  for (const id of ["e-results", "c-job", "q-results"])
    $(id).innerHTML = empty(error.message);
});
