# Detailed design

This document describes ConnectomeChronicle end to end: what it must do,
how it is built, and why each design choice was made. The companion
documents go deeper on one aspect each:

| Document | Content |
|---|---|
| [DATA_FLOW.md](DATA_FLOW.md) | Data flow diagrams (context, level 1, level 2), checked against the notation rules |
| [NORMALISATION.md](NORMALISATION.md) | Functional dependencies, keys and normal form of every relation, verified on the loaded data; anomalies; file maintenance |
| [QUERIES.md](QUERIES.md) | The SQL and MongoDB query catalogue, run on the loaded data with plans and results |
| [DATA_DICTIONARY.md](DATA_DICTIONARY.md) | Every table, column, constraint, index, view, function and collection, read from the catalog |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | ER model, team ER diagram mapping, storage details |

Blocks marked *generated* are produced from the running system by
`python scripts/design_docs.py design` and cannot drift from the code.

## 1. Purpose and requirements

Public fruit-fly connectomes (hemibrain v1.0, v1.1, v1.2; FlyWire 630, 783)
are published as successive releases of very large graphs. Each release
replaces the previous one; nothing records what changed or where a fact
came from. ConnectomeChronicle is a database system that:

1. **Stores every release without duplication** and can reconstruct any
   release exactly (valid time), and what the database believed at any past
   moment (transaction time).
2. **Records provenance**: every neuron, annotation and connection points to
   the run, file, line(s) and transformation that produced it.
3. **Computes differences between releases** of one dataset (ConnectomeDiff),
   repeatably and set-based in SQL.
4. **Keeps cross-dataset comparison separate from temporal evolution**:
   hemibrain and FlyWire are different animals, so correspondences between
   them are proposed, scored and reviewed, never reported as change over time.
5. **Preserves heterogeneous source records** whose columns differ per
   dataset and release (MongoDB).
6. **Distributes the graph** over several database instances and compares
   placement strategies by their edge cut and query cost.
7. **Exposes it all** through a REST API and a browser dashboard, with safe
   query consoles and an editable copy for experimenting with writes.

Non-functional requirements: integrity is enforced by the database, not by
application code (keys, foreign keys, checks, exclusion constraints,
triggers); loads are atomic per release; read-only interfaces cannot write;
every result shown is computed from real data, never pasted.

## 2. Architecture

```
          publishers (neuPrint, FlyWire Codex)
                     │ release files (checksummed)
                     ▼
  ┌────────────── backend/etl ───────────────┐
  │ adapters + YAML schema mappings          │
  │ COPY → TEMP staging → SQL validation     │
  │ bitemporal merge (one transaction)       │
  └───────┬───────────────────────┬──────────┘
          ▼                       ▼
  PostgreSQL coordinator      MongoDB
  schema cc  (system of record)  connectome_meta.neuron_source_record
  schema er  (ER diagram as views)   (original rows, all columns)
  schema lab (editable copy)
          │ partition_assignment + binary COPY, verified
          ▼
  3 PostgreSQL shards (p_hash, p_anatomical, p_community)
          ▲ router (backend/distributed/router.py)
          │
  FastAPI (backend/main.py, workspace.py, jobs.py, lab.py, console.py)
          │ JSON
          ▼
  Express static server → browser dashboard (frontend/public)
```

Runtime: Docker Compose runs PostgreSQL 16 (coordinator, port 55432; shards,
55433 to 55435) and MongoDB 7 (57017). Schema changes are Alembic
migrations, each a reviewed SQL file in `backend/db/sql/`.

### Modules (generated)

<!-- generated:modules -->
| Module | Responsibility |
|---|---|
| `backend/analysis.py` | Reproducible anomaly features and evidence-based correspondence ranking. |
| `backend/cli.py` | ConnectomeChronicle command line. |
| `backend/config.py` | Runtime configuration loaded from environment variables / .env. |
| `backend/console.py` | Read-only query consoles for the dashboard: SQL on the coordinator and find/aggregate on the MongoDB metadata store. |
| `backend/design/api.py` | Design endpoints (/api/v1/design): data flow diagrams, normal forms, anomalies, file maintenance, the query catalogue and the data dictionary. |
| `backend/design/dfd.py` | Data flow diagrams: rule checker and SVG renderer (docs/DATA_FLOW.md). |
| `backend/design/dictionary.py` | Data dictionary, read from the database catalog (never written by hand). |
| `backend/design/fd.py` | Functional-dependency algebra for the normalisation analysis. |
| `backend/design/maintenance.py` | File maintenance and updation (docs/NORMALISATION.md §Maintenance). |
| `backend/design/normalisation.py` | Normal form of every relation, computed from its declared dependencies and checked against the live database (docs/NORMALISATION.md). |
| `backend/design/queries.py` | Run the query catalogue (config/query_catalogue.yaml) on the real data. |
| `backend/design/relations.py` | Declared attributes and functional dependencies of every stored relation. |
| `backend/distributed/bench.py` | Benchmark the three deployed strategies against the single coordinator. |
| `backend/distributed/deploy.py` | Copy the coordinator's current state onto the three shard instances according to one strategy's partition_assignment. |
| `backend/distributed/partition.py` | Compute which shard owns each source neuron, for one partitioning strategy. |
| `backend/distributed/router.py` | Query router over the shard instances for one deployed strategy. |
| `backend/etl/adapters.py` | Source adapters: read a release's files as published and emit normalized frames that the loader copies into staging tables. |
| `backend/etl/loader.py` | Load one release into the bitemporal store. |
| `backend/etl/regions.py` | Per-release brain-region synapse counts (cc.neuron_region_synapses). |
| `backend/etl/sources.py` | Locate and open source files of a downloaded release without modifying them. |
| `backend/jobs.py` | Durable job status with two local workers. Run one API process per database. |
| `backend/lab.py` | The writable side of the dashboard (DATA_EDITOR_PLAN.md). |
| `backend/main.py` | REST API (FastAPI). Thin layer: every endpoint calls a parameterized SQL query or a cc.* SQL function, so the database stays the system of record. |
| `backend/metadata.py` | MongoDB source records: the heterogeneous, source-specific side of a neuron. |
| `backend/revisions.py` | Cheap revision keys: temporal rows are append-only and source loads are versioned. |
| `backend/showcase.py` | Dashboard endpoints' back end that show the DBMS at work: the live system catalog, the integrity rules rejecting real bad writes, the project source (SQL and NoSQL), the MongoDB store, and one routed query on the shards compared with the coordinator. |
| `backend/workspace.py` | Release-aware reads and bounded visual exploration of the authoritative store. |
<!-- /generated:modules -->

## 3. Database design

### 3.1 Conceptual design

The entities and relationships are those of the team ER diagram; the
diagram itself is in [ARCHITECTURE.md](../ARCHITECTURE.md#er-model) and is
available attribute for attribute as views in schema `er`. Summary:

- A **dataset** has ordered **releases**; a release is read from
  checksummed **source files** by **ingestion runs**.
- A **dataset neuron** is a neuron as one dataset identifies it; its
  existence, **annotations** (cell type, side, ...) and outgoing
  **connections** (synapse counts) are facts that hold over a range of
  releases.
- Every fact is produced under a **provenance** record (run, file,
  transformation), which may derive from other provenance records.
- A **canonical neuron** is the project's own identity; **neuron
  mappings** link dataset neurons to it with method, confidence and review
  status.
- A **diff run** compares two releases of one dataset and contains **diff
  events**; ML **model runs** score those events.
- A **partition strategy** assigns each neuron to a **shard**; **shard
  deployments** record verified copies.

### 3.2 Logical design

The relational schema is derived from the ER model with these rules:
strong entities become relations with a surrogate key plus the natural
key as `UNIQUE` (for example `release(dataset_id, version_label)`); 1:N
relationships become foreign keys on the N side; M:N relationships become
relations whose key is both foreign keys (`provenance_parent`,
`ml_model_input`, `neuron_region_synapses`); multivalued attributes become
relations (`brain_region_alias`, `type_alias`, `ml_model_input`);
versioned facts get two interval columns (`valid_rel`, `tx`).

Every relation is analysed in [NORMALISATION.md](NORMALISATION.md): its
functional dependencies, candidate keys and highest normal form are
computed (closure, minimal cover), and each dependency is checked against
the loaded data. Redundancy kept on purpose (for example `dataset_id` on
`connection`, which lets composite foreign keys reject an edge between two
datasets) is listed there with the constraint that keeps the copy
consistent.

### 3.3 Relations (generated)

<!-- generated:inventory -->
| Group | Relation | Rows | Normal form | Purpose |
|---|---|---|---|---|
| Sources | `cc.dataset` | 2 | BCNF | A public connectome (hemibrain, FlyWire). |
| Sources | `cc.release` | 5 | BCNF | One published version of a dataset, ordered within the dataset. |
| Sources | `cc.ingestion_run` | 9 | BCNF | One attempt to load a release. |
| Sources | `cc.source_file` | 17 | BCNF | A checksummed file (or archive member) a release was read from. |
| Sources | `cc.provenance` | 25 | 2NF | How a set of facts was produced: run, source file, transformation. |
| Sources | `cc.provenance_parent` | 0 | BCNF | Lineage: which provenance records a derived record was computed from. |
| Sources | `cc.schema_mapping` | 27 | BCNF | How a source field maps to a canonical field. |
| Vocabulary | `cc.neuropil` | 45 | BCNF | A canonical neuropil code and the super region it belongs to (split from brain_region, 0013). |
| Vocabulary | `cc.brain_region` | 82 | BCNF | A neuropil in one hemisphere (NULL = unpaired). |
| Vocabulary | `cc.brain_region_alias` | 141 | BCNF | A dataset's own region code mapped to the canonical region. |
| Vocabulary | `cc.neuron_type` | 9,959 | BCNF | A canonical cell type name. |
| Vocabulary | `cc.type_alias` | 13,601 | BCNF | A dataset's type name linked to a canonical type (many-to-many). |
| Connectome | `cc.dataset_neuron` | 182,274 | 3NF | A neuron as identified by its dataset (original id kept as text). |
| Connectome | `cc.neuron_version` | 203,554 | BCNF | Existence of a neuron over a range of releases (bitemporal). |
| Connectome | `cc.annotation` | 1,625,410 | BCNF | One attribute value (cell type, side, ...) of a neuron over a range of releases. |
| Connectome | `cc.connection` | 9,001,488 | 2NF | Synapse count between two neurons over a range of releases (bitemporal). |
| Connectome | `cc.neuron_region_synapses` | 1,083,797 | 1NF | Per release, how many synapses a neuron has in each brain region. |
| Identity | `cc.canonical_neuron` | 182,274 | BCNF | A neuron identity across datasets. |
| Identity | `cc.neuron_mapping` | 219,781 | BCNF | A proposed or reviewed link from a dataset neuron to a canonical neuron. |
| ConnectomeDiff | `cc.diff_run` | 5 | 2NF | One comparison of two release states at a transaction time. |
| ConnectomeDiff | `cc.diff_event` | 3,468,914 | 2NF | One change found by a diff run (added, removed, modified). |
| Distribution | `cc.shard` | 3 | BCNF | A PostgreSQL server holding a fragment of the connectome. |
| Distribution | `cc.partition_strategy` | 3 | BCNF | A way of placing neurons on shards (hash, anatomical, community). |
| Distribution | `cc.partition_assignment` | 546,822 | 2NF | The shard (and group) of every neuron under every strategy. |
| Distribution | `cc.query_execution` | 2,644 | BCNF | A routed query's measured cost. |
| Distribution | `cc.shard_deployment` | 18 | 2NF | One shard's copy of one strategy's placement. |
| Distribution | `cc.deployment_batch` | 3 | BCNF | One generation of shard copies for a strategy. |
| Distribution | `cc.active_deployment` | 3 | BCNF | The batch the router currently reads, per strategy. |
| Analysis | `cc.ml_model_run` | 0 | BCNF | One trained change-scoring model. |
| Analysis | `cc.ml_model_input` | 0 | BCNF | The diff runs a model was trained on (was the array ml_model_run.diff_run_ids, 0013). |
| Analysis | `cc.anomaly_result` | 0 | BCNF | A model's score for one diff event. |
| Document store | `cc.metadata_sync` | 5 | BCNF | One synchronisation of a release's source records into MongoDB. |
| Document store | `cc.metadata_record_digest` | 0 | BCNF | Expected count and checksum of a neuron's source records per release. |
| Workspace | `cc.workspace_job` | 1 | BCNF | A background job (diff, scoring, matching, deployment). |
| Lab | `lab.dataset` | 2 | BCNF | Editable copy of cc.dataset (key columns). |
| Lab | `lab.release` | 5 | BCNF | Editable copy of the loaded releases. |
| Lab | `lab.neuron` | 360 | BCNF | Editable neurons with their current cell type, side and primary region. |
| Lab | `lab.connection` | 9,023 | BCNF | Synapse count of a neuron pair in one release (BCNF: no dataset_id copy). |
| Lab | `lab.change_log` | 0 | BCNF | Audit trail written by trigger for every lab change. |

Row counts above 100,000 are the planner's estimate. Totals (2026-11-23 13:13 UTC):39 tables, 268 columns, 65 foreign keys, 89 checks, 4 exclusion constraints, 85 indexes, 10 triggers, 8 views, 17 SQL functions.
<!-- /generated:inventory -->

### 3.4 Physical design

- **Indexes** follow the queries in [QUERIES.md](QUERIES.md); each
  catalogue query states the index it must use and the check fails if the
  plan stops using it. GiST indexes serve the range columns (`valid_rel`,
  `tx`) and the exclusion constraints; B-tree indexes serve keys and
  lookups (`connection (pre_neuron_id)`, `(post_neuron_id)`).
- **Delta storage**: an unchanged fact keeps one row across releases.
  Across the five releases this stores 9,008,996 connection rows instead
  of 15,798,368 (one copy per release).
- **Materialized view** `release_stats` holds per-release counts, which
  would otherwise need a full scan per request.
- **Bulk load** uses COPY into TEMP staging tables, then set-based merge
  statements; there are no row-by-row inserts.
- File-level storage (relation files, sizes, dead tuples, vacuum and
  analyze times) is shown in [NORMALISATION.md](NORMALISATION.md#file-maintenance),
  and on the dashboard, where the lab tables can be vacuumed, analysed and
  reindexed.

### 3.5 Migrations (generated)

<!-- generated:migrations -->
| Revision | What it adds |
|---|---|
| `0001` | core relational schema: sources, provenance, bitemporal facts, mappings, diff, partitions, ML |
| `0002` | temporal and provenance query functions and views |
| `0003` | ConnectomeDiff: set-based temporal diff function and detail view |
| `0004` | entity resolution: source identity, type crosswalk, candidate mappings, review |
| `0005` | metadata_sync: record of MongoDB source-record synchronisation per release |
| `0006` | brain regions: shared vocabulary and per-release neuron region synapse counts |
| `0007` | er schema: the ER diagram as views over the physical tables |
| `0008` | distribution: shards, strategies, deployments, partition quality |
| `0009` | release_stats: materialized per-release counts |
| `0010` | cc_reader: read-only role for the SQL console |
| `0011` | Interactive workspace, model evidence, metadata integrity and deployments. |
| `0012` | cc_tryout and cc_lab: write targets of the dashboard |
| `0013` | Normalisation: decompose brain_region and ml_model_run.diff_run_ids, enforce dependencies. |
| `0014` | Table and column descriptions (COMMENT ON), read by the data dictionary. |
<!-- /generated:migrations -->

## 4. Temporal design

Every versioned row (`neuron_version`, `annotation`, `connection`) carries:

- `valid_rel int4range`: the release orders in which the fact holds. An
  open upper bound means it still holds in the newest release.
- `tx tstzrange`: when the database held this belief. An open upper bound
  means it is current.

Loading release *N* retires the open rows not restated identically in *N*
(their `tx` is closed and a copy valid up to *N* is inserted), then inserts
the new facts valid from *N*. Nothing is overwritten or deleted: the trigger
`guard_history` allows only closing `tx`. GiST exclusion constraints
guarantee that two versions of one fact never overlap in both times.
Queries choose a release (`valid_rel @> order`), a moment (`tx @> ts`) or
both, through SQL functions such as `neuron_at_release`,
`connections_at_release` and `neuron_as_of`.

## 5. Integrity

| Mechanism | Examples |
|---|---|
| Primary keys and natural-key `UNIQUE` | every table; `release(dataset_id, version_label)`, `neuropil(canonical_code)` |
| Composite foreign keys | `connection(pre_neuron_id, dataset_id)` → `dataset_neuron`: an edge cannot join two datasets |
| CHECK constraints | non-negative synapse counts, confidence in [0, 1], status vocabularies, non-empty intervals, `unresolved ⇔ canonical_neuron_id IS NULL`, temporal diff only within one dataset |
| Exclusion constraints | no overlapping versions in valid × transaction time; a region group on only one shard per strategy |
| Partial unique index | at most one approved mapping per source neuron |
| Triggers | `guard_history` (append-only history); lab: same-dataset constraint triggers and the audit trigger writing `lab.change_log` |
| Cross-store check | `metadata check` compares MongoDB document ids, counts and SHA-256 digests with PostgreSQL |

The dashboard's **DBMS concepts** page runs writes that each of these
mechanisms rejects, and shows the database's error.

## 6. Document store (MongoDB)

`connectome_meta.neuron_source_record` holds one document per source neuron,
`_id = dataset_neuron.metadata_doc_id`. For every synced release it keeps
every original row about the neuron, with all its columns, including those
the relational model does not map. The shape differs per dataset, which is
why these rows are documents and not columns. PostgreSQL records each sync
(`metadata_sync`) with independent counts and digests; since no foreign key
can span two stores, `metadata check` verifies the references both ways.
Indexes and their use are in [QUERIES.md](QUERIES.md) (category *MongoDB*).
The lab copy (`connectome_meta_lab`) adds a `$jsonSchema` validator, so a
document written from the Data editor must have the required fields.

## 7. Distributed design

Three shard instances hold read copies. A neuron is owned by one shard; an
edge is stored on the owner of its pre-synaptic neuron and mirrored on the
owner of its post-synaptic neuron, so every one-hop query reads one shard.
Strategies: hash, anatomical (regions packed by edge load) and community
(Louvain communities packed by edge load). A deployment is built from one
coordinator snapshot, verified row for row, and only then activated
(`active_deployment`). The router sends a query only to shards that can
answer it and logs each execution (`query_execution`).

## 8. Security and access

| Role | Can | Used by |
|---|---|---|
| application owner | everything in `cc` | loader, API, migrations |
| `cc_reader` | `SELECT` and function `EXECUTE` on `cc`, `er`; run in a `READ ONLY` transaction with a statement timeout | SQL console (read) |
| `cc_tryout` | DML on `cc`, always in a transaction that is rolled back | SQL console (trial) |
| `cc_lab` | owns schema `lab`; read access to `cc`; statement timeout 10 s | Data editor, lab console |

Writes that change real data (review decisions, jobs) require a review
token when `CC_REVIEW_TOKEN` is set. `set_config` is revoked from `PUBLIC`
so a console session cannot lift its own limits.

## 9. Transactions and concurrency

- Ingestion uses three transactions: register (commit); load the whole
  release (commit or roll back completely); record a failure (commit).
- Concurrent loads of one dataset are serialised with `SELECT … FOR UPDATE`
  on the dataset row.
- A diff run reads both releases at one transaction time (`as_of`), so
  repeating it returns the same events.
- Background jobs (`workspace_job`) are claimed by an atomic
  `UPDATE … WHERE status = 'queued' RETURNING`, so two workers never run the
  same job; identical submissions are serialised with a transaction-level
  advisory lock and reuse the queued job. Status and results are durable.

## 10. Processes and data flow

The processes, stores and flows are drawn in [DATA_FLOW.md](DATA_FLOW.md):
the context diagram (level 0), the eight processes of level 1 and the
decompositions of *Load release* (2.1 to 2.6) and *Compare releases*
(4.1 to 4.5). Each data store of the diagrams lists the tables it holds, and
the diagrams are checked automatically (labels, numbering, balancing, no
black holes or miracles).

## 11. Interfaces

### 11.1 REST API (generated)

<!-- generated:api -->
61 endpoints (interactive OpenAPI at `/docs`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/benchmarks/latest` | latest benchmark |
| GET | `/api/v1/catalog` | system catalog |
| GET | `/api/v1/datasets` | datasets |
| POST | `/api/v1/demo/integrity` | Send deliberately invalid writes; each is rejected by a constraint or trigger and rolled back. |
| POST | `/api/v1/design/anomalies` | Update, insertion and deletion anomalies of an unnormalised copy of the lab connections, next to the normalised tables (rolled back). |
| GET | `/api/v1/design/dfd` | Data flow diagrams (context, level 1, level 2) and the notation check. |
| GET | `/api/v1/design/dfd/{diagram_id}.svg` | One diagram drawn in Gane–Sarson notation. |
| GET | `/api/v1/design/dictionary` | Tables, columns, constraints, indexes, triggers, views, functions and MongoDB collections, read from the catalogs. |
| GET | `/api/v1/design/files` | Storage file, size, live and dead tuples and last vacuum/analyze of every table. |
| POST | `/api/v1/design/maintenance/{operation}` | VACUUM, ANALYZE or REINDEX the lab tables; returns statistics before and after. |
| GET | `/api/v1/design/normalisation` | Keys, dependencies and normal form of every relation, the UNF→BCNF walkthrough, and the last verification against the loaded data. |
| POST | `/api/v1/design/normalisation/verify` | Check every key and dependency against the live database now. |
| GET | `/api/v1/design/queries` | The query catalogue with the results and plans of its last full run. |
| POST | `/api/v1/design/queries/{query_id}/run` | Run one catalogue query now: result, plan and checks. |
| POST | `/api/v1/diffs` | create diff |
| GET | `/api/v1/diffs` | list diffs |
| GET | `/api/v1/diffs/{diff_id}` | get diff |
| GET | `/api/v1/diffs/{diff_id}/events` | diff events |
| POST | `/api/v1/distributed/query` | distributed query |
| GET | `/api/v1/distribution` | distribution |
| GET | `/api/v1/health` | health |
| GET | `/api/v1/lab/changes` | lab changes |
| GET | `/api/v1/lab/mongo/collections` | lab mongo collections |
| POST | `/api/v1/lab/reset` | Drop and rebuild schema lab and the MongoDB lab from the real data. |
| GET | `/api/v1/lab/status` | lab status |
| GET | `/api/v1/lab/tables` | lab tables |
| POST | `/api/v1/lab/tables/lab/{table}/{action}` | Insert, update or delete one row of a lab table; returns the SQL that ran. |
| GET | `/api/v1/lab/tables/{schema}/{table}` | lab rows |
| GET | `/api/v1/mappings` | mappings |
| GET | `/api/v1/mappings/summary` | mappings summary |
| POST | `/api/v1/mappings/{mapping_id}/review` | review |
| GET | `/api/v1/mongo/overview` | mongo overview |
| GET | `/api/v1/neurons` | Existence, filter and returned type share the same release and knowledge time. |
| GET | `/api/v1/neurons/{neuron_id}` | neuron |
| GET | `/api/v1/neurons/{neuron_id}/connections` | neuron connections |
| GET | `/api/v1/neurons/{neuron_id}/history` | neuron history |
| GET | `/api/v1/neurons/{neuron_id}/provenance` | neuron provenance |
| GET | `/api/v1/neurons/{neuron_id}/regions` | neuron regions |
| GET | `/api/v1/overview` | Everything the dashboard's first page shows, from the database itself. |
| GET | `/api/v1/provenance/{table}/{record_id}` | provenance |
| POST | `/api/v1/query/mongo` | query mongo |
| POST | `/api/v1/query/sql` | Run SQL as cc_reader, cc_tryout or cc_lab (see backend/console.py, backend/lab.py). |
| GET | `/api/v1/regions` | Brain regions of one release ranked by synapses (each synapse counted once, on its pre side). |
| GET | `/api/v1/releases` | releases |
| GET | `/api/v1/source` | source files |
| GET | `/api/v1/source/file` | source file |
| GET | `/api/v1/tests/latest` | Summary of the last `pytest --junitxml=results/tests/junit.xml` run. |
| GET | `/api/v1/workspace/context` | context |
| GET | `/api/v1/workspace/diffs/{diff_id}/events` | events |
| GET | `/api/v1/workspace/diffs/{diff_id}/export` | export |
| GET | `/api/v1/workspace/events/{event_id}` | event detail |
| GET | `/api/v1/workspace/examples` | Adapt teaching examples to real identifiers from the connected database. |
| POST | `/api/v1/workspace/jobs` | submit |
| GET | `/api/v1/workspace/jobs` | list jobs |
| GET | `/api/v1/workspace/jobs/{job_id}` | get job |
| POST | `/api/v1/workspace/jobs/{job_id}/cancel` | cancel job |
| GET | `/api/v1/workspace/matching` | matching |
| GET | `/api/v1/workspace/metadata/{dataset}/check` | metadata check |
| GET | `/api/v1/workspace/models` | models |
| GET | `/api/v1/workspace/models/{model_id}/results` | model results |
| GET | `/api/v1/workspace/neurons/{neuron_id}/graph` | graph |
<!-- /generated:api -->

### 11.2 Dashboard

| Section | What it shows |
|---|---|
| Overview | Datasets, releases, storage, architecture |
| Neuron explorer | A neuron at a chosen release and knowledge time: graph, connections, timeline, source evidence |
| Compare releases | ConnectomeDiff runs and events, explanations, export |
| Change quality | Anomaly scoring of diff events, model versus baseline |
| Correspondences | Cross-dataset candidates and review |
| Database lab → Query console | Read-only and trial SQL, MongoDB queries, EXPLAIN |
| Database lab → Data editor | Edit the lab copy; every change audited |
| Database lab → Design | Data flow diagrams, normal forms, anomalies, file maintenance, query catalogue, data dictionary |
| Database lab → DBMS concepts | Live evidence of integrity, transactions, time travel, indexing |
| Database lab → SQL / NoSQL / Distributed | Implementation source, catalog, documents, shards |
| Database lab → Results | Test results and benchmarks |

## 12. Testing

Tests run against a separate database (`connectome_test`) loaded with small
synthetic fixtures, never against the real data. Unit tests cover the
dependency algebra and the diagram rules; integration tests cover the API,
loads, diffs, entity resolution, MongoDB, distribution, the consoles and the
lab.

<!-- generated:tests -->
| File | Tests | What it covers |
|---|---|---|
| `tests/integration/test_api.py` | 7 | REST API against the test database (toyfly fixture). |
| `tests/integration/test_connectome_diff.py` | 6 | ConnectomeDiff correctness on the synthetic toyfly fixture. |
| `tests/integration/test_console.py` | 9 | Read-only query consoles, integrity demo, catalog and source endpoints (test database with the toyfly + toyskip fixtures). |
| `tests/integration/test_design.py` | 9 | Design: the relation catalogue matches the live schema, every key and functional dependency holds on loaded data, the descriptions and the data dictionary come from the catalog, the query catalogue runs, and the design endpoints (diagrams, anomalies, file maintenance). Test database with the toyfly + toyskip fixtures. |
| `tests/integration/test_distributed.py` | 6 | Partitioning, deployment to the shard instances, and routed queries, on the fixtures in the test databases (connectome_test on coordinator and shards). |
| `tests/integration/test_entity_resolution.py` | 7 | Entity resolution on synthetic stand-ins for hemibrain and FlyWire (tests/fixtures/toy/raw/{hemibrain,flywire}/t1). |
| `tests/integration/test_lab.py` | 17 | Data editor: the editable lab (schema lab + MongoDB lab), the trial target on the real tables, and the access rules that keep the real data unchanged (DATA_EDITOR_PLAN.md). Test database with the toyfly + toyskip fixtures. |
| `tests/integration/test_metadata_regions_er.py` | 17 | MongoDB source records, brain-region synapse counts and the ER-diagram views, on the toyfly fixture (tests/fixtures/toy/raw/toyfly). |
| `tests/integration/test_temporal_store.py` | 15 | Ingestion, integrity, bitemporal reconstruction and provenance on the synthetic toyfly fixture (tests/fixtures/toy). |
| `tests/integration/test_workspace.py` | 12 | Regression and workflow checks for the interactive workspace on synthetic data. |
| `tests/unit/test_dfd.py` | 3 | Data flow diagram rules (backend/design/dfd.py): the project's own model in config/dfd.yaml satisfies every rule, and a model broken in one way is reported under the right rule. |
| `tests/unit/test_fd.py` | 10 | Functional dependency algebra (backend/design/fd.py) on textbook cases: closure, candidate keys, minimal cover, normal-form tests, BCNF decomposition, the lossless-join (chase) test and dependency preservation. |

118 test functions.
<!-- /generated:tests -->
