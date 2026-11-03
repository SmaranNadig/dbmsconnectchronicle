-- MongoDB holds one document per dataset neuron (`neuron_source_record`,
-- _id = dataset_neuron.metadata_doc_id) with every original row about that
-- neuron, per release, including source columns the relational model does
-- not map. PostgreSQL stays authoritative: documents are derived from the
-- same checksummed source files and can be rebuilt at any time.
-- Each synchronisation of one release is recorded here.
SET search_path = cc, public;

CREATE TABLE metadata_sync (
    metadata_sync_id   integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_id         integer NOT NULL REFERENCES release,
    mongo_database     text NOT NULL,
    mongo_collection   text NOT NULL,
    pipeline_version   text NOT NULL,
    status             text NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'completed', 'failed')),
    started_at         timestamptz NOT NULL DEFAULT now(),
    finished_at        timestamptz,
    documents_written  integer CHECK (documents_written >= 0),
    records_written    integer CHECK (records_written >= 0),
    stats              jsonb NOT NULL DEFAULT '{}',
    error_message      text,
    CHECK ((status = 'running') = (finished_at IS NULL)),
    CHECK (status <> 'failed' OR error_message IS NOT NULL)
);
CREATE INDEX metadata_sync_release_idx ON metadata_sync (release_id, started_at DESC);
