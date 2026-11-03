# Testing

```bash
python -m pytest          # 74 tests, ~10-60 s; report in results/tests/junit.xml
```

The suite drops and recreates separate test databases — `connectome_test` on
the coordinator and on each shard, and `connectome_meta_test` in MongoDB —
applies all Alembic migrations, and loads the synthetic fixtures in
`tests/fixtures/toy` (hand-written, labelled, never biological). The real
`connectome` / `connectome_meta` databases are not touched (conftest refuses
to drop a MongoDB database with another name).

## Test matrix

| area | test file | what is checked (exact expected values) |
|---|---|---|
| migrations | all (session fixture) | `alembic upgrade head` succeeds on an empty database |
| ingestion | `test_temporal_store.py` | per-release neuron/connection counts; re-ingest returns `already_loaded` with no new rows |
| delta storage | `test_unchanged_facts_are_stored_once` | a fact unchanged in r1..r3 is one row valid from r1, open-ended |
| lossless ids | `test_source_ids_preserved_losslessly` | 64-bit id `720575940596125868` round-trips as text |
| valid-time reconstruction | `test_reconstruct_neuron_per_release`, `..._connections_per_release` | removal and return of neuron 4, B→B2 retype, 1→2 weights 5/8/5, zero weights |
| transaction-time | `test_transaction_time_as_of` | what the DB believed right after r1 vs now |
| append-only history | `test_history_is_append_only` | UPDATE of a fact and DELETE are rejected by trigger |
| exclusion constraint | `test_bitemporal_exclusion_constraint` | overlapping current versions rejected |
| CHECK / FK | `test_check_constraints`, `test_edges_cannot_cross_datasets` | negative synapse count; edge endpoints from another dataset |
| failed ingestion | `test_bad_release_rolls_back` | orphan edge / duplicate pair → run `failed`, zero rows left, release `failed` |
| skip policy | `test_unknown_neuron_rows_skipped_and_reported` | declared skip drops exactly the unknown-id row and reports its line |
| crash recovery | `test_interrupted_run_is_closed_on_retry` | stale `running` run closed as `interrupted` |
| provenance | `test_provenance_trace` | file, line, release, dataset, transformation, SHA-256 |
| ConnectomeDiff | `test_connectome_diff.py` | the full event set r1→r2; stats; weight delta; equal values in different versions are not changes; idempotency for equal `as_of`; events trace to provenance; temporal diff across datasets rejected |
| entity resolution | `test_entity_resolution.py` | identity for every neuron (idempotent); candidates with confidence 1.0 / 0.5; unresolved with reasons; no id-based matching; approval merges identities; one approved mapping per neuron; unresolved ⇔ NULL target |
| MongoDB source records | `test_metadata_regions_er.py` | one document per neuron with every original column; empty cells left out; no entry for releases a neuron is absent from; idempotent re-sync recorded in `metadata_sync`; cross-store check passes, and detects a deleted document; unloaded release refused; rows of skipped neurons reported; API returns the records |
| brain regions | `test_metadata_regions_er.py` | exact per-neuron pre/post synapses per region; source codes recorded as aliases (incl. `a'L(R)` → `MB_VL_R`); primary region; unknown region code fails the release and keeps earlier rows; reload replaces |
| ER-diagram views | `test_metadata_regions_er.py` | `er.connection` between canonical neurons with valid_from/valid_to releases; `er.annotation` retype B→B2 with file:line source; `er.dataset_neuron`; `er.canonical_neuron`; `er.record_provenance` row number |
| distribution | `test_distributed.py` | LPT packing; every neuron exactly one owner; anatomical keeps a region on one shard; deployments verified and complete, mirrors = cut rows; **every routed answer (neighbours in/out, two-hop, region, summary, every neuron and release) equals the coordinator's** for all three strategies; 1-hop touches 1 shard, summary 3; executions logged |
| REST API | `test_api.py` | health/schema revision, releases, neuron states, connections, diff round-trip, 404/422 handling |
| query consoles | `test_console.py` | SQL console runs as `cc_reader` read-only; refuses DELETE, multi-statement `COMMIT` escape, writable CTE, `set_config`, `pg_read_file`; timeout enforced; EXPLAIN; every published example passes the read-only gate; MongoDB count/aggregate and refusal of `$where`/`$out`/`$merge`; all 9 integrity-demo writes rejected with nothing written; catalog and source whitelist (no `.env`, no `../`) |

## Real-data checks

```bash
python -m backend.cli status
docker exec -i cc_coordinator psql -U chronicle -d connectome < scripts/demo_queries.sql
```

## Snapshot round trip

`scripts/snapshot.py import` checks itself: it refuses a file whose SHA-256
differs from the manifest, and after restoring it compares the exact row count
of every `cc` table, the MongoDB document count and the schema revision with
the exporting machine, stopping with a list of differences on any mismatch.
`--with-shards` then re-deploys each strategy, and `deploy` verifies every
shard row by row against the coordinator.

Trial on the real data: export took 86 s (206 MB: 184 MB PostgreSQL dump, 22 MB MongoDB archive); the import trial result will be added here.

## Benchmarks

```bash
python -m backend.cli bench            # --neurons 25 --repeat 3 by default
```

Runs the workload against the coordinator and all three deployed
strategies. The first pass compares every routed answer with the
coordinator's (the run aborts on any mismatch) and serves as warm-up; then
`repeat` shuffled passes are timed. Each execution is logged in
`cc.query_execution`; the summary (p50/p95, shards touched, rows) and the
environment go to `results/benchmarks/bench_<timestamp>.json`.
