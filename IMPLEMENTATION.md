# Implementation status

## Data editor: writing to the database from the dashboard (2026-11-09)

Requested by the evaluator: run any SQL including writes, see the tables, and
add, change and delete records, for SQL and NoSQL. Design, options considered
and acceptance criteria: [DATA_EDITOR_PLAN.md](DATA_EDITOR_PLAN.md).

- Migration 0012: role `cc_tryout` (NOLOGIN; reads and writes `cc`, used only
  inside a rolled-back transaction) and login role `cc_lab` (owns schema `lab`,
  may only read `cc` / `er`; password derived from the administrator's).
- `backend/db/lab_schema.sql` + `backend/lab.py`: schema `lab` rebuilt from the
  real data (dataset, release, neuron, connection with PK / UNIQUE / CHECK /
  composite FKs, `ON DELETE CASCADE`, view `neuron_degree`, audit table
  `change_log` filled by a trigger) and MongoDB database `<mongo_db>_lab` with
  the same neurons' documents, the real store's unique indexes and a
  `$jsonSchema` validator. On the real data: 360 neurons, 9,023 connections
  over 5 releases, 360 documents; rebuilt in about 4-9 s.
- Query console targets: read-only (unchanged), trial (one write on the real
  tables, rolled back) and lab (scripts, DDL, transactions, saved). MongoDB:
  real store read-only; lab allows insert / update / replace / delete,
  indexes and collections.
- Data editor page: browse lab / cc / er tables, insert / edit / delete lab
  rows and documents with the executed statement shown, change log, reset.
- Database connections now use 127.0.0.1 instead of `localhost`, which on
  Windows cost about 2 s per new connection (IPv6 tried first).

Validation: **114 integration tests passing** (27 new in `test_lab.py`), and the
editor and console were exercised in the browser on the real data.

## Interactive workspace extension (2026-11-03)

Implemented and exercised locally with synthetic data:

- Release-aware neuron search, bounded interactive graphs, time travel, source evidence.
- Visual comparisons, deterministic explanations, filtered pagination and CSV/JSON exports.
- Threshold baseline and Isolation Forest, streamed scoring, reproducibility records and model evidence.
- Connectivity/region correspondence ranking and manual review UI with an optional reviewer token.
- Independent metadata content digests and corruption detection.
- Staged, fully hashed shard generations, atomic activation, stale-copy refusal and recovery.
- Persistent two-worker jobs, locked dependencies, a synthetic demo launcher and CI checks.

Validation: **87 integration tests passing**; fresh synthetic benchmark **222 routed
answers, zero mismatches**. Manual browser workflows were exercised. A Chromium
smoke suite is included in CI. Public datasets were not re-ingested for this extension.

Learned embeddings/GraphSAGE, held-out biological matching evaluation and labelled
anomaly accuracy remain research work. Multi-host/high-availability operation is
not established by the local benchmark. See [PROJECT_GUIDE.md](PROJECT_GUIDE.md).

The original real-data implementation record follows; its numerical results are historical.

Last updated 2026-11-03. "Done" means implemented **and** exercised by a
passing test or a run on the real data; nothing here is claimed from code alone.

## SRS traceability

| SRS requirement | Implemented by | Status |
|---|---|---|
| **1 Ingestion & schema integration** | | |
| source-specific ETL adapters | `backend/etl/adapters.py` (Hemibrain, FlyWire; MaleCNS/MANC templates) | done |
| preserve original ids, files, records | ids stored as text; `source_file` (SHA-256, archive member); `source_line(s)` on every fact; raw files untouched | done |
| normalize into canonical model | schema mapping registry (`config/schema_mappings`, `cc.schema_mapping`); `dataset_neuron`, `annotation`, `connection`, `neuron_type`, `brain_region` + `brain_region_alias` (hemibrain ROI and FlyWire neuropil codes → one vocabulary) | done |
| add datasets without redesign | generic mapping-driven adapter; tests add 5 fixture datasets with config only | done |
| **2 Cross-dataset entity resolution** | | |
| source ↔ canonical correspondences | `canonical_neuron`, `neuron_mapping`, `assign_source_identity()` | done |
| graph/connectivity/metadata features | type + side crosswalk (`propose_crosswalk_mappings()`) | partial — connectivity features come with the embedding matcher |
| embeddings / GraphSAGE ranking | — | **not started** |
| store method, confidence, evidence, timestamp | `neuron_mapping` columns + CHECKs | done |
| keep ambiguous / unresolved | `review_status = 'unresolved'`, `canonical_neuron_id NULL`, reason in evidence; `review_mapping()` | done |
| **3 Temporal & version-aware** | | |
| multiple releases without overwrite | `release`, append-only trigger `guard_history` | done (hemibrain v1.0/1.1/1.2 loaded) |
| valid-time & transaction-time | `valid_rel` + `tx` on `neuron_version`, `annotation`, `connection`; GiST exclusion constraints | done |
| reconstruct state at a release / time | `neuron_at_release()`, `connections_at_release()`, `neuron_as_of()` | done |
| distinguish valid vs recorded period | `annotation_history()`, `connection_history` view | done |
| **4 Provenance & lineage** | | |
| provenance for source and derived records | `provenance`, `provenance_parent`, `ingestion_run` | done |
| dataset, release, file, record, row, transformation, pipeline version, timestamp | `trace_provenance()` | done |
| mappings, annotations, changes traceable | mappings carry `provenance_id`; diff events carry record ids → `trace_provenance` | done |
| **5 ConnectomeDiff** | | |
| added / deleted / modified neurons, connections, annotations | `run_temporal_diff()` → `diff_run`, `diff_event` | done |
| canonical representation, linked to releases & provenance | events reference dataset neurons and versioned rows | done |
| weight / synapse-count changes | `old_weight`, `new_weight`, generated `weight_delta` | done |
| Isolation Forest over change features | `ml_model_run`, `anomaly_result` tables exist | **not started** |
| **6 Distributed storage & query** | | |
| PostgreSQL shards via Docker Compose | `docker-compose.yml`: coordinator + 3 shards; `backend/distributed/deploy.py` streams each strategy to schema `p_<strategy>` on every shard, verified against the coordinator | done (all 3 strategies deployed and verified on real data) |
| ID / region / community partitioning | `backend/distributed/partition.py`: hash, anatomical (primary region from `neuron_region_synapses`), community (Louvain, igraph); LPT packing; `partition_quality()` | done |
| query router, cross-shard queries | `backend/distributed/router.py`: neighbours, two-hop, region, summary; parallel scatter-gather; every routed answer checked equal to the coordinator | done |
| latency / cross-shard / balance / join benchmarks | `backend/distributed/bench.py` → `query_execution`, `results/benchmarks/*.json` | done (see below) |
| **7 API & visualization** | | |
| REST endpoints | `backend/main.py` (health, datasets, releases, neurons incl. MongoDB source records, connections, history, provenance, mappings + review, diffs + events) | done (incl. overview, regions, distribution, benchmarks, test results; anomalies pending) |
| dashboard | `frontend/` (npm + Express, plain HTML/JS, live data from the API): organised by DBMS concept, with read-only SQL and MongoDB query consoles, a live integrity test, the SQL/NoSQL source and system catalog, and a routed shard query | done as an HTML dashboard; a React version is not built |
| **8 Integrity, consistency, reproducibility** | | |
| PK, FK, unique, domain, referential constraints | `0001_core_schema.sql` | done, tested |
| transactions & indexing | 3-transaction ingest with full rollback; btree/GiST/partial indexes | done, rollback demonstrated on real data |
| outputs tied to dataset/pipeline/model versions | `ingestion_run.pipeline_version`, `provenance.transformation_version`, `diff_run.as_of`, `ml_model_run` | done for ETL and diff |
| PostgreSQL authoritative, MongoDB for heterogeneous metadata | `backend/metadata.py`: one document per source neuron with all original rows per release; `cc.metadata_sync`; cross-store `metadata check`; API merges the document into `/neurons/{id}` | done (182,274 documents, consistent) |
| copying a loaded system to another machine | `scripts/snapshot.py`: pg_dump + mongodump into `snapshot/` with SHA-256 checksums and exact per-table row counts; import verifies checksums, restores, re-grants `cc_reader`, compares every count, re-deploys and verifies the shards | done; trial restore into scratch databases (see TESTING.md) |

| **Team ER diagram** | | |
| every entity / attribute / relationship of the diagram | schema `er` views (`0007_er_views.sql`), mapping in ARCHITECTURE.md | done |

## Real data loaded

| dataset | release | neurons | connections (neuron pairs) | load time |
|---|---|---|---|---|
| hemibrain | v1.0 | 21,663 | 3,413,160 | 8 m 42 s |
| hemibrain | v1.1 | 21,733 | 3,521,163 | 7 m 31 s |
| hemibrain | v1.2 | 21,739 | 3,550,403 | 2 m 43 s |
| flywire | 630 | 127,978 | 2,613,129 | 3 m 31 s |
| flywire | 783 | 139,255 | 2,700,513 | 4 m 33 s |

Delta storage: 9,008,996 connection rows are stored for all five releases,
versus 15,798,368 if every release were a full snapshot. The database is
3.96 GB (connection: 1.1 GB data + 1.3 GB indexes, mostly the GiST
exclusion index).

## ConnectomeDiff on real releases

| comparison | neurons +/− | annotations +/−/modified | connections +/−/modified | time |
|---|---|---|---|---|
| hemibrain v1.0 → v1.1 | 157 / 87 | 1,357 / 195 / 30,044 | 126,885 / 18,882 / 153,910 | 12.8 s |
| hemibrain v1.1 → v1.2 | 6 / 0 | 19 / 3 / 109 | 30,722 / 1,482 / 74,795 | 8.3 s |
| FlyWire 630 → 783 | 32,470 / 21,193 | 426,422 / 133,238 / 71,768 | 1,213,830 / 1,126,446 / 3,525 | 60.0 s |

The FlyWire comparison is dominated by root-id churn (edited neurons get new
ids); it is not evidence of biological change.

## Entity resolution on real data (FlyWire 783 → hemibrain v1.2)

- 182,274 source identities (21,826 hemibrain, 160,448 FlyWire ids over 630 and 783)
- 9,959 canonical types from hemibrain; 3,642 FlyWire `hemibrain_type` aliases;
  437 FlyWire type tokens with no hemibrain type
- 14,212 proposed candidates (2,071 unique one-to-one, confidence 1.0); none auto-approved
- 23,295 unresolved: 17,465 no hemibrain neuron of that type and side
  (hemibrain covers part of one hemisphere), 5,830 too many candidates

## MongoDB source records (real data)

182,274 documents (hemibrain 21,826, FlyWire 160,448) holding 877,183 original
rows; `metadata check` reports both datasets consistent. FlyWire 630's row for
the unknown root id is reported, not written.

## Distribution on real data (current state of all 5 releases, 7,629,956 rows)

| strategy | rows per shard (share) | edge cut | storage vs one copy | on shards |
|---|---|---|---|---|
| hash | 33.6 / 32.9 / 33.6 % | 66.7 % | 1.67x | 1.57 GB |
| anatomical | 33.3 / 33.0 / 33.7 % | 28.0 % | 1.28x | 1.21 GB |
| community | 33.4 / 32.7 / 33.9 % | 27.1 % | 1.27x | 1.22 GB |

Louvain (igraph, seed 42): hemibrain v1.2 8 communities (modularity 0.66),
FlyWire 783 265 communities (modularity 0.71). Neurons absent from the
latest release (26,354 for community, 5,623 without region data for
anatomical) fall back to the hash owner. All deployments verified.

Benchmark `results/benchmarks/bench_20261103_171907.json` (25 neurons per
dataset, 6 regions, latest release of each dataset, 1 warm-up/correctness
pass + 3 timed passes; one laptop, 4 PostgreSQL containers in one Docker
Desktop VM). 492 routed answers compared with the coordinator: 0 mismatches.

| query | coordinator p50 | hash p50 (shards) | anatomical p50 (shards) | community p50 (shards) |
|---|---|---|---|---|
| neighbours out | 1.02 ms | 0.75 ms (1) | 0.83 ms (1) | 1.05 ms (1) |
| neighbours in | 1.46 ms | 1.10 ms (1) | 1.10 ms (1) | 1.21 ms (1) |
| two-hop | 5.42 ms | 5.82 ms (2.88) | 6.34 ms (2.28) | 6.74 ms (2.20) |
| region (~151k rows) | 308 ms | 868 ms (3) | 661 ms (1) | 968 ms (3) |
| release summary | 3,603 ms | 317 ms (3) | 316 ms (3) | 291 ms (3) |

Reading: the global summary is ~11x faster on three shards scanning in
parallel; 1-hop lookups are single-shard for every strategy (mirrors);
anatomical needs one shard for a region query while hash and community need
three, and grouped strategies touch fewer shards in two-hop queries. Large
region results are slower than the coordinator because ~151k rows are moved
to the router in Python; all shards share one machine, so this measures
routing and placement, not network effects.

## Notable events (all real)

- First hemibrain load failed on a driver error; the run is recorded as
  failed and **no partial rows remained** — rollback verified.
- FlyWire 630 `classification.csv` contains root id `720575940609340228`,
  which is not in its `neurons.csv`. Validation rejected the load; the mapping
  now declares `unknown_neuron_policy: skip` for that file, and the dropped
  row is listed in the run's statistics.
- Docker Desktop stopped during a FlyWire load; the load transaction was
  rolled back by PostgreSQL. Stale `running` runs are now closed as
  `interrupted` when the release is retried.

## Design decisions / deviations from the synopsis

- `DATASET_NEURON` holds one identity per (dataset, original id) across
  releases; existence per release is versioned in `neuron_version`, so an
  unchanged neuron is not duplicated per release (synopsis ties it to one release).
- Valid time is measured in release order; calendar dates stay NULL unless an
  official release date is confirmed.
- Releases of a dataset must be loaded in order (enforced).
- FlyWire connections are stored as pair totals (sum over neuropils) with all
  source lines kept.
- Cross-dataset matching is type-level first; no neuron is linked because ids
  look alike, and nothing is auto-approved.

## Next steps

1. Isolation Forest over `diff_event` features vs threshold baseline.
2. Embedding / GraphSAGE matcher evaluated against Schlegel et al. reference.
3. Anomaly endpoints and dashboard section once Isolation Forest exists.
