const element = id => document.getElementById(id);
const prefix = "/api/v1/memory";
const interventions = {
  paired_A: "Reward A",
  paired_B: "Reward B",
  frozen: "Freeze plasticity",
  unpaired: "Shuffle reward timing",
  rewired: "Rewire sensory inputs",
  silent_output: "Silence output",
};
const arenaNames = {
  combined: "Vision + memory",
  vision_only: "Vision only",
  memory_only: "Memory only",
  disabled: "Both disabled",
};
const percent = value => value == null ? "—" : (100 * Number(value)).toFixed(1) + "%";
const number = new Intl.NumberFormat();
let run, branch, arena, animation;
let trials = [];
let parentTrials = [];
let runRequest = 0;
let branchRequest = 0;
let stateRequest = 0;

function addRow(id, values) {
  const row = element(id).insertRow();
  for (const value of values) row.insertCell().textContent = value;
}

function options(id, items) {
  element(id).replaceChildren(...items.map(([value, label]) => new Option(label, value)));
}

function branchName(data) {
  const parent = run.branches.find(b => b.branch_id === data.parent_id);
  let name = interventions[data.intervention];
  if (data.intervention === "frozen" && parent?.intervention === "silent_output") name = "Restore output";
  if (data.intervention === "frozen" && data.fork_step === 36) name = "Retain memory with gains frozen";
  return "#" + data.branch_id + " · " + name + (parent ? " from trial " + data.fork_step : " · original");
}

async function api(path, body) {
  const request = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const response = await fetch(prefix + path, request);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : "Memory Lab could not load this request.");
  }
  return response.json();
}

function handle(fn) {
  return async event => {
    event?.preventDefault();
    try {
      await fn();
    } catch (error) {
      element("status").textContent = error.message;
    }
  };
}

async function refresh() {
  const list = await api("/runs");
  options("saved", list.map(r => [r.run_id, "Run " + r.run_id + " · seed " + r.seed + " · " + r.version]));
  element("saved").disabled = !list.length;
  return list;
}

function historyChart() {
  const ctx = element("history").getContext("2d");
  const x = step => 38 + step * 10;
  const y = probability => 170 - probability * 140;
  ctx.fillStyle = "#162232";
  ctx.fillRect(0, 0, 860, 200);
  ctx.fillStyle = "#26384c";
  for (const [start, end] of [[9, 24], [37, 68]]) ctx.fillRect(x(start), 17, x(end) - x(start), 157);
  ctx.font = "13px system-ui";
  ctx.fillStyle = "#d4dfed";
  for (const probability of [0, 0.5, 1]) {
    ctx.fillText(String(probability), 4, y(probability));
    ctx.fillRect(35, y(probability), 805, 1);
  }
  for (const step of [0, 8, 24, 36, 68, 80]) ctx.fillText(step, x(step) - 4, 194);
  for (const [series, color] of [[parentTrials, "#f2bd6c"], [trials, "#62c7e6"]]) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.beginPath();
    for (const trial of series) {
      if (trial.p_a !== null) ctx.lineTo(x(trial.step), y(trial.p_a));
    }
    ctx.stroke();
  }
  ctx.fillStyle = "#fff";
  ctx.fillRect(x(Number(element("step").value)), 18, 2, 158);
}

async function inspect() {
  const request = ++stateRequest;
  const id = branch.branch_id;
  const step = Number(element("step").value);
  historyChart();
  element("fork").querySelector("button").disabled = step >= run.last_step;
  const path = "/branches/" + id + "/state/" + step;
  const data = await api(path);
  if (request !== stateRequest || branch.branch_id !== id) return;
  const trial = data.trial;
  const choice = trial.choice ? " · chose " + trial.choice + " · P(A) " + percent(trial.p_a) : "";
  const reward = trial.presented ? " · presented " + trial.presented + (trial.reward ? " with reward" : " without reward") : "";
  element("event").textContent = "Trial " + step + " · " + trial.phase + choice + reward;
  element("digest").textContent = data.changed_from_naive + " gains changed from baseline · SHA-256 " + trial.state_hash;
  element("edges").replaceChildren();
  for (const edge of data.edges) {
    addRow("edges", [edge.pre_root + " → " + edge.post_root, edge.regional_weight, percent(edge.gain),
      edge.neuropil + " · " + number.format(edge.source_line)]);
  }
  const source = data.edges[0];
  element("source").textContent = source?.archive_path ? source.archive_path + " · SHA-256 " + source.sha256 : "";
  element("mixtures").replaceChildren();
  for (const mixture of data.mixtures) {
    addRow("mixtures", [mixture.percent_a + "%", percent(mixture.probability)]);
  }
  element("states").href = prefix + path;
  element("divergenceSql").textContent = "SELECT * FROM cc.memory_divergence WHERE branch_id = " + id + ";";
  element("stateSql").textContent = "SELECT w.pre_root, w.post_root, w.source_line, m.gain / 10000.0 AS effective_gain\n" +
    "FROM cc.memory_state_at(" + id + ", " + step + ") m\n" +
    "JOIN cc.memory_wiring w USING (connection_id)\n" +
    "WHERE w.snapshot_id = " + run.snapshot_id + " ORDER BY effective_gain LIMIT 8;";
}

function drawMaze(frame) {
  if (!arena) return;
  const condition = arena.conditions.find(c => c.condition === element("arenaMode").value);
  const trial = condition.trials[Number(element("arenaTrial").value)];
  const index = frame ?? trial.path.length - 1;
  const ctx = element("maze").getContext("2d");
  const x = position => 360 + position * 20;
  const y = progress => 232 - progress * 204;
  ctx.fillStyle = "#162232";
  ctx.fillRect(0, 0, 720, 260);
  for (const side of [-1, 1]) {
    ctx.strokeStyle = "#35465c";
    ctx.lineWidth = 65;
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(x(0), y(0));
    ctx.lineTo(x(0), y(.3));
    ctx.lineTo(x(side * 10), y(1));
    ctx.stroke();
    const odor = side === -1 ? trial.left : (trial.left === "A" ? "B" : "A");
    ctx.fillStyle = "#fff";
    ctx.font = "15px system-ui";
    ctx.fillText("Odor " + odor, x(side * 10) - 23, 20);
  }
  ctx.strokeStyle = "#69d7d1";
  ctx.lineWidth = 4;
  ctx.beginPath();
  for (const [position, progress] of trial.path.slice(0, index + 1)) ctx.lineTo(x(position), y(progress));
  ctx.stroke();
  const [position, progress] = trial.path[index];
  ctx.fillStyle = "#ffd680";
  ctx.beginPath();
  ctx.arc(x(position), y(progress), 7, 0, 2 * Math.PI);
  ctx.fill();
  element("arenaText").textContent = "Visual run " + arena.visual_run + " · target " + trial.goal + " · chose " +
    trial.choice + " · arrival error " + trial.arrival_error.toFixed(2) + " units · frame " + (index + 1);
}

async function choose(id, initialStep) {
  const request = ++branchRequest;
  ++stateRequest;
  clearInterval(animation);
  const selected = run.branches.find(b => b.branch_id === Number(id));
  const path = "/branches/" + selected.branch_id;
  const [own, parent, navigation] = await Promise.all([
    api(path + "/trials"),
    selected.parent_id ? api("/branches/" + selected.parent_id + "/trials") : [],
    selected.last_step > 68 && run.visual_run_id ? api(path + "/arena") : null,
  ]);
  if (request !== branchRequest) return;
  branch = selected;
  element("branch").value = id;
  trials = own;
  parentTrials = parent;
  arena = navigation;
  const slider = element("step");
  slider.min = branch.fork_step;
  slider.max = branch.last_step;
  slider.value = Math.min(branch.last_step, Math.max(initialStep ?? 36, branch.fork_step));
  element("resume").hidden = branch.last_step >= run.last_step;
  element("cost").textContent = "";
  element("storageReport").hidden = true;
  const divergence = "First different state " + (branch.first_changed_state ?? "—") +
    " · first different choice " + (branch.first_changed_choice ?? "—");
  const reversal = branch.encounters_after_fork ? " · sustained preference for B after " + branch.encounters_after_fork + " encounters" : "";
  element("compare").textContent = branch.parent_id ? divergence + reversal : "Original training history";
  element("arenaReport").hidden = !arena;
  element("arenaEmpty").hidden = !!arena;
  if (arena) {
    options("arenaTrial", arena.conditions[0].trials.map((t, index) => [index, "Trial " + t.step]));
    element("arenaStats").replaceChildren();
    for (const mode of arena.conditions) {
      addRow("arenaStats", [arenaNames[mode.condition], percent(mode.correct_arm),
        percent(mode.arrival_rate), mode.mean_error.toFixed(2)]);
    }
    element("arenaExport").href = prefix + path + "/arena";
    drawMaze();
  }
  await inspect();
}

async function show(id, selected, step) {
  const request = ++runRequest;
  ++branchRequest;
  ++stateRequest;
  const data = await api("/runs/" + id);
  if (request !== runRequest) return;
  run = data;
  element("saved").value = id;
  element("seed").value = run.seed;
  const label = "Run " + id + " · seed " + run.seed + " · " + run.version;
  element("export").href = prefix + "/runs/" + id;
  if (!run.branches.length) {
    element("report").hidden = true;
    element("status").textContent = label + " · start this seed again to finish the interrupted run.";
    return;
  }
  options("branch", run.branches.map(b => [b.branch_id, branchName(b)]));
  element("coverage").textContent = "FlyWire 783 · " +
    run.wiring.map(w => number.format(w.edges) + " " + w.pathway + " edges").join(" · ");
  element("metrics").replaceChildren();
  for (const branch of run.branches) {
    const probes = Object.fromEntries(run.metrics.filter(m => m.branch_id === branch.branch_id)
      .map(m => [m.phase, m.preference_a]));
    addRow("metrics", [branchName(branch), percent(probes.baseline), percent(probes.retention), percent(probes.final)]);
  }
  addRow("metrics", ["Simple odor-value baseline · reward A", percent(run.baseline.baseline),
    percent(run.baseline.retention), percent(run.baseline.final)]);
  await choose(selected || run.branches[0].branch_id, step);
  if (request === runRequest) {
    element("status").textContent = label;
    element("report").hidden = false;
  }
}

element("create").onsubmit = handle(async () => {
  const button = element("create").querySelector("button");
  button.disabled = true;
  element("status").textContent = "Simulating and committing trial histories…";
  try {
    const result = await api("/runs", { seed: Number(element("seed").value) });
    await refresh();
    await show(result.run_id);
  } finally {
    button.disabled = false;
  }
});

element("fork").onsubmit = handle(async () => {
  const id = run.run_id;
  const step = Number(element("step").value);
  const result = await api("/runs/" + id + "/fork", {
    parent: branch.branch_id, step, mode: element("mode").value,
  });
  await show(id, result.branch_id, step);
});
element("resume").onclick = handle(async () => {
  const id = branch.branch_id;
  const runId = run.run_id;
  await api("/branches/" + id + "/advance", {});
  await show(runId, id);
});
element("branch").onchange = handle(() => choose(element("branch").value));
element("saved").onchange = handle(() => show(element("saved").value));
element("step").oninput = historyChart;
element("step").onchange = handle(inspect);

element("benchmark").onclick = handle(async () => {
  const id = branch.branch_id;
  const button = element("benchmark");
  button.disabled = true;
  element("cost").textContent = "Reconstructing trial states in both representations…";
  try {
    const result = await api("/branches/" + id + "/benchmark");
    if (branch.branch_id !== id) return;
    element("storage").replaceChildren();
    const ms = value => value.toFixed(2) + " ms";
    addRow("storage", ["Table and index storage", number.format(result.dense_bytes) + " bytes",
      number.format(result.sparse_bytes) + " bytes"]);
    addRow("storage", ["Bulk insert", ms(result.dense_write_ms), ms(result.sparse_write_ms)]);
    addRow("storage", ["Median replay", ...result.median_read_ms.map(ms)]);
    for (const [label, timing] of [["First", result.first_noncheckpoint], ["Last", result.last_noncheckpoint]]) {
      if (timing) addRow("storage", [label + " trial between checkpoints · " + timing[0], ms(timing[1]), ms(timing[2])]);
    }
    element("cost").textContent = result.verified + " matching state hashes · " + result.gains +
      " gains per full snapshot · " + result.changed_rows + " sparse changes · " + result.checkpoint_count + " checkpoints";
    element("storageReport").hidden = false;
  } finally {
    button.disabled = false;
  }
});

element("arenaMode").onchange = element("arenaTrial").onchange = () => {
  clearInterval(animation);
  drawMaze();
};
element("play").onclick = () => {
  clearInterval(animation);
  let frame = 0;
  const last = arena.conditions[0].trials[0].path.length - 1;
  drawMaze(frame);
  animation = setInterval(() => {
    drawMaze(++frame);
    if (frame === last) clearInterval(animation);
  }, 60);
};

options("mode", Object.entries(interventions));
element("mode").value = "paired_B";
options("arenaMode", Object.entries(arenaNames));
handle(async () => {
  const list = await refresh();
  if (list.length) await show((list.find(r => r.seed === 42) || list[0]).run_id);
  else element("status").textContent = "Run a new experiment to start conditioning.";
})();

