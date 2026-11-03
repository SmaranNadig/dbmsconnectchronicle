-- Entity resolution: source neurons -> canonical neurons.
--
-- 1. assign_source_identity(dataset): every source neuron gets its own
--    canonical neuron (approved, method source_identity). Identity across
--    releases of one dataset is the unchanged source id; FlyWire root ids
--    change when a neuron is edited, so 630 and 783 ids are NOT assumed equal.
-- 2. build_type_crosswalk(): canonical type vocabulary from hemibrain types,
--    and FlyWire hemibrain_type annotations (published crosswalk, Schlegel et
--    al. 2024) linked to it.
-- 3. propose_crosswalk_mappings(fw_release, hb_release): FlyWire -> hemibrain
--    candidates among neurons of the same crosswalked type AND the same side.
--    A shared type is evidence, not proof of identity, so candidates stay
--    'proposed'; neurons with no or too many candidates are stored as
--    'unresolved' with the reason. confidence = 1 / (#hemibrain candidates of
--    the FlyWire neuron x #FlyWire candidates of the hemibrain neuron), i.e. the
--    chance of the pair under uniform assignment within the type and side.
-- 4. review_mapping(): approve / reject; approving merges the FlyWire
--    neuron's singleton canonical into the hemibrain one.

SET search_path = cc, public;

CREATE UNIQUE INDEX canonical_neuron_label_key ON canonical_neuron (label);

CREATE FUNCTION assign_source_identity(p_dataset text) RETURNS bigint
LANGUAGE sql AS $$
    WITH todo AS (
        SELECT dn.dataset_neuron_id, d.code || ':' || dn.source_neuron_id AS label
        FROM cc.dataset_neuron dn JOIN cc.dataset d USING (dataset_id)
        WHERE d.code = p_dataset
          AND NOT EXISTS (SELECT 1 FROM cc.neuron_mapping m
                          WHERE m.dataset_neuron_id = dn.dataset_neuron_id AND m.method = 'source_identity')
    ), ins AS (
        INSERT INTO cc.canonical_neuron (label) SELECT label FROM todo
        RETURNING canonical_neuron_id, label
    ), m AS (
        INSERT INTO cc.neuron_mapping (dataset_neuron_id, canonical_neuron_id, method, confidence,
                                       review_status, evidence, reviewed_at, reviewed_by)
        SELECT t.dataset_neuron_id, i.canonical_neuron_id, 'source_identity', 1, 'approved',
               jsonb_build_object('rule', 'same source identifier within one dataset'), now(), 'system'
        FROM todo t JOIN ins i USING (label)
        RETURNING 1
    )
    SELECT count(*) FROM m
$$;

CREATE FUNCTION build_type_crosswalk() RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
    v_hb smallint := (SELECT dataset_id FROM cc.dataset WHERE code = 'hemibrain');
    v_fw smallint := (SELECT dataset_id FROM cc.dataset WHERE code = 'flywire');
    v_types bigint; v_hb_alias bigint; v_fw_alias bigint; v_unmatched bigint;
BEGIN
    IF v_hb IS NULL OR v_fw IS NULL THEN
        RAISE EXCEPTION 'load hemibrain and flywire first' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO cc.neuron_type (name)
    SELECT DISTINCT a.annotation_value FROM cc.annotation a JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
    WHERE dn.dataset_id = v_hb AND a.annotation_key = 'cell_type' AND upper_inf(a.tx)
    ON CONFLICT (name) DO NOTHING;
    GET DIAGNOSTICS v_types = ROW_COUNT;

    INSERT INTO cc.type_alias (dataset_id, source_type, neuron_type_id, method)
    SELECT v_hb, nt.name, nt.neuron_type_id, 'identical_name' FROM cc.neuron_type nt
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS v_hb_alias = ROW_COUNT;

    -- a FlyWire hemibrain_type may list several hemibrain types, comma separated
    WITH vals AS (
        SELECT DISTINCT a.annotation_value AS v FROM cc.annotation a JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
        WHERE dn.dataset_id = v_fw AND a.annotation_key = 'hemibrain_type' AND upper_inf(a.tx)
    ), tok AS (
        SELECT v, btrim(t) AS t FROM vals, unnest(string_to_array(v, ',')) t
    )
    INSERT INTO cc.type_alias (dataset_id, source_type, neuron_type_id, method)
    SELECT v_fw, tok.v, nt.neuron_type_id, 'published_crosswalk'
    FROM tok JOIN cc.neuron_type nt ON nt.name = tok.t
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS v_fw_alias = ROW_COUNT;

    SELECT count(DISTINCT btrim(t)) INTO v_unmatched
    FROM (SELECT DISTINCT a.annotation_value AS v FROM cc.annotation a JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
          WHERE dn.dataset_id = v_fw AND a.annotation_key = 'hemibrain_type' AND upper_inf(a.tx)) vals,
         unnest(string_to_array(v, ',')) t
    WHERE NOT EXISTS (SELECT 1 FROM cc.neuron_type nt WHERE nt.name = btrim(t));

    RETURN jsonb_build_object('new_canonical_types', v_types, 'hemibrain_aliases', v_hb_alias,
                              'flywire_crosswalk_aliases', v_fw_alias,
                              'flywire_type_tokens_without_hemibrain_type', v_unmatched);
END $$;

CREATE FUNCTION propose_crosswalk_mappings(p_fw_release integer, p_hb_release integer,
                                           p_max_candidates integer DEFAULT 5)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    v_fw smallint; v_fw_ord integer; v_hb smallint; v_hb_ord integer;
    v_prov bigint; v_proposed bigint; v_unresolved bigint;
BEGIN
    SELECT r.dataset_id, r.release_order INTO v_fw, v_fw_ord FROM cc.release r JOIN cc.dataset d USING (dataset_id)
     WHERE r.release_id = p_fw_release AND d.code = 'flywire';
    SELECT r.dataset_id, r.release_order INTO v_hb, v_hb_ord FROM cc.release r JOIN cc.dataset d USING (dataset_id)
     WHERE r.release_id = p_hb_release AND d.code = 'hemibrain';
    IF v_fw IS NULL OR v_hb IS NULL THEN
        RAISE EXCEPTION 'expected a flywire release and a hemibrain release' USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO cc.provenance (dataset_id, release_id, transformation_name, transformation_version, parameters)
    VALUES (v_fw, p_fw_release, 'propose_crosswalk_mappings', '1',
            jsonb_build_object('hemibrain_release', p_hb_release, 'max_candidates', p_max_candidates))
    RETURNING provenance_id INTO v_prov;

    CREATE TEMP TABLE _fw ON COMMIT DROP AS
    SELECT a.dataset_neuron_id AS n, a.annotation_value AS hb_types, s.annotation_value AS side
    FROM cc.annotation a
    JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
    LEFT JOIN cc.annotation s ON s.dataset_neuron_id = a.dataset_neuron_id AND s.annotation_key = 'side'
         AND s.valid_rel @> v_fw_ord AND upper_inf(s.tx)
    WHERE dn.dataset_id = v_fw AND a.annotation_key = 'hemibrain_type'
      AND a.valid_rel @> v_fw_ord AND upper_inf(a.tx)
      AND NOT EXISTS (SELECT 1 FROM cc.neuron_mapping m WHERE m.dataset_neuron_id = a.dataset_neuron_id
                      AND m.method = 'published_type_crosswalk');

    CREATE TEMP TABLE _hb ON COMMIT DROP AS
    SELECT t.dataset_neuron_id AS n, nt.neuron_type_id,
           CASE WHEN i.annotation_value ~ '_R$' THEN 'right'
                WHEN i.annotation_value ~ '_L$' THEN 'left' END AS side,
           m.canonical_neuron_id
    FROM cc.annotation t
    JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
    JOIN cc.neuron_type nt ON nt.name = t.annotation_value
    JOIN cc.neuron_mapping m ON m.dataset_neuron_id = t.dataset_neuron_id
         AND m.method = 'source_identity' AND m.review_status = 'approved'
    LEFT JOIN cc.annotation i ON i.dataset_neuron_id = t.dataset_neuron_id AND i.annotation_key = 'instance'
         AND i.valid_rel @> v_hb_ord AND upper_inf(i.tx)
    WHERE dn.dataset_id = v_hb AND t.annotation_key = 'cell_type'
      AND t.valid_rel @> v_hb_ord AND upper_inf(t.tx);

    CREATE TEMP TABLE _pair ON COMMIT DROP AS
    SELECT DISTINCT f.n AS fw_n, h.n AS hb_n, h.canonical_neuron_id, f.hb_types, f.side
    FROM _fw f
    JOIN cc.type_alias ta ON ta.dataset_id = v_fw AND ta.source_type = f.hb_types
    JOIN _hb h ON h.neuron_type_id = ta.neuron_type_id AND h.side = f.side;

    CREATE TEMP TABLE _scored ON COMMIT DROP AS
    SELECT p.*, count(*) OVER (PARTITION BY fw_n) AS n_hb, count(*) OVER (PARTITION BY hb_n) AS n_fw
    FROM _pair p;

    INSERT INTO cc.neuron_mapping (dataset_neuron_id, canonical_neuron_id, method, confidence, review_status,
                                   evidence, provenance_id)
    SELECT fw_n, canonical_neuron_id, 'published_type_crosswalk', round(1.0 / (n_hb * n_fw), 3), 'proposed',
           jsonb_build_object('hemibrain_type', hb_types, 'side', side, 'hemibrain_candidates', n_hb,
                              'flywire_candidates', n_fw, 'hemibrain_release', p_hb_release),
           v_prov
    FROM _scored WHERE n_hb <= p_max_candidates;
    GET DIAGNOSTICS v_proposed = ROW_COUNT;

    INSERT INTO cc.neuron_mapping (dataset_neuron_id, canonical_neuron_id, method, review_status, evidence,
                                   provenance_id)
    SELECT f.n, NULL, 'published_type_crosswalk', 'unresolved',
           jsonb_build_object('hemibrain_type', f.hb_types, 'side', f.side,
                              'reason', CASE WHEN c.n_hb IS NULL THEN 'no hemibrain neuron of this type and side'
                                             ELSE 'too many candidates' END,
                              'hemibrain_candidates', coalesce(c.n_hb, 0)),
           v_prov
    FROM _fw f
    LEFT JOIN (SELECT fw_n, max(n_hb) AS n_hb FROM _scored GROUP BY 1) c ON c.fw_n = f.n
    WHERE c.n_hb IS NULL OR c.n_hb > p_max_candidates;
    GET DIAGNOSTICS v_unresolved = ROW_COUNT;

    DROP TABLE _fw, _hb, _pair, _scored;
    RETURN jsonb_build_object('proposed', v_proposed, 'unresolved', v_unresolved, 'provenance_id', v_prov);
END $$;

-- Approve or reject one proposed mapping. Approval merges the source
-- neuron's singleton canonical identity into the mapped canonical neuron.
CREATE FUNCTION review_mapping(p_mapping bigint, p_decision text, p_reviewer text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    m cc.neuron_mapping%ROWTYPE;
BEGIN
    IF p_decision NOT IN ('approved', 'rejected') THEN
        RAISE EXCEPTION 'decision must be approved or rejected' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO m FROM cc.neuron_mapping WHERE mapping_id = p_mapping FOR UPDATE;
    IF NOT FOUND OR m.review_status <> 'proposed' THEN
        RAISE EXCEPTION 'mapping % is not a proposed mapping', p_mapping USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_decision = 'approved' THEN
        UPDATE cc.canonical_neuron c SET status = 'merged'
          FROM cc.neuron_mapping s
         WHERE s.dataset_neuron_id = m.dataset_neuron_id AND s.review_status = 'approved'
           AND c.canonical_neuron_id = s.canonical_neuron_id AND s.method = 'source_identity';
        UPDATE cc.neuron_mapping SET review_status = 'rejected', reviewed_at = now(), reviewed_by = p_reviewer,
               evidence = evidence || jsonb_build_object('superseded_by_mapping', p_mapping)
         WHERE dataset_neuron_id = m.dataset_neuron_id AND review_status = 'approved';
    END IF;
    UPDATE cc.neuron_mapping SET review_status = p_decision, reviewed_at = now(), reviewed_by = p_reviewer
     WHERE mapping_id = p_mapping;
END $$;
