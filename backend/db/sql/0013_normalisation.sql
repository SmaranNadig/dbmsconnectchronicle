-- Normalisation of the core schema (docs/NORMALISATION.md).
--
-- The functional-dependency analysis of every relation
-- (backend/design/relations.py) found three kinds of issue; this migration
-- removes or enforces each of them:
--
--  1. brain_region: canonical_code -> super_region is a partial dependency on
--     the candidate key (canonical_code, hemisphere), so the super region was
--     stored once per hemisphere (2NF violation). Decomposed into neuropil
--     (canonical_code, super_region) and brain_region without super_region.
--  2. ml_model_run.diff_run_ids bigint[]: a list of references to diff_run in
--     one column (a repeating group, 1NF) that no foreign key could check.
--     Decomposed into ml_model_input (model_run_id, diff_run_id).
--  3. Dependencies that held in the data but were not enforced:
--       provenance:            source_file_id -> release_id, ingestion_run_id -> release_id
--       shard_deployment:      batch_id -> strategy_id
--       active_deployment:     batch_id -> strategy_id (and batch_id is a key)
--       partition_assignment:  (strategy_id, group_key) -> shard_id
--       dataset_neuron:        metadata_doc_id is a candidate key
--     Each is now a constraint, so a copy can never disagree with its source.
SET search_path = cc, public;

-- 1. brain_region -> neuropil + brain_region ---------------------------------
CREATE TABLE neuropil (
    canonical_code  text PRIMARY KEY CHECK (canonical_code ~ '^[A-Z][A-Z0-9_]*$'),
    super_region    text NOT NULL CHECK (super_region IN ('optic', 'central', 'vnc', 'other'))
);
INSERT INTO neuropil (canonical_code, super_region)
SELECT DISTINCT canonical_code, super_region FROM brain_region;   -- fails if a code had two super regions

ALTER TABLE brain_region
    ADD CONSTRAINT brain_region_neuropil_fkey FOREIGN KEY (canonical_code) REFERENCES neuropil;

-- same columns as before, so every query that reads the view is unchanged
CREATE OR REPLACE VIEW brain_region_label AS
SELECT b.brain_region_id, b.canonical_code || coalesce('_' || b.hemisphere, '') AS code, b.name, n.super_region
FROM brain_region b JOIN neuropil n USING (canonical_code);

ALTER TABLE brain_region DROP COLUMN super_region;

-- 2. ml_model_run.diff_run_ids -> ml_model_input ------------------------------
CREATE TABLE ml_model_input (
    model_run_id  bigint NOT NULL REFERENCES ml_model_run ON DELETE CASCADE,
    diff_run_id   bigint NOT NULL REFERENCES diff_run,
    PRIMARY KEY (model_run_id, diff_run_id)
);
CREATE INDEX ml_model_input_diff_idx ON ml_model_input (diff_run_id);
INSERT INTO ml_model_input (model_run_id, diff_run_id)
SELECT DISTINCT m.model_run_id, d.diff_run_id
FROM ml_model_run m CROSS JOIN LATERAL unnest(m.diff_run_ids) d(diff_run_id);
ALTER TABLE ml_model_run DROP COLUMN diff_run_ids;

-- 3. enforce the remaining dependencies ----------------------------------------
-- provenance: the source file and the ingestion run belong to the same release
ALTER TABLE source_file   ADD CONSTRAINT source_file_id_release_key UNIQUE (source_file_id, release_id);
ALTER TABLE ingestion_run ADD CONSTRAINT ingestion_run_id_release_key UNIQUE (run_id, release_id);
ALTER TABLE provenance
    ADD CONSTRAINT provenance_source_file_release_fkey
        FOREIGN KEY (source_file_id, release_id) REFERENCES source_file (source_file_id, release_id),
    ADD CONSTRAINT provenance_run_release_fkey
        FOREIGN KEY (ingestion_run_id, release_id) REFERENCES ingestion_run (run_id, release_id);

-- shard_deployment: a deployment row belongs to its batch's strategy
ALTER TABLE deployment_batch ADD CONSTRAINT deployment_batch_id_strategy_key UNIQUE (batch_id, strategy_id);
ALTER TABLE shard_deployment
    ADD CONSTRAINT shard_deployment_batch_strategy_fkey
        FOREIGN KEY (batch_id, strategy_id) REFERENCES deployment_batch (batch_id, strategy_id);
ALTER TABLE active_deployment
    ADD CONSTRAINT active_deployment_batch_key UNIQUE (batch_id),
    ADD CONSTRAINT active_deployment_batch_strategy_fkey
        FOREIGN KEY (batch_id, strategy_id) REFERENCES deployment_batch (batch_id, strategy_id);

-- partition_assignment: all neurons of one group are placed on one shard
-- (an exclusion constraint states the functional dependency directly)
ALTER TABLE partition_assignment
    ADD CONSTRAINT partition_assignment_group_one_shard
        EXCLUDE USING gist (strategy_id WITH =, group_key WITH =, shard_id WITH <>);

-- dataset_neuron: the MongoDB document id identifies the neuron
ALTER TABLE dataset_neuron ADD CONSTRAINT dataset_neuron_metadata_doc_id_key UNIQUE (metadata_doc_id);

GRANT SELECT ON neuropil, ml_model_input TO cc_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON neuropil, ml_model_input TO cc_tryout;
