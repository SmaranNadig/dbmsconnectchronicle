# Data dictionary

Every table, column, constraint, index, trigger, view and function of the
PostgreSQL schemas `cc` (the system of record) and `lab` (the editable
copy), and the MongoDB collections. Nothing here is typed by hand: the
content below is read from `pg_catalog` and from MongoDB by
`backend/design/dictionary.py`, and the descriptions are the database's own
`COMMENT ON` texts (migration `0014`, `backend/db/lab_schema.sql`). The
same dictionary is on the dashboard (**Database lab → Design → Dictionary**)
and from `GET /api/v1/design/dictionary`; `\d+ cc.<table>` in psql shows the
same descriptions.

Regenerate with `python scripts/design_docs.py dictionary`.

Column *Key*: PK = part of the primary key, FK = part of a foreign key.
*Null* = yes when the column accepts NULL. Normal forms, keys and functional
dependencies of each table are in [NORMALISATION.md](NORMALISATION.md).

<!-- generated:dictionary -->
Read from the PostgreSQL catalog and MongoDB on 2026-11-23 13:04 UTC: 39 tables (39 described), 268 columns, 222 constraints (65 foreign keys, 89 checks, 4 exclusion), 85 indexes, 10 triggers, 8 views, 17 functions, 2 MongoDB collection(s).


## Schema `cc`


### `cc.active_deployment`

The batch the router currently reads, per strategy.  
About 3 rows, 40.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `strategy_id` | smallint |  | PK FK |  |  |
| 2 | `batch_id` | bigint |  | FK |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `active_deployment_pkey` | primary key | `PRIMARY KEY (strategy_id)` |
| `active_deployment_batch_key` | unique | `UNIQUE (batch_id)` |
| `active_deployment_batch_id_fkey` | foreign key | `FOREIGN KEY (batch_id) REFERENCES deployment_batch(batch_id)` |
| `active_deployment_batch_strategy_fkey` | foreign key | `FOREIGN KEY (batch_id, strategy_id) REFERENCES deployment_batch(batch_id, strategy_id)` |
| `active_deployment_strategy_id_fkey` | foreign key | `FOREIGN KEY (strategy_id) REFERENCES partition_strategy(strategy_id)` |

Indexes: `active_deployment_batch_key` (btree, unique, 16.0 kB)


### `cc.annotation`

One attribute value (cell type, side, ...) of a neuron over a range of releases.  
About 1,625,410 rows, 438.7 MB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `annotation_id` | bigint |  | PK | identity |  |
| 2 | `dataset_neuron_id` | bigint |  | FK |  |  |
| 3 | `annotation_key` | text |  |  |  | attribute name (cell_type, side, ...), mapped from source columns by schema_mapping |
| 4 | `annotation_value` | text |  |  |  |  |
| 5 | `valid_rel` | int4range |  |  |  | valid time in release_order units of the neuron's dataset |
| 6 | `tx` | tstzrange |  |  | tstzrange(now(), NULL::timestamp with t… | transaction time; closed (never deleted) when a later load retires the belief |
| 7 | `provenance_id` | bigint |  | FK |  |  |
| 8 | `source_line` | integer | yes |  |  | line of the source file the value was read from |

| Constraint | Kind | Definition |
|---|---|---|
| `annotation_pkey` | primary key | `PRIMARY KEY (annotation_id)` |
| `annotation_dataset_neuron_id_fkey` | foreign key | `FOREIGN KEY (dataset_neuron_id) REFERENCES dataset_neuron(dataset_neuron_id)` |
| `annotation_provenance_id_fkey` | foreign key | `FOREIGN KEY (provenance_id) REFERENCES provenance(provenance_id)` |
| `annotation_dataset_neuron_id_annotation_key_valid_rel_tx_excl` | exclusion | `EXCLUDE USING gist (dataset_neuron_id WITH =, annotation_key WITH =, valid_rel WITH &&, tx WITH &&)` |
| `annotation_annotation_key_check` | check | `CHECK ((annotation_key ~ '^[a-z][a-z0-9_]*$'::text))` |
| `annotation_annotation_value_check` | check | `CHECK ((annotation_value <> ''::text))` |
| `annotation_source_line_check` | check | `CHECK ((source_line > 0))` |
| `annotation_tx_check` | check | `CHECK (((NOT isempty(tx)) AND (NOT lower_inf(tx))))` |
| `annotation_valid_rel_check` | check | `CHECK (((NOT isempty(valid_rel)) AND (NOT lower_inf(valid_rel))))` |

Indexes: `annotation_dataset_neuron_id_annotation_key_valid_rel_tx_excl` (gist, 184.6 MB); `annotation_key_value_idx` (btree, 13.6 MB)

Triggers: `annotation_history`


### `cc.anomaly_result`

A model's score for one diff event.  
About 0 rows, 16.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `model_run_id` | bigint |  | PK FK |  |  |
| 2 | `diff_event_id` | bigint |  | PK FK |  |  |
| 3 | `score` | double precision |  |  |  |  |
| 4 | `is_flagged` | boolean |  |  |  |  |
| 5 | `score_rank` | integer |  |  |  |  |
| 6 | `features` | jsonb |  |  | '{}'::jsonb |  |
| 7 | `explanation` | text |  |  | ''::text |  |
| 8 | `baseline_flagged` | boolean |  |  | false |  |

| Constraint | Kind | Definition |
|---|---|---|
| `anomaly_result_pkey` | primary key | `PRIMARY KEY (model_run_id, diff_event_id)` |
| `anomaly_result_diff_event_id_fkey` | foreign key | `FOREIGN KEY (diff_event_id) REFERENCES diff_event(diff_event_id) ON DELETE CASCADE` |
| `anomaly_result_model_run_id_fkey` | foreign key | `FOREIGN KEY (model_run_id) REFERENCES ml_model_run(model_run_id) ON DELETE CASCADE` |
| `anomaly_result_score_rank_check` | check | `CHECK ((score_rank > 0))` |


### `cc.brain_region`

A neuropil in one hemisphere (NULL = unpaired).  
About 82 rows, 80.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `brain_region_id` | integer |  | PK | identity |  |
| 2 | `canonical_code` | text |  | FK |  |  |
| 3 | `hemisphere` | character(1) | yes |  |  | L or R; NULL for unpaired neuropils |
| 5 | `name` | text | yes |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `brain_region_pkey` | primary key | `PRIMARY KEY (brain_region_id)` |
| `brain_region_canonical_code_hemisphere_key` | unique | `UNIQUE NULLS NOT DISTINCT (canonical_code, hemisphere)` |
| `brain_region_neuropil_fkey` | foreign key | `FOREIGN KEY (canonical_code) REFERENCES neuropil(canonical_code)` |
| `brain_region_hemisphere_check` | check | `CHECK ((hemisphere = ANY (ARRAY['L'::bpchar, 'R'::bpchar])))` |

Indexes: `brain_region_canonical_code_hemisphere_key` (btree, unique, 16.0 kB)


### `cc.brain_region_alias`

A dataset's own region code mapped to the canonical region.  
About 141 rows, 72.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `dataset_id` | smallint |  | PK FK |  |  |
| 2 | `source_code` | text |  | PK |  |  |
| 3 | `brain_region_id` | integer |  | FK |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `brain_region_alias_pkey` | primary key | `PRIMARY KEY (dataset_id, source_code)` |
| `brain_region_alias_brain_region_id_fkey` | foreign key | `FOREIGN KEY (brain_region_id) REFERENCES brain_region(brain_region_id)` |
| `brain_region_alias_dataset_id_fkey` | foreign key | `FOREIGN KEY (dataset_id) REFERENCES dataset(dataset_id)` |


### `cc.canonical_neuron`

A neuron identity across datasets.  
About 182,274 rows, 30.7 MB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `canonical_neuron_id` | bigint |  | PK | identity |  |
| 2 | `status` | text |  |  | 'active'::text |  |
| 3 | `neuron_type_id` | integer | yes | FK |  |  |
| 4 | `label` | text | yes |  |  |  |
| 5 | `created_at` | timestamp with time zone |  |  | now() |  |

| Constraint | Kind | Definition |
|---|---|---|
| `canonical_neuron_pkey` | primary key | `PRIMARY KEY (canonical_neuron_id)` |
| `canonical_neuron_neuron_type_id_fkey` | foreign key | `FOREIGN KEY (neuron_type_id) REFERENCES neuron_type(neuron_type_id)` |
| `canonical_neuron_status_check` | check | `CHECK ((status = ANY (ARRAY['active'::text, 'merged'::text, 'retired'::text])))` |

Indexes: `canonical_neuron_label_key` (btree, unique, 12.2 MB)


### `cc.connection`

Synapse count between two neurons over a range of releases (bitemporal).  
About 9,001,488 rows, 2.8 GB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `connection_id` | bigint |  | PK | identity |  |
| 2 | `dataset_id` | smallint |  | FK |  | copy of both endpoints' dataset, checked by composite foreign keys (an edge cannot join two connectomes) |
| 3 | `pre_neuron_id` | bigint |  | FK |  |  |
| 4 | `post_neuron_id` | bigint |  | FK |  |  |
| 5 | `synapse_count` | integer |  |  |  | number of synapses from pre to post (summed over duplicate source rows) |
| 6 | `valid_rel` | int4range |  |  |  | valid time in release_order units; one row covers every release in which the count is unchanged |
| 7 | `tx` | tstzrange |  |  | tstzrange(now(), NULL::timestamp with t… | transaction time; closed when a later load retires this version |
| 8 | `provenance_id` | bigint |  | FK |  |  |
| 9 | `source_lines` | integer[] |  |  |  | line numbers of the source rows summed into this edge (evidence, read as a whole) |

| Constraint | Kind | Definition |
|---|---|---|
| `connection_pkey` | primary key | `PRIMARY KEY (connection_id)` |
| `connection_post_neuron_id_dataset_id_fkey` | foreign key | `FOREIGN KEY (post_neuron_id, dataset_id) REFERENCES dataset_neuron(dataset_neuron_id, dataset_id)` |
| `connection_pre_neuron_id_dataset_id_fkey` | foreign key | `FOREIGN KEY (pre_neuron_id, dataset_id) REFERENCES dataset_neuron(dataset_neuron_id, dataset_id)` |
| `connection_provenance_id_fkey` | foreign key | `FOREIGN KEY (provenance_id) REFERENCES provenance(provenance_id)` |
| `connection_pre_neuron_id_post_neuron_id_valid_rel_tx_excl` | exclusion | `EXCLUDE USING gist (pre_neuron_id WITH =, post_neuron_id WITH =, valid_rel WITH &&, tx WITH &&)` |
| `connection_source_lines_check` | check | `CHECK ((cardinality(source_lines) > 0))` |
| `connection_synapse_count_check` | check | `CHECK ((synapse_count >= 0))` |
| `connection_tx_check` | check | `CHECK (((NOT isempty(tx)) AND (NOT lower_inf(tx))))` |
| `connection_valid_rel_check` | check | `CHECK (((NOT isempty(valid_rel)) AND (NOT lower_inf(valid_rel))))` |

Indexes: `connection_dataset_open_idx` (btree, 46.9 MB); `connection_post_idx` (btree, 93.4 MB); `connection_pre_idx` (btree, 91.3 MB); `connection_pre_neuron_id_post_neuron_id_valid_rel_tx_excl` (gist, 1.1 GB)

Triggers: `connection_history`


### `cc.dataset`

A public connectome (hemibrain, FlyWire).  
About 2 rows, 48.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `dataset_id` | smallint |  | PK | identity |  |
| 2 | `code` | text |  |  |  |  |
| 3 | `name` | text |  |  |  |  |
| 4 | `organism` | text | yes |  |  |  |
| 5 | `description` | text | yes |  |  |  |
| 6 | `source_url` | text | yes |  |  |  |
| 7 | `license` | text | yes |  |  |  |
| 8 | `created_at` | timestamp with time zone |  |  | now() |  |
| 9 | `organization` | text | yes |  |  |  |
| 10 | `repository` | text | yes |  |  |  |
| 11 | `data_formats` | text[] |  |  | '{}'::text[] |  |

| Constraint | Kind | Definition |
|---|---|---|
| `dataset_pkey` | primary key | `PRIMARY KEY (dataset_id)` |
| `dataset_code_key` | unique | `UNIQUE (code)` |
| `dataset_code_check` | check | `CHECK ((code ~ '^[a-z][a-z0-9_]*$'::text))` |

Indexes: `dataset_code_key` (btree, unique, 16.0 kB)


### `cc.dataset_neuron`

A neuron as identified by its dataset (original id kept as text).  
About 182,274 rows, 46.6 MB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `dataset_neuron_id` | bigint |  | PK | identity |  |
| 2 | `dataset_id` | smallint |  | FK |  |  |
| 3 | `source_neuron_id` | text |  |  |  | the neuron id as published (hemibrain bodyId, FlyWire root_id), kept as text |
| 4 | `first_release_id` | integer |  | FK |  | release in which the neuron first appeared (same dataset: composite foreign key) |
| 5 | `provenance_id` | bigint |  | FK |  |  |
| 6 | `source_line` | integer | yes |  |  |  |
| 7 | `metadata_doc_id` | text | yes |  |  | _id of the neuron's MongoDB source document (dataset code:source id) |

| Constraint | Kind | Definition |
|---|---|---|
| `dataset_neuron_pkey` | primary key | `PRIMARY KEY (dataset_neuron_id)` |
| `dataset_neuron_dataset_id_source_neuron_id_key` | unique | `UNIQUE (dataset_id, source_neuron_id)` |
| `dataset_neuron_dataset_neuron_id_dataset_id_key` | unique | `UNIQUE (dataset_neuron_id, dataset_id)` |
| `dataset_neuron_metadata_doc_id_key` | unique | `UNIQUE (metadata_doc_id)` |
| `dataset_neuron_dataset_id_fkey` | foreign key | `FOREIGN KEY (dataset_id) REFERENCES dataset(dataset_id)` |
| `dataset_neuron_first_release_id_dataset_id_fkey` | foreign key | `FOREIGN KEY (first_release_id, dataset_id) REFERENCES release(release_id, dataset_id)` |
| `dataset_neuron_provenance_id_fkey` | foreign key | `FOREIGN KEY (provenance_id) REFERENCES provenance(provenance_id)` |
| `dataset_neuron_source_line_check` | check | `CHECK ((source_line > 0))` |
| `dataset_neuron_source_neuron_id_check` | check | `CHECK ((source_neuron_id <> ''::text))` |

Indexes: `dataset_neuron_dataset_id_source_neuron_id_key` (btree, unique, 10.0 MB); `dataset_neuron_dataset_neuron_id_dataset_id_key` (btree, unique, 5.5 MB); `dataset_neuron_metadata_doc_id_key` (btree, unique, 8.5 MB)


### `cc.deployment_batch`

One generation of shard copies for a strategy.  
About 3 rows, 48.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `batch_id` | bigint |  | PK | identity |  |
| 2 | `strategy_id` | smallint |  | FK |  |  |
| 3 | `source_revision` | character(64) |  |  |  |  |
| 4 | `placement_revision` | character(64) |  |  |  |  |
| 5 | `status` | text |  |  | 'building'::text |  |
| 6 | `details` | jsonb |  |  | '{}'::jsonb |  |
| 7 | `created_at` | timestamp with time zone |  |  | now() |  |

| Constraint | Kind | Definition |
|---|---|---|
| `deployment_batch_pkey` | primary key | `PRIMARY KEY (batch_id)` |
| `deployment_batch_id_strategy_key` | unique | `UNIQUE (batch_id, strategy_id)` |
| `deployment_batch_strategy_id_fkey` | foreign key | `FOREIGN KEY (strategy_id) REFERENCES partition_strategy(strategy_id)` |
| `deployment_batch_status_check` | check | `CHECK ((status = ANY (ARRAY['building'::text, 'verified'::text, 'failed'::text])))` |

Indexes: `deployment_batch_id_strategy_key` (btree, unique, 16.0 kB)


### `cc.diff_event`

One change found by a diff run (added, removed, modified).  
About 3,468,914 rows, 536.1 MB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `diff_event_id` | bigint |  | PK | identity |  |
| 2 | `diff_run_id` | bigint |  | FK |  |  |
| 3 | `entity_type` | text |  |  |  |  |
| 4 | `change_type` | text |  |  |  |  |
| 5 | `dataset_neuron_id` | bigint |  | FK |  |  |
| 6 | `post_neuron_id` | bigint | yes | FK |  |  |
| 7 | `annotation_key` | text | yes |  |  |  |
| 8 | `old_value` | text | yes |  |  |  |
| 9 | `new_value` | text | yes |  |  |  |
| 10 | `old_weight` | integer | yes |  |  |  |
| 11 | `new_weight` | integer | yes |  |  |  |
| 12 | `weight_delta` | integer | yes |  | generated | new_weight - old_weight, generated by the database |
| 13 | `record_a_id` | bigint | yes |  |  | versioned row (connection, annotation or neuron_version id) in release A |
| 14 | `record_b_id` | bigint | yes |  |  | versioned row in release B |

| Constraint | Kind | Definition |
|---|---|---|
| `diff_event_pkey` | primary key | `PRIMARY KEY (diff_event_id)` |
| `diff_event_dataset_neuron_id_fkey` | foreign key | `FOREIGN KEY (dataset_neuron_id) REFERENCES dataset_neuron(dataset_neuron_id)` |
| `diff_event_diff_run_id_fkey` | foreign key | `FOREIGN KEY (diff_run_id) REFERENCES diff_run(diff_run_id) ON DELETE CASCADE` |
| `diff_event_post_neuron_id_fkey` | foreign key | `FOREIGN KEY (post_neuron_id) REFERENCES dataset_neuron(dataset_neuron_id)` |
| `diff_event_change_type_check` | check | `CHECK ((change_type = ANY (ARRAY['added'::text, 'removed'::text, 'modified'::text])))` |
| `diff_event_check` | check | `CHECK (((entity_type = 'connection'::text) = (post_neuron_id IS NOT NULL)))` |
| `diff_event_check1` | check | `CHECK (((entity_type = 'annotation'::text) = (annotation_key IS NOT NULL)))` |
| `diff_event_check2` | check | `CHECK (((change_type <> 'added'::text) OR ((record_a_id IS NULL) AND (record_b_id IS NOT NULL))))` |
| `diff_event_check3` | check | `CHECK (((change_type <> 'removed'::text) OR ((record_a_id IS NOT NULL) AND (record_b_id IS NULL))))` |
| `diff_event_check4` | check | `CHECK (((change_type <> 'modified'::text) OR ((record_a_id IS NOT NULL) AND (record_b_id IS NOT NULL))))` |
| `diff_event_entity_type_check` | check | `CHECK ((entity_type = ANY (ARRAY['neuron'::text, 'connection'::text, 'annotation'::text])))` |
| `diff_event_new_weight_check` | check | `CHECK ((new_weight >= 0))` |
| `diff_event_old_weight_check` | check | `CHECK ((old_weight >= 0))` |

Indexes: `diff_event_run_idx` (btree, 25.0 MB)


### `cc.diff_run`

One comparison of two release states at a transaction time.  
About 5 rows, 48.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `diff_run_id` | bigint |  | PK | identity |  |
| 2 | `mode` | text |  |  |  | temporal (two releases of one dataset) or cross_dataset (never reported as temporal change) |
| 3 | `dataset_a` | smallint |  | FK |  |  |
| 4 | `release_a` | integer |  | FK |  |  |
| 5 | `dataset_b` | smallint |  | FK |  |  |
| 6 | `release_b` | integer |  | FK |  |  |
| 7 | `as_of` | timestamp with time zone |  |  |  | transaction time at which both release states were read (makes a diff repeatable) |
| 8 | `parameters` | jsonb |  |  | '{}'::jsonb |  |
| 9 | `status` | text |  |  | 'running'::text |  |
| 10 | `started_at` | timestamp with time zone |  |  | clock_timestamp() |  |
| 11 | `finished_at` | timestamp with time zone | yes |  |  |  |
| 12 | `stats` | jsonb |  |  | '{}'::jsonb |  |

| Constraint | Kind | Definition |
|---|---|---|
| `diff_run_pkey` | primary key | `PRIMARY KEY (diff_run_id)` |
| `diff_run_release_a_dataset_a_fkey` | foreign key | `FOREIGN KEY (release_a, dataset_a) REFERENCES release(release_id, dataset_id)` |
| `diff_run_release_b_dataset_b_fkey` | foreign key | `FOREIGN KEY (release_b, dataset_b) REFERENCES release(release_id, dataset_id)` |
| `diff_run_check` | check | `CHECK ((release_a <> release_b))` |
| `diff_run_check1` | check | `CHECK (((mode <> 'temporal'::text) OR (dataset_a = dataset_b)))` |
| `diff_run_check2` | check | `CHECK (((mode <> 'cross_dataset'::text) OR (dataset_a <> dataset_b)))` |
| `diff_run_mode_check` | check | `CHECK ((mode = ANY (ARRAY['temporal'::text, 'cross_dataset'::text])))` |
| `diff_run_status_check` | check | `CHECK ((status = ANY (ARRAY['running'::text, 'succeeded'::text, 'failed'::text])))` |

Indexes: `diff_run_idempotent` (btree, unique, 16.0 kB)


### `cc.ingestion_run`

One attempt to load a release.  
About 9 rows, 80.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `run_id` | bigint |  | PK | identity |  |
| 2 | `release_id` | integer |  | FK |  |  |
| 3 | `pipeline_version` | text |  |  |  |  |
| 4 | `status` | text |  |  | 'running'::text |  |
| 5 | `started_at` | timestamp with time zone |  |  | clock_timestamp() |  |
| 6 | `finished_at` | timestamp with time zone | yes |  |  |  |
| 7 | `stats` | jsonb |  |  | '{}'::jsonb |  |
| 8 | `error_message` | text | yes |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `ingestion_run_pkey` | primary key | `PRIMARY KEY (run_id)` |
| `ingestion_run_id_release_key` | unique | `UNIQUE (run_id, release_id)` |
| `ingestion_run_release_id_fkey` | foreign key | `FOREIGN KEY (release_id) REFERENCES release(release_id)` |
| `ingestion_run_check` | check | `CHECK (((finished_at IS NULL) OR (finished_at >= started_at)))` |
| `ingestion_run_check1` | check | `CHECK (((status = 'running'::text) = (finished_at IS NULL)))` |
| `ingestion_run_status_check` | check | `CHECK ((status = ANY (ARRAY['running'::text, 'succeeded'::text, 'failed'::text])))` |

Indexes: `ingestion_run_id_release_key` (btree, unique, 16.0 kB)


### `cc.metadata_record_digest`

Expected count and checksum of a neuron's source records per release.  
About 0 rows, 8.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `dataset_neuron_id` | bigint |  | PK FK |  |  |
| 2 | `release_id` | integer |  | PK FK |  |  |
| 3 | `record_count` | integer |  |  |  |  |
| 4 | `sha256` | character(64) |  |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `metadata_record_digest_pkey` | primary key | `PRIMARY KEY (dataset_neuron_id, release_id)` |
| `metadata_record_digest_dataset_neuron_id_fkey` | foreign key | `FOREIGN KEY (dataset_neuron_id) REFERENCES dataset_neuron(dataset_neuron_id)` |
| `metadata_record_digest_release_id_fkey` | foreign key | `FOREIGN KEY (release_id) REFERENCES release(release_id)` |
| `metadata_record_digest_record_count_check` | check | `CHECK ((record_count >= 0))` |


### `cc.metadata_sync`

One synchronisation of a release's source records into MongoDB.  
About 5 rows, 48.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `metadata_sync_id` | integer |  | PK | identity |  |
| 2 | `release_id` | integer |  | FK |  |  |
| 3 | `mongo_database` | text |  |  |  |  |
| 4 | `mongo_collection` | text |  |  |  |  |
| 5 | `pipeline_version` | text |  |  |  |  |
| 6 | `status` | text |  |  | 'running'::text |  |
| 7 | `started_at` | timestamp with time zone |  |  | now() |  |
| 8 | `finished_at` | timestamp with time zone | yes |  |  |  |
| 9 | `documents_written` | integer | yes |  |  |  |
| 10 | `records_written` | integer | yes |  |  |  |
| 11 | `stats` | jsonb |  |  | '{}'::jsonb |  |
| 12 | `error_message` | text | yes |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `metadata_sync_pkey` | primary key | `PRIMARY KEY (metadata_sync_id)` |
| `metadata_sync_release_id_fkey` | foreign key | `FOREIGN KEY (release_id) REFERENCES release(release_id)` |
| `metadata_sync_check` | check | `CHECK (((status = 'running'::text) = (finished_at IS NULL)))` |
| `metadata_sync_check1` | check | `CHECK (((status <> 'failed'::text) OR (error_message IS NOT NULL)))` |
| `metadata_sync_documents_written_check` | check | `CHECK ((documents_written >= 0))` |
| `metadata_sync_records_written_check` | check | `CHECK ((records_written >= 0))` |
| `metadata_sync_status_check` | check | `CHECK ((status = ANY (ARRAY['running'::text, 'completed'::text, 'failed'::text])))` |

Indexes: `metadata_sync_release_idx` (btree, 16.0 kB)


### `cc.ml_model_input`

The diff runs a model was trained on (was the array ml_model_run.diff_run_ids, 0013).  
About 0 rows, 16.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `model_run_id` | bigint |  | PK FK |  |  |
| 2 | `diff_run_id` | bigint |  | PK FK |  | a diff run whose events the model was trained on |

| Constraint | Kind | Definition |
|---|---|---|
| `ml_model_input_pkey` | primary key | `PRIMARY KEY (model_run_id, diff_run_id)` |
| `ml_model_input_diff_run_id_fkey` | foreign key | `FOREIGN KEY (diff_run_id) REFERENCES diff_run(diff_run_id)` |
| `ml_model_input_model_run_id_fkey` | foreign key | `FOREIGN KEY (model_run_id) REFERENCES ml_model_run(model_run_id) ON DELETE CASCADE` |

Indexes: `ml_model_input_diff_idx` (btree, 8.0 kB)


### `cc.ml_model_run`

One trained change-scoring model.  
About 0 rows, 24.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `model_run_id` | bigint |  | PK | identity |  |
| 2 | `model_kind` | text |  |  |  |  |
| 3 | `model_version` | text |  |  |  |  |
| 4 | `parameters` | jsonb |  |  | '{}'::jsonb |  |
| 5 | `feature_schema` | jsonb |  |  | '[]'::jsonb |  |
| 7 | `random_seed` | integer | yes |  |  |  |
| 8 | `artifact_path` | text | yes |  |  |  |
| 9 | `artifact_sha256` | character(64) | yes |  |  |  |
| 10 | `created_at` | timestamp with time zone |  |  | now() |  |
| 11 | `metrics` | jsonb |  |  | '{}'::jsonb |  |

| Constraint | Kind | Definition |
|---|---|---|
| `ml_model_run_pkey` | primary key | `PRIMARY KEY (model_run_id)` |
| `ml_model_run_model_kind_model_version_key` | unique | `UNIQUE (model_kind, model_version)` |
| `ml_model_run_model_kind_check` | check | `CHECK ((model_kind = ANY (ARRAY['isolation_forest'::text, 'threshold'::text, 'embedding'::text, 'graphsage'::text])))` |

Indexes: `ml_model_run_model_kind_model_version_key` (btree, unique, 8.0 kB)


### `cc.neuron_mapping`

A proposed or reviewed link from a dataset neuron to a canonical neuron.  
About 219,781 rows, 66.8 MB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `mapping_id` | bigint |  | PK | identity |  |
| 2 | `dataset_neuron_id` | bigint |  | FK |  |  |
| 3 | `canonical_neuron_id` | bigint | yes | FK |  |  |
| 4 | `method` | text |  |  |  | how the link was proposed (source_identity, published_type_crosswalk, rule_based, embedding, graphsage, manual) |
| 5 | `confidence` | numeric(4,3) | yes |  |  | score between 0 and 1 |
| 6 | `review_status` | text |  |  | 'proposed'::text | proposed, approved, rejected or unresolved (unresolved = no canonical neuron) |
| 7 | `evidence` | jsonb |  |  | '{}'::jsonb |  |
| 8 | `evidence_doc_id` | text | yes |  |  |  |
| 9 | `model_version` | text | yes |  |  |  |
| 10 | `provenance_id` | bigint | yes | FK |  |  |
| 11 | `mapped_at` | timestamp with time zone |  |  | now() |  |
| 12 | `reviewed_at` | timestamp with time zone | yes |  |  |  |
| 13 | `reviewed_by` | text | yes |  |  |  |
| 14 | `score_details` | jsonb |  |  | '{}'::jsonb |  |

| Constraint | Kind | Definition |
|---|---|---|
| `neuron_mapping_pkey` | primary key | `PRIMARY KEY (mapping_id)` |
| `neuron_mapping_canonical_neuron_id_fkey` | foreign key | `FOREIGN KEY (canonical_neuron_id) REFERENCES canonical_neuron(canonical_neuron_id)` |
| `neuron_mapping_dataset_neuron_id_fkey` | foreign key | `FOREIGN KEY (dataset_neuron_id) REFERENCES dataset_neuron(dataset_neuron_id)` |
| `neuron_mapping_provenance_id_fkey` | foreign key | `FOREIGN KEY (provenance_id) REFERENCES provenance(provenance_id)` |
| `neuron_mapping_check` | check | `CHECK (((review_status = 'unresolved'::text) = (canonical_neuron_id IS NULL)))` |
| `neuron_mapping_check1` | check | `CHECK (((method <> ALL (ARRAY['embedding'::text, 'graphsage'::text])) OR (model_version IS NOT NULL)))` |
| `neuron_mapping_check2` | check | `CHECK (((review_status = ANY (ARRAY['proposed'::text, 'unresolved'::text])) OR (reviewed_at IS NOT NULL)))` |
| `neuron_mapping_confidence_check` | check | `CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric)))` |
| `neuron_mapping_method_check` | check | `CHECK ((method = ANY (ARRAY['source_identity'::text, 'published_type_crosswalk'::text, 'rule_based'::text, 'embedding'::text, 'graphsage'::…` |
| `neuron_mapping_review_status_check` | check | `CHECK ((review_status = ANY (ARRAY['proposed'::text, 'approved'::text, 'rejected'::text, 'unresolved'::text])))` |

Indexes: `neuron_mapping_canonical_idx` (btree, 4.5 MB); `neuron_mapping_no_dup` (btree, unique, 13.5 MB); `neuron_mapping_one_approved` (btree, unique, 4.3 MB)


### `cc.neuron_region_synapses`

Per release, how many synapses a neuron has in each brain region.  
About 1,083,797 rows, 146.9 MB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `release_id` | integer |  | PK FK |  |  |
| 2 | `dataset_id` | smallint |  | FK |  |  |
| 3 | `dataset_neuron_id` | bigint |  | PK FK |  |  |
| 4 | `brain_region_id` | integer |  | PK FK |  |  |
| 5 | `pre_synapses` | integer |  |  |  | outgoing synapses of the neuron in the region |
| 6 | `post_synapses` | integer |  |  |  | incoming synapses of the neuron in the region |
| 7 | `provenance_id` | bigint |  | FK |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `neuron_region_synapses_pkey` | primary key | `PRIMARY KEY (release_id, dataset_neuron_id, brain_region_id)` |
| `neuron_region_synapses_brain_region_id_fkey` | foreign key | `FOREIGN KEY (brain_region_id) REFERENCES brain_region(brain_region_id)` |
| `neuron_region_synapses_dataset_neuron_id_dataset_id_fkey` | foreign key | `FOREIGN KEY (dataset_neuron_id, dataset_id) REFERENCES dataset_neuron(dataset_neuron_id, dataset_id)` |
| `neuron_region_synapses_provenance_id_fkey` | foreign key | `FOREIGN KEY (provenance_id) REFERENCES provenance(provenance_id)` |
| `neuron_region_synapses_release_id_dataset_id_fkey` | foreign key | `FOREIGN KEY (release_id, dataset_id) REFERENCES release(release_id, dataset_id)` |
| `neuron_region_synapses_check` | check | `CHECK (((pre_synapses + post_synapses) > 0))` |
| `neuron_region_synapses_post_synapses_check` | check | `CHECK ((post_synapses >= 0))` |
| `neuron_region_synapses_pre_synapses_check` | check | `CHECK ((pre_synapses >= 0))` |

Indexes: `neuron_region_synapses_neuron_idx` (btree, 17.2 MB); `neuron_region_synapses_region_idx` (btree, 7.8 MB)


### `cc.neuron_type`

A canonical cell type name.  
About 9,959 rows, 1.0 MB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `neuron_type_id` | integer |  | PK | identity |  |
| 2 | `name` | text |  |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `neuron_type_pkey` | primary key | `PRIMARY KEY (neuron_type_id)` |
| `neuron_type_name_key` | unique | `UNIQUE (name)` |
| `neuron_type_name_check` | check | `CHECK ((name <> ''::text))` |

Indexes: `neuron_type_name_key` (btree, unique, 320.0 kB)


### `cc.neuron_version`

Existence of a neuron over a range of releases (bitemporal).  
About 203,554 rows, 42.5 MB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `neuron_version_id` | bigint |  | PK | identity |  |
| 2 | `dataset_neuron_id` | bigint |  | FK |  |  |
| 3 | `valid_rel` | int4range |  |  |  | valid time: [first release order it holds in, first order it no longer holds in); upper NULL = still holds |
| 4 | `tx` | tstzrange |  |  | tstzrange(now(), NULL::timestamp with t… | transaction time: when the database held this belief; upper NULL = current belief |
| 5 | `provenance_id` | bigint |  | FK |  |  |
| 6 | `source_line` | integer | yes |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `neuron_version_pkey` | primary key | `PRIMARY KEY (neuron_version_id)` |
| `neuron_version_dataset_neuron_id_fkey` | foreign key | `FOREIGN KEY (dataset_neuron_id) REFERENCES dataset_neuron(dataset_neuron_id)` |
| `neuron_version_provenance_id_fkey` | foreign key | `FOREIGN KEY (provenance_id) REFERENCES provenance(provenance_id)` |
| `neuron_version_dataset_neuron_id_valid_rel_tx_excl` | exclusion | `EXCLUDE USING gist (dataset_neuron_id WITH =, valid_rel WITH &&, tx WITH &&)` |
| `neuron_version_source_line_check` | check | `CHECK ((source_line > 0))` |
| `neuron_version_tx_check` | check | `CHECK (((NOT isempty(tx)) AND (NOT lower_inf(tx))))` |
| `neuron_version_valid_rel_check` | check | `CHECK (((NOT isempty(valid_rel)) AND (NOT lower_inf(valid_rel))))` |

Indexes: `neuron_version_dataset_neuron_id_valid_rel_tx_excl` (gist, 17.4 MB)

Triggers: `neuron_version_history`


### `cc.neuropil`

A canonical neuropil code and the super region it belongs to (split from brain_region, 0013).  
About 45 rows, 32.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `canonical_code` | text |  | PK |  |  |
| 2 | `super_region` | text |  |  |  | optic, central, vnc or other |

| Constraint | Kind | Definition |
|---|---|---|
| `neuropil_pkey` | primary key | `PRIMARY KEY (canonical_code)` |
| `neuropil_canonical_code_check` | check | `CHECK ((canonical_code ~ '^[A-Z][A-Z0-9_]*$'::text))` |
| `neuropil_super_region_check` | check | `CHECK ((super_region = ANY (ARRAY['optic'::text, 'central'::text, 'vnc'::text, 'other'::text])))` |


### `cc.partition_assignment`

The shard (and group) of every neuron under every strategy.  
About 546,822 rows, 97.7 MB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `strategy_id` | smallint |  | PK FK |  |  |
| 2 | `dataset_neuron_id` | bigint |  | PK FK |  |  |
| 3 | `shard_id` | smallint |  | FK |  |  |
| 4 | `assigned_at` | timestamp with time zone |  |  | now() |  |
| 5 | `group_key` | text | yes |  |  | region or community the strategy grouped the neuron by; all of a group is on one shard |

| Constraint | Kind | Definition |
|---|---|---|
| `partition_assignment_pkey` | primary key | `PRIMARY KEY (strategy_id, dataset_neuron_id)` |
| `partition_assignment_dataset_neuron_id_fkey` | foreign key | `FOREIGN KEY (dataset_neuron_id) REFERENCES dataset_neuron(dataset_neuron_id)` |
| `partition_assignment_shard_id_fkey` | foreign key | `FOREIGN KEY (shard_id) REFERENCES shard(shard_id)` |
| `partition_assignment_strategy_id_fkey` | foreign key | `FOREIGN KEY (strategy_id) REFERENCES partition_strategy(strategy_id)` |
| `partition_assignment_group_one_shard` | exclusion | `EXCLUDE USING gist (strategy_id WITH =, group_key WITH =, shard_id WITH <>)` |

Indexes: `partition_assignment_group_one_shard` (gist, 36.6 MB); `partition_assignment_shard_idx` (btree, 3.4 MB)


### `cc.partition_strategy`

A way of placing neurons on shards (hash, anatomical, community).  
About 3 rows, 48.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `strategy_id` | smallint |  | PK | identity |  |
| 2 | `code` | text |  |  |  |  |
| 3 | `description` | text | yes |  |  |  |
| 4 | `parameters` | jsonb |  |  | '{}'::jsonb |  |
| 5 | `created_at` | timestamp with time zone |  |  | now() |  |

| Constraint | Kind | Definition |
|---|---|---|
| `partition_strategy_pkey` | primary key | `PRIMARY KEY (strategy_id)` |
| `partition_strategy_code_key` | unique | `UNIQUE (code)` |
| `partition_strategy_code_check` | check | `CHECK ((code = ANY (ARRAY['hash'::text, 'anatomical'::text, 'community'::text])))` |

Indexes: `partition_strategy_code_key` (btree, unique, 16.0 kB)


### `cc.provenance`

How a set of facts was produced: run, source file, transformation.  
About 25 rows, 32.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `provenance_id` | bigint |  | PK | identity |  |
| 2 | `dataset_id` | smallint |  | FK |  |  |
| 3 | `release_id` | integer |  | FK |  |  |
| 4 | `source_file_id` | integer | yes | FK |  |  |
| 5 | `ingestion_run_id` | bigint | yes | FK |  |  |
| 6 | `transformation_name` | text |  |  |  | what was done (register_neuron, extract_annotation, aggregate_pair_synapses, ...) |
| 7 | `transformation_version` | text |  |  |  |  |
| 8 | `parameters` | jsonb |  |  | '{}'::jsonb | parameters of the transformation (columns used, policies) |
| 9 | `created_at` | timestamp with time zone |  |  | now() |  |

| Constraint | Kind | Definition |
|---|---|---|
| `provenance_pkey` | primary key | `PRIMARY KEY (provenance_id)` |
| `provenance_dataset_id_fkey` | foreign key | `FOREIGN KEY (dataset_id) REFERENCES dataset(dataset_id)` |
| `provenance_ingestion_run_id_fkey` | foreign key | `FOREIGN KEY (ingestion_run_id) REFERENCES ingestion_run(run_id)` |
| `provenance_release_id_dataset_id_fkey` | foreign key | `FOREIGN KEY (release_id, dataset_id) REFERENCES release(release_id, dataset_id)` |
| `provenance_run_release_fkey` | foreign key | `FOREIGN KEY (ingestion_run_id, release_id) REFERENCES ingestion_run(run_id, release_id)` |
| `provenance_source_file_id_fkey` | foreign key | `FOREIGN KEY (source_file_id) REFERENCES source_file(source_file_id)` |
| `provenance_source_file_release_fkey` | foreign key | `FOREIGN KEY (source_file_id, release_id) REFERENCES source_file(source_file_id, release_id)` |


### `cc.provenance_parent`

Lineage: which provenance records a derived record was computed from.  
About 0 rows, 8.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `provenance_id` | bigint |  | PK FK |  |  |
| 2 | `parent_id` | bigint |  | PK FK |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `provenance_parent_pkey` | primary key | `PRIMARY KEY (provenance_id, parent_id)` |
| `provenance_parent_parent_id_fkey` | foreign key | `FOREIGN KEY (parent_id) REFERENCES provenance(provenance_id)` |
| `provenance_parent_provenance_id_fkey` | foreign key | `FOREIGN KEY (provenance_id) REFERENCES provenance(provenance_id) ON DELETE CASCADE` |
| `provenance_parent_check` | check | `CHECK ((provenance_id <> parent_id))` |


### `cc.query_execution`

A routed query's measured cost.  
About 2,644 rows, 600.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `query_execution_id` | bigint |  | PK | identity |  |
| 2 | `strategy_id` | smallint | yes | FK |  |  |
| 3 | `query_kind` | text |  |  |  |  |
| 4 | `parameters` | jsonb |  |  | '{}'::jsonb |  |
| 5 | `shards_touched` | smallint |  |  |  |  |
| 6 | `rows_returned` | bigint |  |  |  |  |
| 7 | `bytes_transferred` | bigint | yes |  |  |  |
| 8 | `latency_ms` | double precision |  |  |  |  |
| 9 | `executed_at` | timestamp with time zone |  |  | now() |  |

| Constraint | Kind | Definition |
|---|---|---|
| `query_execution_pkey` | primary key | `PRIMARY KEY (query_execution_id)` |
| `query_execution_strategy_id_fkey` | foreign key | `FOREIGN KEY (strategy_id) REFERENCES partition_strategy(strategy_id)` |
| `query_execution_bytes_transferred_check` | check | `CHECK ((bytes_transferred >= 0))` |
| `query_execution_latency_ms_check` | check | `CHECK ((latency_ms >= (0)::double precision))` |
| `query_execution_rows_returned_check` | check | `CHECK ((rows_returned >= 0))` |
| `query_execution_shards_touched_check` | check | `CHECK ((shards_touched >= 0))` |


### `cc.release`

One published version of a dataset, ordered within the dataset.  
About 5 rows, 80.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `release_id` | integer |  | PK | identity |  |
| 2 | `dataset_id` | smallint |  | FK |  |  |
| 3 | `version_label` | text |  |  |  |  |
| 4 | `release_order` | integer |  |  |  | position of the release within its dataset (1, 2, ...); valid_rel ranges are in these units |
| 5 | `release_date` | date | yes |  |  | official publication date; NULL when no date is confirmed (unknown valid time) |
| 6 | `manifest_hash` | character(64) | yes |  |  | SHA-256 over the checksums of the release's source files |
| 7 | `ingestion_status` | text |  |  | 'registered'::text | registered, loading, loaded or failed |
| 8 | `registered_at` | timestamp with time zone |  |  | now() |  |
| 9 | `loaded_at` | timestamp with time zone | yes |  |  |  |
| 10 | `description` | text | yes |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `release_pkey` | primary key | `PRIMARY KEY (release_id)` |
| `release_dataset_id_release_order_key` | unique | `UNIQUE (dataset_id, release_order)` |
| `release_dataset_id_version_label_key` | unique | `UNIQUE (dataset_id, version_label)` |
| `release_release_id_dataset_id_key` | unique | `UNIQUE (release_id, dataset_id)` |
| `release_dataset_id_fkey` | foreign key | `FOREIGN KEY (dataset_id) REFERENCES dataset(dataset_id) ON DELETE RESTRICT` |
| `release_check` | check | `CHECK (((ingestion_status <> 'loaded'::text) OR (loaded_at IS NOT NULL)))` |
| `release_ingestion_status_check` | check | `CHECK ((ingestion_status = ANY (ARRAY['registered'::text, 'loading'::text, 'loaded'::text, 'failed'::text])))` |
| `release_manifest_hash_check` | check | `CHECK ((manifest_hash ~ '^[0-9a-f]{64}$'::text))` |
| `release_release_order_check` | check | `CHECK ((release_order > 0))` |
| `release_version_label_check` | check | `CHECK ((version_label <> ''::text))` |

Indexes: `release_dataset_id_release_order_key` (btree, unique, 16.0 kB); `release_dataset_id_version_label_key` (btree, unique, 16.0 kB); `release_release_id_dataset_id_key` (btree, unique, 16.0 kB)


### `cc.schema_mapping`

How a source field maps to a canonical field.  
About 27 rows, 48.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `schema_mapping_id` | integer |  | PK | identity |  |
| 2 | `dataset_id` | smallint |  | FK |  |  |
| 3 | `source_file_pattern` | text |  |  |  |  |
| 4 | `source_field` | text |  |  |  |  |
| 5 | `canonical_entity` | text |  |  |  |  |
| 6 | `canonical_field` | text |  |  |  |  |
| 7 | `transform` | text |  |  | 'identity'::text |  |
| 8 | `notes` | text | yes |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `schema_mapping_pkey` | primary key | `PRIMARY KEY (schema_mapping_id)` |
| `schema_mapping_dataset_id_source_file_pattern_source_field__key` | unique | `UNIQUE (dataset_id, source_file_pattern, source_field, canonical_entity, canonical_field)` |
| `schema_mapping_dataset_id_fkey` | foreign key | `FOREIGN KEY (dataset_id) REFERENCES dataset(dataset_id)` |
| `schema_mapping_canonical_entity_check` | check | `CHECK ((canonical_entity = ANY (ARRAY['dataset_neuron'::text, 'annotation'::text, 'connection'::text, 'brain_region'::text, 'neuron_type'::…` |

Indexes: `schema_mapping_dataset_id_source_file_pattern_source_field__key` (btree, unique, 16.0 kB)


### `cc.shard`

A PostgreSQL server holding a fragment of the connectome.  
About 3 rows, 48.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `shard_id` | smallint |  | PK |  |  |
| 2 | `name` | text |  |  |  |  |
| 3 | `host` | text |  |  |  |  |
| 4 | `port` | integer |  |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `shard_pkey` | primary key | `PRIMARY KEY (shard_id)` |
| `shard_name_key` | unique | `UNIQUE (name)` |
| `shard_port_check` | check | `CHECK (((port >= 1) AND (port <= 65535)))` |
| `shard_shard_id_check` | check | `CHECK (((shard_id >= 1) AND (shard_id <= 64)))` |

Indexes: `shard_name_key` (btree, unique, 16.0 kB)


### `cc.shard_deployment`

One shard's copy of one strategy's placement.  
About 18 rows, 64.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `deployment_id` | integer |  | PK | identity |  |
| 2 | `strategy_id` | smallint |  | FK |  |  |
| 3 | `shard_id` | smallint |  | FK |  |  |
| 4 | `schema_name` | text |  |  |  |  |
| 5 | `neurons` | integer |  |  |  |  |
| 6 | `connections` | bigint |  |  |  |  |
| 7 | `mirrors` | bigint |  |  |  |  |
| 8 | `bytes_on_shard` | bigint | yes |  |  |  |
| 9 | `seconds` | double precision |  |  |  |  |
| 10 | `deployed_at` | timestamp with time zone |  |  | now() |  |
| 11 | `verified` | boolean |  |  | false |  |
| 12 | `batch_id` | bigint | yes | FK |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `shard_deployment_pkey` | primary key | `PRIMARY KEY (deployment_id)` |
| `shard_deployment_batch_id_fkey` | foreign key | `FOREIGN KEY (batch_id) REFERENCES deployment_batch(batch_id)` |
| `shard_deployment_batch_strategy_fkey` | foreign key | `FOREIGN KEY (batch_id, strategy_id) REFERENCES deployment_batch(batch_id, strategy_id)` |
| `shard_deployment_shard_id_fkey` | foreign key | `FOREIGN KEY (shard_id) REFERENCES shard(shard_id)` |
| `shard_deployment_strategy_id_fkey` | foreign key | `FOREIGN KEY (strategy_id) REFERENCES partition_strategy(strategy_id)` |
| `shard_deployment_bytes_on_shard_check` | check | `CHECK ((bytes_on_shard >= 0))` |
| `shard_deployment_connections_check` | check | `CHECK ((connections >= 0))` |
| `shard_deployment_mirrors_check` | check | `CHECK ((mirrors >= 0))` |
| `shard_deployment_neurons_check` | check | `CHECK ((neurons >= 0))` |
| `shard_deployment_seconds_check` | check | `CHECK ((seconds >= (0)::double precision))` |

Indexes: `shard_deployment_batch` (btree, 16.0 kB); `shard_deployment_strategy_idx` (btree, 16.0 kB)


### `cc.source_file`

A checksummed file (or archive member) a release was read from.  
About 17 rows, 64.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `source_file_id` | integer |  | PK | identity |  |
| 2 | `release_id` | integer |  | FK |  |  |
| 3 | `archive_path` | text |  |  |  |  |
| 4 | `member_path` | text |  |  | ''::text | path inside the archive ('' when the file is not in an archive) |
| 5 | `sha256` | character(64) |  |  |  | checksum of the file as downloaded |
| 6 | `size_bytes` | bigint |  |  |  |  |
| 7 | `source_url` | text | yes |  |  |  |
| 8 | `row_count` | bigint | yes |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `source_file_pkey` | primary key | `PRIMARY KEY (source_file_id)` |
| `source_file_id_release_key` | unique | `UNIQUE (source_file_id, release_id)` |
| `source_file_release_id_archive_path_member_path_key` | unique | `UNIQUE (release_id, archive_path, member_path)` |
| `source_file_release_id_fkey` | foreign key | `FOREIGN KEY (release_id) REFERENCES release(release_id)` |
| `source_file_row_count_check` | check | `CHECK ((row_count >= 0))` |
| `source_file_sha256_check` | check | `CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text))` |
| `source_file_size_bytes_check` | check | `CHECK ((size_bytes >= 0))` |

Indexes: `source_file_id_release_key` (btree, unique, 16.0 kB); `source_file_release_id_archive_path_member_path_key` (btree, unique, 16.0 kB)


### `cc.type_alias`

A dataset's type name linked to a canonical type (many-to-many).  
About 13,601 rows, 1.5 MB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `dataset_id` | smallint |  | PK FK |  |  |
| 2 | `source_type` | text |  | PK |  |  |
| 3 | `neuron_type_id` | integer |  | PK FK |  |  |
| 4 | `method` | text |  |  |  |  |
| 5 | `provenance_id` | bigint | yes | FK |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `type_alias_pkey` | primary key | `PRIMARY KEY (dataset_id, source_type, neuron_type_id)` |
| `type_alias_dataset_id_fkey` | foreign key | `FOREIGN KEY (dataset_id) REFERENCES dataset(dataset_id)` |
| `type_alias_neuron_type_id_fkey` | foreign key | `FOREIGN KEY (neuron_type_id) REFERENCES neuron_type(neuron_type_id)` |
| `type_alias_provenance_id_fkey` | foreign key | `FOREIGN KEY (provenance_id) REFERENCES provenance(provenance_id)` |
| `type_alias_method_check` | check | `CHECK ((method = ANY (ARRAY['identical_name'::text, 'published_crosswalk'::text, 'manual'::text])))` |


### `cc.workspace_job`

A background job (diff, scoring, matching, deployment).  
About 1 rows, 48.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `job_id` | uuid |  | PK |  |  |
| 2 | `kind` | text |  |  |  |  |
| 3 | `status` | text |  |  | 'queued'::text |  |
| 4 | `request` | jsonb |  |  |  |  |
| 5 | `result` | jsonb | yes |  |  |  |
| 6 | `progress` | integer |  |  | 0 |  |
| 7 | `message` | text |  |  | 'Waiting for a worker'::text |  |
| 8 | `created_at` | timestamp with time zone |  |  | now() |  |
| 9 | `updated_at` | timestamp with time zone |  |  | now() |  |

| Constraint | Kind | Definition |
|---|---|---|
| `workspace_job_pkey` | primary key | `PRIMARY KEY (job_id)` |
| `workspace_job_kind_check` | check | `CHECK ((kind = ANY (ARRAY['diff'::text, 'anomalies'::text, 'matching'::text, 'deploy'::text])))` |
| `workspace_job_progress_check` | check | `CHECK (((progress >= 0) AND (progress <= 100)))` |
| `workspace_job_status_check` | check | `CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text])))` |

Indexes: `workspace_job_recent` (btree, 16.0 kB)


## Schema `lab`


### `lab.change_log`

Audit trail written by trigger for every lab change.  
About 0 rows, 80.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `change_id` | bigint |  | PK | identity |  |
| 2 | `changed_at` | timestamp with time zone |  |  | clock_timestamp() |  |
| 3 | `changed_by` | text |  |  | CURRENT_USER |  |
| 4 | `table_name` | text |  |  |  |  |
| 5 | `operation` | text |  |  |  |  |
| 6 | `old_row` | jsonb | yes |  |  |  |
| 7 | `new_row` | jsonb | yes |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `change_log_pkey` | primary key | `PRIMARY KEY (change_id)` |
| `change_log_operation_check` | check | `CHECK ((operation = ANY (ARRAY['INSERT'::text, 'UPDATE'::text, 'DELETE'::text])))` |


### `lab.connection`

Synapse count of a neuron pair in one release (BCNF: no dataset_id copy).  
About 9,023 rows, 1.2 MB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `release_id` | integer |  | PK FK |  |  |
| 2 | `pre_neuron_id` | bigint |  | PK FK |  |  |
| 3 | `post_neuron_id` | bigint |  | PK FK |  |  |
| 4 | `synapse_count` | integer |  |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `connection_pkey` | primary key | `PRIMARY KEY (release_id, pre_neuron_id, post_neuron_id)` |
| `connection_post_neuron_id_fkey` | foreign key | `FOREIGN KEY (post_neuron_id) REFERENCES lab.neuron(neuron_id) ON UPDATE CASCADE ON DELETE CASCADE` |
| `connection_pre_neuron_id_fkey` | foreign key | `FOREIGN KEY (pre_neuron_id) REFERENCES lab.neuron(neuron_id) ON UPDATE CASCADE ON DELETE CASCADE` |
| `connection_release_id_fkey` | foreign key | `FOREIGN KEY (release_id) REFERENCES lab.release(release_id) ON DELETE CASCADE` |
| `connection_synapse_count_check` | check | `CHECK ((synapse_count > 0))` |
| `connection_same_dataset` | trigger | `TRIGGER` |

Indexes: `connection_post_idx` (btree, 104.0 kB)

Triggers: `connection_log`; `connection_same_dataset` (constraint trigger)


### `lab.dataset`

Editable copy of cc.dataset (key columns).  
About 2 rows, 80.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `dataset_id` | smallint |  | PK | identity |  |
| 2 | `code` | text |  |  |  |  |
| 3 | `name` | text |  |  |  |  |
| 4 | `organism` | text | yes |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `dataset_pkey` | primary key | `PRIMARY KEY (dataset_id)` |
| `dataset_code_key` | unique | `UNIQUE (code)` |
| `dataset_code_check` | check | `CHECK ((code ~ '^[a-z][a-z0-9_]*$'::text))` |
| `dataset_name_check` | check | `CHECK ((name <> ''::text))` |

Indexes: `dataset_code_key` (btree, unique, 16.0 kB)

Triggers: `dataset_log`


### `lab.neuron`

Editable neurons with their current cell type, side and primary region.  
About 360 rows, 144.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `neuron_id` | bigint |  | PK | identity |  |
| 2 | `dataset_id` | smallint |  | FK |  |  |
| 3 | `source_neuron_id` | text |  |  |  |  |
| 4 | `cell_type` | text | yes |  |  |  |
| 5 | `side` | text | yes |  |  |  |
| 6 | `primary_region` | text | yes |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `neuron_pkey` | primary key | `PRIMARY KEY (neuron_id)` |
| `neuron_dataset_id_source_neuron_id_key` | unique | `UNIQUE (dataset_id, source_neuron_id)` |
| `neuron_dataset_id_fkey` | foreign key | `FOREIGN KEY (dataset_id) REFERENCES lab.dataset(dataset_id) ON DELETE RESTRICT` |
| `neuron_side_check` | check | `CHECK ((side = ANY (ARRAY['L'::text, 'R'::text, 'center'::text])))` |
| `neuron_source_neuron_id_check` | check | `CHECK ((source_neuron_id <> ''::text))` |
| `neuron_same_dataset` | trigger | `TRIGGER` |

Indexes: `neuron_cell_type_idx` (btree, 16.0 kB); `neuron_dataset_id_source_neuron_id_key` (btree, unique, 40.0 kB)

Triggers: `neuron_log`; `neuron_same_dataset` (constraint trigger)


### `lab.release`

Editable copy of the loaded releases.  
About 5 rows, 96.0 kB with indexes.

| # | Column | Type | Null | Key | Default | Description |
|---|---|---|---|---|---|---|
| 1 | `release_id` | integer |  | PK | identity |  |
| 2 | `dataset_id` | smallint |  | FK |  |  |
| 3 | `version_label` | text |  |  |  |  |
| 4 | `release_order` | integer |  |  |  |  |

| Constraint | Kind | Definition |
|---|---|---|
| `release_pkey` | primary key | `PRIMARY KEY (release_id)` |
| `release_dataset_id_release_order_key` | unique | `UNIQUE (dataset_id, release_order)` |
| `release_dataset_id_version_label_key` | unique | `UNIQUE (dataset_id, version_label)` |
| `release_dataset_id_fkey` | foreign key | `FOREIGN KEY (dataset_id) REFERENCES lab.dataset(dataset_id) ON DELETE RESTRICT` |
| `release_release_order_check` | check | `CHECK ((release_order > 0))` |
| `release_version_label_check` | check | `CHECK ((version_label <> ''::text))` |
| `release_same_dataset` | trigger | `TRIGGER` |

Indexes: `release_dataset_id_release_order_key` (btree, unique, 16.0 kB); `release_dataset_id_version_label_key` (btree, unique, 16.0 kB)

Triggers: `release_log`; `release_same_dataset` (constraint trigger)


## Views

| View | Kind | Description |
|---|---|---|
| `cc.brain_region_label` | view |  |
| `cc.connection_history` | view |  |
| `cc.diff_event_detail` | view |  |
| `cc.neuron_latest_region` | view |  |
| `cc.neuron_primary_region` | view |  |
| `cc.release_overview` | view |  |
| `cc.release_stats` | materialized |  |
| `lab.neuron_degree` | view |  |

## Functions

| Function | Returns | Language | Volatility |
|---|---|---|---|
| `cc.annotation_history(p_neuron bigint)` | TABLE(annotation_key text, annotation_value text,… | sql | stable |
| `cc.assign_source_identity(p_dataset text)` | bigint | sql | volatile |
| `cc.build_type_crosswalk()` | jsonb | plpgsql | volatile |
| `cc.connections_at_release(p_neuron bigint, p_release integer, p_as_of timestamp with time zone,…)` | TABLE(direction text, partner_neuron_id bigint, p… | sql | stable |
| `cc.find_neuron(p_dataset text, p_source_id text)` | bigint | sql | stable |
| `cc.guard_history()` | trigger | plpgsql | volatile |
| `cc.neuron_as_of(p_neuron bigint, p_as_of timestamp with time zone)` | TABLE(annotation_key text, annotation_value text,… | sql | stable |
| `cc.neuron_at_release(p_neuron bigint, p_release integer, p_as_of timestamp with time zone)` | TABLE(dataset_neuron_id bigint, source_neuron_id … | sql | stable |
| `cc.partition_quality(p_strategy text)` | TABLE(shard_id smallint, neurons bigint, owned_ro… | plpgsql | stable |
| `cc.propose_crosswalk_mappings(p_fw_release integer, p_hb_release integer, p_max_candidates integer)` | jsonb | plpgsql | volatile |
| `cc.release_label(p_dataset smallint, p_order integer)` | text | sql | stable |
| `cc.review_mapping(p_mapping bigint, p_decision text, p_reviewer text)` | void | plpgsql | volatile |
| `cc.run_temporal_diff(p_release_a integer, p_release_b integer, p_as_of timestamp with time…)` | bigint | plpgsql | volatile |
| `cc.trace_provenance(p_table text, p_id bigint)` | jsonb | plpgsql | stable |
| `lab.check_connection_dataset()` | trigger | plpgsql | volatile |
| `lab.log_change()` | trigger | plpgsql | volatile |
| `lab.same_dataset_violation(p_release integer, p_pre bigint, p_post bigint)` | boolean | sql | stable |

## MongoDB collections


### `connectome_meta.neuron_source_record`

182,274 documents. No server-side validator: each document keeps every original source column, so the shape differs per dataset; the loader writes them and `metadata check` verifies them against PostgreSQL.

| Index | Keys | Unique | Partial filter |
|---|---|---|---|
| `_id_` | `{"_id": 1}` |  |  |
| `dataset_1_source_neuron_id_1` | `{"dataset": 1, "source_neuron_id": 1}` | yes |  |
| `dataset_neuron_id_1` | `{"dataset_neuron_id": 1}` | yes |  |

### `connectome_meta_lab.neuron_source_record`

360 documents. Validator: `{"$jsonSchema": {"bsonType": "object", "required": ["dataset", "source_neuron_id", "dataset_neuron_id"], "properties": {"dataset": {"bsonType": "string", "description": "dataset code, e.g. hemibrain"}, "source_neuron_id": {"bsonType": "string", "description": "the id used by the source, as text"}, "dataset_neuron_id": {"bsonType": ["int", "long"], "description": "key of the neuron in PostgreSQL"}, "releases": {"bsonType": "object", "description": "original source rows per release id"}}}}`.

| Index | Keys | Unique | Partial filter |
|---|---|---|---|
| `_id_` | `{"_id": 1}` |  |  |
| `dataset_1_source_neuron_id_1` | `{"dataset": 1, "source_neuron_id": 1}` | yes |  |
| `dataset_neuron_id_1` | `{"dataset_neuron_id": 1}` | yes |  |
<!-- /generated:dictionary -->
