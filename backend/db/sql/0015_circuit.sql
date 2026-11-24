SET search_path = cc, public;

CREATE TABLE circuit_snapshot (
    snapshot_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_id integer NOT NULL REFERENCES release,
    digest text NOT NULL UNIQUE CHECK (length(digest) = 64),
    column_sha256 text NOT NULL,
    radius smallint NOT NULL CHECK (radius BETWEEN 2 AND 6),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE circuit_node (
    snapshot_id bigint REFERENCES circuit_snapshot,
    neuron_id bigint REFERENCES dataset_neuron,
    cell_type text NOT NULL,
    p smallint NOT NULL,
    q smallint NOT NULL,
    PRIMARY KEY (snapshot_id, neuron_id)
);
CREATE TABLE circuit_edge (
    snapshot_id bigint NOT NULL,
    pre_id bigint NOT NULL,
    post_id bigint NOT NULL,
    weight integer NOT NULL CHECK (weight > 0),
    connection_id bigint NOT NULL REFERENCES connection,
    PRIMARY KEY (snapshot_id, pre_id, post_id),
    FOREIGN KEY (snapshot_id, pre_id) REFERENCES circuit_node,
    FOREIGN KEY (snapshot_id, post_id) REFERENCES circuit_node
);
CREATE TABLE circuit_run (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_id bigint NOT NULL REFERENCES circuit_snapshot,
    model_version text NOT NULL,
    seed integer NOT NULL CHECK (seed BETWEEN 0 AND 999999),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (snapshot_id, model_version, seed)
);
CREATE TABLE circuit_trial (
    run_id bigint REFERENCES circuit_run,
    condition text NOT NULL,
    trial smallint NOT NULL,
    direction smallint NOT NULL CHECK (direction IN (-1,1)),
    score double precision NOT NULL,
    heading_error double precision NOT NULL CHECK (heading_error >= 0),
    open_loop_error double precision NOT NULL CHECK (open_loop_error > 0),
    replay jsonb NOT NULL,
    PRIMARY KEY (run_id, condition, trial)
);
CREATE VIEW circuit_results AS
SELECT run_id, condition, count(*) AS trials,
       avg((sign(score) = direction)::int) AS accuracy,
       avg(heading_error) AS heading_error,
       1 - sum(heading_error)/sum(open_loop_error) AS heading_improvement
FROM circuit_trial GROUP BY run_id, condition;
COMMENT ON TABLE circuit_edge IS 'Frozen model weights linked to bitemporal FlyWire connection records and source lines.';
COMMENT ON TABLE circuit_trial IS 'Paired held-out visual trials with fixed decoder, residual heading error and replay.';

