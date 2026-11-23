"""Data flow diagrams: rule checker and SVG renderer (docs/DATA_FLOW.md).

config/dfd.yaml is the only description of the data flows. check() applies
the DFD rules to it, render() draws each diagram in Gane-Sarson notation
from the same data, so a drawing can only show flows the checker has seen.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from html import escape
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "config" / "dfd.yaml"

VERBS = {"acquire", "load", "synchronise", "compare", "resolve", "distribute", "answer", "edit", "register",
         "stage", "validate", "merge", "record", "reconstruct", "compute", "score", "report"}


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load(path: Path = SPEC) -> dict:
    return _load(str(path), path.stat().st_mtime)


def kind(spec: dict, eid: str, diagram: dict) -> str | None:
    if eid in diagram["processes"]:
        return "process"
    if eid in spec["externals"]:
        return "external"
    if eid in spec["stores"]:
        return "store"
    return None


def _key(flow: dict) -> str:
    """The parent-level flow a flow belongs to (for balancing)."""
    return flow.get("part_of") or flow["label"]


# ------------------------------------------------------------------ rules
RULES = {
    "R1": "every flow has a label and connects elements of its diagram",
    "R2": "data moves only through a process: no external→external, external↔store or store→store flow",
    "R3": "every process has at least one input and one output (no black hole, no miracle)",
    "R4": "process numbers are hierarchical: a child of process n is numbered n.1, n.2, ...",
    "R5": "process names start with a verb (verb + object); the context process names the system",
    "R6": "a child diagram is balanced: its flows across the boundary are exactly the parent process's flows "
          "(data stores first shown on the child are local to the process)",
    "R7": "the context diagram has one process (0) and no data stores",
    "R8": "every data store is both written and read in the model",
    "R9": "no flow is drawn twice (same source, target and label)",
}


def check(spec: dict | None = None) -> dict:
    spec = spec or load()
    errors: list[dict] = []
    err = lambda rule, where, msg: errors.append({"rule": rule, "diagram": where, "message": msg})
    diagrams = {d["id"]: d for d in spec["diagrams"]}
    by_process = {}           # process number -> diagram that contains it
    for d in spec["diagrams"]:
        for pid in d["processes"]:
            by_process[pid] = d

    for d in spec["diagrams"]:
        did = d["id"]
        placed = set(d.get("place", {}))
        seen = set()
        for f in d["flows"]:
            label = (f.get("label") or "").strip()
            if not label:
                err("R1", did, f"flow {f.get('from')} → {f.get('to')} has no label")
            ks, kt = kind(spec, f["from"], d), kind(spec, f["to"], d)
            for end, k in ((f["from"], ks), (f["to"], kt)):
                if k is None:
                    err("R1", did, f"flow '{label}' uses unknown element {end}")
                elif k != "process" and end not in placed:
                    err("R1", did, f"flow '{label}' uses {end}, which is not placed on the diagram")
            if ks and kt and "process" not in (ks, kt):
                err("R2", did, f"'{label}' goes {ks} {f['from']} → {kt} {f['to']} without a process")
            sig = (f["from"], f["to"], label)
            if sig in seen:
                err("R9", did, f"'{label}' {f['from']} → {f['to']} is drawn twice")
            seen.add(sig)
        for pid, p in d["processes"].items():
            ins = [f for f in d["flows"] if f["to"] == pid]
            outs = [f for f in d["flows"] if f["from"] == pid]
            if not ins:
                err("R3", did, f"process {pid} '{p['name']}' has no input (miracle)")
            if not outs:
                err("R3", did, f"process {pid} '{p['name']}' has no output (black hole)")
            if d["level"] > 0 and p["name"].split()[0].lower() not in VERBS:   # level 0 names the system
                err("R5", did, f"process {pid} '{p['name']}' does not start with a verb")
            parent = d.get("parent")
            if d["level"] == 1 and not re.fullmatch(r"[1-9][0-9]*", pid):
                err("R4", did, f"level-1 process {pid} is not numbered 1, 2, ...")
            if d["level"] >= 2 and not re.fullmatch(rf"{re.escape(parent)}\.[1-9][0-9]*", pid):
                err("R4", did, f"process {pid} is not numbered {parent}.n")
        for e in placed:
            if kind(spec, e, d) is None:
                err("R1", did, f"placed element {e} is not defined")
        if d["level"] == 0:
            if list(d["processes"]) != ["0"]:
                err("R7", did, "the context diagram must contain exactly process 0")
            if any(kind(spec, e, d) == "store" for e in placed):
                err("R7", did, "the context diagram shows a data store")

        # balancing against the parent process
        parent = d.get("parent")
        if parent is not None:
            pd = by_process.get(parent)
            if pd is None:
                err("R6", did, f"parent process {parent} does not exist")
                continue
            parent_flows = {(f["from"], f["to"], f["label"]) for f in pd["flows"] if parent in (f["from"], f["to"])}
            # inside the parent process: its child processes, and data stores that
            # first appear on the child diagram (local to the process)
            inside = set(d["processes"]) | {e for e in d.get("place", {})
                                            if e in spec["stores"] and e not in pd.get("place", {})}
            covered = set()
            for f in d["flows"]:
                inner_from, inner_to = f["from"] in inside, f["to"] in inside
                if inner_from and inner_to:
                    continue
                src = parent if inner_from else f["from"]
                dst = parent if inner_to else f["to"]
                sig = (src, dst, _key(f))
                if sig not in parent_flows:
                    err("R6", did, f"'{f['label']}' ({f['from']} → {f['to']}) has no matching flow "
                                   f"'{_key(f)}' {src} → {dst} on {pd['id']}")
                covered.add(sig)
            for missing in sorted(parent_flows - covered):
                err("R6", did, f"parent flow '{missing[2]}' {missing[0]} → {missing[1]} is not shown")

    # stores written and read, over the whole model (level 1 has them all)
    for sid in spec["stores"]:
        writes = any(f["to"] == sid for d in spec["diagrams"] for f in d["flows"])
        reads = any(f["from"] == sid for d in spec["diagrams"] for f in d["flows"])
        if not writes:
            err("R8", "model", f"data store {sid} is never written")
        if not reads:
            err("R8", "model", f"data store {sid} is never read")

    counts = {d["id"]: {"processes": len(d["processes"]), "flows": len(d["flows"]),
                        "externals": sum(1 for e in d.get("place", {}) if e in spec["externals"]),
                        "stores": sum(1 for e in d.get("place", {}) if e in spec["stores"])}
              for d in spec["diagrams"]}
    return {"ok": not errors, "errors": errors, "rules": RULES, "diagrams": counts}


# ---------------------------------------------------------------- drawing
PW, PH = 210, 104          # process box
EW, EH = 170, 64           # external entity
SW, SH = 250, 40           # data store
FONT = "font-family='Segoe UI, Helvetica, Arial, sans-serif'"

STYLE = """
<style>
  .proc { fill: #eef4ff; stroke: #2f5cb8; stroke-width: 1.6; }
  .band { fill: #2f5cb8; }
  .ext  { fill: #fff6e6; stroke: #a15c00; stroke-width: 1.6; }
  .shadow { fill: #c9b089; }
  .store { fill: #eefaf1; stroke: #2e7d4f; stroke-width: 1.6; }
  .flow { fill: none; stroke: #444; stroke-width: 1.3; }
  .lbl  { fill: #fff; fill-opacity: 0.92; }
  text { fill: #1d1d1f; }
  .num  { fill: #fff; font-weight: 600; }
  .small { fill: #555; }
  .title { font-weight: 600; }
</style>"""


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return lines + ([line] if line else [])


def _box(spec, d, eid):
    if eid in d["processes"]:
        p = d["processes"][eid]
        x, y = p["at"]
        return x, y, p.get("w", PW), p.get("h", PH)
    x, y = d["place"][eid]
    return (x, y, EW, EH) if eid in spec["externals"] else (x, y, SW, SH)


def _clip(cx, cy, w, h, tx, ty):
    """Point where the ray from the box centre towards (tx, ty) leaves the box."""
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    sx = (w / 2) / abs(dx) if dx else math.inf
    sy = (h / 2) / abs(dy) if dy else math.inf
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def _text(x, y, lines, size=13, cls="", anchor="middle", weight=None):
    out = []
    y0 = y - (len(lines) - 1) * size * 0.6
    for i, line in enumerate(lines):
        w = f" font-weight='{weight}'" if weight else ""
        out.append(f"<text x='{x:.0f}' y='{y0 + i * size * 1.2:.0f}' font-size='{size}' text-anchor='{anchor}' "
                   f"dominant-baseline='middle' class='{cls}'{w}>{escape(line)}</text>")
    return "".join(out)


def _process(pid, p):
    x, y = p["at"]
    w, h = p.get("w", PW), p.get("h", PH)
    l, t = x - w / 2, y - h / 2
    name = _wrap(p["name"], 24 if w <= PW else 30)
    by = _wrap(p.get("by", ""), 34 if w <= PW else 44)[:2]
    return (f"<g><rect class='proc' x='{l:.0f}' y='{t:.0f}' width='{w}' height='{h}' rx='12'/>"
            f"<path class='band' d='M{l:.0f},{t + 24:.0f} V{t + 12:.0f} Q{l:.0f},{t:.0f} {l + 12:.0f},{t:.0f} "
            f"H{l + w - 12:.0f} Q{l + w:.0f},{t:.0f} {l + w:.0f},{t + 12:.0f} V{t + 24:.0f} Z'/>"
            + _text(x, t + 12, [pid], 13, "num")
            + _text(x, y + 2 - (6 if by else 0), name, 14, weight="600")
            + (f"<line x1='{l:.0f}' x2='{l + w:.0f}' y1='{t + h - 26:.0f}' y2='{t + h - 26:.0f}' stroke='#9db3e0'/>"
               + _text(x, t + h - 13, by[:1], 10.5, "small") if by else "")
            + "</g>")


def _external(eid, e, x, y):
    l, t = x - EW / 2, y - EH / 2
    return (f"<g><rect class='shadow' x='{l + 6:.0f}' y='{t + 6:.0f}' width='{EW}' height='{EH}'/>"
            f"<rect class='ext' x='{l:.0f}' y='{t:.0f}' width='{EW}' height='{EH}'/>"
            + _text(x, y - 7, _wrap(e["name"], 20), 14, weight="600") + _text(x, t + EH - 10, [eid], 11, "small")
            + "</g>")


def _store(sid, s, x, y):
    l, t = x - SW / 2, y - SH / 2
    return (f"<g><rect x='{l:.0f}' y='{t:.0f}' width='{SW}' height='{SH}' class='store' stroke='none'/>"
            f"<path class='store' fill='none' d='M{l + SW:.0f},{t:.0f} H{l:.0f} V{t + SH:.0f} H{l + SW:.0f}'/>"
            f"<line x1='{l + 44:.0f}' x2='{l + 44:.0f}' y1='{t:.0f}' y2='{t + SH:.0f}' stroke='#2e7d4f' stroke-width='1.6'/>"
            + _text(l + 22, y, [sid], 13, weight="600") + _text(l + 44 + (SW - 44) / 2, y, [s["name"]], 13.5)
            + "</g>")


def label_at(d, f) -> float:
    """Where the label sits along its flow: given, or staggered (0.32 from each
    source) when a flow runs the other way between the same two elements."""
    if "label_at" in f:
        return f["label_at"]
    reverse = any(g["from"] == f["to"] and g["to"] == f["from"] for g in d["flows"])
    return 0.32 if reverse else 0.5


def _flow(spec, d, f):
    (sx, sy, sw, sh), (tx, ty, tw, th) = _box(spec, d, f["from"]), _box(spec, d, f["to"])
    bend = f.get("bend", 0)
    mx, my = (sx + tx) / 2, (sy + ty) / 2
    length = math.hypot(tx - sx, ty - sy) or 1
    nx, ny = -(ty - sy) / length, (tx - sx) / length          # left normal of the direction
    cx, cy = mx + nx * bend * 2, my + ny * bend * 2           # quadratic control point
    ax, ay = _clip(sx, sy, sw, sh, cx, cy)
    bx, by = _clip(tx, ty, tw, th, cx, cy)
    t = label_at(d, f)
    lx = (1 - t) ** 2 * ax + 2 * (1 - t) * t * cx + t ** 2 * bx
    ly = (1 - t) ** 2 * ay + 2 * (1 - t) * t * cy + t ** 2 * by
    lines = _wrap(f["label"], 22)
    wbox = max(len(s) for s in lines) * 6.4 + 10
    hbox = len(lines) * 14 + 4
    return (f"<path class='flow' d='M{ax:.0f},{ay:.0f} Q{cx:.0f},{cy:.0f} {bx:.0f},{by:.0f}' marker-end='url(#arrow)'/>",
            f"<rect class='lbl' x='{lx - wbox / 2:.0f}' y='{ly - hbox / 2:.0f}' width='{wbox:.0f}' height='{hbox:.0f}' rx='3'/>"
            + _text(lx, ly, lines, 11.5))


def _legend(x, y):
    return (f"<g transform='translate({x},{y})'>"
            "<rect x='0' y='0' width='392' height='56' fill='#fafafa' stroke='#ccc'/>"
            "<rect class='proc' x='10' y='12' width='36' height='30' rx='6'/><rect class='band' x='10' y='12' width='36' height='8'/>"
            "<text x='52' y='28' font-size='11' dominant-baseline='middle'>process</text>"
            "<rect class='shadow' x='113' y='17' width='34' height='26'/><rect class='ext' x='110' y='14' width='34' height='26'/>"
            "<text x='152' y='28' font-size='11' dominant-baseline='middle'>external entity</text>"
            "<path class='store' fill='#eefaf1' d='M282,16 H244 V40 H282'/><line x1='254' x2='254' y1='16' y2='40' stroke='#2e7d4f'/>"
            "<text x='288' y='28' font-size='11' dominant-baseline='middle'>data store</text>"
            "<path class='flow' d='M345,28 H382' marker-end='url(#arrow)'/>"
            "<text x='363' y='46' font-size='10' text-anchor='middle'>flow</text></g>")


def render(diagram_id: str, spec: dict | None = None) -> str:
    spec = spec or load()
    d = next(x for x in spec["diagrams"] if x["id"] == diagram_id)
    w, h = d["size"]
    lines, labels = [], []
    for f in d["flows"]:
        a, b = _flow(spec, d, f)
        lines.append(a)
        labels.append(b)
    shapes = [_process(pid, p) for pid, p in d["processes"].items()]
    for eid, (x, y) in d.get("place", {}).items():
        shapes.append(_external(eid, spec["externals"][eid], x, y) if eid in spec["externals"]
                      else _store(eid, spec["stores"][eid], x, y))
    head = 46
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 -{head} {w} {h + head + 70}' {FONT} "
            f"role='img' aria-label='{escape(d['title'])}'>{STYLE}"
            "<defs><marker id='arrow' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='8' markerHeight='8' "
            "orient='auto-start-reverse'><path d='M0,0 L10,5 L0,10 z' fill='#444'/></marker></defs>"
            f"<rect x='0' y='-{head}' width='{w}' height='{h + head + 70}' fill='#fff'/>"
            f"<text x='16' y='-18' font-size='18' class='title'>{escape(d['title'])}</text>"
            + "".join(lines) + "".join(shapes) + "".join(labels)
            + _legend(w - 408, h + 8) + "</svg>")


def diagrams(spec: dict | None = None) -> list[dict]:
    spec = spec or load()
    return [{"id": d["id"], "level": d["level"], "parent": d.get("parent"), "title": d["title"],
             "processes": [{"id": k, **{x: v[x] for x in ("name", "by") if x in v}} for k, v in d["processes"].items()],
             "flows": [{k: f[k] for k in ("from", "to", "label", "part_of") if k in f} for f in d["flows"]]}
            for d in spec["diagrams"]]
