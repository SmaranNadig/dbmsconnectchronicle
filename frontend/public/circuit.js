const el = id => document.getElementById(id);
const base = "/api/v1/circuit";
const names = {intact:"Intact circuit",silence_T4:"Silence T4",silence_T5:"Silence T5",
  random_silence:"Silence a matched random set",rewired:"Locally rewired",shuffled_weights:"Shuffled weights",
  disconnected:"Disconnected output",pixel_correlator:"Pixel correlator"};
let current, traces, timer, generation = 0;
async function api(path, options) {
  const response = await fetch(base + path, options);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw Error(error.detail || "Circuit API unavailable (" + response.status + ")");
  }
  return response.json();
}
function row(parent, values) {
  const tr = parent.insertRow();
  values.forEach(value => { tr.insertCell().textContent = value; });
}
function freezePlayback() {
  clearInterval(timer);
  timer = null;
  el("play").textContent = "Play";
}
function render() {
  if (!traces) return;
  const i = Number(el("frame").value);
  const peak = Math.max(1e-12, ...traces.flatMap(t => t.replay.flatMap(f => f.activity)));
  traces.forEach((trial, j) => {
    const f = trial.replay[i], panel = el("panels").children[j];
    panel.querySelector("h3").textContent = names[trial.condition];
    const ctx = panel.querySelector("canvas").getContext("2d");
    const width=400, height=125, step=width/f.pixels.length;
    ctx.fillStyle="#182332"; ctx.fillRect(0,0,width,height);
    f.pixels.forEach((v,k) => { const n=Math.round(255*v); ctx.fillStyle="rgb("+[n,n,n]+")"; ctx.fillRect(k*step,0,step+1,48); });
    f.activity.forEach((v,k) => {
      ctx.fillStyle=k<4?"#6bc8db":"#eab364";
      const h=v/peak*50; ctx.fillRect(8+k*49,103-h,34,h);
      ctx.fillStyle="#ffffff"; ctx.fillText(current.outputs[k],8+k*49,119);
    });
    panel.querySelector("p").textContent = "Frame " + (i+1) + " · Drift " + f.position.toFixed(2) +
      " · Steering " + f.action.toFixed(2) + " · Scene moves " + (trial.direction===1?"right":"left");
  });
}
async function loadReplay() {
  freezePlayback();
  const ticket=++generation;
  const path="/runs/"+current.run_id+"/replay?trial="+el("trial").value+"&condition=";
  const condition=el("condition").value;
  const diff=current.paired_diff.find(d=>d.condition===condition);
  el("paired").textContent=names[condition]+" lost "+diff.lost_correct+" correct predictions and gained "+
    diff.gained_correct+" across "+diff.paired_trials+" paired scenes. Mean change in drift: "+diff.added_drift.toFixed(2)+" grid units.";
  const pair=await Promise.all(["intact",condition].map(c=>api(path+c)));
  if(ticket!==generation) return;
  traces=pair; el("frame").value=0;
  el("trace").href=base+path+condition; render();
}
async function showRun(id) {
  freezePlayback();
  const ticket=++generation;
  const data=await api("/runs/"+id);
  const [evidence,probe]=await Promise.all(["evidence","polarity"].map(key=>api("/snapshots/"+data.snapshot_id+"/"+key)));
  if(ticket!==generation) return;
  current=data;
  el("probe").textContent=["bright","dark"].map(key=>key+" edge · T4 "+probe[key].T4.toExponential(2)+
    " · T5 "+probe[key].T5.toExponential(2)).join("  /  ");
  el("metrics").replaceChildren();
  for(const key of Object.keys(names)) {
    const m=data.metrics.find(item=>item.condition===key);
    row(el("metrics"),[names[key],(100*m.accuracy).toFixed(1)+"%",m.heading_error.toFixed(2),(100*m.heading_improvement).toFixed(1)+"%"]);
  }
  el("edges").replaceChildren();
  evidence.edges.forEach(e=>row(el("edges"),[e.pre_type+" → "+e.post_type,e.pre_root+" → "+e.post_root,e.weight,e.archive_path+" · "+e.source_lines.join(", ")]));
  el("identity").textContent="Snapshot "+data.snapshot_id+" · FlyWire 783 · SHA-256 "+data.digest;
  el("sqlid").textContent=id; el("export").href=base+"/runs/"+id;
  el("seed").value=data.seed; el("patch").value=data.snapshot_id; el("history").value=id;
  el("status").textContent="Run "+id+" · Seed "+data.seed+" · "+data.model_version;
  el("report").hidden=false; await loadReplay();
}
async function refresh() {
  const runs=await api("/runs"); el("history").length=1;
  runs.forEach(r=>el("history").add(new Option("Run "+r.run_id+" · patch "+r.snapshot_id+" · seed "+r.seed,r.run_id)));
  return runs;
}
function guarded(fn) {return async event=>{try{await fn(event);}catch(e){el("status").textContent=e.message;}};}
el("setup").onsubmit=guarded(async event=>{
  event.preventDefault(); el("run").disabled=true; el("status").textContent="Simulating paired trials…";
  try {
    const data=await api("/runs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({snapshot_id:Number(el("patch").value),seed:Number(el("seed").value)})});
    await refresh(); await showRun(data.run_id);
  } finally {el("run").disabled=false;}
});
el("history").onchange=guarded(()=>el("history").value && showRun(el("history").value));
el("trial").onchange=el("condition").onchange=guarded(loadReplay);
el("frame").oninput=()=>{freezePlayback(); render();};
el("play").onclick=()=>{
  if(timer){freezePlayback();return;}
  el("play").textContent="Pause";
  timer=setInterval(()=>{el("frame").value=(Number(el("frame").value)+1)%32; render();},120);
};
for(const [key,name] of Object.entries(names)) if(key!=="intact") el("condition").add(new Option(name,key));
for(let i=0;i<24;i++) el("trial").add(new Option(String(i+1),i));
for(let i=0;i<2;i++) {
  const panel=document.createElement("div");
  panel.innerHTML='<h3></h3><canvas width="400" height="125" aria-label="Eye brightness and cell-type activity"></canvas><p></p>';
  el("panels").append(panel);
}
guarded(async()=>{
  const patches=await api("/snapshots");
  for(const p of patches) el("patch").add(new Option("Radius "+p.radius+" · "+p.neurons+" neurons · "+p.edges+" edges",p.snapshot_id));
  if(!patches.length){el("status").textContent="No FlyWire circuit is loaded in this database. Open the real-data dashboard or import a snapshot.";return;}
  const preferred=patches.find(p=>p.radius===4)||patches[0];
  el("patch").value=preferred.snapshot_id; el("run").disabled=false;
  el("status").textContent="Ready to run.";
  const runs=await refresh();
  const saved=runs.find(r=>r.snapshot_id===preferred.snapshot_id && r.seed===42)||runs[0];
  if(saved) await showRun(saved.run_id);
})();
