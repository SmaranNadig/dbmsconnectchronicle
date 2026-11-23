# Design documentation and verification — plan and checklist

This phase turns the design of ConnectomeChronicle into documents that are
**generated from, and checked against, the running system**: the query
catalogue is executed, the data flow diagrams are checked against DFD rules,
the normal form of every relation is computed from its functional
dependencies and each dependency is verified on the loaded data, and the
data dictionary is read from the PostgreSQL catalog. Nothing in these
documents is typed in by hand where the database can answer instead, so the
documentation cannot drift from the implementation.

Everything is part of the product: the same material is served by the API
and shown on a new **Design** page of the dashboard, next to the existing
concept pages, query consoles and Data editor.

## Deliverables

| Area | Where | Generated / checked by |
|---|---|---|
| Query catalogue (SQL and MongoDB), executed, with results and plans | `docs/QUERIES.md`, Design → Queries, `config/query_catalogue.yaml` | `backend/design/queries.py` runs every query through the read-only console path and records rows, timing, and plan (index or sequential scan) |
| Data flow diagrams, levels 0, 1 and 2 | `docs/DATA_FLOW.md`, `docs/diagrams/*.svg`, Design → Data flow | `config/dfd.yaml` is the single source; `backend/design/dfd.py` checks the rules and draws the SVGs |
| Normalisation (1NF, 2NF, 3NF, BCNF) of every relation | `docs/NORMALISATION.md`, Design → Normalisation | `backend/design/fd.py` (closures, candidate keys, normal-form tests, decomposition), `backend/design/relations.py` (declared dependencies), each dependency verified with SQL on the live data |
| File maintenance and updation | `docs/NORMALISATION.md` §Maintenance, Design → Maintenance | insertion / update / deletion anomalies reproduced on an unnormalised copy and shown absent in the normalised lab; table file statistics and `VACUUM (ANALYZE)` on the lab |
| Detailed design | `docs/DESIGN.md`, `docs/DATA_DICTIONARY.md` | architecture, modules, relational schema, data dictionary read from the catalog, API, security, transactions |

## Decisions

1. **One notation for all DFDs: Gane–Sarson.** Processes are rounded
   rectangles with a number band, data stores are open rectangles with a
   `D`-number, external entities are shaded squares. Every flow is labelled
   with the data it carries. Numbering is hierarchical (`2` → `2.1 … 2.6`).
2. **The DFD is data, not a drawing.** `config/dfd.yaml` lists entities,
   processes, stores and flows per level. The checker enforces:
   - every flow is labelled and connects known elements;
   - no flow runs entity → entity, entity → store, store → entity or
     store → store (data moves only through a process);
   - every process has at least one input and one output (no black holes,
     no miracles);
   - every store is both written and read somewhere;
   - a child diagram is balanced with its parent process (same external
     inputs and outputs);
   - child process numbers extend their parent's number.

   The SVGs are drawn from the same file, so a diagram cannot show a flow
   that the checker has not seen.
3. **Normal forms are computed, not asserted.** Each relation declares its
   attributes and functional dependencies (FDs). The code computes the
   candidate keys and the highest normal form, and decomposes a relation to
   BCNF when a dependency breaks it. A test fails if a relation in the
   database has no entry, or if an entry's attributes differ from the live
   columns. Every declared FD `X → Y` is checked on the loaded data with
   `GROUP BY X HAVING count(DISTINCT Y) > 1`; zero rows must come back.
4. **Report the core schema's controlled redundancy honestly.** Some
   relations keep a copy of `dataset_id` (e.g. `connection`, `provenance`,
   `neuron_region_synapses`, `diff_run`). The copy exists so that a
   composite foreign key can guarantee that an edge never joins two
   different connectomes. Strictly, this breaks 3NF / BCNF.

   For each such relation the documentation shows:
   - the violating dependency;
   - the BCNF decomposition, the proof (chase) that it is lossless, and the
     dependency it would no longer preserve, if any (for `connection`,
     `neuron_region_synapses`, `provenance` and `dataset_neuron` BCNF would
     lose a dependency, which is why the copy is kept and protected);
   - the constraint that keeps the copy consistent, so the update anomaly
     cannot occur;
   - a live check that zero rows disagree.
5. **The lab schema is strictly BCNF.** `lab.connection` drops its
   `dataset_id` copy. The same "one dataset per edge" rule is enforced by a
   constraint trigger instead. The lab is where insert, update and delete
   are demonstrated, so it shows the textbook design next to the core
   schema's trade-off.
6. **Queries are run, not pasted.** The catalogue states a question per
   query, the SQL or MongoDB operation, and why it is written that way
   (which index or constraint it relies on). The runner executes it on the
   real database as `cc_reader` (read-only, rolled back), and records:
   - the result rows (first rows and total count);
   - the execution time;
   - the plan nodes (`Index Scan` vs `Seq Scan`; `IXSCAN` vs `COLLSCAN`).

   A query that errors or changes its plan class shows up as a failed check.
7. **The data dictionary comes from `information_schema` / `pg_catalog`**,
   with column descriptions stored as `COMMENT ON` in a migration (0014), so
   `\d+` in psql shows the same text as the document.

## Checklist

### Normalisation
- [x] `backend/design/fd.py`: closure, candidate keys, prime attributes, 2NF / 3NF / BCNF tests, BCNF decomposition, lossless-join (chase) and dependency-preservation checks
- [x] `backend/design/relations.py`: attributes and FDs of every relation in `cc` and `lab`, 1NF notes for array / jsonb columns, and the protecting constraint for each controlled redundancy
- [x] live FD verification (SQL per FD) and live redundancy-consistency checks
- [x] unnormalised → 1NF → 2NF → 3NF → BCNF walk-through, filled with a real neuron's rows from the loaded data (hemibrain 487144598, v1.2)
- [x] anomaly demonstration (insert / update / delete) on an unnormalised copy versus the lab, inside a rolled-back transaction
- [x] file statistics (relation files, sizes, live / dead tuples, last vacuum) and `VACUUM (ANALYZE)` on the lab
- [x] `lab.connection` in BCNF (constraint trigger), examples and tests updated
- [x] `docs/NORMALISATION.md`

### Data flow
- [x] `config/dfd.yaml`: context (level 0), level 1, level 2 for ingestion (process 2) and ConnectomeDiff (process 4)
- [x] `backend/design/dfd.py`: rule checker and SVG renderer (legend, numbering, labels)
- [x] `docs/diagrams/*.svg`, `docs/DATA_FLOW.md`

### Queries
- [x] `config/query_catalogue.yaml`: SQL (selection, joins, aggregation, subqueries, set operations, window functions, recursive CTE, temporal / as-of, views and functions, DML in the trial target) and MongoDB (find, projection, aggregate, index use)
- [x] MongoDB `explain` support for the catalogue
- [x] runner, `results/design/queries.json`, `docs/QUERIES.md`

### Detailed design
- [x] migration 0014: `COMMENT ON` tables and columns (0013 holds the normalisation fixes)
- [x] `backend/design/dictionary.py` and `docs/DATA_DICTIONARY.md`
- [x] `docs/DESIGN.md` (architecture, modules, schema, API, security, transactions, UI)

### Dashboard and API
- [x] `/api/v1/design/...` endpoints (dfd, normalisation, dictionary, queries, maintenance)
- [x] Design page with tabs: Data flow, Normal forms, Anomalies & files, Queries, Dictionary
- [x] links from README / ARCHITECTURE / IMPLEMENTATION

### Verification
- [x] unit tests: FD algebra on textbook relations, DFD rules (the real spec passes; broken specs fail for the right reason)
- [x] integration tests: every live relation is catalogued with matching columns; every declared FD holds; every catalogue query runs; anomaly demo; maintenance endpoint
- [x] full test suite green (151 passed); `scripts/design_docs.py` regenerates all generated documents and diagrams
