# ConnectomeChronicle: review and improvement plan

Historical pre-implementation assessment from 3 November 2026. The findings and
local-state descriptions below refer to the original review. The subsequent
implementation is documented in [PROJECT_GUIDE.md](PROJECT_GUIDE.md) and
[IMPLEMENTATION.md](IMPLEMENTATION.md).

## Recommendation

Keep the database core. Its strongest work is already implemented: temporal storage, database-enforced integrity, provenance, release comparison, heterogeneous metadata, and verified query routing. The largest visible improvement would be an interactive workspace where a user can search for a neuron, inspect its connections, compare releases, and trace a change back to its source.

Start with the correctness issues found during this review, then build a visual release-comparison workflow. Add anomaly detection after that workflow can display and explain the results. Treat learned cross-dataset matching as a separate research milestone with an evaluation plan.

## What is implemented and how it works

| Area | Implementation | Assessment |
|---|---|---|
| Data acquisition | Dataset catalog, per-source YAML mappings, download script, SHA-256 manifest, adapters for hemibrain and FlyWire | Implemented; raw public data is excluded from this checkout |
| Release ingestion | Chunked source reads, PostgreSQL COPY into temporary staging tables, SQL validation, delta merges | Implemented; registration and failure logging surround the atomic data-load transaction |
| Temporal database | `neuron_version`, `annotation`, and `connection` use release ranges (`valid_rel`) and transaction-time ranges (`tx`); GiST exclusions prevent overlapping versions; triggers restrict history changes | Implemented and tested |
| Provenance | Facts retain original source identifiers, file checksums, row numbers, ingestion runs, and transformation versions | Implemented; source identities remain available alongside canonical identities |
| ConnectomeDiff | SQL functions compare two releases of one dataset, persist added/removed/modified events, and calculate connection-weight changes | Implemented; anomaly detection and a dedicated interactive diff workspace are missing |
| Entity resolution | Source identities, type aliases, type-and-side crosswalk candidates, heuristic scores, unresolved cases, manual approval/rejection | Implemented baseline; embeddings/GraphSAGE are not implemented |
| MongoDB | One source-record document per neuron, with release-specific original rows; idempotent sync and PostgreSQL sync logs | Implemented; the consistency check currently verifies references and release-entry presence, not full record contents |
| Distribution | Hash, anatomical, and Louvain community placement; three PostgreSQL shards; mirrored cross-shard edges; parallel scatter/gather | Implemented for deployed snapshots; lifecycle and freshness need work |
| API | FastAPI endpoints for datasets, releases, neurons, history, provenance, diffs, mappings, consoles, and shard queries | Broad coverage; some input errors and current-state semantics need correction |
| Dashboard | Express proxy, plain HTML/CSS/JS, live schema catalog, SQL/MongoDB consoles, integrity demonstration, routing display, stored reports | Useful DBMS demonstration; most domain workflows still require SQL or direct API calls |

The data path is:

```text
Public files -> source adapters + mappings -> temporary PostgreSQL staging
             -> validation -> atomic temporal merge -> authoritative cc schema
                                                      |-> temporal queries / diffs / provenance
                                                      |-> er views for the team ER model
                                                      |-> partition assignments -> three shard copies -> router
Original neuron rows -> MongoDB documents <------------| metadata_doc_id
FastAPI -> Express /api proxy -> dashboard
```

An unchanged fact can span multiple releases in one valid-time interval. When a fact changes, the loader retires the old transaction-time belief and inserts the revised release intervals. This supports both “what did release r2 contain?” and “what did the database believe at time t?” These are separate questions and should become separate controls in the interface.

Shards contain the currently believed release history, filtered with `upper_inf(tx)`. They do not currently offer arbitrary transaction-time reconstruction. Keep this distinction explicit when exposing time-travel features.

## What I actually ran

Docker, a project `.env`, downloaded public data, and running databases were absent initially. I prepared a local virtual environment and portable PostgreSQL/MongoDB instances to execute the real application with the repository's synthetic fixtures.

Environment: Python 3.12.13, PostgreSQL 16.6, MongoDB 7.0.14, Node.js 24.15.0. One coordinator and three separate PostgreSQL processes used ports 55432–55435; MongoDB used port 57017. These are native processes on one machine, not Docker containers or a multi-host deployment.

- **74 integration tests passed**, zero failures/skips, in **67.93 seconds**. One dependency deprecation warning was reported.
- All migrations applied through revision `0010`.
- The integration suite exercised ingestion, rollback, temporal reconstruction, provenance, diffs, mapping review, MongoDB sync, regions, constraints, consoles, and distributed queries.
- I separately exercised **15 routed API queries**: five query types across all three strategies. Every answer matched the coordinator.
- All **nine deliberate integrity violations** were rejected.
- In the browser, I ran the SQL console, counted synthetic MongoDB records, and ran an anatomical shard query that returned an identical coordinator answer while touching one shard.
- I reproduced the API/data-integrity gaps below using the running API and disposable fixture databases, and observed the hard-coded MongoDB sample failure in the browser. The temporary document alteration used for the consistency check was restored.

Evidence is saved locally in `.dbdata/review/test-results.xml`, `runtime-probes.json`, and `consistency-probe.json`. That folder is ignored by Git. The committed test report and real-data benchmark were preserved.

After testing, I copied the fixture state into separate demo databases: `connectome_review` on the coordinator and each shard, and `connectome_meta_review` in MongoDB. The API and dashboard run against those review databases. The demo contains 17 synthetic source-neuron identities and 8 synced MongoDB documents; the fixture suite intentionally does not sync every loaded dataset.

Dashboard: <http://127.0.0.1:3000/>. API documentation: <http://127.0.0.1:8000/docs>.

The repository's saved real-data results—182,274 source identities, roughly 9 million stored connection rows, and 492 benchmark comparisons—are historical evidence from the supplied files. I did not re-ingest or re-benchmark the public datasets in this session. The current dashboard can show that saved benchmark beside live fixture counts; this is a presentation issue to fix.

## Confirmed issues and behavior to improve

### 1. “Current neuron” search matches historical cell types

`backend/main.py:162` describes the endpoint as current-neuron search. Its type filter checks the transaction-time range but not the valid-release range.

Reproduction: `GET /api/v1/neurons?dataset=toyfly&cell_type=B` returns source neuron `2` with displayed type `B2`. Its actual history is B in r1 and B2 in r2/r3. The filter matches an old valid-time interval while the displayed value comes from the current interval.

**Change:** resolve one release and one transaction-time instant for the request, and apply both consistently to neuron existence, filtering, and returned annotations. Default to the latest loaded release; offer an explicit historical mode.

### 2. Invalid future timestamps surface as server errors

`backend/main.py:287` passes future `as_of` values into a SQL function that rejects them. The endpoint does not translate that failure into a client error.

Reproduction: a diff request with tomorrow's timestamp returns **HTTP 500**, with `Internal Server Error`.

**Change:** validate the request and map expected database errors to stable 400/422 responses. Define timezone behavior and validate that both requested releases are loaded.

### 3. Metadata consistency can pass with source records missing

`backend/metadata.py:157` checks neuron references and the presence of release entries. It does not compare the actual original records, their counts, or their content hashes.

Reproduction: after setting one synthetic document's release `records` array to `[]`, `check_consistency('toyfly')` still returned `consistent: true`. Restoring the original document also passed.

**Change:** expose reference consistency and content integrity as distinct results. Store expected per-release record counts and deterministic content digests during sync, and verify them. Report missing, extra, or altered release entries; add repair/re-sync support. Update UI claims to match what is checked.

### 4. Repeated default diff requests create new runs

Two equivalent requests without `as_of` produced run IDs 9 and 10. Each request chooses a new `now()` value. SQL idempotency for an identical explicit timestamp is implemented and tested; this default behavior is a product/caching decision, not a failure of that guarantee.

**Change:** let the UI reuse a result for the same releases and data revision, with an explicit refresh action. Preserve exact timestamp semantics for deliberate historical queries.

### 5. A missing hard-coded sample crashes part of the MongoDB panel

The sample document is fixed to `hemibrain:1688868386` in `backend/showcase.py:156`. The fixture database does not contain it. On the running dashboard, `frontend/public/app.js:428` raised `TypeError: Cannot read properties of null (reading '_id')`. MongoDB statistics and the query console still worked, but the sample display failed.

**Change:** choose a sample from the active dataset/release, allow a user-selected neuron, and render an explicit empty/not-synced state when no document exists. Add a browser check with a fresh fixture database and with an empty metadata collection.

## Design gaps found in code inspection

These were identified from implementation, rather than reproduced as production incidents.

- **Shard verification is weaker than the dashboard claims.** `backend/distributed/deploy.py:135` compares owned-row counts and sums of connection IDs and weights. It does not establish row-by-row equality, and those comparisons do not verify mirror contents. Add checks of full row content, endpoints, valid ranges, dimensions, and mirrors, using exact comparisons for fixtures and deterministic chunked digests for large deployments.
- **Deployment success is aggregated across all history.** `backend/distributed/router.py:62` uses `bool_and(verified)` over every deployment record for a strategy. A recorded failed attempt can prevent a later successful deployment from being accepted. Select one complete, verified deployment generation instead.
- **Placement caches can become stale.** Router ownership/region maps and the coordinator's temporary primary-region table are built once. `backend/showcase.py:186` retains them indefinitely. Ingestion or repartitioning needs an explicit deployment version and cache invalidation/rebuild.
- **The demo routing endpoint serializes requests.** `backend/showcase.py:216` holds a global lock across the routed query and coordinator comparison. It also always executes both workloads. Separate demonstration mode from ordinary serving, use request-safe connection pools, and return execution metadata per request.
- **Ingestion concurrency deserves explicit coverage.** The registration phase marks existing running attempts interrupted before the load phase acquires its dataset lock. Concurrent requests and post-lock status rechecks need testing before offering ingestion through a UI.
- **The shared-app access model is unfinished.** Mapping reviews accept a caller-supplied reviewer string, and the API has no authenticated reviewer role. Before sharing the service, add authenticated attribution, authorization, least-privilege service credentials, and resource limits. The frontend currently listens on all interfaces by default.
- **Reports and examples are tied to assumptions about the data.** Examples, the sample MongoDB document, region choices, and several labels are hard-coded. Benchmark metadata includes a literal Docker topology string, and the latest saved benchmark is displayed without checking that it matches the current database. Make these dataset-aware and label each report with environment, dataset, release, revision, and timestamp.

## Scientific meaning to preserve

The project tracks revisions of reconstructed wiring diagrams. A release difference should be described as a reconstruction, annotation, or connectivity-record change unless there is separate evidence for a biological interpretation.

FlyWire root IDs can change after proofreading. A raw-ID comparison is useful, but additions/removals can reflect identifier churn. Where reliable lineage is available, add a lineage-aware comparison that can represent split/merge relationships and preserves unresolved cases. See the [CAVE project documentation on cell IDs and root IDs](https://github.com/CAVEconnectome/cave-diver/blob/main/docs/datastack-config.md).

Also distinguish within-specimen identity from cross-specimen cell-type correspondence. FlyWire/hemibrain matching should be presented as homologous cell or cell-type correspondence across brains, with an explicit relationship type. A shared type and side do not establish that two records describe the same individual biological neuron. This recommendation follows the distinction explored in [Schlegel et al., 2024](https://doi.org/10.1038/s41586-024-07686-5).

The current `1 / (candidate counts)` score is a heuristic assignment score. A value of 1 means unique under the chosen rules, not independently validated biological certainty. Label it accordingly before adding a matching review UI.

## Proposed implementation sequence

Effort labels are relative scope estimates, not delivery commitments.

| Milestone | Work | Completion criteria | Effort |
|---|---|---|---|
| **0. Correctness and a reproducible demo** | Fix current-state filtering and API errors; strengthen consistency checks; distinguish fixtures/live/saved reports; dataset-aware examples; pin a supported Python version and lock dependencies; one command to start the complete stack and seed fixtures | Fresh checkout starts without manual data surgery; regression cases above pass; all existing tests pass; dashboard clearly identifies data and report origins | Medium |
| **1. Neuron explorer and time travel** | Search by ID/type/region; neuron detail panel; incoming/outgoing partners; bounded graph; release selector and separate “known as of” control; source/provenance drawer; shareable URL state | A user can follow search -> neuron -> connections -> history -> source row without writing SQL; graph remains bounded and usable on real releases | Medium–large |
| **2. ConnectomeDiff workspace** | Dataset/release selectors; persisted job/result selection; added/removed/modified summaries; filters and sortable event table; before/after graph; annotation timeline; change explanations; CSV/JSON export | One release comparison can be explored from summary to an individual event and its provenance; added/removed states are accessible without relying only on color | Large |
| **3. Explainable anomaly analysis** | First implement a threshold baseline, then Isolation Forest; persist feature definitions, training scope, seed, model version, and scores in the existing ML tables; add ranked review UI | Each flagged change shows the features/reason, its source, and its baseline comparison; evaluation uses held-out or reviewed examples; no unsupported biological anomaly claims | Medium–large |
| **4. Reliable shard and long-job lifecycle** | Complete deployment generations, coherent snapshot reads, stronger content verification, staged shard copies, explicit activation, router refresh, reconnect/failure reporting; background jobs for costly operations | Concurrent load/deploy/query scenarios are tested; stale or incomplete copies are not silently served; recovered successful deployments work; users see progress and actionable failures | Large |
| **5. Evaluated cross-dataset matching** | Separate correspondence types; improve candidates with region/connectivity fingerprints; build evidence/approval queue; compare to the current type-and-side baseline; add embeddings/GraphSAGE when justified by evaluation or the course rubric | Held-out top-k retrieval and reviewed-match results are reported, leakage is controlled, ambiguous cases remain unresolved, and scores are clearly defined | Research / large |

### The most valuable next increment

Complete Milestone 0, then deliver a focused slice of Milestones 1 and 2:

1. Select hemibrain v1.0 and v1.1.
2. View the change summary and filter the strongest weight changes.
3. Click one event to see the neuron's before/after connections.
4. Move the release selector and inspect its annotation history.
5. Open the exact source file, row number, checksum, and transformation behind the event.
6. Optionally run the same supported neighbourhood query on shards and compare its answer with the coordinator.

This exposes the existing database work through one coherent demonstration. Keep the DBMS concepts, catalog, consoles, and integrity tests available as an evidence area.

## Implementation choices

- Continue with PostgreSQL, MongoDB, FastAPI, Alembic, and the current SQL functions. Add a separate graph database only if a measured workload requires it.
- A frontend framework is optional for capability. Modularize the current JavaScript as the workspace grows; if React is an explicit course requirement, include that migration in Milestone 1 and preserve API behavior.
- Bound graph expansion by hops, nodes, edges, and minimum weight. Aggregate large graphs by region/type and fetch detail on demand. Do not send millions of edges to the browser.
- Use background jobs when real workloads justify them, particularly diff/model/deployment work. Keep small read queries direct. Define job progress, cancellation boundaries, retry behavior, and result reuse before selecting infrastructure.
- Add CI with the actual PostgreSQL/MongoDB services and synthetic fixtures, plus a small browser workflow check. New tests should target the observed semantic failures, concurrency, deployment recovery, and report/data identity.
- Keep same-machine benchmark claims scoped to placement/routing and local resource contention. Add repeated p50/p95 measurements, concurrent clients, larger result sizes, and controlled failure scenarios before making broader scalability claims.

## Local review setup

Generated local setup is limited to the ignored `.venv/`, `.env`, `frontend/node_modules/`, and `.dbdata/review/` paths. The review databases contain synthetic fixtures. Running process information is in `.dbdata/review/review-processes.txt`; the active API log is `api-demo.stderr.log`.

The only added review deliverable outside those ignored paths is this document. Existing application code, committed benchmarks, and committed test results were preserved.
