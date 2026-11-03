-- ConnectomeDiff (temporal mode): compares two releases of the SAME dataset
-- as the database believed them at transaction time p_as_of. Purely
-- set-based: each entity class is a FULL OUTER JOIN of the two release
-- states. Results are persisted in diff_run / diff_event. Calling it again
-- with the same inputs returns the existing run (idempotent).

SET search_path = cc, public;

CREATE FUNCTION run_temporal_diff(p_release_a integer, p_release_b integer,
                                  p_as_of timestamptz DEFAULT now())
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    v_ds_a smallint; v_ord_a integer;
    v_ds_b smallint; v_ord_b integer;
    v_run bigint;
    v_stats jsonb;
BEGIN
    IF p_as_of > now() THEN
        RAISE EXCEPTION 'as_of % is in the future', p_as_of USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT dataset_id, release_order INTO v_ds_a, v_ord_a FROM cc.release WHERE release_id = p_release_a;
    SELECT dataset_id, release_order INTO v_ds_b, v_ord_b FROM cc.release WHERE release_id = p_release_b;
    IF v_ds_a IS NULL OR v_ds_b IS NULL THEN
        RAISE EXCEPTION 'unknown release' USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT diff_run_id INTO v_run FROM cc.diff_run
     WHERE mode = 'temporal' AND release_a = p_release_a AND release_b = p_release_b
       AND as_of = p_as_of AND md5(parameters::text) = md5('{}'::jsonb::text) AND status = 'succeeded';
    IF v_run IS NOT NULL THEN
        RETURN v_run;
    END IF;

    -- the CHECK constraints on diff_run reject cross-dataset input here
    INSERT INTO cc.diff_run (mode, dataset_a, release_a, dataset_b, release_b, as_of)
    VALUES ('temporal', v_ds_a, p_release_a, v_ds_b, p_release_b, p_as_of)
    RETURNING diff_run_id INTO v_run;

    -- neurons: existence
    INSERT INTO cc.diff_event (diff_run_id, entity_type, change_type, dataset_neuron_id, record_a_id, record_b_id)
    SELECT v_run, 'neuron', CASE WHEN a.id IS NULL THEN 'added' ELSE 'removed' END,
           coalesce(a.n, b.n), a.id, b.id
    FROM (SELECT nv.dataset_neuron_id AS n, nv.neuron_version_id AS id
            FROM cc.neuron_version nv JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
           WHERE dn.dataset_id = v_ds_a AND nv.valid_rel @> v_ord_a AND nv.tx @> p_as_of) a
    FULL JOIN
         (SELECT nv.dataset_neuron_id AS n, nv.neuron_version_id AS id
            FROM cc.neuron_version nv JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
           WHERE dn.dataset_id = v_ds_b AND nv.valid_rel @> v_ord_b AND nv.tx @> p_as_of) b
      ON a.n = b.n
    WHERE a.n IS NULL OR b.n IS NULL;

    -- annotations: per (neuron, key)
    INSERT INTO cc.diff_event (diff_run_id, entity_type, change_type, dataset_neuron_id, annotation_key,
                               old_value, new_value, record_a_id, record_b_id)
    SELECT v_run, 'annotation',
           CASE WHEN a.id IS NULL THEN 'added' WHEN b.id IS NULL THEN 'removed' ELSE 'modified' END,
           coalesce(a.n, b.n), coalesce(a.k, b.k), a.v, b.v, a.id, b.id
    FROM (SELECT x.dataset_neuron_id AS n, x.annotation_key AS k, x.annotation_value AS v, x.annotation_id AS id
            FROM cc.annotation x JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
           WHERE dn.dataset_id = v_ds_a AND x.valid_rel @> v_ord_a AND x.tx @> p_as_of) a
    FULL JOIN
         (SELECT x.dataset_neuron_id AS n, x.annotation_key AS k, x.annotation_value AS v, x.annotation_id AS id
            FROM cc.annotation x JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
           WHERE dn.dataset_id = v_ds_b AND x.valid_rel @> v_ord_b AND x.tx @> p_as_of) b
      ON a.n = b.n AND a.k = b.k
    WHERE a.id IS NULL OR b.id IS NULL OR a.v IS DISTINCT FROM b.v;

    -- connections: per (pre, post). Rows shared by both releases (same
    -- versioned row) are unchanged by construction and drop out cheaply.
    INSERT INTO cc.diff_event (diff_run_id, entity_type, change_type, dataset_neuron_id, post_neuron_id,
                               old_weight, new_weight, record_a_id, record_b_id)
    SELECT v_run, 'connection',
           CASE WHEN a.id IS NULL THEN 'added' WHEN b.id IS NULL THEN 'removed' ELSE 'modified' END,
           coalesce(a.pre, b.pre), coalesce(a.post, b.post), a.w, b.w, a.id, b.id
    FROM (SELECT pre_neuron_id AS pre, post_neuron_id AS post, synapse_count AS w, connection_id AS id
            FROM cc.connection WHERE dataset_id = v_ds_a AND valid_rel @> v_ord_a AND tx @> p_as_of) a
    FULL JOIN
         (SELECT pre_neuron_id AS pre, post_neuron_id AS post, synapse_count AS w, connection_id AS id
            FROM cc.connection WHERE dataset_id = v_ds_b AND valid_rel @> v_ord_b AND tx @> p_as_of) b
      ON a.pre = b.pre AND a.post = b.post
    WHERE a.id IS NULL OR b.id IS NULL OR (a.id <> b.id AND a.w <> b.w);

    SELECT jsonb_object_agg(k, n) INTO v_stats FROM (
        SELECT entity_type || '_' || change_type AS k, count(*) AS n
        FROM cc.diff_event WHERE diff_run_id = v_run GROUP BY 1) s;

    UPDATE cc.diff_run SET status = 'succeeded', finished_at = clock_timestamp(),
                           stats = coalesce(v_stats, '{}'::jsonb)
     WHERE diff_run_id = v_run;
    RETURN v_run;
END $$;

-- Readable diff events with source identifiers and release labels.
CREATE VIEW diff_event_detail AS
SELECT e.diff_event_id, e.diff_run_id, e.entity_type, e.change_type,
       n.source_neuron_id AS neuron_source_id, p.source_neuron_id AS post_source_id,
       e.annotation_key, e.old_value, e.new_value, e.old_weight, e.new_weight, e.weight_delta,
       ra.version_label AS release_a, rb.version_label AS release_b, d.code AS dataset,
       e.record_a_id, e.record_b_id
FROM cc.diff_event e
JOIN cc.diff_run r USING (diff_run_id)
JOIN cc.release ra ON ra.release_id = r.release_a
JOIN cc.release rb ON rb.release_id = r.release_b
JOIN cc.dataset d ON d.dataset_id = r.dataset_a
JOIN cc.dataset_neuron n ON n.dataset_neuron_id = e.dataset_neuron_id
LEFT JOIN cc.dataset_neuron p ON p.dataset_neuron_id = e.post_neuron_id;
