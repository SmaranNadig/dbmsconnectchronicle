-- ConnectomeChronicle demo. Run with:
--   docker exec -i cc_coordinator psql -U chronicle -d connectome < scripts/demo_queries.sql
-- Every number shown comes from the loaded public datasets.
\pset pager off
\timing on
SET search_path = cc, public;

\echo '\n=== 1. Schema: tables in the cc schema ==='
SELECT table_name FROM information_schema.tables WHERE table_schema = 'cc' AND table_type = 'BASE TABLE' ORDER BY 1;

\echo '\n=== 2. Releases loaded (neurons / connections valid in each release) ==='
SELECT dataset, version_label, release_order, ingestion_status, neurons, connections FROM release_overview
ORDER BY dataset, release_order;

\echo '\n=== 3. Ingestion runs (incl. the failed one, which left no partial data) ==='
SELECT run_id, release_id, status, finished_at - started_at AS duration, left(error_message, 60) AS error
FROM ingestion_run ORDER BY run_id;

\echo '\n=== 4. Storage: rows actually stored vs a full snapshot per release ==='
SELECT (SELECT count(*) FROM connection) AS connection_rows_stored,
       (SELECT sum(connections) FROM release_overview) AS rows_if_every_release_were_a_snapshot;

\echo '\n=== 5. One real neuron through time: hemibrain bodyId 1688868386 ==='
SELECT r.version_label, n.present, n.annotations
FROM release r JOIN dataset d USING (dataset_id),
     LATERAL neuron_at_release(find_neuron('hemibrain', '1688868386'), r.release_id) n
WHERE d.code = 'hemibrain' AND r.ingestion_status = 'loaded' ORDER BY r.release_order;

\echo '\n=== 6. Its bitemporal annotation history (valid releases + when the DB recorded it) ==='
SELECT annotation_key, annotation_value, valid_from_release, valid_until_release,
       recorded_from::timestamp(0), recorded_until::timestamp(0)
FROM annotation_history(find_neuron('hemibrain', '1688868386'));

\echo '\n=== 7. Its strongest outgoing partners in each release ==='
SELECT r.version_label, c.partner_source_id, c.synapse_count
FROM release r JOIN dataset d USING (dataset_id),
     LATERAL (SELECT * FROM connections_at_release(find_neuron('hemibrain', '1688868386'), r.release_id)
              WHERE direction = 'out' ORDER BY synapse_count DESC LIMIT 3) c
WHERE d.code = 'hemibrain' AND r.ingestion_status = 'loaded' ORDER BY r.release_order, c.synapse_count DESC;

\echo '\n=== 8. ConnectomeDiff hemibrain v1.0 -> v1.1 (persisted; the same as_of returns the same run) ==='
SELECT run_temporal_diff(
    (SELECT release_id FROM release JOIN dataset USING (dataset_id) WHERE code = 'hemibrain' AND version_label = 'v1.0'),
    (SELECT release_id FROM release JOIN dataset USING (dataset_id) WHERE code = 'hemibrain' AND version_label = 'v1.1'))
    AS diff_run_id \gset
SELECT diff_run_id, finished_at - started_at AS duration, jsonb_pretty(stats) AS stats
FROM diff_run WHERE diff_run_id = :diff_run_id;

\echo '\n=== 9. Largest synapse-weight changes in that diff ==='
SELECT neuron_source_id AS pre, post_source_id AS post, old_weight, new_weight, weight_delta
FROM diff_event_detail WHERE diff_run_id = :diff_run_id AND change_type = 'modified' AND entity_type = 'connection'
ORDER BY abs(weight_delta) DESC LIMIT 5;

\echo '\n=== 10. Provenance of one changed connection, back to the source file and line ==='
SELECT jsonb_pretty(trace_provenance('connection', record_b_id) - 'record' - 'ingestion_run')
FROM diff_event_detail WHERE diff_run_id = :diff_run_id AND change_type = 'modified' AND entity_type = 'connection'
ORDER BY abs(weight_delta) DESC LIMIT 1;

\echo '\n=== 11. Integrity: these statements must all be rejected ==='
\set ON_ERROR_STOP off
BEGIN;
UPDATE connection SET synapse_count = 999 WHERE connection_id = (SELECT min(connection_id) FROM connection);
ROLLBACK;
BEGIN;
DELETE FROM annotation WHERE annotation_id = (SELECT min(annotation_id) FROM annotation);
ROLLBACK;
BEGIN;
INSERT INTO connection (dataset_id, pre_neuron_id, post_neuron_id, synapse_count, valid_rel, provenance_id, source_lines)
SELECT dataset_id, pre_neuron_id, post_neuron_id, -5, int4range(1, NULL), provenance_id, '{1}' FROM connection LIMIT 1;
ROLLBACK;
BEGIN;
INSERT INTO connection (dataset_id, pre_neuron_id, post_neuron_id, synapse_count, valid_rel, provenance_id, source_lines)
SELECT dataset_id, pre_neuron_id, post_neuron_id, 1, valid_rel, provenance_id, '{1}' FROM connection
WHERE upper_inf(tx) LIMIT 1;
ROLLBACK;
\set ON_ERROR_STOP on
\echo '(4 errors above are expected: trigger x2, CHECK, exclusion constraint)'

\echo '\n=== 12. Query plan: neighbourhood lookup uses the index ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT * FROM connection WHERE pre_neuron_id = find_neuron('hemibrain', '1688868386') AND upper_inf(tx);

\echo '\n=== 13. The team ER diagram, queried directly (schema er) ==='
SELECT * FROM er.dataset_neuron WHERE raw_metadata = 'hemibrain:1688868386';
SELECT annotation_type, annotation_value, valid_from, valid_to, source
FROM er.annotation
WHERE canonical_neuron_id = (SELECT canonical_neuron_id FROM er.canonical_neuron WHERE name = 'hemibrain:1688868386')
  AND annotation_type = 'cell_type';
SELECT * FROM er.connection
WHERE from_neuron_id = (SELECT canonical_neuron_id FROM er.canonical_neuron WHERE name = 'hemibrain:1688868386')
ORDER BY synapse_count DESC LIMIT 3;

\echo '\n=== 14. Brain regions: where this neuron makes and receives synapses (v1.2) ==='
SELECT l.code, l.name, s.pre_synapses, s.post_synapses
FROM neuron_region_synapses s JOIN brain_region_label l USING (brain_region_id)
JOIN release r USING (release_id)
WHERE s.dataset_neuron_id = find_neuron('hemibrain', '1688868386') AND r.version_label = 'v1.2'
ORDER BY s.pre_synapses + s.post_synapses DESC;

\echo '\n=== 15. MongoDB synchronisation runs (documents hold the original source rows) ==='
SELECT d.code, r.version_label, s.status, s.documents_written, s.records_written,
       s.finished_at - s.started_at AS duration
FROM metadata_sync s JOIN release r USING (release_id) JOIN dataset d USING (dataset_id)
WHERE s.status = 'completed' ORDER BY s.metadata_sync_id;

\echo '\n=== 16. Distribution: the three strategies on the three shards (verified copies) ==='
SELECT p.code AS strategy, d.shard_id, d.neurons AS owned_neurons, d.connections AS owned_rows, d.mirrors,
       pg_size_pretty(d.bytes_on_shard) AS size, d.verified
FROM shard_deployment d JOIN partition_strategy p USING (strategy_id)
WHERE d.deployment_id IN (SELECT max(deployment_id) FROM shard_deployment GROUP BY strategy_id, shard_id)
ORDER BY p.strategy_id, d.shard_id;
SELECT p.code AS strategy, round(sum(d.mirrors)::numeric / sum(d.connections), 4) AS edge_cut_fraction
FROM shard_deployment d JOIN partition_strategy p USING (strategy_id)
WHERE d.deployment_id IN (SELECT max(deployment_id) FROM shard_deployment GROUP BY strategy_id, shard_id)
GROUP BY p.strategy_id, p.code ORDER BY p.strategy_id;
