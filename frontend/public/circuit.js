const el = id => document.getElementById(id);
const {number, percent, signed, node, row, meter, roots, stat, canvas, chart} = window.Lab;
const base = "/api/v1/circuit";
const controls = {
  intact: ["Intact circuit", "The frozen FlyWire patch and its fitted readout."],
  silence_T4: ["Silence T4", "Sets all T4 subtype outputs to zero. T5 and the fitted readout still participate."],
  silence_T5: ["Silence T5", "Sets all T5 subtype outputs to zero. T4 and the fitted readout still participate."],
  random_silence: ["Silence a matched random set", "Silences a seeded random group with the same number of cells as the T4 intervention."],
  rewired: ["Locally rewired", "Shuffles destinations within cell-type and local spatial groups. The readout stays fixed."],
  shuffled_weights: ["Shuffled weights", "Keeps the edge endpoints and redistributes synapse counts within each stage."],
  disconnected: ["Disconnected output", "Sets every T4 and T5 output to zero. The readout has no motion signal and steering stays at zero."],
  pixel_correlator: ["Pixel correlator", "Estimates motion from successive brightness strips and drives the controller directly. The activity bars still show the circuit."],
};
const title = key => controls[key][0];
let snapshots, current, evidence, traces, timer, selectedPath;
let runRequest = 0, replayRequest = 0, evidenceRequest = 0;

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  };
  const response = await fetch(base + path, options);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw Error(typeof error.detail === "string" ? error.detail : "Circuit Lab request failed.");
  }
  return response.json();
}

function guarded(fn, status = "status") {
  return async event => {
    try { await fn(event); }
    catch (error) { el(status).textContent = error.message; }
  };
}

function freezePlayback() {
  clearInterval(timer);
  timer = null;
  el("play").textContent = "Play";
}

const direction = value => value > 0 ? "Right" : value < 0 ? "Left" : "No direction";

function renderBrightness(target, replay, frame) {
  const surface = canvas(target);
  if (!surface) return;
  const {ctx, width, height} = surface;
  const rowHeight = height / replay.length;
  const pixelWidth = width / replay[0].pixels.length;
  replay.forEach(({pixels}, step) => pixels.forEach((light, i) => {
    const shade = Math.round(light * 255);
    ctx.fillStyle = "rgb(" + shade + "," + shade + "," + shade + ")";
    ctx.fillRect(i * pixelWidth, step * rowHeight, pixelWidth + .5, rowHeight + .5);
  }));
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#68d9ce";
  ctx.strokeRect(1, frame * rowHeight + 1, width - 2, Math.max(1, rowHeight - 1));
}

function renderReplay() {
  if (!traces) return;
  const step = Number(el("frame").value);
  const frames = traces[0].replay.length;
  const peak = Math.max(1e-12, ...traces.flatMap(t => t.replay.flatMap(f => f.activity)));
  el("frame-count").textContent = (step + 1) + " / " + frames;
  el("previous-frame").disabled = step === 0;
  el("next-frame").disabled = step === frames - 1;
  traces.forEach((trial, index) => {
    const panel = el("panels").children[index];
    const frame = trial.replay[step];
    panel.querySelector("h3").textContent = title(trial.condition);
    renderBrightness(panel.querySelector("canvas"), trial.replay, step);
    const bars = frame.activity.map((value, i) => {
      const column = node("div", null, "activity-column" + (i >= 4 ? " amber" : ""));
      const well = node("div", null, "activity-well");
      const fill = node("i");
      fill.style.setProperty("--level", (100 * value / peak) + "%");
      well.append(fill);
      column.append(well, node("span", current.outputs[i]));
      column.title = current.outputs[i] + " activity " + value.toExponential(2);
      column.setAttribute("aria-label", column.title);
      return column;
    });
    panel.querySelector(".activity-grid").replaceChildren(...bars);
    panel.querySelector(".current-frame").textContent =
      "Drift " + signed(frame.position) + " grid units · correction " + signed(frame.action) + " per frame";
  });
  const drift = Math.max(.1, ...traces.flatMap(t => t.replay.map(frame => Math.abs(frame.position))));
  for (const [id, key, bound] of [
    ["drift-chart", "position", Math.ceil(drift * 1.05)],
    ["steering-chart", "action", .45],
  ]) {
    chart(el(id), {
      end: frames - 1,
      y: [-bound, bound],
      ticks: [-bound, 0, bound],
      format: x => String(x),
      labels: [[0, "1"], [Math.floor(frames / 2) - 1, String(Math.floor(frames / 2))], [frames - 1, String(frames)]],
      cursor: step,
      series: traces.map((t, index) => ({dashed: index === 1, points: t.replay.map((frame, i) => [i, frame[key]])})),
    });
  }
}

function renderOutcomes(condition) {
  const intact = current.metrics.find(item => item.condition === "intact");
  const control = current.metrics.find(item => item.condition === condition);
  const comparison = current.paired_diff.find(item => item.condition === condition);
  const accuracyChange = 100 * (Number(control.accuracy) - Number(intact.accuracy));
  el("outcome-summary").replaceChildren(
    stat(percent(intact.accuracy), "Direction accuracy · intact"),
    stat(percent(control.accuracy), "Direction accuracy · " + title(condition)),
    stat(signed(accuracyChange, 1) + " pp", "Control minus intact accuracy"),
  );
  el("paired").textContent = title(condition) + " lost " + comparison.lost_correct + " correct predictions and gained " +
    comparison.gained_correct + " across " + comparison.paired_trials + " matched scenes. Mean drift changed by " +
    signed(comparison.added_drift) + " grid units. Positive means more drift.";
  el("metrics").replaceChildren();
  for (const key of Object.keys(controls)) {
    const m = current.metrics.find(item => item.condition === key);
    row(el("metrics"), [title(key), meter(m.accuracy), m.heading_error.toFixed(2) + " units", percent(m.heading_improvement)],
      key === condition || key === "intact");
  }
}

async function loadReplay() {
  freezePlayback();
  const ticket = ++replayRequest;
  const query = "/runs/" + current.run_id + "/replay?trial=" + el("trial").value + "&condition=";
  const condition = el("condition").value;
  el("replay-status").textContent = "Loading paired trial…";
  const pair = await Promise.all(["intact", condition].map(key => api(query + key)));
  if (ticket !== replayRequest) return;
  traces = pair;
  el("frame").max = pair[0].replay.length - 1;
  el("frame").value = 0;
  el("trace").href = base + query + condition;
  el("trace-legend").textContent = title(condition);
  el("control-description").textContent = controls[condition][1];
  el("scene-summary").replaceChildren(
    stat(direction(pair[0].direction), "Actual direction · trial " + (pair[0].trial + 1)),
    stat(direction(pair[0].score), "Open-loop prediction · intact"),
    stat(direction(pair[1].score), "Open-loop prediction · " + title(condition)),
  );
  el("replay-status").textContent = "The predictions above use the open-loop trial. The plots show the paired feedback replay.";
  renderOutcomes(condition);
  renderReplay();
}

function renderProbe(probe) {
  const peak = Math.max(1e-12, ...Object.values(probe).flatMap(Object.values));
  el("probe").replaceChildren(...["bright", "dark"].map(label => {
    const card = node("div", null, "lab-card");
    card.append(node("h3", label === "bright" ? "Bright edge" : "Dark edge"));
    for (const type of ["T4", "T5"]) {
      const value = probe[label][type];
      card.append(meter(value / peak, type + " · " + value.toExponential(2) + " model units", type === "T5" ? "amber" : ""));
    }
    return card;
  }));
}

function showEvidence(data, path) {
  el("edges").replaceChildren();
  for (const edge of data.edges) {
    const source = node("div", edge.archive_path, "lab-archive");
    source.append(node("small", " · rows " + edge.source_lines.join(", ")));
    source.title = "SHA-256 " + edge.sha256;
    row(el("edges"), [edge.pre_type + " → " + edge.post_type, roots(edge), number.format(edge.weight), source]);
  }
  el("pathway-status").textContent = path
    ? path.pre_type + " → " + path.post_type + " · " + number.format(path.edges) + " neuron pairs · " +
      number.format(path.synapses) + " synapses · showing " + data.edges.length + " strongest pairs"
    : "Showing " + data.edges.length + " strongest recorded pairs across the patch.";
  el("clear-pathway").disabled = !path;
}

async function selectPath(path) {
  const ticket = ++evidenceRequest;
  selectedPath = path;
  for (const button of el("pathway-matrix").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.pre === path?.pre_type && button.dataset.post === path?.post_type));
  }
  if (!path) {
    showEvidence(evidence, null);
    return;
  }
  const query = new URLSearchParams({pre_type: path.pre_type, post_type: path.post_type});
  el("pathway-status").textContent = "Loading source edges…";
  const data = await api("/snapshots/" + current.snapshot_id + "/evidence?" + query);
  if (ticket === evidenceRequest) showEvidence(data, path);
}

function renderMatrix() {
  if (!evidence.layers) {
    el("pathway-matrix").textContent = "The wiring matrix is available after the API reloads. The saved source pairs are below.";
    return;
  }
  const stage = Number(el("matrix-stage").value);
  const [senders, receivers] = evidence.layers.slice(stage, stage + 2);
  const pairs = evidence.pathways.filter(p => senders.includes(p.pre_type));
  const maximum = Math.max(1, ...pairs.map(p => p.synapses));
  const map = new Map(pairs.map(p => [p.pre_type + "/" + p.post_type, p]));
  const table = node("table", null, "lab-matrix");
  const headers = table.createTHead().insertRow();
  for (const label of ["From / to", ...receivers]) {
    const heading = node("th", label);
    heading.scope = "col";
    headers.append(heading);
  }
  const body = table.createTBody();
  for (const sender of senders) {
    const tr = body.insertRow();
    const heading = node("th", sender);
    heading.scope = "row";
    tr.append(heading);
    for (const receiver of receivers) {
      const cell = tr.insertCell();
      const path = map.get(sender + "/" + receiver);
      if (!path) { cell.textContent = "—"; continue; }
      const button = node("button", number.format(path.synapses));
      button.type = "button";
      button.dataset.pre = sender;
      button.dataset.post = receiver;
      button.setAttribute("aria-pressed", String(selectedPath === path));
      button.setAttribute("aria-label", sender + " to " + receiver + ", " + path.synapses + " synapses");
      button.style.setProperty("--heat", (5 + 22 * Math.sqrt(path.synapses / maximum)) + "%");
      button.onclick = guarded(() => selectPath(path), "pathway-status");
      cell.append(button);
    }
  }
  el("pathway-matrix").replaceChildren(table);
}

async function showRun(id) {
  freezePlayback();
  const ticket = ++runRequest;
  ++replayRequest;
  ++evidenceRequest;
  const data = await api("/runs/" + id);
  const path = "/snapshots/" + data.snapshot_id;
  const [wiring, probe] = await Promise.all([api(path + "/evidence"), api(path + "/polarity")]);
  if (ticket !== runRequest) return;
  current = data;
  evidence = wiring;
  selectedPath = null;
  renderProbe(probe);
  renderMatrix();
  showEvidence(evidence, null);
  const patch = snapshots.find(p => p.snapshot_id === data.snapshot_id);
  el("identity").textContent = "Snapshot " + data.snapshot_id + " · radius " + data.radius + " · " +
    number.format(patch.neurons) + " cells · " + number.format(patch.edges) + " edges · SHA-256 " + data.digest;
  el("column-source").href = wiring.columns;
  el("sqlid").textContent = id;
  el("export").href = base + "/runs/" + id;
  el("seed").value = data.seed;
  el("patch").value = data.snapshot_id;
  el("history").value = id;
  el("report").hidden = false;
  await loadReplay();
  if (ticket === runRequest) el("status").textContent = "Run " + id + " · Seed " + data.seed + " · " + data.model_version;
}

async function refresh() {
  const runs = await api("/runs");
  el("history").length = 1;
  for (const r of runs) {
    el("history").add(new Option("Run " + r.run_id + " · patch " + r.snapshot_id + " · seed " + r.seed, r.run_id));
  }
  return runs;
}

function moveFrame(step) {
  freezePlayback();
  el("frame").value = step;
  renderReplay();
}

el("setup").onsubmit = guarded(async event => {
  event.preventDefault();
  el("run").disabled = true;
  el("status").textContent = "Simulating paired trials…";
  try {
    const saved = await api("/runs", {snapshot_id: Number(el("patch").value), seed: Number(el("seed").value)});
    await refresh();
    await showRun(saved.run_id);
  } finally { el("run").disabled = false; }
});
el("history").onchange = guarded(() => el("history").value && showRun(el("history").value));
el("condition").onchange = el("trial").onchange = guarded(loadReplay, "replay-status");
el("matrix-stage").onchange = () => { selectedPath = null; renderMatrix(); selectPath(null); };
el("clear-pathway").onclick = () => selectPath(null);
el("frame").oninput = () => moveFrame(Number(el("frame").value));
el("previous-frame").onclick = () => moveFrame(Number(el("frame").value) - 1);
el("next-frame").onclick = () => moveFrame(Number(el("frame").value) + 1);
el("play").onclick = () => {
  if (timer) { freezePlayback(); return; }
  if (!traces) return;
  el("play").textContent = "Pause";
  timer = setInterval(() => {
    el("frame").value = (Number(el("frame").value) + 1) % traces[0].replay.length;
    renderReplay();
  }, 120);
};

for (const key of Object.keys(controls)) {
  if (key !== "intact") el("condition").add(new Option(title(key), key));
}
for (let i = 0; i < 24; i++) el("trial").add(new Option(String(i + 1), i));
for (let i = 0; i < 2; i++) {
  const panel = node("div", null, "lab-card");
  panel.innerHTML = '<h3></h3><canvas role="img" aria-label="Brightness by eye column and frame"></canvas>' +
    '<div class="retina-labels"><span>Left column</span><span>Right column</span></div>' +
    '<div class="activity-grid" aria-label="T4 and T5 activity"></div><p class="current-frame note"></p>';
  el("panels").append(panel);
}
new ResizeObserver(renderReplay).observe(el("panels"));

guarded(async () => {
  snapshots = await api("/snapshots");
  for (const p of snapshots) {
    el("patch").add(new Option("Radius " + p.radius + " · " + number.format(p.neurons) + " cells · " +
      number.format(p.edges) + " edges", p.snapshot_id));
  }
  if (!snapshots.length) {
    el("status").textContent = "Load a FlyWire circuit snapshot to begin an experiment.";
    return;
  }
  const preferred = snapshots.find(p => p.radius === 4) || snapshots[0];
  el("patch").value = preferred.snapshot_id;
  el("run").disabled = false;
  const runs = await refresh();
  const saved = runs.find(r => r.snapshot_id === preferred.snapshot_id && r.seed === 42) || runs[0];
  if (saved) await showRun(saved.run_id);
  else el("status").textContent = "Ready to run.";
})();
