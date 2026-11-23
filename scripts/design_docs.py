"""Regenerate the generated parts of the design documents from the live system.

    python scripts/design_docs.py            # everything
    python scripts/design_docs.py normalisation dfd queries dictionary

Each document in docs/ is hand-written prose with generated blocks between
    <!-- generated:NAME -->  and  <!-- /generated:NAME -->
Only those blocks are replaced. Results are also saved as JSON in
results/design/ (served by the API, shown on the dashboard's Design page).
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"
RESULTS = ROOT / "results" / "design"


def fill(doc: Path, name: str, content: str) -> None:
    text = doc.read_text(encoding="utf-8")
    pattern = re.compile(rf"(<!-- generated:{name} -->\n).*?(<!-- /generated:{name} -->)", re.S)
    if not pattern.search(text):
        raise SystemExit(f"{doc.name}: no generated block '{name}'")
    text = pattern.sub(lambda m: m.group(1) + content.rstrip() + "\n" + m.group(2), text)
    doc.write_text(text, encoding="utf-8", newline="\n")


def save(name: str, data) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")


def table(header: list[str], rows: list[list]) -> str:
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def keys_text(keys) -> str:
    return "; ".join("(" + ", ".join(k) + ")" for k in keys)


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ------------------------------------------------------------- normalisation
def normalisation() -> None:
    from backend.db import connect
    from backend.design import fd as F, maintenance, normalisation as N

    analysis = N.analyse()
    t0 = time.perf_counter()
    with connect() as conn:
        live = N.verify(conn, scan_data=True)
        stats = maintenance.file_stats(conn=conn)
    seconds = round(time.perf_counter() - t0, 1)
    demo = maintenance.anomalies()
    with connect() as conn:
        walk = N.walkthrough(conn)
    save("normalisation", {"generated_at": stamp(), "analysis": analysis, "verification": live,
                           "verification_seconds": seconds, "walkthrough": walk, "anomalies": demo})
    doc = DOCS / "NORMALISATION.md"
    live_by = {r["relation"]: r for r in live["relations"]}

    rows = []
    for r in analysis["relations"]:
        notes = []
        if r["violations"]:
            notes.append("controlled redundancy: " + "; ".join(v["fd"] for v in r["violations"]))
        if r["first_nf"]["non_scalar"]:
            notes.append("non-scalar: " + ", ".join(r["first_nf"]["non_scalar"]))
        if r.get("temporal"):
            notes.append("temporal key")
        rows.append([f"`{r['relation']}`", keys_text(r["candidate_keys"]), r["highest_form"],
                     "✓" if all(d["status"] in ("enforced", "holds") for d in live_by[r["relation"]]["dependencies"])
                     else "✗", "; ".join(notes)])
    s = analysis["summary"]
    head = (f"{s['relations']} relations ({live['coverage']['live_tables']} base tables in the database, all "
            f"catalogued with matching columns: {'yes' if not live['coverage']['missing'] and not live['coverage']['column_mismatch'] else 'NO'}). "
            f"Highest normal form: " + ", ".join(f"{k} {v}" for k, v in s["by_highest_form"].items() if v) +
            f". Every dependency checked on the loaded data: {'all hold' if live['ok'] else 'FAILURES, see results/design/normalisation.json'} "
            f"({seconds} s, {stamp()}).")
    fill(doc, "summary", head + "\n\n" + table(["Relation", "Candidate keys", "Highest NF", "FDs hold", "Notes"], rows))

    parts = []
    for r in analysis["relations"]:
        if not r["violations"]:
            continue
        lv = live_by[r["relation"]]
        parts.append(f"#### `{r['relation']}` — {r['highest_form']}\n")
        parts.append(f"Keys: {keys_text(r['candidate_keys'])}. Minimal cover: " +
                     "; ".join(f"`{f}`" for f in r["minimal_cover"]) + ".\n")
        for red in r["redundancy"]:
            check = next((d for d in lv["dependencies"] if d["fd"] == F.fmt(F.parse(red["fd"]))), None)
            result = (f"{check['violating_groups']} violating groups ({check['ms']} ms)" if check and "ms" in check
                      else (check or {}).get("status", "-"))
            parts.append(f"- `{F.fmt(F.parse(red['fd']))}`: {red['why'][0].upper() + red['why'][1:]}. Enforced by "
                         f"{', '.join('`' + c + '`' for c in red['enforced_by'])}. Live check: {result}.")
        d = r["bcnf_decomposition"]
        parts.append("\nBCNF decomposition: " + " ⋈ ".join(
            "(" + ", ".join(p["attributes"]) + ")" for p in d["parts"]) +
            f" — lossless: {'yes' if d['lossless'] else 'no'}; dependencies not preserved: "
            f"{', '.join('`' + x + '`' for x in d['not_preserved']) or 'none'}.\n")
    fill(doc, "redundancy", "\n".join(parts))

    lines = []
    for step in walk:
        lines.append(f"**{step['form']} — {step['title']}**\n")
        sm = step.get("sample")
        if sm and step["form"] == "UNF":
            lines.append(f"Real rows: {sm['neuron']} in release {sm['release']}, read from the loaded data "
                         f"({sm['partners_total']:,} partners; the {sm['partners_shown']} strongest are shown). "
                         f"In 1NF every partner row repeats the neuron's facts, so they are stored "
                         f"{sm['partners_total']:,} times for this neuron alone.\n")
        if sm and sm.get("note"):
            lines.append(f"*Note:* {sm['note']}.\n")
        for r in step["relations"]:
            k = f" — keys {keys_text(r['keys'])}, highest form **{r['highest_form']}**" if r.get("keys") else ""
            lines.append(f"- `{r['name']}`({', '.join(r['attrs'])}){k}")
            for v in r.get("violations", []):
                lines.append(f"  - breaks {v['form']}: `{v['fd']}` ({v['reason']})")
            if r.get("note"):
                lines.append(f"  - {r['note']}")
            if r.get("rows"):
                lines.append("\n" + table(list(r["attrs"]), [[_cell(v, 70) for v in row] for row in r["rows"]]) + "\n")
        if step.get("decision"):
            lines.append(f"\n{step['decision']}")
        lines.append("")
    fill(doc, "walkthrough", "\n".join(lines))

    red = demo["redundancy"]
    an = [f"Unnormalised copy: {red['unnormalised_rows']} rows. Neuron {red['neuron']['source_neuron_id']} "
          f"(`{red['neuron']['cell_type']}`) has its cell type stored {red['copies_of_its_cell_type']} times there, "
          f"once in `lab.neuron`.\n",
          table(["Anomaly", "Unnormalised `connection_flat`", "Normalised lab tables"], [
              ["Update", f"`{demo['update']['unnormalised']['sql']}` → {demo['update']['unnormalised']['status']}; "
                         f"{demo['update']['unnormalised']['result']}",
               f"`{demo['update']['normalised']['sql']}` → {demo['update']['normalised']['status']}; "
               f"{demo['update']['normalised']['result']}"],
              ["Insertion", f"`{demo['insert']['unnormalised']['sql']}` → {demo['insert']['unnormalised'].get('error')}",
               f"`{demo['insert']['normalised']['sql']}` → {demo['insert']['normalised']['status']}; "
               f"{demo['insert']['normalised']['result']}"],
              ["Deletion", f"`{demo['delete']['unnormalised']['sql']}` → {demo['delete']['unnormalised']['status']}; "
                           f"{demo['delete']['unnormalised']['result']}",
               f"`{demo['delete']['normalised']['sql']}` → {demo['delete']['normalised']['status']}; "
               f"{demo['delete']['normalised']['result']}"]]),
          f"\nAll of it ran in one transaction that was rolled back ({demo['seconds']} s, {stamp()})."]
    fill(doc, "anomalies", "\n".join(an))

    mb = lambda b: f"{(b or 0) / 1048576:,.1f}"
    srows = [[f"`{x['schema']}.{x['table']}`", f"`{x['file']}`", mb(x["heap_bytes"]), mb(x["index_bytes"]),
              mb(x["toast_bytes"]), f"{x['live_tuples'] or 0:,}", f"{x['dead_tuples'] or 0:,}",
              str(x["last_autovacuum"] or x["last_vacuum"] or "-")[:16]] for x in stats if x["schema"] == "cc"][:12]
    fill(doc, "files", table(["Table", "File (under the data directory)", "Heap MB", "Index MB", "TOAST MB",
                              "Live tuples", "Dead tuples", "Last vacuum"], srows) +
         f"\n\nThe 12 largest tables of schema `cc`, {stamp()}.")
    print(f"normalisation: {s['relations']} relations, verified={live['ok']} in {seconds}s")


# ---------------------------------------------------------------- data flow
def data_flow() -> None:
    from backend.design import dfd

    spec = dfd.load()
    result = dfd.check(spec)
    out = DOCS / "diagrams"
    out.mkdir(parents=True, exist_ok=True)
    for d in spec["diagrams"]:
        (out / f"dfd-{d['id']}.svg").write_text(dfd.render(d["id"], spec), encoding="utf-8", newline="\n")
    save("dfd", {"generated_at": stamp(), "check": result, "diagrams": dfd.diagrams(spec)})
    doc = DOCS / "DATA_FLOW.md"
    rules = table(["Rule", "Checked", "Result"],
                  [[k, v, "✓" if not any(e["rule"] == k for e in result["errors"]) else
                    "✗ " + "; ".join(e["message"] for e in result["errors"] if e["rule"] == k)]
                   for k, v in result["rules"].items()])
    counts = table(["Diagram", "Processes", "External entities", "Data stores", "Flows"],
                   [[k, v["processes"], v["externals"], v["stores"], v["flows"]] for k, v in result["diagrams"].items()])
    verdict = "All rules hold" if result["ok"] else "RULES BROKEN"
    fill(doc, "check", f"{verdict} ({stamp()}).\n\n{rules}\n\n{counts}")
    externals = table(["Id", "Name", "Role"], [[k, v["name"], v["description"]] for k, v in spec["externals"].items()])
    stores = table(["Id", "Name", "Holds"], [[k, v["name"], v["holds"]] for k, v in spec["stores"].items()])
    fill(doc, "elements", f"**External entities**\n\n{externals}\n\n"
                          f"**Data stores** (what each holds in the implementation)\n\n{stores}")
    for d in spec["diagrams"]:
        name = d["id"]
        rows = [[f["from"], f["to"], f["label"], f.get("part_of", "")] for f in d["flows"]]
        procs = table(["No.", "Process", "Implemented by"],
                      [[k, v["name"], v.get("by", "")] for k, v in d["processes"].items()])
        flows = table(["From", "To", "Data", "Part of (parent flow)"], rows)
        fill(doc, name, f"![{d['title']}](diagrams/dfd-{name}.svg)\n\n{procs}\n\n"
                        f"<details><summary>{len(rows)} flows</summary>\n\n{flows}\n\n</details>")
    print(f"data flow: {len(spec['diagrams'])} diagrams, rules ok={result['ok']}")


# ------------------------------------------------------------------ queries
def _cell(v, width=60) -> str:
    text = json.dumps(v, default=str) if isinstance(v, (dict, list)) else ("NULL" if v is None else str(v))
    return text if len(text) <= width else text[: width - 1] + "…"


def queries() -> None:
    from backend.design import queries as Q

    t0 = time.perf_counter()
    data = Q.run_all()
    save("queries", {"generated_at": stamp(), **data})
    doc = DOCS / "QUERIES.md"
    s = data["summary"]
    lines = [f"{s['queries']} queries ({s['sql']} SQL, {s['mongo']} MongoDB); {s['passed']} passed every check; "
             f"run on the loaded data in {time.perf_counter() - t0:.0f} s ({stamp()}).\n",
             table(["Category", "Queries", "Passed"],
                   [[c["title"], sum(r["category"] == c["id"] for r in data["results"]),
                     sum(r["ok"] for r in data["results"] if r["category"] == c["id"])] for c in data["categories"]])]
    for cat in data["categories"]:
        lines.append(f"\n### {cat['title']}\n")
        for r in (x for x in data["results"] if x["category"] == cat["id"]):
            target = {"read": "read-only", "trial": "trial on the real tables, rolled back",
                      "lab": "lab, rolled back"}.get(r["target"], r["target"])
            lines.append(f"#### `{r['id']}`: {r['question']}\n")
            if r["language"] == "sql":
                lines.append(f"```sql\n{r['code'].strip()}\n```\n")
            else:
                lines.append(f"```js\ndb.neuron_source_record.{r['code']['op']}({json.dumps({k: v for k, v in r['code'].items() if k != 'op'})})\n```\n")
            lines.append(f"*Why this form:* {r['why']}\n")
            res, plan = r.get("result"), r.get("plan") or {}
            if r.get("error"):
                lines.append(f"Result ({target}): rejected: `{r['error'].splitlines()[0]}`\n")
            elif res and r["language"] == "sql" and res.get("columns"):
                more = f" (first {len(res['rows'])} shown)" if res["row_count"] > len(res["rows"]) else ""
                lines.append(f"Result ({target}): {res['row_count']} row(s){more}, {res['elapsed_ms']} ms.\n")
                lines.append(table(res["columns"], [[_cell(v) for v in row] for row in res["rows"][:6]]) + "\n")
            elif res and r["language"] == "mongo":
                lines.append(f"Result: {res['row_count']} document(s), {res['elapsed_ms']} ms.\n")
                lines.append("```json\n" + _cell(res["documents"][:3], 600) + "\n```\n")
            if plan.get("nodes") is not None:
                idx = ", ".join(f"`{x}`" for x in plan["indexes"]) or "none"
                seq = ", ".join(f"`{x}`" for x in plan["seq_scans"]) or "none"
                lines.append(f"Plan: top node {plan['top_node']}; indexes used: {idx}; sequential scans: {seq}; "
                             f"execution {plan['execution_ms']} ms.\n")
            elif plan.get("stages"):
                lines.append(f"Plan: {' → '.join(reversed(plan['stages']))}; indexes: "
                             f"{', '.join(plan['indexes']) or 'none'}; documents examined: {plan['docs_examined']}; "
                             f"keys examined: {plan['keys_examined']}.\n")
            lines.append("Checks: " + "; ".join(("✓ " if c["ok"] else "✗ ") + c["check"] for c in r["checks"]) + "\n")
    fill(doc, "catalogue", "\n".join(lines))
    print(f"queries: {s['passed']}/{s['queries']} passed")


# ---------------------------------------------------------------- dictionary
def _bytes(n) -> str:
    n = float(n or 0)
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def dictionary() -> None:
    from backend.db import connect
    from backend.design import dictionary as D

    with connect() as conn:
        data = D.build(conn)
    save("dictionary", {"generated_at": stamp(), **data})
    s, pg = data["summary"], data["postgres"]
    out = [f"Read from the PostgreSQL catalog and MongoDB on {stamp()}: {s['tables']} tables "
           f"({s['described_tables']} described), {s['columns']} columns, {s['constraints']} constraints "
           f"({s['foreign_keys']} foreign keys, {s['checks']} checks, {s['exclusions']} exclusion), "
           f"{s['indexes']} indexes, {s['triggers']} triggers, {s['views']} views, {s['functions']} functions, "
           f"{s['mongo_collections']} MongoDB collection(s).\n"]
    for schema in ("cc", "lab"):
        out.append(f"\n## Schema `{schema}`\n")
        for t in (x for x in pg["tables"] if x["schema"] == schema):
            out.append(f"\n### `{schema}.{t['name']}`\n")
            out.append(f"{t['description'] or '(no description)'}  \n"
                       f"About {max(t['approx_rows'], 0):,} rows, {_bytes(t['total_bytes'])} with indexes.\n")
            out.append(table(["#", "Column", "Type", "Null", "Key", "Default", "Description"],
                             [[c["position"], f"`{c['name']}`", c["type"], "" if c["not_null"] else "yes", c["key"],
                               "generated" if c["generated"] else ("identity" if c["identity"]
                                                                    else _cell(c["default_expr"] or "", 40)),
                               c["description"] or ""] for c in t["columns"]]) + "\n")
            out.append(table(["Constraint", "Kind", "Definition"],
                             [[f"`{k['name']}`", k["kind"], f"`{_cell(k['definition'], 140)}`"]
                              for k in t["constraints"]]) + "\n")
            extra = [i for i in t["indexes"] if not i["is_primary"]]
            if extra:
                out.append("Indexes: " + "; ".join(
                    f"`{i['name']}` ({i['method']}{', unique' if i['is_unique'] else ''}, {_bytes(i['bytes'])})"
                    for i in extra) + "\n")
            if t["triggers"]:
                out.append("Triggers: " + "; ".join(
                    f"`{g['name']}`" + (" (constraint trigger)" if g["constraint_trigger"] else "")
                    for g in t["triggers"]) + "\n")
    out.append("\n## Views\n")
    out.append(table(["View", "Kind", "Description"],
                     [[f"`{v['schema']}.{v['name']}`", "materialized" if v["materialized"] else "view",
                       v["description"] or ""] for v in pg["views"]]))
    out.append("\n## Functions\n")
    out.append(table(["Function", "Returns", "Language", "Volatility"],
                     [[f"`{f['schema']}.{f['name']}({_cell(f['arguments'], 70)})`", _cell(f["returns"], 50),
                       f["language"], f["volatility"]] for f in pg["functions"]]))
    out.append("\n## MongoDB collections\n")
    for m in data["mongo"]:
        if m["validator"]:
            v = "Validator: `" + json.dumps(m["validator"]) + "`."
        else:
            v = ("No server-side validator: each document keeps every original source column, so the shape "
                 "differs per dataset; the loader writes them and `metadata check` verifies them against PostgreSQL.")
        out.append(f"\n### `{m['database']}.{m['name']}`\n\n{m['documents']:,} documents. {v}\n")
        out.append(table(["Index", "Keys", "Unique", "Partial filter"],
                         [[f"`{i['name']}`", f"`{json.dumps(i['keys'])}`", "yes" if i["unique"] else "",
                           f"`{json.dumps(i['partial'])}`" if i["partial"] else ""] for i in m["indexes"]]))
    fill(DOCS / "DATA_DICTIONARY.md", "dictionary", "\n".join(out))
    print(f"dictionary: {s['tables']} tables, {s['columns']} columns")


# -------------------------------------------------------------------- design
def _first_line(doc) -> str:
    para = (doc or "").strip().split("\n\n")[0]
    return " ".join(para.split())


def design() -> None:
    import ast
    from fastapi.routing import APIRoute
    from backend.db import connect
    from backend.design import dictionary as D, normalisation as N
    from backend.design.relations import RELATIONS
    from backend.main import app

    with connect() as conn:
        dic = D.build(conn, with_mongo=False)
    rows = {(t["schema"], t["name"]): t for t in dic["postgres"]["tables"]}
    highest = {r["relation"]: r["highest_form"] for r in N.analyse()["relations"]}
    doc = DOCS / "DESIGN.md"
    groups: dict[str, list] = {}
    for r in RELATIONS:
        groups.setdefault(r["group"], []).append(r)
    inv = [table(["Group", "Relation", "Rows", "Normal form", "Purpose"],
                 [[g, f"`{r['schema']}.{r['name']}`",
                   f"{max(rows[(r['schema'], r['name'])]['approx_rows'], 0):,}",
                   highest.get(f"{r['schema']}.{r['name']}", ""), r["purpose"]]
                  for g, rs in groups.items() for r in rs])]
    s = dic["summary"]
    inv.append(f"\nRow counts above 100,000 are the planner's estimate. Totals ({stamp()}):{s['tables']} tables, {s['columns']} columns, {s['foreign_keys']} foreign "
               f"keys, {s['checks']} checks, {s['exclusions']} exclusion constraints, {s['indexes']} indexes, "
               f"{s['triggers']} triggers, {s['views']} views, {s['functions']} SQL functions.")
    fill(doc, "inventory", "\n".join(inv))

    routes = sorted((r for r in app.routes if isinstance(r, APIRoute)), key=lambda r: r.path)
    fill(doc, "api", f"{len(routes)} endpoints (interactive OpenAPI at `/docs`).\n\n" + table(
        ["Method", "Path", "Purpose"],
        [[", ".join(sorted(r.methods - {"HEAD"})), f"`{r.path}`",
          _first_line(r.endpoint.__doc__) or r.name.replace("_", " ")] for r in routes]))

    mig = [[f"`{f.stem[:4]}`", _first_line(ast.get_docstring(ast.parse(f.read_text(encoding="utf-8"))))]
           for f in sorted((ROOT / "alembic" / "versions").glob("0*.py"))]
    fill(doc, "migrations", table(["Revision", "What it adds"], mig))

    mods = [[f"`{f.relative_to(ROOT).as_posix()}`",
             _first_line(ast.get_docstring(ast.parse(f.read_text(encoding="utf-8"))))]
            for f in sorted((ROOT / "backend").rglob("*.py"))
            if f.name != "__init__.py" and "__pycache__" not in f.parts]
    fill(doc, "modules", table(["Module", "Responsibility"], mods))

    tests = []
    for f in sorted((ROOT / "tests").rglob("test_*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        n = sum(isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and x.name.startswith("test_")
                for x in ast.walk(tree))
        tests.append([f"`{f.relative_to(ROOT).as_posix()}`", n, _first_line(ast.get_docstring(tree))])
    fill(doc, "tests", table(["File", "Tests", "What it covers"], tests)
         + f"\n\n{sum(t[1] for t in tests)} test functions.")
    print("design: inventory, api, migrations, modules, tests")


TASKS = {"normalisation": normalisation, "dfd": data_flow, "queries": queries,
         "dictionary": dictionary, "design": design}

if __name__ == "__main__":
    wanted = sys.argv[1:] or list(TASKS)
    for name in wanted:
        TASKS[name]()
