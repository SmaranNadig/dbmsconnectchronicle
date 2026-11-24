const element = id => document.getElementById(id);
const {number, percent, signed, node, row, meter, roots, stat, canvas, chart} = window.Lab;
const prefix = "/api/v1/memory";
const interventions = {
  paired_A: ["Reward A", "Pairs A with reward during training. The readout stays active and gains can change."],
  paired_B: ["Reward B", "Pairs B with reward during training. Fork at trial 36 to reverse the learned A preference."],
  frozen: ["Freeze plasticity", "Keeps the inherited gains fixed and the readout active. This can restore choice after silencing."],
  unpaired: ["Shuffle reward timing", "Uses the stored shuffled reward schedule without consistently rewarding either odor."],
  rewired: ["Rewire sensory inputs", "Shuffles which Kenyon cells receive the input connections while preserving endpoint stub counts."],
  silent_output: ["Silence output", "Sets both choice probabilities to 50%. Feedback can still update the gains."],
};
const arenaNames = {
  combined: "Vision + memory",
  vision_only: "Vision only",
  memory_only: "Memory only",
  disabled: "Both disabled",
};
const phaseNames = {baseline: "Baseline", conditioning: "Condition", retention: "Retention",
  retraining: "Retrain", final: "Final test"};
const feedbackPhases = new Set(["conditioning", "retraining"]);
let run, branch, arena, animation, historyArea;
let trials = [], parentTrials = [], phases = [];
let runRequest = 0, branchRequest = 0, stateRequest = 0;

const addRow = (id, values) => row(element(id), values);
function options(id, items) {
  element(id).replaceChildren(...items.map(([value, label]) => new Option(label, value)));
}

function branchName(data) {
  const parent = run.branches.find(b => b.branch_id === data.parent_id);
  let name = interventions[data.intervention][0];
  if (data.intervention === "frozen" && data.fork_step === 36) name = "Retain memory with gains frozen";
  if (data.intervention === "frozen" && parent?.intervention === "silent_output") name = "Restore output";
  return "#" + data.branch_id + " · " + name + (parent ? " from trial " + data.fork_step : " · original");
}

async function api(path, body) {
  const request = body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  };
  const response = await fetch(prefix + path, request);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw Error(typeof error.detail === "string" ? error.detail : "Memory Lab could not load this request.");
  }
  return response.json();
}

function handle(fn) {
  return async event => {
    event?.preventDefault();
    try { await fn(event); }
    catch (error) { element("status").textContent = error.message; }
  };
}

async function refresh() {
  const list = await api("/runs");
  options("saved", list.map(r => [r.run_id, "Run " + r.run_id + " · seed " + r.seed + " · " + r.version]));
  element("saved").disabled = !list.length;
  return list;
}

function preparePhases() {
  phases = [];
  run.protocol.events.forEach((event, i) => {
    const current = phases.at(-1);
    if (current?.phase === event.phase) current.end = i + 1;
    else phases.push({phase: event.phase, start: i + 1, end: i + 1});
  });
}

function renderPhaseButtons() {
  element("phase-jumps").replaceChildren(...phases.map(phase => {
    const button = node("button", phaseNames[phase.phase], feedbackPhases.has(phase.phase) ? "training" : "");
    button.type = "button";
    button.append(node("small", "Trials " + phase.start + "–" + phase.end));
    button.dataset.start = phase.start;
    button.dataset.end = phase.end;
    button.disabled = phase.end < branch.fork_step || phase.start > branch.last_step;
    button.onclick = handle(() => inspectStep(Math.min(phase.end, branch.last_step)));
    return button;
  }));
}

function historyChart() {
  if (!branch) return;
  const current = Number(element("step").value);
  historyArea = chart(element("history"), {
    end: run.last_step, y: [0, 1], ticks: [0, .5, 1], format: value => 100 * value + "%",
    labels: [[0, "0"], ...phases.map(p => [p.end, String(p.end)])],
    bands: phases.filter(p => feedbackPhases.has(p.phase)).map(p => [p.start, p.end]),
    cursor: current,
    series: [parentTrials, trials].map((series, index) => ({
      dashed: index === 0,
      points: series.filter(t => t.p_a !== null).map(t => [t.step, t.p_a]),
    })),
  });
  for (const button of element("phase-jumps").children) {
    button.setAttribute("aria-current", String(current >= Number(button.dataset.start) && current <= Number(button.dataset.end)));
  }
}

function inspectStep(step) {
  element("step").value = Math.max(branch.fork_step, Math.min(branch.last_step, step));
  return inspect();
}

function showEncounter(data) {
  const trial = data.trial;
  const explanation = data.explanation;
  element("decision-trace").hidden = !explanation;
  element("checkpoint-note").hidden = !!explanation;
  if (!explanation) {
    if (explanation === undefined) {
      element("checkpoint-note").textContent = "The encounter explanation is available after the API reloads. The saved trial and gains are shown on this page.";
      return;
    }
    element("checkpoint-note").textContent = branch.parent_id
      ? "Trial " + trial.step + " is this branch’s starting checkpoint. It inherits the parent’s gains. Select a later trial to see a choice and feedback."
      : "Trial 0 stores the starting gains. Select an encounter to see the odors, choice, and update.";
    return;
  }

  element("odor-cards").replaceChildren(...explanation.odors.map(odor => {
    const card = node("div", null, "lab-card");
    const color = odor.odor === "B" ? "amber" : "";
    const channels = node("div", null, "odor-channels");
    for (const channel of odor.channels) {
      channels.append(meter(channel.value, channel.name + " · " + percent(channel.value), color));
    }
    const side = trial.left_odor === odor.odor ? "left" : "right";
    card.append(node("h3", "Odor " + odor.odor + " · " + side + " arm"),
      node("p", "Strongest olfactory input channels", "note"), channels,
      node("p", odor.active_kenyon_cells + " active Kenyon cells", "odor-metrics"),
      meter(odor.output_reduction, percent(odor.output_reduction) + " reduction in modeled MBON01 drive", color));
    return card;
  }));
  element("choice-detail").replaceChildren(
    meter(trial.p_a, "Odor A · " + percent(trial.p_a)),
    meter(1 - trial.p_a, "Odor B · " + percent(1 - trial.p_a), "amber"),
    node("p", "Recorded choice · Odor " + trial.choice),
    node("p", explanation.output_silenced
      ? "Output silencing gives both odors a 50% chance."
      : "The odor with greater output reduction receives the higher probability. The stored draw selects one outcome.", "note"),
  );
  const feedback = trial.presented
    ? "Odor " + trial.presented + (trial.reward ? " received reward." : " was presented without reward.")
    : "Unrewarded test encounter. Learning pauses during tests.";
  const update = explanation.plasticity_frozen ? "This branch freezes gains." :
    "Only gains eligible for the modeled update can change.";
  element("update-detail").replaceChildren(
    node("p", feedback),
    node("p", explanation.weakened + " weakened · " + explanation.recovered + " partially restored"),
    node("p", update, "note"),
  );
}

async function inspect() {
  const ticket = ++stateRequest;
  const id = branch.branch_id;
  const step = Number(element("step").value);
  historyChart();
  element("fork").querySelector("button").disabled = step >= run.last_step;
  const path = "/branches/" + id + "/state/" + step;
  const data = await api(path);
  if (ticket !== stateRequest || branch.branch_id !== id) return;
  const trial = data.trial;
  const choice = trial.choice ? " · chose " + trial.choice + " · P(A) " + percent(trial.p_a) : "";
  const reward = trial.presented ? " · presented " + trial.presented + (trial.reward ? " with reward" : " without reward") : "";
  element("event").textContent = "Trial " + step + " · " + trial.phase + choice + reward;
  showEncounter(data);
  const parentState = parentTrials.find(t => t.step === step);
  const sameState = parentState && parentState.state_hash === trial.state_hash;
  const storage = data.reconstruction ? [
    stat(String(data.reconstruction.checkpoint), "Checkpoint used"),
    stat(number.format(data.reconstruction.changes), "Later gain rows applied"),
  ] : [];
  element("store-summary").replaceChildren(
    ...storage,
    stat(String(data.changed_from_naive), "Gains changed from baseline"),
    ...(parentState ? [stat(sameState ? "Same state" : "Diverged", "State hash compared with the parent")] : []),
  );
  element("digest").textContent = data.changed_from_naive + " gains changed from baseline · SHA-256 " + trial.state_hash;
  element("edges").replaceChildren();
  for (const edge of data.edges) {
    addRow("edges", [roots(edge), edge.regional_weight, meter(edge.gain),
      edge.neuropil + " · row " + number.format(edge.source_line)]);
  }
  const source = data.edges[0];
  element("source").textContent = source?.archive_path ? source.archive_path + " · SHA-256 " + source.sha256 : "";
  element("mixtures").replaceChildren();
  for (const mixture of data.mixtures) {
    addRow("mixtures", [mixture.percent_a + "%", meter(mixture.probability)]);
  }
  element("states").href = prefix + path;
  element("divergenceSql").textContent = "SELECT * FROM cc.memory_divergence WHERE branch_id = " + id + ";";
  element("stateSql").textContent = "SELECT w.pre_root, w.post_root, w.source_line, m.gain / 10000.0 AS effective_gain\n" +
    "FROM cc.memory_state_at(" + id + ", " + step + ") m\n" +
    "JOIN cc.memory_wiring w USING (connection_id)\n" +
    "WHERE w.snapshot_id = " + run.snapshot_id + " ORDER BY effective_gain LIMIT 8;";
}

function stopArena() {
  clearInterval(animation);
  animation = null;
  element("play").textContent = "Play route";
}

function drawMaze() {
  if (!arena) return;
  const condition = arena.conditions.find(c => c.condition === element("arenaMode").value);
  const trialIndex = Number(element("arenaTrial").value);
  const trial = condition.trials[trialIndex];
  const reference = arena.conditions[0].trials[trialIndex];
  const overlay = condition.condition !== "combined" && element("arenaOverlay").checked;
  const index = Number(element("arenaFrame").value);
  const surface = canvas(element("maze"));
  if (!surface) return;
  const {ctx, width, height} = surface;
  const extent = Math.max(14, ...[...trial.path, ...reference.path].map(([x]) => Math.abs(x) + 1));
  const x = position => width / 2 + position / extent * (width / 2 - 24);
  const y = progress => height - 24 - progress * (height - 64);

  for (const side of [-1, 1]) {
    ctx.strokeStyle = "#354b60";
    ctx.lineWidth = Math.min(56, width / 9);
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(x(0), y(0));
    ctx.lineTo(x(0), y(.3));
    ctx.lineTo(x(side * 10), y(1));
    ctx.stroke();
    const odor = side === -1 ? trial.left : (trial.left === "A" ? "B" : "A");
    ctx.textAlign = "center";
    ctx.fillStyle = odor === trial.goal ? "#ffcc82" : "#dbe6ef";
    ctx.fillText("Odor " + odor + (odor === trial.goal ? " · target" : ""), x(side * 10), 15);
    if (odor === trial.goal) ctx.fillRect(x(side * 10 - 3), y(1) + 14, x(6) - x(0), 2);
  }

  for (const [route, dashed] of [...(overlay ? [[reference, true]] : []), [trial, false]]) {
    const color = dashed ? "#ffcc82" : "#68d9ce";
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 3;
    ctx.setLineDash(dashed ? [6,5] : []);
    ctx.beginPath();
    for (const [position, progress] of route.path.slice(0, index + 1)) ctx.lineTo(x(position), y(progress));
    ctx.stroke();
    ctx.setLineDash([]);
    const [position, progress] = route.path[index];
    ctx.beginPath();
    ctx.arc(x(position), y(progress), dashed ? 4 : 5, 0, 2 * Math.PI);
    ctx.fill();
  }
  element("route-legend").textContent = arenaNames[condition.condition];
  element("route-reference").hidden = !overlay;
  element("arenaText").textContent = "Visual run " + arena.visual_run + " · target " + trial.goal + " · chose " +
    trial.choice + " · arrival error " + trial.arrival_error.toFixed(2) + " units · frame " + (index + 1);
  element("arena-detail").replaceChildren(
    stat(signed(trial.steering[index]), "Current steering correction"),
    stat(trial.correct_arm ? "Target arm" : "Other arm", "Odor selected"),
    stat(trial.arrived ? "Reached target" : "Missed target", "Final arrival in this replay"),
    ...(overlay ? [stat(reference.arrival_error.toFixed(2), "Vision + memory arrival error")] : []),
  );
  for (const tr of element("arenaStats").rows) tr.classList.toggle("is-selected", tr.dataset.condition === condition.condition);
  element("arenaOverlay").disabled = condition.condition === "combined";
}

async function choose(id, initialStep) {
  const request = ++branchRequest;
  ++stateRequest;
  stopArena();
  const selected = run.branches.find(b => b.branch_id === Number(id));
  const path = "/branches/" + selected.branch_id;
  const [own, parent, navigation] = await Promise.all([
    api(path + "/trials"),
    selected.parent_id ? api("/branches/" + selected.parent_id + "/trials") : [],
    selected.last_step > (phases.find(p => p.phase === "final").start - 1) && run.visual_run_id ? api(path + "/arena") : null,
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
  renderPhaseButtons();
  element("parent-legend").hidden = !branch.parent_id;
  element("resume").hidden = branch.last_step >= run.last_step;
  element("cost").textContent = "";
  element("storageReport").hidden = true;
  const divergence = "First different state " + (branch.first_changed_state ?? "—") +
    " · first different choice " + (branch.first_changed_choice ?? "—");
  const reversal = branch.encounters_after_fork ? " · sustained preference for B after " + branch.encounters_after_fork + " encounters" : "";
  element("compare").textContent = branch.parent_id ? divergence + reversal : "Original training history";
  const probes = run.paired_probes.filter(p => p.branch_id === branch.branch_id);
  element("matched-probes").textContent = probes.map(p => phaseNames[p.phase] + " · " +
    signed(100 * p.preference_change, 1) + " pp in P(A) vs parent across " + p.paired_trials + " pairs").join("  /  ");
  for (const tr of element("metrics").rows) tr.classList.toggle("is-selected", Number(tr.dataset.branch) === branch.branch_id);
  element("arenaReport").hidden = !arena;
  element("arenaEmpty").hidden = !!arena;
  if (arena) {
    options("arenaTrial", arena.conditions[0].trials.map((t, index) => [index, "Trial " + t.step]));
    element("arenaStats").replaceChildren();
    for (const mode of arena.conditions) {
      const tr = addRow("arenaStats", [arenaNames[mode.condition], meter(mode.correct_arm),
        meter(mode.arrival_rate), mode.mean_error.toFixed(2) + " units"]);
      tr.dataset.condition = mode.condition;
    }
    element("arenaExport").href = prefix + path + "/arena";
    element("arenaFrame").max = arena.conditions[0].trials[0].path.length - 1;
    element("arenaFrame").value = element("arenaFrame").max;
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
  preparePhases();
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
  element("coverage").textContent = "FlyWire 783 · " + run.protocol.channels.length + " input channels · " +
    run.wiring.map(w => number.format(w.edges) + " " + w.pathway + " edges").join(" · ");
  element("metrics").replaceChildren();
  for (const b of run.branches) {
    const probes = Object.fromEntries(run.metrics.filter(m => m.branch_id === b.branch_id)
      .map(m => [m.phase, m.preference_a]));
    addRow("metrics", [branchName(b), meter(probes.baseline), meter(probes.retention), meter(probes.final)]).dataset.branch = b.branch_id;
  }
  addRow("metrics", ["Simple odor-value baseline · reward A", meter(run.baseline.baseline),
    meter(run.baseline.retention), meter(run.baseline.final)]);
  element("report").hidden = false;
  await choose(selected || run.branches[0].branch_id, step);
  if (request === runRequest) element("status").textContent = label;
}

element("create").onsubmit = handle(async () => {
  const button = element("create").querySelector("button");
  button.disabled = true;
  element("status").textContent = "Simulating and committing trial histories…";
  try {
    const result = await api("/runs", {seed: Number(element("seed").value)});
    await refresh();
    await show(result.run_id);
  } finally { button.disabled = false; }
});

element("fork").onsubmit = handle(async () => {
  const id = run.run_id;
  const step = Number(element("step").value);
  const button = element("fork").querySelector("button");
  button.disabled = true;
  try {
    const result = await api("/runs/" + id + "/fork", {
      parent: branch.branch_id, step, mode: element("mode").value,
    });
    await show(id, result.branch_id, step);
  } finally { button.disabled = Number(element("step").value) >= run.last_step; }
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
element("history").onclick = handle(event => {
  if (!historyArea) return;
  const {left, right, end} = historyArea;
  return inspectStep(Math.round((event.offsetX - left) * end / (right - left)));
});

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
    const maxBytes = Math.max(result.dense_bytes, result.sparse_bytes);
    addRow("storage", ["Table and index storage",
      meter(result.dense_bytes / maxBytes, number.format(result.dense_bytes) + " bytes"),
      meter(result.sparse_bytes / maxBytes, number.format(result.sparse_bytes) + " bytes", "amber")]);
    addRow("storage", ["Bulk insert", ms(result.dense_write_ms), ms(result.sparse_write_ms)]);
    addRow("storage", ["Median replay", ...result.median_read_ms.map(ms)]);
    for (const [label, timing] of [["First", result.first_noncheckpoint], ["Last", result.last_noncheckpoint]]) {
      if (timing) addRow("storage", [label + " trial between checkpoints · " + timing[0], ms(timing[1]), ms(timing[2])]);
    }
    element("cost").textContent = result.verified + " matching state hashes · " + result.gains +
      " gains per full snapshot · " + result.changed_rows + " sparse changes · " + result.checkpoint_count + " checkpoints";
    element("storageReport").hidden = false;
  } finally { button.disabled = false; }
});

element("arenaMode").onchange = element("arenaTrial").onchange = () => {
  stopArena();
  element("arenaFrame").value = element("arenaFrame").max;
  drawMaze();
};
element("arenaOverlay").onchange = drawMaze;
element("arenaFrame").oninput = () => { stopArena(); drawMaze(); };
element("play").onclick = () => {
  if (animation) { stopArena(); return; }
  const slider = element("arenaFrame");
  if (slider.value === slider.max) slider.value = 0;
  element("play").textContent = "Pause route";
  drawMaze();
  animation = setInterval(() => {
    slider.value = Number(slider.value) + 1;
    drawMaze();
    if (slider.value === slider.max) stopArena();
  }, 65);
};

options("mode", Object.entries(interventions).map(([key, [label]]) => [key, label]));
element("mode").value = "paired_B";
element("mode").onchange = () => { element("intervention-guide").textContent = interventions[element("mode").value][1]; };
element("mode").onchange();
options("arenaMode", Object.entries(arenaNames));
new ResizeObserver(historyChart).observe(element("history"));
new ResizeObserver(drawMaze).observe(element("maze"));
handle(async () => {
  const list = await refresh();
  if (list.length) await show((list.find(r => r.seed === 42) || list[0]).run_id);
  else element("status").textContent = "Run a new experiment to start conditioning.";
})();
