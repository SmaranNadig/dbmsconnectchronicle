-- Distributed storage: three PostgreSQL shard instances, three partitioning
-- strategies, and a record of every deployment of a strategy to the shards.
--
-- Placement rule (all strategies): a neuron is owned by one shard; a
-- connection row is stored on the shard owning its pre-synaptic neuron and,
-- when the post-synaptic neuron is owned elsewhere, mirrored there too
-- (is_mirror). Every 1-hop neighbourhood is then answered by one shard; the
-- number of mirrored rows is the cost of the cut and is what the strategies
-- compete on. Shards hold the current belief (open transaction time) with the
-- full valid-time history; transaction-time audit stays on the coordinator.
SET search_path = cc, public;

-- host/port as seen from the coordinator container (docker network);
-- the Python router reaches the same instances through the published ports.
INSERT INTO shard (shard_id, name, host, port) VALUES
    (1, 'shard_1', 'postgres_shard_1', 5432),
    (2, 'shard_2', 'postgres_shard_2', 5432),
    (3, 'shard_3', 'postgres_shard_3', 5432);

INSERT INTO partition_strategy (code, description, parameters) VALUES
    ('hash', 'neuron owner = hash of dataset_neuron_id modulo shard count', '{"function": "hashint8"}'),
    ('anatomical', 'neurons grouped by primary brain region; regions packed onto shards by edge load',
     '{"fallback": "hash"}'),
    ('community', 'neurons grouped by Louvain communities of the latest release graph; communities packed by edge load',
     '{"algorithm": "igraph multilevel (Louvain)", "fallback": "hash"}');

-- Primary region of each neuron in the latest release that has region data.
CREATE VIEW neuron_latest_region AS
SELECT DISTINCT ON (s.dataset_neuron_id) s.dataset_neuron_id, l.code, s.brain_region_id
FROM neuron_region_synapses s JOIN release r USING (release_id)
JOIN brain_region_label l USING (brain_region_id)
ORDER BY s.dataset_neuron_id, r.release_order DESC, s.pre_synapses + s.post_synapses DESC, s.brain_region_id;

CREATE INDEX partition_assignment_shard_idx ON partition_assignment (strategy_id, shard_id);

-- Group (region or community) of each neuron, for strategies that use one.
ALTER TABLE partition_assignment ADD COLUMN group_key text;

CREATE TABLE shard_deployment (
    deployment_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_id       smallint NOT NULL REFERENCES partition_strategy,
    shard_id          smallint NOT NULL REFERENCES shard,
    schema_name       text NOT NULL,
    neurons           integer NOT NULL CHECK (neurons >= 0),
    connections       bigint NOT NULL CHECK (connections >= 0),     -- owned rows
    mirrors           bigint NOT NULL CHECK (mirrors >= 0),         -- copies of cut rows
    bytes_on_shard    bigint CHECK (bytes_on_shard >= 0),
    seconds           double precision NOT NULL CHECK (seconds >= 0),
    deployed_at       timestamptz NOT NULL DEFAULT now(),
    verified          boolean NOT NULL DEFAULT false
);
CREATE INDEX shard_deployment_strategy_idx ON shard_deployment (strategy_id, deployed_at DESC);

-- Quality of the current assignment of a strategy, from the coordinator's
-- data, in one pass over the current connection rows. plpgsql so the query is
-- planned with the actual strategy id (parallel hash joins).
CREATE FUNCTION partition_quality(p_strategy text)
RETURNS TABLE (shard_id smallint, neurons bigint, owned_rows bigint, mirrored_in bigint,
               share_of_rows numeric, cut_fraction numeric)
STABLE LANGUAGE plpgsql AS $$
DECLARE
    sid smallint := (SELECT strategy_id FROM cc.partition_strategy WHERE code = p_strategy);
BEGIN
    RETURN QUERY
    WITH e AS MATERIALIZED (
        SELECT ap.shard_id AS pre_shard, aq.shard_id AS post_shard, count(*) AS n
        FROM cc.connection c
        JOIN cc.partition_assignment ap ON ap.strategy_id = sid AND ap.dataset_neuron_id = c.pre_neuron_id
        JOIN cc.partition_assignment aq ON aq.strategy_id = sid AND aq.dataset_neuron_id = c.post_neuron_id
        WHERE upper_inf(c.tx)
        GROUP BY 1, 2),
    per AS (
        SELECT sh.shard_id,
               (SELECT count(*) FROM cc.partition_assignment pa
                WHERE pa.strategy_id = sid AND pa.shard_id = sh.shard_id) AS neurons,
               coalesce((SELECT sum(e.n) FROM e WHERE e.pre_shard = sh.shard_id), 0)::bigint AS owned,
               coalesce((SELECT sum(e.n) FROM e WHERE e.post_shard = sh.shard_id
                                                 AND e.pre_shard <> sh.shard_id), 0)::bigint AS mirrored
        FROM cc.shard sh)
    SELECT per.shard_id, per.neurons, per.owned, per.mirrored,
           round(per.owned / nullif(sum(per.owned) OVER (), 0), 4),
           round((SELECT sum(e.n) FROM e WHERE e.pre_shard <> e.post_shard)
                 / nullif((SELECT sum(e.n) FROM e), 0), 4)
    FROM per ORDER BY per.shard_id;
END
$$;
