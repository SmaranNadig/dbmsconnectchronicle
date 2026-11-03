-- ConnectomeChronicle core relational schema (PostgreSQL 16).
--
-- Temporal model (bitemporal):
--   valid_rel  int4range  -- VALID time, measured in release_order units of the
--                            row's dataset: [first release it holds in, first
--                            release it no longer holds in). Upper bound NULL
--                            = still holds in the newest loaded release.
--   tx         tstzrange  -- TRANSACTION time: when the database believed the
--                            row. Upper bound NULL = current belief.
-- History is never deleted or rewritten: a row's belief is retired by closing
-- its tx interval and a corrected row is inserted (enforced by trigger).
-- Calendar valid time comes from release.release_date, which stays NULL when
-- no official date is confirmed ("unknown valid time" is kept distinct from
-- transaction time).

CREATE SCHEMA IF NOT EXISTS cc;
SET search_path = cc, public;
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- ---------------------------------------------------------------- sources
CREATE TABLE dataset (
    dataset_id   smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code         text NOT NULL UNIQUE CHECK (code ~ '^[a-z][a-z0-9_]*$'),
    name         text NOT NULL,
    organism     text,
    description  text,
    source_url   text,
    license      text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE release (
    release_id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id        smallint NOT NULL REFERENCES dataset ON DELETE RESTRICT,
    version_label     text NOT NULL CHECK (version_label <> ''),
    release_order     integer NOT NULL CHECK (release_order > 0),
    release_date      date,
    manifest_hash     char(64) CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    ingestion_status  text NOT NULL DEFAULT 'registered'
                      CHECK (ingestion_status IN ('registered', 'loading', 'loaded', 'failed')),
    registered_at     timestamptz NOT NULL DEFAULT now(),
    loaded_at         timestamptz,
    UNIQUE (dataset_id, version_label),
    UNIQUE (dataset_id, release_order),
    UNIQUE (release_id, dataset_id),            -- target of composite FKs
    CHECK (ingestion_status <> 'loaded' OR loaded_at IS NOT NULL)
);

CREATE TABLE ingestion_run (
    run_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_id        integer NOT NULL REFERENCES release,
    pipeline_version  text NOT NULL,
    status            text NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at       timestamptz,
    stats             jsonb NOT NULL DEFAULT '{}',
    error_message     text,
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK ((status = 'running') = (finished_at IS NULL))
);

CREATE TABLE source_file (
    source_file_id  integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_id      integer NOT NULL REFERENCES release,
    archive_path    text NOT NULL,              -- relative to data/raw
    member_path     text NOT NULL DEFAULT '',   -- path inside the archive, '' if none
    sha256          char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes      bigint NOT NULL CHECK (size_bytes >= 0),
    source_url      text,
    row_count       bigint CHECK (row_count >= 0),
    UNIQUE (release_id, archive_path, member_path)
);

-- One provenance record per (ingestion run, source file, transformation).
-- Individual facts point at it plus their line number(s) in that file, which
-- gives row-level lineage without one provenance row per synapse edge.
CREATE TABLE provenance (
    provenance_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id              smallint NOT NULL REFERENCES dataset,
    release_id              integer NOT NULL,
    source_file_id          integer REFERENCES source_file,   -- NULL for derived facts
    ingestion_run_id        bigint REFERENCES ingestion_run,
    transformation_name     text NOT NULL,
    transformation_version  text NOT NULL,
    parameters              jsonb NOT NULL DEFAULT '{}',
    created_at              timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (release_id, dataset_id) REFERENCES release (release_id, dataset_id)
);

CREATE TABLE provenance_parent (
    provenance_id  bigint NOT NULL REFERENCES provenance ON DELETE CASCADE,
    parent_id      bigint NOT NULL REFERENCES provenance,
    PRIMARY KEY (provenance_id, parent_id),
    CHECK (provenance_id <> parent_id)
);

-- Schema mapping registry: how each source field maps to a canonical field.
CREATE TABLE schema_mapping (
    schema_mapping_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id           smallint NOT NULL REFERENCES dataset,
    source_file_pattern  text NOT NULL,
    source_field         text NOT NULL,
    canonical_entity     text NOT NULL CHECK (canonical_entity IN
                         ('dataset_neuron', 'annotation', 'connection', 'brain_region', 'neuron_type')),
    canonical_field      text NOT NULL,
    transform            text NOT NULL DEFAULT 'identity',
    notes                text,
    UNIQUE (dataset_id, source_file_pattern, source_field, canonical_entity, canonical_field)
);

-- ------------------------------------------------------- canonical vocabulary
CREATE TABLE brain_region (
    brain_region_id  integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_code   text NOT NULL,
    hemisphere       char(1) CHECK (hemisphere IN ('L', 'R')),   -- NULL = unpaired
    super_region     text CHECK (super_region IN ('optic', 'central', 'vnc', 'other')),
    UNIQUE NULLS NOT DISTINCT (canonical_code, hemisphere)
);

CREATE TABLE brain_region_alias (
    dataset_id       smallint NOT NULL REFERENCES dataset,
    source_code      text NOT NULL,              -- e.g. 'SLP(R)' or 'SLP_R'
    brain_region_id  integer NOT NULL REFERENCES brain_region,
    PRIMARY KEY (dataset_id, source_code)
);

CREATE TABLE neuron_type (
    neuron_type_id  integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            text NOT NULL UNIQUE CHECK (name <> '')
);

-- Dataset-specific type names linked to canonical types.
CREATE TABLE type_alias (
    dataset_id      smallint NOT NULL REFERENCES dataset,
    source_type     text NOT NULL,
    neuron_type_id  integer NOT NULL REFERENCES neuron_type,
    method          text NOT NULL CHECK (method IN ('identical_name', 'published_crosswalk', 'manual')),
    provenance_id   bigint REFERENCES provenance,
    PRIMARY KEY (dataset_id, source_type, neuron_type_id)
);

-- ---------------------------------------------------------- source neurons
-- Dataset-scoped identity: one row per (dataset, original id). The original
-- id is stored as text so 64-bit FlyWire root ids and any future non-numeric
-- ids survive losslessly. Attributes and existence are versioned separately.
CREATE TABLE dataset_neuron (
    dataset_neuron_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id         smallint NOT NULL REFERENCES dataset,
    source_neuron_id   text NOT NULL CHECK (source_neuron_id <> ''),
    first_release_id   integer NOT NULL,
    provenance_id      bigint NOT NULL REFERENCES provenance,
    source_line        integer CHECK (source_line > 0),
    metadata_doc_id    text,                    -- MongoDB source_metadata _id
    UNIQUE (dataset_id, source_neuron_id),
    UNIQUE (dataset_neuron_id, dataset_id),     -- target of composite FKs
    FOREIGN KEY (first_release_id, dataset_id) REFERENCES release (release_id, dataset_id)
);

-- Existence of a source neuron per release range.
CREATE TABLE neuron_version (
    neuron_version_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_neuron_id  bigint NOT NULL REFERENCES dataset_neuron,
    valid_rel          int4range NOT NULL,
    tx                 tstzrange NOT NULL DEFAULT tstzrange(now(), NULL),
    provenance_id      bigint NOT NULL REFERENCES provenance,
    source_line        integer CHECK (source_line > 0),
    CHECK (NOT isempty(valid_rel) AND NOT lower_inf(valid_rel)),
    CHECK (NOT isempty(tx) AND NOT lower_inf(tx)),
    EXCLUDE USING gist (dataset_neuron_id WITH =, valid_rel WITH &&, tx WITH &&)
);

CREATE TABLE annotation (
    annotation_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_neuron_id  bigint NOT NULL REFERENCES dataset_neuron,
    annotation_key     text NOT NULL CHECK (annotation_key ~ '^[a-z][a-z0-9_]*$'),
    annotation_value   text NOT NULL CHECK (annotation_value <> ''),
    valid_rel          int4range NOT NULL,
    tx                 tstzrange NOT NULL DEFAULT tstzrange(now(), NULL),
    provenance_id      bigint NOT NULL REFERENCES provenance,
    source_line        integer CHECK (source_line > 0),
    CHECK (NOT isempty(valid_rel) AND NOT lower_inf(valid_rel)),
    CHECK (NOT isempty(tx) AND NOT lower_inf(tx)),
    EXCLUDE USING gist (dataset_neuron_id WITH =, annotation_key WITH =, valid_rel WITH &&, tx WITH &&)
);

-- Neuron-to-neuron connectivity (total synapse count of the pair). Both
-- endpoints must belong to the edge's dataset (composite FKs), so an edge can
-- never silently join two different connectomes.
CREATE TABLE connection (
    connection_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id      smallint NOT NULL,
    pre_neuron_id   bigint NOT NULL,
    post_neuron_id  bigint NOT NULL,
    synapse_count   integer NOT NULL CHECK (synapse_count >= 0),
    valid_rel       int4range NOT NULL,
    tx              tstzrange NOT NULL DEFAULT tstzrange(now(), NULL),
    provenance_id   bigint NOT NULL REFERENCES provenance,
    source_lines    integer[] NOT NULL CHECK (cardinality(source_lines) > 0),
    CHECK (NOT isempty(valid_rel) AND NOT lower_inf(valid_rel)),
    CHECK (NOT isempty(tx) AND NOT lower_inf(tx)),
    FOREIGN KEY (pre_neuron_id, dataset_id) REFERENCES dataset_neuron (dataset_neuron_id, dataset_id),
    FOREIGN KEY (post_neuron_id, dataset_id) REFERENCES dataset_neuron (dataset_neuron_id, dataset_id),
    EXCLUDE USING gist (pre_neuron_id WITH =, post_neuron_id WITH =, valid_rel WITH &&, tx WITH &&)
);
-- Neighbourhood lookups at any release / as-of time. The exclusion
-- constraint's GiST index already serves (pre, post) pair lookups.
CREATE INDEX connection_pre_idx  ON connection (pre_neuron_id);
CREATE INDEX connection_post_idx ON connection (post_neuron_id);
CREATE INDEX connection_dataset_open_idx ON connection (dataset_id) WHERE upper_inf(tx) AND upper_inf(valid_rel);
CREATE INDEX annotation_key_value_idx ON annotation (annotation_key, annotation_value) WHERE upper_inf(tx);

-- ----------------------------------------------- canonical identity & mapping
CREATE TABLE canonical_neuron (
    canonical_neuron_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status               text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'merged', 'retired')),
    neuron_type_id       integer REFERENCES neuron_type,
    label                text,
    created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE neuron_mapping (
    mapping_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_neuron_id    bigint NOT NULL REFERENCES dataset_neuron,
    canonical_neuron_id  bigint REFERENCES canonical_neuron,     -- NULL = unresolved
    method               text NOT NULL CHECK (method IN
                         ('source_identity', 'published_type_crosswalk', 'rule_based',
                          'embedding', 'graphsage', 'manual')),
    confidence           numeric(4, 3) CHECK (confidence BETWEEN 0 AND 1),
    review_status        text NOT NULL DEFAULT 'proposed'
                         CHECK (review_status IN ('proposed', 'approved', 'rejected', 'unresolved')),
    evidence             jsonb NOT NULL DEFAULT '{}',
    evidence_doc_id      text,                                   -- MongoDB evidence document
    model_version        text,
    provenance_id        bigint REFERENCES provenance,
    mapped_at            timestamptz NOT NULL DEFAULT now(),
    reviewed_at          timestamptz,
    reviewed_by          text,
    CHECK ((review_status = 'unresolved') = (canonical_neuron_id IS NULL)),
    CHECK (method NOT IN ('embedding', 'graphsage') OR model_version IS NOT NULL),
    CHECK (review_status IN ('proposed', 'unresolved') OR reviewed_at IS NOT NULL)
);
-- A source neuron has at most one approved identity at a time.
CREATE UNIQUE INDEX neuron_mapping_one_approved ON neuron_mapping (dataset_neuron_id)
    WHERE review_status = 'approved';
CREATE UNIQUE INDEX neuron_mapping_no_dup ON neuron_mapping (dataset_neuron_id, canonical_neuron_id, method)
    WHERE canonical_neuron_id IS NOT NULL;
CREATE INDEX neuron_mapping_canonical_idx ON neuron_mapping (canonical_neuron_id);

-- ------------------------------------------------------------ ConnectomeDiff
CREATE TABLE diff_run (
    diff_run_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mode         text NOT NULL CHECK (mode IN ('temporal', 'cross_dataset')),
    dataset_a    smallint NOT NULL,
    release_a    integer NOT NULL,
    dataset_b    smallint NOT NULL,
    release_b    integer NOT NULL,
    as_of        timestamptz NOT NULL,          -- transaction time both states were read at
    parameters   jsonb NOT NULL DEFAULT '{}',
    status       text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at  timestamptz,
    stats        jsonb NOT NULL DEFAULT '{}',
    FOREIGN KEY (release_a, dataset_a) REFERENCES release (release_id, dataset_id),
    FOREIGN KEY (release_b, dataset_b) REFERENCES release (release_id, dataset_id),
    CHECK (release_a <> release_b),
    -- temporal evolution only within one dataset; cross-dataset comparison is
    -- a different mode and is never reported as temporal change
    CHECK (mode <> 'temporal' OR dataset_a = dataset_b),
    CHECK (mode <> 'cross_dataset' OR dataset_a <> dataset_b)
);
CREATE UNIQUE INDEX diff_run_idempotent
    ON diff_run (mode, release_a, release_b, as_of, md5(parameters::text));

CREATE TABLE diff_event (
    diff_event_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    diff_run_id        bigint NOT NULL REFERENCES diff_run ON DELETE CASCADE,
    entity_type        text NOT NULL CHECK (entity_type IN ('neuron', 'connection', 'annotation')),
    change_type        text NOT NULL CHECK (change_type IN ('added', 'removed', 'modified')),
    dataset_neuron_id  bigint NOT NULL REFERENCES dataset_neuron,   -- subject, or pre-synaptic neuron
    post_neuron_id     bigint REFERENCES dataset_neuron,
    annotation_key     text,
    old_value          text,
    new_value          text,
    old_weight         integer CHECK (old_weight >= 0),
    new_weight         integer CHECK (new_weight >= 0),
    weight_delta       integer GENERATED ALWAYS AS (coalesce(new_weight, 0) - coalesce(old_weight, 0)) STORED,
    record_a_id        bigint,   -- versioned row in release A (connection/annotation/neuron_version id)
    record_b_id        bigint,   -- versioned row in release B
    CHECK ((entity_type = 'connection') = (post_neuron_id IS NOT NULL)),
    CHECK ((entity_type = 'annotation') = (annotation_key IS NOT NULL)),
    CHECK (change_type <> 'added'    OR (record_a_id IS NULL AND record_b_id IS NOT NULL)),
    CHECK (change_type <> 'removed'  OR (record_a_id IS NOT NULL AND record_b_id IS NULL)),
    CHECK (change_type <> 'modified' OR (record_a_id IS NOT NULL AND record_b_id IS NOT NULL))
);
CREATE INDEX diff_event_run_idx ON diff_event (diff_run_id, entity_type, change_type);

-- --------------------------------------------------- distribution & ML (later phases)
CREATE TABLE shard (
    shard_id  smallint PRIMARY KEY CHECK (shard_id BETWEEN 1 AND 64),
    name      text NOT NULL UNIQUE,
    host      text NOT NULL,
    port      integer NOT NULL CHECK (port BETWEEN 1 AND 65535)
);

CREATE TABLE partition_strategy (
    strategy_id  smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code         text NOT NULL UNIQUE CHECK (code IN ('hash', 'anatomical', 'community')),
    description  text,
    parameters   jsonb NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE partition_assignment (
    strategy_id        smallint NOT NULL REFERENCES partition_strategy,
    dataset_neuron_id  bigint NOT NULL REFERENCES dataset_neuron,
    shard_id           smallint NOT NULL REFERENCES shard,
    assigned_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (strategy_id, dataset_neuron_id)
);

CREATE TABLE query_execution (
    query_execution_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_id         smallint REFERENCES partition_strategy,
    query_kind          text NOT NULL,
    parameters          jsonb NOT NULL DEFAULT '{}',
    shards_touched      smallint NOT NULL CHECK (shards_touched >= 0),
    rows_returned       bigint NOT NULL CHECK (rows_returned >= 0),
    bytes_transferred   bigint CHECK (bytes_transferred >= 0),
    latency_ms          double precision NOT NULL CHECK (latency_ms >= 0),
    executed_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ml_model_run (
    model_run_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_kind       text NOT NULL CHECK (model_kind IN ('isolation_forest', 'threshold', 'embedding', 'graphsage')),
    model_version    text NOT NULL,
    parameters       jsonb NOT NULL DEFAULT '{}',
    feature_schema   jsonb NOT NULL DEFAULT '[]',
    diff_run_ids     bigint[] NOT NULL DEFAULT '{}',
    random_seed      integer,
    artifact_path    text,
    artifact_sha256  char(64),
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (model_kind, model_version)
);

CREATE TABLE anomaly_result (
    model_run_id   bigint NOT NULL REFERENCES ml_model_run ON DELETE CASCADE,
    diff_event_id  bigint NOT NULL REFERENCES diff_event ON DELETE CASCADE,
    score          double precision NOT NULL,
    is_flagged     boolean NOT NULL,
    score_rank     integer NOT NULL CHECK (score_rank > 0),
    PRIMARY KEY (model_run_id, diff_event_id)
);

-- --------------------------------------------- append-only history guard
-- Versioned rows may only have their transaction-time interval closed
-- (upper bound set once, not before the lower bound). Every other change and
-- every DELETE is rejected, so past database states stay reconstructible.
CREATE FUNCTION guard_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'history is append-only: DELETE on % is not allowed', TG_TABLE_NAME
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT upper_inf(OLD.tx) THEN
        RAISE EXCEPTION 'row % in % is already retired and cannot change', OLD, TG_TABLE_NAME
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF lower(NEW.tx) <> lower(OLD.tx) OR upper_inf(NEW.tx) OR upper(NEW.tx) < lower(OLD.tx)
       OR (to_jsonb(NEW) - 'tx') <> (to_jsonb(OLD) - 'tx') THEN
        RAISE EXCEPTION 'only closing the transaction-time interval is allowed on %', TG_TABLE_NAME
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER neuron_version_history BEFORE UPDATE OR DELETE ON neuron_version
    FOR EACH ROW EXECUTE FUNCTION guard_history();
CREATE TRIGGER annotation_history BEFORE UPDATE OR DELETE ON annotation
    FOR EACH ROW EXECUTE FUNCTION guard_history();
CREATE TRIGGER connection_history BEFORE UPDATE OR DELETE ON connection
    FOR EACH ROW EXECUTE FUNCTION guard_history();
