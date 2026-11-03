-- Temporal / provenance query layer. Every function takes an optional
-- transaction time p_as_of (default now()): "what did the database believe at
-- that moment?" Release-specific queries use valid time (release_order).

SET search_path = cc, public;

-- Label of the release with a given order inside a dataset (NULL if none).
CREATE FUNCTION release_label(p_dataset smallint, p_order integer) RETURNS text
STABLE LANGUAGE sql AS $$
    SELECT version_label FROM cc.release WHERE dataset_id = p_dataset AND release_order = p_order
$$;

-- Human-readable view of the bitemporal intervals.
CREATE VIEW connection_history AS
SELECT c.connection_id, c.dataset_id, d.code AS dataset,
       pre.source_neuron_id AS pre_source_id, post.source_neuron_id AS post_source_id,
       c.synapse_count,
       release_label(c.dataset_id, lower(c.valid_rel)) AS valid_from_release,
       release_label(c.dataset_id, upper(c.valid_rel)) AS valid_until_release,
       lower(c.tx) AS recorded_from, upper(c.tx) AS recorded_until,
       c.provenance_id, c.source_lines
FROM cc.connection c
JOIN cc.dataset d USING (dataset_id)
JOIN cc.dataset_neuron pre  ON pre.dataset_neuron_id  = c.pre_neuron_id
JOIN cc.dataset_neuron post ON post.dataset_neuron_id = c.post_neuron_id;

-- Releases with calendar valid time when known.
CREATE VIEW release_overview AS
SELECT r.release_id, d.code AS dataset, r.version_label, r.release_order, r.release_date,
       r.ingestion_status, r.loaded_at,
       (SELECT count(*) FROM cc.neuron_version nv JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
         WHERE dn.dataset_id = r.dataset_id AND nv.valid_rel @> r.release_order AND upper_inf(nv.tx)) AS neurons,
       (SELECT count(*) FROM cc.connection c
         WHERE c.dataset_id = r.dataset_id AND c.valid_rel @> r.release_order AND upper_inf(c.tx)) AS connections
FROM cc.release r JOIN cc.dataset d USING (dataset_id);

-- Resolve a source identifier to the internal dataset-scoped id.
CREATE FUNCTION find_neuron(p_dataset text, p_source_id text) RETURNS bigint
STABLE LANGUAGE sql AS $$
    SELECT dn.dataset_neuron_id FROM cc.dataset_neuron dn JOIN cc.dataset d USING (dataset_id)
    WHERE d.code = p_dataset AND dn.source_neuron_id = p_source_id
$$;

-- State of one neuron in one release: existence plus all annotations.
CREATE FUNCTION neuron_at_release(p_neuron bigint, p_release integer, p_as_of timestamptz DEFAULT now())
RETURNS TABLE (dataset_neuron_id bigint, source_neuron_id text, release text, present boolean, annotations jsonb)
STABLE LANGUAGE sql AS $$
    WITH r AS (SELECT release_order, dataset_id, version_label FROM cc.release WHERE release_id = p_release)
    SELECT dn.dataset_neuron_id, dn.source_neuron_id, r.version_label,
           EXISTS (SELECT 1 FROM cc.neuron_version nv
                    WHERE nv.dataset_neuron_id = dn.dataset_neuron_id
                      AND nv.valid_rel @> r.release_order AND nv.tx @> p_as_of),
           coalesce((SELECT jsonb_object_agg(a.annotation_key, a.annotation_value ORDER BY a.annotation_key)
                       FROM cc.annotation a
                      WHERE a.dataset_neuron_id = dn.dataset_neuron_id
                        AND a.valid_rel @> r.release_order AND a.tx @> p_as_of), '{}'::jsonb)
    FROM cc.dataset_neuron dn JOIN r ON r.dataset_id = dn.dataset_id
    WHERE dn.dataset_neuron_id = p_neuron
$$;

-- Incoming and outgoing connections of a neuron in one release.
CREATE FUNCTION connections_at_release(p_neuron bigint, p_release integer,
                                       p_as_of timestamptz DEFAULT now(), p_min_weight integer DEFAULT 1)
RETURNS TABLE (direction text, partner_neuron_id bigint, partner_source_id text,
               synapse_count integer, connection_id bigint)
STABLE LANGUAGE sql AS $$
    WITH r AS (SELECT release_order, dataset_id FROM cc.release WHERE release_id = p_release)
    SELECT 'out', c.post_neuron_id, dn.source_neuron_id, c.synapse_count, c.connection_id
      FROM cc.connection c JOIN r ON c.dataset_id = r.dataset_id
      JOIN cc.dataset_neuron dn ON dn.dataset_neuron_id = c.post_neuron_id
     WHERE c.pre_neuron_id = p_neuron AND c.valid_rel @> r.release_order
       AND c.tx @> p_as_of AND c.synapse_count >= p_min_weight
    UNION ALL
    SELECT 'in', c.pre_neuron_id, dn.source_neuron_id, c.synapse_count, c.connection_id
      FROM cc.connection c JOIN r ON c.dataset_id = r.dataset_id
      JOIN cc.dataset_neuron dn ON dn.dataset_neuron_id = c.pre_neuron_id
     WHERE c.post_neuron_id = p_neuron AND c.valid_rel @> r.release_order
       AND c.tx @> p_as_of AND c.synapse_count >= p_min_weight
$$;

-- Full bitemporal history of a neuron's annotations: one row per version,
-- including versions the database has since retired (recorded_until set).
CREATE FUNCTION annotation_history(p_neuron bigint)
RETURNS TABLE (annotation_key text, annotation_value text, valid_from_release text,
               valid_until_release text, recorded_from timestamptz, recorded_until timestamptz,
               annotation_id bigint)
STABLE LANGUAGE sql AS $$
    SELECT a.annotation_key, a.annotation_value,
           cc.release_label(dn.dataset_id, lower(a.valid_rel)),
           cc.release_label(dn.dataset_id, upper(a.valid_rel)),
           lower(a.tx), upper(a.tx), a.annotation_id
    FROM cc.annotation a JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
    WHERE a.dataset_neuron_id = p_neuron
    ORDER BY a.annotation_key, lower(a.valid_rel), lower(a.tx)
$$;

-- What the database believed about a neuron at a transaction time, across all
-- releases it knew of then (pure transaction-time query).
CREATE FUNCTION neuron_as_of(p_neuron bigint, p_as_of timestamptz)
RETURNS TABLE (annotation_key text, annotation_value text, valid_from_release text, valid_until_release text)
STABLE LANGUAGE sql AS $$
    SELECT a.annotation_key, a.annotation_value,
           cc.release_label(dn.dataset_id, lower(a.valid_rel)),
           cc.release_label(dn.dataset_id, upper(a.valid_rel))
    FROM cc.annotation a JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
    WHERE a.dataset_neuron_id = p_neuron AND a.tx @> p_as_of
    ORDER BY a.annotation_key, lower(a.valid_rel)
$$;

-- Lineage of a stored fact back to the source file, line(s), release,
-- dataset and ingestion run. p_table is one of connection | annotation |
-- neuron_version | dataset_neuron.
CREATE FUNCTION trace_provenance(p_table text, p_id bigint) RETURNS jsonb
STABLE LANGUAGE plpgsql AS $$
DECLARE
    v_prov bigint;
    v_lines integer[];
    v_record jsonb;
BEGIN
    CASE p_table
        WHEN 'connection' THEN
            SELECT provenance_id, source_lines, to_jsonb(c) - 'source_lines' INTO v_prov, v_lines, v_record
            FROM cc.connection c WHERE connection_id = p_id;
        WHEN 'annotation' THEN
            SELECT provenance_id, ARRAY[source_line], to_jsonb(a) INTO v_prov, v_lines, v_record
            FROM cc.annotation a WHERE annotation_id = p_id;
        WHEN 'neuron_version' THEN
            SELECT provenance_id, ARRAY[source_line], to_jsonb(n) INTO v_prov, v_lines, v_record
            FROM cc.neuron_version n WHERE neuron_version_id = p_id;
        WHEN 'dataset_neuron' THEN
            SELECT provenance_id, ARRAY[source_line], to_jsonb(n) INTO v_prov, v_lines, v_record
            FROM cc.dataset_neuron n WHERE dataset_neuron_id = p_id;
        ELSE
            RAISE EXCEPTION 'unknown table %', p_table USING ERRCODE = 'invalid_parameter_value';
    END CASE;
    IF v_prov IS NULL THEN
        RETURN NULL;
    END IF;
    RETURN jsonb_build_object(
        'record', v_record,
        'source_lines', to_jsonb(v_lines),
        'provenance', (SELECT to_jsonb(p) FROM cc.provenance p WHERE p.provenance_id = v_prov),
        'source_file', (SELECT to_jsonb(f) FROM cc.provenance p JOIN cc.source_file f USING (source_file_id)
                         WHERE p.provenance_id = v_prov),
        'release', (SELECT to_jsonb(r) FROM cc.provenance p JOIN cc.release r USING (release_id)
                     WHERE p.provenance_id = v_prov),
        'dataset', (SELECT jsonb_build_object('code', d.code, 'name', d.name, 'source_url', d.source_url,
                                              'license', d.license)
                      FROM cc.provenance p JOIN cc.dataset d USING (dataset_id) WHERE p.provenance_id = v_prov),
        'ingestion_run', (SELECT to_jsonb(i) FROM cc.provenance p JOIN cc.ingestion_run i
                            ON i.run_id = p.ingestion_run_id WHERE p.provenance_id = v_prov)
    );
END $$;
