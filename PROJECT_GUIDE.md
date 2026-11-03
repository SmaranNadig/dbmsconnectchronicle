# ConnectomeChronicle — concise project guide

## What this project is

ConnectomeChronicle stores and explores successive releases of fruit-fly brain wiring reconstructions. A connectome is a directed graph: neurons are nodes; a connection's weight is its synapse count. The system answers **what changed, when the database knew it, and which source records support it**.

The public adapters support hemibrain and FlyWire. A separate, clearly labelled synthetic demo lets the complete workflow run without downloading biological data. Synthetic scores and timings are demonstrations, not scientific findings.

## How the parts fit together

```text
Published releases → checksummed files → source mappings → staging + validation
                                                        ↓ atomic transaction
                                               PostgreSQL coordinator
                                             /          |            \
                                 history + provenance   diffs      partition placement
                                             |          |            ↓
Original source rows → MongoDB ← neuron key   |     quality analysis   3 verified shards
                                             \          |            /
                                              FastAPI + durable jobs
                                                       ↓
                                                Express → browser
```

**PostgreSQL is authoritative.** Keys, checks, composite foreign keys, temporal exclusion constraints and history triggers enforce integrity. SQL functions reconstruct releases, trace lineage and calculate differences. Views expose the original ER model; a materialized view holds release counts.

**MongoDB preserves heterogeneous source rows.** Each neuron document contains release-specific records, including columns that differ between sources. PostgreSQL stores independent record counts and SHA-256 content digests. The consistency check distinguishes reference consistency from verified content; old syncs require re-syncing to establish digests.

**Shards are derived read copies.** Hash, anatomical and Louvain-community strategies assign neuron owners. Cross-shard edges are mirrored so incoming and outgoing one-hop queries can use one shard. A deployment reads one exported coordinator snapshot, builds new shard schemas, and verifies the full binary content of release, neuron and connection tables, including mirrors. Only a complete verified generation is activated. Changed source data or placement makes the old generation stale and requires rebuilding.

## Methodology

1. **Acquire:** use the dataset catalog, retain immutable raw files, and record checksums and source URLs.
2. **Integrate:** adapters and YAML mappings translate source fields. Bulk COPY fills temporary staging tables; invalid IDs, duplicates, weights and references are rejected.
3. **Version:** one atomic load merges facts. Unchanged facts span multiple releases; changed facts create new versions. Ingestion attempts and failures are recorded separately. A dataset lease serializes concurrent ingestion attempts.
4. **Compare:** SQL compares two release states, producing added, removed and modified neuron, annotation and connection events. Weight deltas and source-record references are persisted.
5. **Prioritize:** a threshold baseline and Isolation Forest score connection changes. Correspondence candidates use type/side rules, then optional connectivity and region-profile ranking. Human review remains explicit.
6. **Inspect:** the API exposes these records; the browser links graphs, timelines, explanations and provenance. Expensive operations run as jobs with persisted status and results.

**Two clocks:** release order is valid time; transaction time records when the database believed a fact. The explorer applies both consistently. Region aggregates and source documents are release-derived; arbitrary transaction-time audit stays on the coordinator, rather than the shards.

## What each tab does

| Tab | Purpose |
|---|---|
| **Overview** | Live dataset/release counts, storage and architecture. A persistent badge identifies synthetic, public or mixed data. |
| **Neuron explorer** | Search by exact source ID, cell type or region at a selected release and knowledge time. Explore a bounded one-/two-hop graph, incoming/outgoing connections, release timeline and source evidence. Links retain neuron, release and knowledge-time state. |
| **Compare releases** | Create or reuse a comparison; filter and page through changes. Select an event for its explanation, source provenance and aligned before/after neighbourhoods. Export filtered CSV/JSON, capped at 10,000 rows. |
| **Change quality** | Score a comparison, select a saved model run, compare model/baseline flags, read features and explanations, and open the underlying change. |
| **Correspondences** | Inspect type-and-side candidates, rank up to 5,000 candidates per UI run using connectivity/region similarity, and approve/reject with a reviewer name. Scores are heuristic, not calibrated probabilities. |
| **Database lab → Query console** | Read-only SQL/MongoDB queries, dataset-aware teaching examples and PostgreSQL EXPLAIN/EXPLAIN ANALYZE. |
| **Database lab → DBMS concepts** | Live evidence for relational design, integrity, ACID transactions, time travel, indexing, views, lineage, NoSQL and distribution. Includes nine deliberately rejected writes. |
| **Database lab → SQL implementation** | Migration/application source plus PostgreSQL's live tables, constraints, indexes, triggers and functions. |
| **Database lab → NoSQL implementation** | Document structure, indexes, sync logs and a neuron shown in both stores. An empty collection is handled explicitly. |
| **Database lab → Distributed** | Placement trade-offs, generation freshness, rebuild jobs and routed queries compared with coordinator answers. |
| **Database lab → Results** | Timestamped test results and saved benchmarks; benchmark revision identity distinguishes historical reports from current data. |
| **How it works** | The methodology, interpretation limits, tab guide and recent job status. |

## Explainability and evaluation

Change explanations are deterministic statements about recorded before/after facts and provenance. No Groq key or other external AI API is required.

Isolation Forest uses six features: logarithms of old weight, new weight and absolute delta; relative delta; and added/removed indicators. It fits a deterministic reservoir of at most 20,000 changes with seed 42, then scores all changes in batches. Model parameters, feature schema, artifact checksum and per-event features/explanations are stored. Fewer than eight changed connections triggers an explicit threshold-only fallback.

The threshold flags absolute deltas of at least five synapses and relative deltas of at least 50%. Model/baseline agreement is **not accuracy**. Without reviewed labels, the app makes no accuracy claim. Feature explanations are observations, not causal model attributions.

Correspondence ranking compares typed incoming/outgoing partner profiles and region synapse profiles with weighted cosine similarity. Any available reviewed-match comparison is retrospective and explicitly labelled. Learned embeddings/GraphSAGE and held-out biological evaluation remain research extensions; they are not claimed as implemented.

FlyWire root IDs can change with proofreading. Apparent additions/removals can therefore be identifier churn. Across different specimens, matches represent homologous cells/types, not the same individual biological neuron. A release change or unusual score does not establish biological change.

## Run and verify

Prerequisites: **Python 3.12**, uv, Node.js 18+ and a running Docker Desktop. On Windows, from the project root:

```powershell
powershell -File scripts/start-demo.ps1
```

This installs locked dependencies, starts the five database services, creates separate `connectome_demo` / `connectome_meta_demo` databases, generates synthetic releases, syncs evidence, verifies all placements, and seeds an example comparison/model. The generator refuses database names without a `_demo` or `_review` suffix. It does not replace public data. Open **http://127.0.0.1:3000**; API docs are at **http://127.0.0.1:8000/docs**.

For an existing/public installation, install `requirements.lock`, run `alembic upgrade head`, and follow README's public-data ingestion steps. After upgrading, re-sync metadata and rebuild shard placements to establish the new integrity evidence.

```powershell
.venv\Scripts\python.exe -m pytest
node --check frontend/public/workspace.js
```

Tests use separate, disposable test databases. CI additionally installs Chromium and runs the browser smoke checks (`npm --prefix frontend run test:browser`) against a seeded demo. Local logs/process IDs from the launcher are under `.dbdata/demo/`.

Use **one API process per database** for the local two-worker job runner. Queued jobs can be cancelled; running transactions finish atomically. Interrupted running jobs are marked failed on restart; queued jobs resume. Old shard generations are retained so existing readers can finish; long-lived installations need an administrative retention policy.

Services bind to localhost by default. Set `CC_REVIEW_TOKEN` before sharing the API; reviews, matching jobs and deployment jobs then require `X-Review-Token`. This is a small-service access gate, not a multi-user identity provider. The token entered in the browser is kept only in page memory.

Same-machine benchmarks describe the configured local workload and routing/placement costs. They do not establish multi-host scalability or scientific validity. The original public-data reports remain available alongside newly generated synthetic reports.
