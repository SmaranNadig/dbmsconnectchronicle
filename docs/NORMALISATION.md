# Normalisation

This document gives the normal form of every relation stored in the
database, how the relations were reached from the published source files,
and how the database keeps them correct when rows are inserted, updated and
deleted.

Nothing here is asserted by hand. Each relation's attributes and functional
dependencies (FDs) are declared once, in
[`backend/design/relations.py`](../backend/design/relations.py). From them
[`backend/design/fd.py`](../backend/design/fd.py) computes:
- attribute closures;
- candidate keys and prime attributes;
- the 2NF, 3NF and BCNF tests;
- a minimal cover;
- a BCNF decomposition;
- the chase test for lossless joins;
- the dependency-preservation test.

[`backend/design/normalisation.py`](../backend/design/normalisation.py) then
checks each declared design against the running database:
- the catalogue covers every base table, with exactly its live columns;
- every candidate key is enforced by a `PRIMARY KEY` or `UNIQUE` constraint;
- every constraint named as protecting a dependency exists;
- every dependency whose left side is not already unique holds on the loaded
  rows.

That last check groups by the left side and counts distinct right sides.
Zero groups must have more than one:

```sql
SELECT count(*) FROM (SELECT 1 FROM cc.connection
                      WHERE pre_neuron_id IS NOT NULL
                      GROUP BY pre_neuron_id
                      HAVING count(DISTINCT dataset_id) > 1 ...) bad;   -- 0
```

The tables and results below are written by `python scripts/design_docs.py
normalisation`. The dashboard's **Design → Normalisation** page shows the same
results and can re-run the checks.

## Definitions used

- **1NF**: every attribute holds one value of its domain. There are no
  repeating groups and no lists of references to other rows.
- **2NF**: 1NF, and no non-prime attribute depends on a proper part of a
  candidate key (no partial dependency).
- **3NF**: for every non-trivial FD `X → A`, either `X` is a superkey or `A`
  is prime (no transitive dependency).
- **BCNF**: for every non-trivial FD `X → A`, `X` is a superkey.

A *prime* attribute belongs to some candidate key. Testing the FDs of a
minimal cover is sufficient for 3NF and BCNF. 2NF is tested against every
proper subset of every candidate key.

## Result for every relation

<!-- generated:summary -->
53 relations (53 base tables in the database, all catalogued with matching columns: yes). Highest normal form: 1NF 2, 2NF 6, 3NF 1, BCNF 44. Every dependency checked on the loaded data: all hold (23.5 s, 2026-11-24 16:27 UTC).

| Relation | Candidate keys | Highest NF | FDs hold | Notes |
|---|---|---|---|---|
| `cc.memory_snapshot` | (digest); (snapshot_id) | BCNF | ✓ |  |
| `cc.memory_edge` | (connection_id, snapshot_id) | BCNF | ✓ |  |
| `cc.memory_run` | (run_id); (seed, snapshot_id, version) | BCNF | ✓ | non-scalar: channels |
| `cc.memory_stimulus` | (odor, run_id) | BCNF | ✓ | non-scalar: activity |
| `cc.memory_schedule` | (run_id, step) | BCNF | ✓ |  |
| `cc.memory_branch` | (branch_id); (fork_step, intervention, parent_id, run_id) | BCNF | ✓ |  |
| `cc.memory_trial` | (branch_id, step) | BCNF | ✓ |  |
| `cc.memory_change` | (branch_id, connection_id, step) | BCNF | ✓ |  |
| `cc.memory_checkpoint` | (branch_id, step) | BCNF | ✓ |  |
| `cc.circuit_snapshot` | (digest); (snapshot_id) | BCNF | ✓ |  |
| `cc.circuit_node` | (neuron_id, snapshot_id) | BCNF | ✓ |  |
| `cc.circuit_edge` | (connection_id, snapshot_id); (post_id, pre_id, snapshot_id) | 1NF | ✓ | controlled redundancy: connection_id → weight |
| `cc.circuit_run` | (run_id); (model_version, seed, snapshot_id) | BCNF | ✓ |  |
| `cc.circuit_trial` | (condition, run_id, trial) | BCNF | ✓ | non-scalar: replay |
| `cc.dataset` | (code); (dataset_id) | BCNF | ✓ | non-scalar: data_formats |
| `cc.release` | (release_id); (dataset_id, release_order); (dataset_id, version_label) | BCNF | ✓ |  |
| `cc.ingestion_run` | (run_id) | BCNF | ✓ | non-scalar: stats |
| `cc.source_file` | (source_file_id); (archive_path, member_path, release_id) | BCNF | ✓ |  |
| `cc.provenance` | (provenance_id) | 2NF | ✓ | controlled redundancy: ingestion_run_id → release_id; release_id → dataset_id; source_file_id → release_id; non-scalar: parameters |
| `cc.provenance_parent` | (parent_id, provenance_id) | BCNF | ✓ |  |
| `cc.schema_mapping` | (schema_mapping_id); (canonical_entity, canonical_field, dataset_id, source_field, source_file_pattern) | BCNF | ✓ |  |
| `cc.neuropil` | (canonical_code) | BCNF | ✓ |  |
| `cc.brain_region` | (brain_region_id); (canonical_code, hemisphere) | BCNF | ✓ |  |
| `cc.brain_region_alias` | (dataset_id, source_code) | BCNF | ✓ |  |
| `cc.neuron_type` | (name); (neuron_type_id) | BCNF | ✓ |  |
| `cc.type_alias` | (dataset_id, neuron_type_id, source_type) | BCNF | ✓ |  |
| `cc.dataset_neuron` | (dataset_neuron_id); (metadata_doc_id); (dataset_id, source_neuron_id); (first_release_id, source_neuron_id) | 3NF | ✓ | controlled redundancy: first_release_id → dataset_id |
| `cc.neuron_version` | (neuron_version_id) | BCNF | ✓ | non-scalar: valid_rel, tx; temporal key |
| `cc.annotation` | (annotation_id) | BCNF | ✓ | non-scalar: valid_rel, tx; temporal key |
| `cc.connection` | (connection_id) | 2NF | ✓ | controlled redundancy: post_neuron_id → dataset_id; pre_neuron_id → dataset_id; non-scalar: valid_rel, tx, source_lines; temporal key |
| `cc.neuron_region_synapses` | (brain_region_id, dataset_neuron_id, release_id) | 1NF | ✓ | controlled redundancy: dataset_neuron_id → dataset_id; release_id → dataset_id |
| `cc.canonical_neuron` | (canonical_neuron_id) | BCNF | ✓ |  |
| `cc.neuron_mapping` | (mapping_id) | BCNF | ✓ | non-scalar: evidence, score_details |
| `cc.diff_run` | (diff_run_id) | 2NF | ✓ | controlled redundancy: release_a → dataset_a; release_b → dataset_b; non-scalar: parameters, stats |
| `cc.diff_event` | (diff_event_id) | 2NF | ✓ | controlled redundancy: new_weight, old_weight → weight_delta |
| `cc.shard` | (name); (shard_id) | BCNF | ✓ |  |
| `cc.partition_strategy` | (code); (strategy_id) | BCNF | ✓ | non-scalar: parameters |
| `cc.partition_assignment` | (dataset_neuron_id, strategy_id) | 2NF | ✓ | controlled redundancy: group_key, strategy_id → shard_id |
| `cc.query_execution` | (query_execution_id) | BCNF | ✓ | non-scalar: parameters |
| `cc.shard_deployment` | (deployment_id) | 2NF | ✓ | controlled redundancy: batch_id → strategy_id |
| `cc.deployment_batch` | (batch_id) | BCNF | ✓ | non-scalar: details |
| `cc.active_deployment` | (batch_id); (strategy_id) | BCNF | ✓ |  |
| `cc.ml_model_run` | (model_run_id); (model_kind, model_version) | BCNF | ✓ | non-scalar: parameters, feature_schema, metrics |
| `cc.ml_model_input` | (diff_run_id, model_run_id) | BCNF | ✓ |  |
| `cc.anomaly_result` | (diff_event_id, model_run_id) | BCNF | ✓ | non-scalar: features |
| `cc.metadata_sync` | (metadata_sync_id) | BCNF | ✓ | non-scalar: stats |
| `cc.metadata_record_digest` | (dataset_neuron_id, release_id) | BCNF | ✓ |  |
| `cc.workspace_job` | (job_id) | BCNF | ✓ | non-scalar: request, result |
| `lab.dataset` | (code); (dataset_id) | BCNF | ✓ |  |
| `lab.release` | (release_id); (dataset_id, release_order); (dataset_id, version_label) | BCNF | ✓ |  |
| `lab.neuron` | (neuron_id); (dataset_id, source_neuron_id) | BCNF | ✓ |  |
| `lab.connection` | (post_neuron_id, pre_neuron_id, release_id) | BCNF | ✓ |  |
| `lab.change_log` | (change_id) | BCNF | ✓ | non-scalar: old_row, new_row |
<!-- /generated:summary -->

### What the analysis changed in the schema (migration 0013)

The first run of the analysis found three problems. Migration 0013 fixes
them; it does not just document them.

1. **`brain_region` was in 1NF only.** `canonical_code → super_region` is a
   partial dependency on the candidate key `(canonical_code, hemisphere)`, so
   the super region was stored once per hemisphere. The relation is now
   decomposed:
   - `neuropil(canonical_code, super_region)`;
   - `brain_region(brain_region_id, canonical_code, hemisphere, name)`.

   The view `brain_region_label` returns the same columns as before, so no
   query changed.
2. **`ml_model_run.diff_run_ids bigint[]` broke 1NF.** It was a list of
   references to `diff_run` inside one column, so no foreign key could check
   it. It is now the relation
   `ml_model_input(model_run_id, diff_run_id)`, whose foreign keys check each
   reference.
3. **Four dependencies held in the data but were not enforced:**
   - `provenance`: `source_file_id → release_id` and
     `ingestion_run_id → release_id`;
   - `shard_deployment` and `active_deployment`: `batch_id → strategy_id`;
   - `partition_assignment`: `(strategy_id, group_key) → shard_id`.

   Also, `dataset_neuron.metadata_doc_id` was a candidate key without a
   unique constraint. Each is now a constraint:
   - composite foreign keys;
   - an exclusion constraint `(strategy_id =, group_key =, shard_id <>)`,
     which states the FD directly;
   - a `UNIQUE` constraint.

### 1NF: non-scalar columns

Ranges, arrays and `jsonb` were each checked against the 1NF rule.

- **Ranges are single values.** `valid_rel` (`int4range`) and `tx`
  (`tstzrange`) are each one interval, compared, indexed and constrained as
  a whole. The GiST exclusion constraints use them as scalars.
- **One array was decomposed.** `ml_model_run.diff_run_ids` was a list of
  references, a repeating group, so it is now the relation `ml_model_input`
  (see above).
- **Two arrays are kept as values:**
  - `connection.source_lines` holds the line numbers of the source rows
    summed into one edge. It is evidence, written once and read whole, and
    no query or constraint uses an element of it.
  - `dataset.data_formats` describes the dataset as a whole.

  Splitting `source_lines` out would add 11 million rows and no
  dependency.
- **`jsonb` columns hold opaque documents:** run parameters, statistics, a
  matcher's evidence, a job's request. Each is stored and read as one value,
  and no key, foreign key or dependency refers to a field inside one. The
  semi-structured source records themselves live in MongoDB (see
  ARCHITECTURE.md).

## Controlled redundancy in the core schema

Eight relations keep one dependency that breaks 3NF or BCNF. In each case
the "redundant" attribute is a checked copy, either a composite foreign key
or a generated column, so the update anomaly that normalisation prevents
cannot happen. The copy is still stored because it is what lets the
database state a rule declaratively.

The main example is `connection.dataset_id`. The composite foreign keys
`(pre_neuron_id, dataset_id)` and `(post_neuron_id, dataset_id)` →
`dataset_neuron` mean that an edge between two different connectomes
cannot exist.

The BCNF decomposition of each relation is shown with its result. It is
always lossless, but for `connection`, `neuron_region_synapses`,
`provenance` and `dataset_neuron` it does **not preserve** a dependency.
After the split, the rule would need a trigger instead of a key. The
editable lab schema shows that alternative: `lab.connection` is in BCNF, and
the same rule is the constraint trigger `connection_same_dataset`.

<!-- generated:redundancy -->
#### `cc.circuit_edge` — 1NF

Keys: (connection_id, snapshot_id); (post_id, pre_id, snapshot_id). Minimal cover: `connection_id → post_id, pre_id, weight`; `post_id, pre_id, snapshot_id → connection_id`.

- `connection_id → post_id, pre_id, weight`: Freezing endpoints and weights supports replay of the exact model input.. Enforced by `circuit_edge_source`. Live check: 0 violating groups (8.5 ms).

BCNF decomposition: (pre_id, post_id, weight, connection_id) ⋈ (snapshot_id, connection_id) — lossless: yes; dependencies not preserved: `post_id, pre_id, snapshot_id → connection_id`.

#### `cc.provenance` — 2NF

Keys: (provenance_id). Minimal cover: `ingestion_run_id → release_id`; `provenance_id → created_at, ingestion_run_id, parameters, source_file_id, transformation_name, transformation_version`; `release_id → dataset_id`; `source_file_id → release_id`.

- `release_id → dataset_id`: The composite foreign key (release_id, dataset_id) → release makes the copy match its release; provenance is filtered by dataset without a join. Enforced by `provenance_release_id_dataset_id_fkey`. Live check: 0 violating groups (0.9 ms).
- `source_file_id → release_id`: Release_id is needed for derived facts that have no source file (source_file_id NULL); where a file is given, the composite key forces the same release. Enforced by `provenance_source_file_release_fkey`. Live check: 0 violating groups (0.5 ms).
- `ingestion_run_id → release_id`: Release_id is needed for facts that were not produced by an ingestion run; where a run is given, the composite key forces the same release. Enforced by `provenance_run_release_fkey`. Live check: 0 violating groups (0.6 ms).

BCNF decomposition: (provenance_id, source_file_id, ingestion_run_id, transformation_name, transformation_version, parameters, created_at) ⋈ (dataset_id, release_id) ⋈ (release_id, ingestion_run_id) — lossless: yes; dependencies not preserved: `source_file_id → release_id`.

#### `cc.dataset_neuron` — 3NF

Keys: (dataset_neuron_id); (metadata_doc_id); (dataset_id, source_neuron_id); (first_release_id, source_neuron_id). Minimal cover: `dataset_neuron_id → metadata_doc_id`; `first_release_id → dataset_id`; `metadata_doc_id → dataset_neuron_id, first_release_id, provenance_id, source_line, source_neuron_id`; `dataset_id, source_neuron_id → metadata_doc_id`.

- `first_release_id → dataset_id`: Dataset_id is part of the natural key (dataset_id, source_neuron_id), so the relation is in 3NF; the composite key to release keeps the first release inside the same dataset. Enforced by `dataset_neuron_first_release_id_dataset_id_fkey`. Live check: 0 violating groups (36.5 ms).

BCNF decomposition: (dataset_id, first_release_id) ⋈ (dataset_neuron_id, source_neuron_id, first_release_id, provenance_id, source_line, metadata_doc_id) — lossless: yes; dependencies not preserved: `dataset_id, source_neuron_id → metadata_doc_id`.

#### `cc.connection` — 2NF

Keys: (connection_id). Minimal cover: `connection_id → post_neuron_id, pre_neuron_id, provenance_id, source_lines, synapse_count, tx, valid_rel`; `post_neuron_id → dataset_id`; `pre_neuron_id → dataset_id`.

- `pre_neuron_id → dataset_id`: The composite foreign keys (pre, dataset) and (post, dataset) → dataset_neuron are what makes an edge between two different connectomes impossible; the copy also serves the partial index connection_dataset_open_idx that every per-dataset scan of 9 M rows uses. Enforced by `connection_pre_neuron_id_dataset_id_fkey`. Live check: 0 violating groups (9210.6 ms).
- `post_neuron_id → dataset_id`: The composite foreign key (post_neuron_id, dataset_id) → dataset_neuron keeps the post-synaptic neuron in the edge's dataset. Enforced by `connection_post_neuron_id_dataset_id_fkey`. Live check: 0 violating groups (11151.6 ms).

BCNF decomposition: (connection_id, pre_neuron_id, post_neuron_id, synapse_count, valid_rel, tx, provenance_id, source_lines) ⋈ (dataset_id, post_neuron_id) — lossless: yes; dependencies not preserved: `pre_neuron_id → dataset_id`.

#### `cc.neuron_region_synapses` — 1NF

Keys: (brain_region_id, dataset_neuron_id, release_id). Minimal cover: `dataset_neuron_id → dataset_id`; `release_id → dataset_id`; `brain_region_id, dataset_neuron_id, release_id → post_synapses, pre_synapses, provenance_id`.

- `release_id → dataset_id`: The two composite foreign keys force the release and the neuron into the same dataset. Enforced by `neuron_region_synapses_release_id_dataset_id_fkey`. Live check: 0 violating groups (251.0 ms).
- `dataset_neuron_id → dataset_id`: The composite foreign key (dataset_neuron_id, dataset_id) → dataset_neuron keeps the neuron in the release's dataset. Enforced by `neuron_region_synapses_dataset_neuron_id_dataset_id_fkey`. Live check: 0 violating groups (891.4 ms).

BCNF decomposition: (release_id, dataset_neuron_id, brain_region_id, pre_synapses, post_synapses, provenance_id) ⋈ (dataset_id, dataset_neuron_id) — lossless: yes; dependencies not preserved: `release_id → dataset_id`.

#### `cc.diff_run` — 2NF

Keys: (diff_run_id). Minimal cover: `diff_run_id → as_of, finished_at, mode, parameters, release_a, release_b, started_at, stats, status`; `release_a → dataset_a`; `release_b → dataset_b`.

- `release_a → dataset_a`: The CHECK constraints that keep temporal evolution (same dataset) apart from cross-dataset comparison need both dataset ids in the row; the composite key keeps them correct. Enforced by `diff_run_release_a_dataset_a_fkey`, `diff_run_check1`. Live check: 0 violating groups (1.2 ms).
- `release_b → dataset_b`: The composite foreign key (release_b, dataset_b) → release keeps the copy correct for the second release. Enforced by `diff_run_release_b_dataset_b_fkey`. Live check: 0 violating groups (0.7 ms).

BCNF decomposition: (diff_run_id, mode, release_a, release_b, as_of, parameters, status, started_at, finished_at, stats) ⋈ (dataset_a, release_a) ⋈ (dataset_b, release_b) — lossless: yes; dependencies not preserved: none.

#### `cc.diff_event` — 2NF

Keys: (diff_event_id). Minimal cover: `diff_event_id → annotation_key, change_type, dataset_neuron_id, diff_run_id, entity_type, new_value, new_weight, old_value, old_weight, post_neuron_id, record_a_id, record_b_id`; `new_weight, old_weight → weight_delta`.

- `new_weight, old_weight → weight_delta`: A generated column: PostgreSQL computes it and rejects any write, so it cannot disagree; stored so that events can be ranked by change size with an index. Enforced by `GENERATED ALWAYS AS (stored)`. Live check: 0 violating groups (1581.1 ms).

BCNF decomposition: (diff_event_id, diff_run_id, entity_type, change_type, dataset_neuron_id, post_neuron_id, annotation_key, old_value, new_value, old_weight, new_weight, record_a_id, record_b_id) ⋈ (old_weight, new_weight, weight_delta) — lossless: yes; dependencies not preserved: none.

#### `cc.partition_assignment` — 2NF

Keys: (dataset_neuron_id, strategy_id). Minimal cover: `dataset_neuron_id, strategy_id → assigned_at, group_key`; `group_key, strategy_id → shard_id`.

- `group_key, strategy_id → shard_id`: The router looks up one neuron's shard by key; hash placement has no group, so shard_id cannot move to a group table. The exclusion constraint (strategy =, group =, shard <>) states the dependency itself. Enforced by `partition_assignment_group_one_shard`. Live check: 0 violating groups (215.0 ms).

BCNF decomposition: (strategy_id, dataset_neuron_id, assigned_at, group_key) ⋈ (strategy_id, shard_id, group_key) — lossless: yes; dependencies not preserved: none.

#### `cc.shard_deployment` — 2NF

Keys: (deployment_id). Minimal cover: `batch_id → strategy_id`; `deployment_id → batch_id, bytes_on_shard, connections, deployed_at, mirrors, neurons, schema_name, seconds, shard_id, verified`.

- `batch_id → strategy_id`: Rows from before deployment batches existed have no batch (batch_id NULL) and still need their strategy; the composite key keeps newer rows consistent with their batch. Enforced by `shard_deployment_batch_strategy_fkey`. Live check: 0 violating groups (1.8 ms).

BCNF decomposition: (deployment_id, shard_id, schema_name, neurons, connections, mirrors, bytes_on_shard, seconds, deployed_at, verified, batch_id) ⋈ (strategy_id, batch_id) — lossless: yes; dependencies not preserved: none.
<!-- /generated:redundancy -->

## From the source files to the relations

A published release is, in effect, a list of neuron records. Each record
carries its dataset and release, its attributes, and a repeating group of
partners with synapse counts. The steps below normalise that record. The
keys and normal forms are computed by the same code as above.

<!-- generated:walkthrough -->
**UNF — A release as published: one record per neuron with repeating groups**

Real rows: hemibrain 487144598 in release v1.2, read from the loaded data (1,876 partners; the 3 strongest are shown). In 1NF every partner row repeats the neuron's facts, so they are stored 1,876 times for this neuron alone.

*Note:* side is NULL: hemibrain publishes no side column (the side only appears as the suffix of `instance`, e.g. LHCENT3_R), and nothing is invented here.

- `release_record`(dataset_code, dataset_name, version_label, release_order, source_neuron_id, cell_type, side, primary_region, super_region, {partners: post_neuron_id, synapse_count})
  - partners is a repeating group (a list inside the record): not a relation

| dataset_code | dataset_name | version_label | release_order | source_neuron_id | cell_type | side | primary_region | super_region | {partners: post_neuron_id, synapse_count} |
|---|---|---|---|---|---|---|---|---|---|
| hemibrain | Janelia FlyEM hemibrain | v1.2 | 3 | 487144598 | LHCENT3 | NULL | SMP_R | central | [328533761: 349; 579234980: 169; 5813021291: 168; … 1876 partners] |


**1NF — Flatten the repeating group: one row per (release, neuron, partner)**

- `connection_row`(dataset_code, dataset_name, version_label, release_order, source_neuron_id, cell_type, side, primary_region, super_region, post_neuron_id, synapse_count) — keys (dataset_code, post_neuron_id, release_order, source_neuron_id); (dataset_code, post_neuron_id, source_neuron_id, version_label), highest form **1NF**
  - breaks 2NF: `dataset_code → dataset_name` (partial dependency: dataset_name depend(s) on dataset_code, a proper part of the key (dataset_code, post_neuron_id, release_order, source_neuron_id))
  - breaks 2NF: `dataset_code, release_order, source_neuron_id → cell_type, primary_region, side, super_region` (partial dependency: cell_type, primary_region, side, super_region depend(s) on dataset_code, release_order, source_neuron_id, a proper part of the key (dataset_code, post_neuron_id, release_order, source_neuron_id))
  - breaks 2NF: `dataset_code, source_neuron_id, version_label → cell_type, primary_region, side, super_region` (partial dependency: cell_type, primary_region, side, super_region depend(s) on dataset_code, source_neuron_id, version_label, a proper part of the key (dataset_code, post_neuron_id, source_neuron_id, version_label))
  - breaks 3NF: `primary_region → super_region` (transitive dependency: primary_region is not a superkey and super_region is non-prime)
  - breaks BCNF: `dataset_code, release_order → version_label` (dataset_code, release_order is not a superkey (allowed in 3NF because version_label is prime))
  - breaks BCNF: `dataset_code, version_label → release_order` (dataset_code, version_label is not a superkey (allowed in 3NF because release_order is prime))

| dataset_code | dataset_name | version_label | release_order | source_neuron_id | cell_type | side | primary_region | super_region | post_neuron_id | synapse_count |
|---|---|---|---|---|---|---|---|---|---|---|
| hemibrain | Janelia FlyEM hemibrain | v1.2 | 3 | 487144598 | LHCENT3 | NULL | SMP_R | central | 328533761 | 349 |
| hemibrain | Janelia FlyEM hemibrain | v1.2 | 3 | 487144598 | LHCENT3 | NULL | SMP_R | central | 579234980 | 169 |
| hemibrain | Janelia FlyEM hemibrain | v1.2 | 3 | 487144598 | LHCENT3 | NULL | SMP_R | central | 5813021291 | 168 |


**2NF — Remove partial dependencies on the key (dataset, release, neuron, partner)**

- `dataset`(dataset_code, dataset_name) — keys (dataset_code), highest form **BCNF**

| dataset_code | dataset_name |
|---|---|
| hemibrain | Janelia FlyEM hemibrain |

- `release`(dataset_code, version_label, release_order) — keys (dataset_code, release_order); (dataset_code, version_label), highest form **BCNF**

| dataset_code | version_label | release_order |
|---|---|---|
| hemibrain | v1.2 | 3 |

- `neuron_in_release`(dataset_code, version_label, source_neuron_id, cell_type, side, primary_region, super_region) — keys (dataset_code, source_neuron_id, version_label), highest form **2NF**
  - breaks 3NF: `primary_region → super_region` (transitive dependency: primary_region is not a superkey and super_region is non-prime)

| dataset_code | version_label | source_neuron_id | cell_type | side | primary_region | super_region |
|---|---|---|---|---|---|---|
| hemibrain | v1.2 | 487144598 | LHCENT3 | NULL | SMP_R | central |

- `connection`(dataset_code, version_label, source_neuron_id, post_neuron_id, synapse_count) — keys (dataset_code, post_neuron_id, source_neuron_id, version_label), highest form **BCNF**

| dataset_code | version_label | source_neuron_id | post_neuron_id | synapse_count |
|---|---|---|---|---|
| hemibrain | v1.2 | 487144598 | 328533761 | 349 |
| hemibrain | v1.2 | 487144598 | 579234980 | 169 |
| hemibrain | v1.2 | 487144598 | 5813021291 | 168 |


**3NF — Remove the transitive dependency primary_region → super_region**

- `neuron_in_release`(dataset_code, version_label, source_neuron_id, cell_type, side, primary_region) — keys (dataset_code, source_neuron_id, version_label), highest form **BCNF**

| dataset_code | version_label | source_neuron_id | cell_type | side | primary_region |
|---|---|---|---|---|---|
| hemibrain | v1.2 | 487144598 | LHCENT3 | NULL | SMP_R |

- `neuropil`(primary_region, super_region) — keys (primary_region), highest form **BCNF**
  - this is the split made in the real schema by migration 0013 (cc.neuropil)

| primary_region | super_region |
|---|---|
| SMP_R | central |


**BCNF — Surrogate identity: a neuron is first seen in one release**

- `neuron_identity (3NF, not BCNF)`(dataset_code, source_neuron_id, first_release) — keys (dataset_code, source_neuron_id); (first_release, source_neuron_id), highest form **3NF**
  - breaks BCNF: `first_release → dataset_code` (first_release is not a superkey (allowed in 3NF because dataset_code is prime))
  - first_release → dataset_code: the left side is not a superkey, but dataset_code is prime, so 3NF holds and BCNF does not

| dataset_code | source_neuron_id | first_release |
|---|---|---|
| hemibrain | 487144598 | v1.0 |

- `BCNF option A: release_of`(first_release, dataset_code) — keys (first_release), highest form **BCNF**
- `BCNF option A: neuron_first_seen`(first_release, source_neuron_id) — keys (first_release, source_neuron_id), highest form **BCNF**
  - lossless, but (dataset_code, source_neuron_id) → first_release is no longer checked inside one relation: two releases of a dataset could both claim the same neuron

cc.dataset_neuron keeps the 3NF form and protects first_release → dataset with the composite foreign key (first_release_id, dataset_id) → release, so both dependencies stay enforced. This is the classic case where BCNF and dependency preservation cannot both be had.
<!-- /generated:walkthrough -->

The real schema adds three things beyond these steps:
- **Surrogate keys**, such as `dataset_neuron_id`. The natural keys stay
  `UNIQUE`, so they are still enforced.
- **Bitemporal versioning**, so that one fact unchanged over many releases is
  stored once (`valid_rel`), and corrections never overwrite history (`tx`).
- **Provenance pointers.**

See ARCHITECTURE.md for the ER model these relations implement.

## Maintenance: insertion, update and deletion

### Anomalies, reproduced on real data

[`backend/design/maintenance.py`](../backend/design/maintenance.py) builds
`connection_flat`, the 1NF relation from the walk-through, from the lab's
real rows: one wide row per connection, with the neurons' facts repeated on
every row. It then runs the same three changes on it and on the normalised
lab tables, and rolls everything back. The dashboard (**Design →
Maintenance**) runs it on demand.

<!-- generated:anomalies -->
Unnormalised copy: 9023 rows. Neuron 487144598 (`LHCENT3`) has its cell type stored 129 times there, once in `lab.neuron`.

| Anomaly | Unnormalised `connection_flat` | Normalised lab tables |
|---|---|---|
| Update | `UPDATE connection_flat SET pre_cell_type = 'renamed_type' WHERE pre_neuron_id = 135669 AND release_id = 2` → UPDATE 43; the neuron now has 2 different cell types: LHCENT3, renamed_type | `UPDATE lab.neuron SET cell_type = 'renamed_type' WHERE neuron_id = 135669` → UPDATE 1; one row changed; every connection sees the new type through the join |
| Insertion | `INSERT INTO connection_flat (release_id, version_label, dataset_code, pre_neuron_id, pre_source_id, pre_cell_type) VALUES (5, '783', 'flywire', 900000000001, 'new-neuron', 'new_type')` → null value in column "post_neuron_id" of relation "connection_flat" violates not-null constraint | `INSERT INTO lab.neuron (dataset_id, source_neuron_id, cell_type) VALUES (1, 'new-neuron', 'new_type')` → INSERT 0 1; recorded in lab.neuron; connections can be added later |
| Deletion | `DELETE FROM connection_flat WHERE 79046 IN (pre_neuron_id, post_neuron_id)` → DELETE 1; no row mentions neuron 720575940627692565 any more (0 rows): its cell type 'LHAV6g1' is lost | `DELETE FROM lab.connection WHERE 79046 IN (pre_neuron_id, post_neuron_id)` → DELETE 1; the neuron stays in lab.neuron with cell type 'LHAV6g1' |

All of it ran in one transaction that was rolled back (0.3 s, 2026-11-24 16:27 UTC).
<!-- /generated:anomalies -->

### How the normalised schema is kept up to date

- **Loading a release is the only way facts change in `cc`.** It runs in one
  transaction (`backend/etl/loader.py`). Unchanged facts keep their row. A
  fact that changed has its row *retired*: its transaction-time interval is
  closed. The new value is then inserted. The trigger `guard_history`
  rejects every other `UPDATE` and every `DELETE` on the versioned tables,
  so past states can always be reconstructed.
- **Constraints are checked on every write:**
  - keys and `UNIQUE`;
  - `CHECK`;
  - composite foreign keys;
  - GiST exclusion (no two overlapping versions of one fact).

  A failed load is rolled back completely. The console's **Trial** target
  sends a write to the real tables as role `cc_tryout`. Every constraint and
  trigger fires, then the transaction is rolled back.
- **The lab is the place to change data by hand.** The Data editor and the
  console's **Lab** target insert, update and delete rows in BCNF tables. The
  foreign keys cascade deletes, the same-dataset trigger guards connections,
  and the trigger `log_change` records every change in `lab.change_log`.

### How the data is stored: files, versions and VACUUM

Each table is one file under the database's data directory, extended in
1 GB segments, with one file per index and a TOAST file for long values.

Under MVCC, an `UPDATE` writes a new version of the row and leaves the old
one as a *dead tuple* until `VACUUM` marks its space reusable. `ANALYZE`
refreshes the statistics the planner uses, and `REINDEX` rebuilds an
index's file. Autovacuum does this in the background for `cc`.

In the lab, **Design → Maintenance** runs `VACUUM (ANALYZE)`, `ANALYZE` or
`REINDEX` as role `cc_lab` and shows the dead tuples and statistics before
and after. For example, after an update of 2,576 lab connections, the dead
tuples went from 2,576 to 0.

<!-- generated:files -->
| Table | File (under the data directory) | Heap MB | Index MB | TOAST MB | Live tuples | Dead tuples | Last vacuum |
|---|---|---|---|---|---|---|---|
| `cc.connection` | `base/16384/21783` | 1,337.2 | 1,613.0 | 0.4 | 9,035,853 | 2 | 2026-11-24 13:32 |
| `cc.diff_event` | `base/16384/21896` | 372.2 | 96.3 | 0.1 | 3,468,243 | 0 | 2026-11-24 13:33 |
| `cc.annotation` | `base/16384/21757` | 191.6 | 251.3 | 0.1 | 1,625,410 | 5 | 2026-11-24 13:30 |
| `cc.neuron_region_synapses` | `base/16384/22079` | 70.6 | 83.5 | 0.0 | 1,083,797 | 0 | 2026-11-24 13:31 |
| `cc.anomaly_result` | `base/16384/22008` | 110.4 | 6.5 | 0.1 | 106,999 | 0 | 2026-11-24 13:38 |
| `cc.partition_assignment` | `base/16384/21953` | 35.4 | 62.4 | 0.0 | 527,529 | 0 | 2026-11-24 13:44 |
| `cc.neuron_mapping` | `base/16384/21833` | 41.6 | 29.4 | 0.0 | 219,781 | 200 | 2026-11-24 13:31 |
| `cc.dataset_neuron` | `base/16384/21704` | 18.7 | 31.5 | 0.0 | 182,274 | 0 | 2026-11-24 13:30 |
| `cc.metadata_record_digest` | `base/16384/22202` | 37.1 | 10.5 | 0.0 | 332,368 | 0 | 2026-11-24 13:37 |
| `cc.neuron_version` | `base/16384/21733` | 18.5 | 23.2 | 0.0 | 203,554 | 0 | 2026-11-24 13:30 |
| `cc.canonical_neuron` | `base/16384/21817` | 14.5 | 16.1 | 0.0 | 182,274 | 0 | 2026-11-24 13:31 |
| `cc.circuit_trial` | `base/16384/25232` | 0.4 | 0.1 | 6.8 | 1,920 | 0 | 2026-11-24 15:08 |

The 12 largest tables of schema `cc`, 2026-11-24 16:27 UTC.
<!-- /generated:files -->

The bitemporal design keeps `cc` almost free of dead tuples. A load closes
the `tx` interval of the rows it retires, which is one update each, and
inserts the rest, so storage grows with change, not with the number of
releases.
