SET search_path = cc, public;

-- Independent, relational expectations for the document store.
CREATE TABLE metadata_record_digest (
    dataset_neuron_id bigint NOT NULL REFERENCES dataset_neuron,
    release_id integer NOT NULL REFERENCES release,
    record_count integer NOT NULL CHECK (record_count >= 0),
    sha256 char(64) NOT NULL,
    PRIMARY KEY (dataset_neuron_id, release_id)
);

CREATE TABLE workspace_job (
    job_id uuid PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('diff', 'anomalies', 'matching', 'deploy')),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
    request jsonb NOT NULL,
    result jsonb,
    progress integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    message text NOT NULL DEFAULT 'Waiting for a worker',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX workspace_job_recent ON workspace_job (created_at DESC);

ALTER TABLE anomaly_result ADD COLUMN features jsonb NOT NULL DEFAULT '{}',
    ADD COLUMN explanation text NOT NULL DEFAULT '',
    ADD COLUMN baseline_flagged boolean NOT NULL DEFAULT false;
ALTER TABLE ml_model_run ADD COLUMN metrics jsonb NOT NULL DEFAULT '{}';
ALTER TABLE neuron_mapping ADD COLUMN score_details jsonb NOT NULL DEFAULT '{}';

CREATE TABLE deployment_batch (
    batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_id smallint NOT NULL REFERENCES partition_strategy,
    source_revision char(64) NOT NULL,
    placement_revision char(64) NOT NULL,
    status text NOT NULL DEFAULT 'building' CHECK (status IN ('building','verified','failed')),
    details jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE active_deployment (
    strategy_id smallint PRIMARY KEY REFERENCES partition_strategy,
    batch_id bigint NOT NULL REFERENCES deployment_batch
);
ALTER TABLE shard_deployment ADD COLUMN batch_id bigint REFERENCES deployment_batch;
CREATE INDEX shard_deployment_batch ON shard_deployment (batch_id, shard_id);

GRANT SELECT ON ALL TABLES IN SCHEMA cc TO cc_reader;
