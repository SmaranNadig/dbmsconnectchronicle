"""Declared attributes and functional dependencies of every stored relation.

This is the design statement that the normalisation analysis
(backend/design/normalisation.py) works from. Tests check that it covers
every base table of schemas cc and lab with exactly the live columns, that
every computed candidate key is enforced by a PRIMARY KEY or UNIQUE
constraint, that every constraint named in `enforced_by` exists, and that
every dependency holds on the loaded data.

Conventions
  fds         'X -> Y' strings. A key is written as key -> every other attribute.
              A nullable left side means: the dependency holds where it is not NULL.
  redundancy  dependencies that are kept on purpose although they break 3NF or
              BCNF: which constraint makes the copy impossible to get wrong,
              and why it is stored.
  atomic      columns of a non-scalar type (range, array, jsonb) and why each
              value is a single atomic value of its domain (1NF).
  temporal    keys that hold per point of valid and transaction time; enforced
              by exclusion constraints, not expressible as plain FDs.
"""
from __future__ import annotations

ALL = "*"   # stands for "every attribute not on the left side"

RANGE_NOTE = "one interval of its range type, compared and indexed as a single value"
JSON_NOTE = ("an opaque document (run parameters / statistics) stored and read as a whole; "
             "no relational rule depends on a key inside it")

RELATIONS: list[dict] = [
    # ------------------------------------------------------------- sources
    {"schema": "cc", "name": "dataset", "group": "Sources",
     "purpose": "A public connectome (hemibrain, FlyWire).",
     "attrs": ["dataset_id", "code", "name", "organism", "description", "source_url", "license", "created_at",
               "organization", "repository", "data_formats"],
     "fds": ["dataset_id -> *", "code -> *"],
     "atomic": {"data_formats": "the list of file formats the publisher offers, a descriptive value of the "
                                "dataset as a whole (never queried by element)"}},
    {"schema": "cc", "name": "release", "group": "Sources",
     "purpose": "One published version of a dataset, ordered within the dataset.",
     "attrs": ["release_id", "dataset_id", "version_label", "release_order", "release_date", "manifest_hash",
               "ingestion_status", "registered_at", "loaded_at", "description"],
     "fds": ["release_id -> *", "dataset_id, version_label -> *", "dataset_id, release_order -> *"]},
    {"schema": "cc", "name": "ingestion_run", "group": "Sources",
     "purpose": "One attempt to load a release.",
     "attrs": ["run_id", "release_id", "pipeline_version", "status", "started_at", "finished_at", "stats",
               "error_message"],
     "fds": ["run_id -> *"], "atomic": {"stats": JSON_NOTE}},
    {"schema": "cc", "name": "source_file", "group": "Sources",
     "purpose": "A checksummed file (or archive member) a release was read from.",
     "attrs": ["source_file_id", "release_id", "archive_path", "member_path", "sha256", "size_bytes",
               "source_url", "row_count"],
     "fds": ["source_file_id -> *", "release_id, archive_path, member_path -> *"]},
    {"schema": "cc", "name": "provenance", "group": "Sources",
     "purpose": "How a set of facts was produced: run, source file, transformation.",
     "attrs": ["provenance_id", "dataset_id", "release_id", "source_file_id", "ingestion_run_id",
               "transformation_name", "transformation_version", "parameters", "created_at"],
     "fds": ["provenance_id -> *", "release_id -> dataset_id", "source_file_id -> release_id",
             "ingestion_run_id -> release_id"],
     "atomic": {"parameters": JSON_NOTE},
     "redundancy": [
         {"fd": "release_id -> dataset_id", "enforced_by": ["provenance_release_id_dataset_id_fkey"],
          "why": "the composite foreign key (release_id, dataset_id) → release makes the copy match its release; "
                 "provenance is filtered by dataset without a join"},
         {"fd": "source_file_id -> release_id",
          "enforced_by": ["provenance_source_file_release_fkey"],
          "why": "release_id is needed for derived facts that have no source file (source_file_id NULL); "
                 "where a file is given, the composite key forces the same release"},
         {"fd": "ingestion_run_id -> release_id", "enforced_by": ["provenance_run_release_fkey"],
          "why": "release_id is needed for facts that were not produced by an ingestion run; where a run is given, "
                 "the composite key forces the same release"}]},
    {"schema": "cc", "name": "provenance_parent", "group": "Sources",
     "purpose": "Lineage: which provenance records a derived record was computed from.",
     "attrs": ["provenance_id", "parent_id"], "fds": []},
    {"schema": "cc", "name": "schema_mapping", "group": "Sources",
     "purpose": "How a source field maps to a canonical field.",
     "attrs": ["schema_mapping_id", "dataset_id", "source_file_pattern", "source_field", "canonical_entity",
               "canonical_field", "transform", "notes"],
     "fds": ["schema_mapping_id -> *",
             "dataset_id, source_file_pattern, source_field, canonical_entity, canonical_field -> *"]},
    # --------------------------------------------------------- vocabulary
    {"schema": "cc", "name": "neuropil", "group": "Vocabulary",
     "purpose": "A canonical neuropil code and the super region it belongs to (split from brain_region, 0013).",
     "attrs": ["canonical_code", "super_region"], "fds": ["canonical_code -> *"]},
    {"schema": "cc", "name": "brain_region", "group": "Vocabulary",
     "purpose": "A neuropil in one hemisphere (NULL = unpaired).",
     "attrs": ["brain_region_id", "canonical_code", "hemisphere", "name"],
     "fds": ["brain_region_id -> *", "canonical_code, hemisphere -> *"]},
    {"schema": "cc", "name": "brain_region_alias", "group": "Vocabulary",
     "purpose": "A dataset's own region code mapped to the canonical region.",
     "attrs": ["dataset_id", "source_code", "brain_region_id"], "fds": ["dataset_id, source_code -> *"]},
    {"schema": "cc", "name": "neuron_type", "group": "Vocabulary",
     "purpose": "A canonical cell type name.",
     "attrs": ["neuron_type_id", "name"], "fds": ["neuron_type_id -> *", "name -> *"]},
    {"schema": "cc", "name": "type_alias", "group": "Vocabulary",
     "purpose": "A dataset's type name linked to a canonical type (many-to-many).",
     "attrs": ["dataset_id", "source_type", "neuron_type_id", "method", "provenance_id"],
     "fds": ["dataset_id, source_type, neuron_type_id -> *"]},
    # ---------------------------------------------------- source neurons
    {"schema": "cc", "name": "dataset_neuron", "group": "Connectome",
     "purpose": "A neuron as identified by its dataset (original id kept as text).",
     "attrs": ["dataset_neuron_id", "dataset_id", "source_neuron_id", "first_release_id", "provenance_id",
               "source_line", "metadata_doc_id"],
     "fds": ["dataset_neuron_id -> *", "dataset_id, source_neuron_id -> *", "metadata_doc_id -> *",
             "first_release_id -> dataset_id"],
     "redundancy": [
         {"fd": "first_release_id -> dataset_id", "enforced_by": ["dataset_neuron_first_release_id_dataset_id_fkey"],
          "why": "dataset_id is part of the natural key (dataset_id, source_neuron_id), so the relation is in "
                 "3NF; the composite key to release keeps the first release inside the same dataset"}]},
    {"schema": "cc", "name": "neuron_version", "group": "Connectome",
     "purpose": "Existence of a neuron over a range of releases (bitemporal).",
     "attrs": ["neuron_version_id", "dataset_neuron_id", "valid_rel", "tx", "provenance_id", "source_line"],
     "fds": ["neuron_version_id -> *"],
     "atomic": {"valid_rel": RANGE_NOTE, "tx": RANGE_NOTE},
     "temporal": {"key": "dataset_neuron_id at each point of (valid_rel, tx)",
                  "enforced_by": "neuron_version_dataset_neuron_id_valid_rel_tx_excl"}},
    {"schema": "cc", "name": "annotation", "group": "Connectome",
     "purpose": "One attribute value (cell type, side, ...) of a neuron over a range of releases.",
     "attrs": ["annotation_id", "dataset_neuron_id", "annotation_key", "annotation_value", "valid_rel", "tx",
               "provenance_id", "source_line"],
     "fds": ["annotation_id -> *"],
     "atomic": {"valid_rel": RANGE_NOTE, "tx": RANGE_NOTE},
     "temporal": {"key": "(dataset_neuron_id, annotation_key) at each point of (valid_rel, tx) → annotation_value",
                  "enforced_by": "annotation_dataset_neuron_id_annotation_key_valid_rel_tx_excl"}},
    {"schema": "cc", "name": "connection", "group": "Connectome",
     "purpose": "Synapse count between two neurons over a range of releases (bitemporal).",
     "attrs": ["connection_id", "dataset_id", "pre_neuron_id", "post_neuron_id", "synapse_count", "valid_rel",
               "tx", "provenance_id", "source_lines"],
     "fds": ["connection_id -> *", "pre_neuron_id -> dataset_id", "post_neuron_id -> dataset_id"],
     "atomic": {"valid_rel": RANGE_NOTE, "tx": RANGE_NOTE,
                "source_lines": "the line numbers of the source rows that were summed into this edge: one "
                                "evidence value, written once and read as a whole (never searched by element)"},
     "temporal": {"key": "(pre_neuron_id, post_neuron_id) at each point of (valid_rel, tx)",
                  "enforced_by": "connection_pre_neuron_id_post_neuron_id_valid_rel_tx_excl"},
     "redundancy": [
         {"fd": "pre_neuron_id -> dataset_id", "enforced_by": ["connection_pre_neuron_id_dataset_id_fkey"],
          "why": "the composite foreign keys (pre, dataset) and (post, dataset) → dataset_neuron are what makes "
                 "an edge between two different connectomes impossible; the copy also serves the partial index "
                 "connection_dataset_open_idx that every per-dataset scan of 9 M rows uses"},
         {"fd": "post_neuron_id -> dataset_id", "enforced_by": ["connection_post_neuron_id_dataset_id_fkey"],
          "why": "the composite foreign key (post_neuron_id, dataset_id) → dataset_neuron keeps the "
                 "post-synaptic neuron in the edge's dataset"}]},
    {"schema": "cc", "name": "neuron_region_synapses", "group": "Connectome",
     "purpose": "Per release, how many synapses a neuron has in each brain region.",
     "attrs": ["release_id", "dataset_id", "dataset_neuron_id", "brain_region_id", "pre_synapses",
               "post_synapses", "provenance_id"],
     "fds": ["release_id, dataset_neuron_id, brain_region_id -> *", "release_id -> dataset_id",
             "dataset_neuron_id -> dataset_id"],
     "redundancy": [
         {"fd": "release_id -> dataset_id", "enforced_by": ["neuron_region_synapses_release_id_dataset_id_fkey"],
          "why": "the two composite foreign keys force the release and the neuron into the same dataset"},
         {"fd": "dataset_neuron_id -> dataset_id",
          "enforced_by": ["neuron_region_synapses_dataset_neuron_id_dataset_id_fkey"],
          "why": "the composite foreign key (dataset_neuron_id, dataset_id) → dataset_neuron keeps the neuron in "
                 "the release's dataset"}]},
    # ------------------------------------------------- identity & mapping
    {"schema": "cc", "name": "canonical_neuron", "group": "Identity",
     "purpose": "A neuron identity across datasets.",
     "attrs": ["canonical_neuron_id", "status", "neuron_type_id", "label", "created_at"],
     "fds": ["canonical_neuron_id -> *"]},
    {"schema": "cc", "name": "neuron_mapping", "group": "Identity",
     "purpose": "A proposed or reviewed link from a dataset neuron to a canonical neuron.",
     "attrs": ["mapping_id", "dataset_neuron_id", "canonical_neuron_id", "method", "confidence", "review_status",
               "evidence", "evidence_doc_id", "model_version", "provenance_id", "mapped_at", "reviewed_at",
               "reviewed_by", "score_details"],
     "fds": ["mapping_id -> *"],
     "atomic": {"evidence": "the matcher's evidence for one proposal, read as a whole by the reviewer",
                "score_details": JSON_NOTE}},
    # ---------------------------------------------------- ConnectomeDiff
    {"schema": "cc", "name": "diff_run", "group": "ConnectomeDiff",
     "purpose": "One comparison of two release states at a transaction time.",
     "attrs": ["diff_run_id", "mode", "dataset_a", "release_a", "dataset_b", "release_b", "as_of", "parameters",
               "status", "started_at", "finished_at", "stats"],
     "fds": ["diff_run_id -> *", "release_a -> dataset_a", "release_b -> dataset_b"],
     "atomic": {"parameters": JSON_NOTE, "stats": JSON_NOTE},
     "redundancy": [
         {"fd": "release_a -> dataset_a", "enforced_by": ["diff_run_release_a_dataset_a_fkey", "diff_run_check1"],
          "why": "the CHECK constraints that keep temporal evolution (same dataset) apart from cross-dataset "
                 "comparison need both dataset ids in the row; the composite key keeps them correct"},
         {"fd": "release_b -> dataset_b", "enforced_by": ["diff_run_release_b_dataset_b_fkey"],
          "why": "the composite foreign key (release_b, dataset_b) → release keeps the copy correct for the "
                 "second release"}]},
    {"schema": "cc", "name": "diff_event", "group": "ConnectomeDiff",
     "purpose": "One change found by a diff run (added, removed, modified).",
     "attrs": ["diff_event_id", "diff_run_id", "entity_type", "change_type", "dataset_neuron_id",
               "post_neuron_id", "annotation_key", "old_value", "new_value", "old_weight", "new_weight",
               "weight_delta", "record_a_id", "record_b_id"],
     "fds": ["diff_event_id -> *", "old_weight, new_weight -> weight_delta"],
     "redundancy": [
         {"fd": "old_weight, new_weight -> weight_delta", "enforced_by": ["GENERATED ALWAYS AS (stored)"],
          "why": "a generated column: PostgreSQL computes it and rejects any write, so it cannot disagree; "
                 "stored so that events can be ranked by change size with an index"}]},
    # ------------------------------------------------------ distribution
    {"schema": "cc", "name": "shard", "group": "Distribution",
     "purpose": "A PostgreSQL server holding a fragment of the connectome.",
     "attrs": ["shard_id", "name", "host", "port"], "fds": ["shard_id -> *", "name -> *"]},
    {"schema": "cc", "name": "partition_strategy", "group": "Distribution",
     "purpose": "A way of placing neurons on shards (hash, anatomical, community).",
     "attrs": ["strategy_id", "code", "description", "parameters", "created_at"],
     "fds": ["strategy_id -> *", "code -> *"], "atomic": {"parameters": JSON_NOTE}},
    {"schema": "cc", "name": "partition_assignment", "group": "Distribution",
     "purpose": "The shard (and group) of every neuron under every strategy.",
     "attrs": ["strategy_id", "dataset_neuron_id", "shard_id", "assigned_at", "group_key"],
     "fds": ["strategy_id, dataset_neuron_id -> *", "strategy_id, group_key -> shard_id"],
     "redundancy": [
         {"fd": "strategy_id, group_key -> shard_id", "enforced_by": ["partition_assignment_group_one_shard"],
          "why": "the router looks up one neuron's shard by key; hash placement has no group, so shard_id cannot "
                 "move to a group table. The exclusion constraint (strategy =, group =, shard <>) states the "
                 "dependency itself"}]},
    {"schema": "cc", "name": "query_execution", "group": "Distribution",
     "purpose": "A routed query's measured cost.",
     "attrs": ["query_execution_id", "strategy_id", "query_kind", "parameters", "shards_touched",
               "rows_returned", "bytes_transferred", "latency_ms", "executed_at"],
     "fds": ["query_execution_id -> *"], "atomic": {"parameters": JSON_NOTE}},
    {"schema": "cc", "name": "shard_deployment", "group": "Distribution",
     "purpose": "One shard's copy of one strategy's placement.",
     "attrs": ["deployment_id", "strategy_id", "shard_id", "schema_name", "neurons", "connections", "mirrors",
               "bytes_on_shard", "seconds", "deployed_at", "verified", "batch_id"],
     "fds": ["deployment_id -> *", "batch_id -> strategy_id"],
     "redundancy": [
         {"fd": "batch_id -> strategy_id", "enforced_by": ["shard_deployment_batch_strategy_fkey"],
          "why": "rows from before deployment batches existed have no batch (batch_id NULL) and still need "
                 "their strategy; the composite key keeps newer rows consistent with their batch"}]},
    {"schema": "cc", "name": "deployment_batch", "group": "Distribution",
     "purpose": "One generation of shard copies for a strategy.",
     "attrs": ["batch_id", "strategy_id", "source_revision", "placement_revision", "status", "details",
               "created_at"],
     "fds": ["batch_id -> *"], "atomic": {"details": JSON_NOTE}},
    {"schema": "cc", "name": "active_deployment", "group": "Distribution",
     "purpose": "The batch the router currently reads, per strategy.",
     "attrs": ["strategy_id", "batch_id"], "fds": ["strategy_id -> *", "batch_id -> *"]},
    # ------------------------------------------------------------- models
    {"schema": "cc", "name": "ml_model_run", "group": "Analysis",
     "purpose": "One trained change-scoring model.",
     "attrs": ["model_run_id", "model_kind", "model_version", "parameters", "feature_schema", "random_seed",
               "artifact_path", "artifact_sha256", "created_at", "metrics"],
     "fds": ["model_run_id -> *", "model_kind, model_version -> *"],
     "atomic": {"parameters": JSON_NOTE, "feature_schema": JSON_NOTE, "metrics": JSON_NOTE}},
    {"schema": "cc", "name": "ml_model_input", "group": "Analysis",
     "purpose": "The diff runs a model was trained on (was the array ml_model_run.diff_run_ids, 0013).",
     "attrs": ["model_run_id", "diff_run_id"], "fds": []},
    {"schema": "cc", "name": "anomaly_result", "group": "Analysis",
     "purpose": "A model's score for one diff event.",
     "attrs": ["model_run_id", "diff_event_id", "score", "is_flagged", "score_rank", "features", "explanation",
               "baseline_flagged"],
     "fds": ["model_run_id, diff_event_id -> *"], "atomic": {"features": "the feature vector of one scored event"}},
    # ------------------------------------------------------ document sync
    {"schema": "cc", "name": "metadata_sync", "group": "Document store",
     "purpose": "One synchronisation of a release's source records into MongoDB.",
     "attrs": ["metadata_sync_id", "release_id", "mongo_database", "mongo_collection", "pipeline_version",
               "status", "started_at", "finished_at", "documents_written", "records_written", "stats",
               "error_message"],
     "fds": ["metadata_sync_id -> *"], "atomic": {"stats": JSON_NOTE}},
    {"schema": "cc", "name": "metadata_record_digest", "group": "Document store",
     "purpose": "Expected count and checksum of a neuron's source records per release.",
     "attrs": ["dataset_neuron_id", "release_id", "record_count", "sha256"],
     "fds": ["dataset_neuron_id, release_id -> *"]},
    {"schema": "cc", "name": "workspace_job", "group": "Workspace",
     "purpose": "A background job (diff, scoring, matching, deployment).",
     "attrs": ["job_id", "kind", "status", "request", "result", "progress", "message", "created_at", "updated_at"],
     "fds": ["job_id -> *"],
     "atomic": {"request": "the job's request body, replayed as a whole", "result": "the job's result body"}},
    # ---------------------------------------------------------------- lab
    {"schema": "lab", "name": "dataset", "group": "Lab",
     "purpose": "Editable copy of cc.dataset (key columns).",
     "attrs": ["dataset_id", "code", "name", "organism"], "fds": ["dataset_id -> *", "code -> *"]},
    {"schema": "lab", "name": "release", "group": "Lab",
     "purpose": "Editable copy of the loaded releases.",
     "attrs": ["release_id", "dataset_id", "version_label", "release_order"],
     "fds": ["release_id -> *", "dataset_id, version_label -> *", "dataset_id, release_order -> *"]},
    {"schema": "lab", "name": "neuron", "group": "Lab",
     "purpose": "Editable neurons with their current cell type, side and primary region.",
     "attrs": ["neuron_id", "dataset_id", "source_neuron_id", "cell_type", "side", "primary_region"],
     "fds": ["neuron_id -> *", "dataset_id, source_neuron_id -> *"]},
    {"schema": "lab", "name": "connection", "group": "Lab",
     "purpose": "Synapse count of a neuron pair in one release (BCNF: no dataset_id copy).",
     "attrs": ["release_id", "pre_neuron_id", "post_neuron_id", "synapse_count"],
     "fds": ["release_id, pre_neuron_id, post_neuron_id -> *"],
     "rules": [{"rule": "the release and both neurons belong to one dataset",
                "enforced_by": "connection_same_dataset (constraint trigger)"}]},
    {"schema": "lab", "name": "change_log", "group": "Lab",
     "purpose": "Audit trail written by trigger for every lab change.",
     "attrs": ["change_id", "changed_at", "changed_by", "table_name", "operation", "old_row", "new_row"],
     "fds": ["change_id -> *"],
     "atomic": {"old_row": "a snapshot of the whole row before the change", "new_row": "the row after it"}},
]


def qualified(rel: dict) -> str:
    return f"{rel['schema']}.{rel['name']}"


def by_name(name: str) -> dict:
    schema, _, table = name.rpartition(".")
    for rel in RELATIONS:
        if rel["name"] == table and rel["schema"] == (schema or "cc"):
            return rel
    raise KeyError(name)
