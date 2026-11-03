-- Schema `er`: the team's ER diagram, entity by entity and attribute by
-- attribute, as views over the physical tables in `cc`.
--
-- The physical design stores each fact once with a valid-time range of
-- releases and a transaction-time range (bitemporal), keeps source neurons
-- per dataset, and puts row numbers on the facts themselves. These views
-- present exactly the diagram's columns so the conceptual model can be
-- queried directly:
--
--   SELECT * FROM er.connection WHERE from_neuron_id = 42;
--
-- Conventions
--   release_id / valid_from  release in which the fact first holds
--   valid_to                 first release in which it no longer holds (NULL = still holds)
--   transaction_from / _to   when the database recorded / retired the fact
--   from/to_neuron_id, canonical_neuron_id
--                            the canonical neuron of the source neuron via its
--                            approved neuron_mapping (every source neuron has
--                            one: its own identity, or an approved cross-dataset match)
--   raw_metadata             _id of the MongoDB document holding the original rows
SET search_path = cc, public;

ALTER TABLE dataset ADD COLUMN organization text,
                    ADD COLUMN repository text,
                    ADD COLUMN data_formats text[] NOT NULL DEFAULT '{}';
ALTER TABLE release ADD COLUMN description text;

CREATE INDEX neuron_region_synapses_neuron_idx ON neuron_region_synapses (dataset_neuron_id, release_id DESC);

CREATE SCHEMA er;

-- helpers --------------------------------------------------------------------
-- Latest value of each annotation for a source neuron (current belief).
CREATE FUNCTION er.latest_annotations(p_neuron bigint) RETURNS jsonb
STABLE LANGUAGE sql AS $$
    SELECT coalesce(jsonb_object_agg(annotation_key, annotation_value), '{}')
    FROM (SELECT DISTINCT ON (annotation_key) annotation_key, annotation_value
          FROM cc.annotation
          WHERE dataset_neuron_id = p_neuron AND upper_inf(tx)
          ORDER BY annotation_key, upper(valid_rel) DESC NULLS FIRST) a
$$;

-- Region with most synapses in the latest release that has region data.
CREATE FUNCTION er.primary_region(p_neuron bigint) RETURNS text
STABLE LANGUAGE sql AS $$
    SELECT l.code FROM cc.neuron_region_synapses s JOIN cc.brain_region_label l USING (brain_region_id)
    WHERE s.dataset_neuron_id = p_neuron
    ORDER BY s.release_id DESC, s.pre_synapses + s.post_synapses DESC, s.brain_region_id
    LIMIT 1
$$;

-- entities -------------------------------------------------------------------
CREATE VIEW er.dataset AS
SELECT d.dataset_id, d.name, d.description, d.organism, d.organization, d.repository, d.source_url,
       d.data_formats,                                                   -- multivalued
       (SELECT count(*) FROM cc.release r WHERE r.dataset_id = d.dataset_id) AS release_count   -- derived
FROM cc.dataset d;

CREATE VIEW er.release AS
SELECT r.release_id, r.dataset_id, r.version_label AS version_tag, r.release_date, r.description,
       r.manifest_hash AS checksum,
       o.neurons AS neuron_count, o.connections AS connection_count        -- derived
FROM cc.release r JOIN cc.release_overview o USING (release_id);

CREATE VIEW er.dataset_neuron AS
SELECT dn.dataset_neuron_id,
       dn.first_release_id AS release_id,                                 -- DEFINES: release that introduced it
       dn.source_neuron_id AS external_neuron_id,
       coalesce(a.v ->> 'instance', a.v ->> 'cell_type') AS original_name,
       a.v ->> 'cell_type' AS original_type,
       er.primary_region(dn.dataset_neuron_id) AS original_region,
       dn.metadata_doc_id AS raw_metadata,
       ARRAY(SELECT d2.code || ':' || o.source_neuron_id
             FROM cc.neuron_mapping m
             JOIN cc.neuron_mapping m2 ON m2.canonical_neuron_id = m.canonical_neuron_id
                                      AND m2.review_status = 'approved'
                                      AND m2.dataset_neuron_id <> m.dataset_neuron_id
             JOIN cc.dataset_neuron o ON o.dataset_neuron_id = m2.dataset_neuron_id
             JOIN cc.dataset d2 ON d2.dataset_id = o.dataset_id
             WHERE m.dataset_neuron_id = dn.dataset_neuron_id AND m.review_status = 'approved'
       ) AS alternate_ids                                                 -- multivalued
FROM cc.dataset_neuron dn,
     LATERAL (SELECT er.latest_annotations(dn.dataset_neuron_id) AS v) a;

CREATE VIEW er.canonical_neuron AS
SELECT c.canonical_neuron_id,
       c.label AS name,
       coalesce(t.name, a.v ->> 'cell_type') AS cell_type,
       coalesce(a.v ->> 'class', a.v ->> 'super_class') AS cell_class,
       er.primary_region(rep.dataset_neuron_id) AS brain_region,
       c.created_at,
       ARRAY(SELECT DISTINCT d.code || ':' || dn.source_neuron_id
             FROM cc.neuron_mapping m JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
             JOIN cc.dataset d USING (dataset_id)
             WHERE m.canonical_neuron_id = c.canonical_neuron_id AND m.review_status = 'approved'
             ORDER BY 1) AS alternative_names                             -- multivalued
FROM cc.canonical_neuron c
LEFT JOIN cc.neuron_type t USING (neuron_type_id)
-- representative source neuron: the one that created this identity
LEFT JOIN LATERAL (SELECT m.dataset_neuron_id FROM cc.neuron_mapping m
                   WHERE m.canonical_neuron_id = c.canonical_neuron_id AND m.review_status = 'approved'
                   ORDER BY (m.method = 'source_identity') DESC, m.mapping_id LIMIT 1) rep ON true
LEFT JOIN LATERAL (SELECT er.latest_annotations(rep.dataset_neuron_id) AS v) a ON true
WHERE c.status = 'active';

CREATE VIEW er.neuron_mapping AS
SELECT mapping_id, dataset_neuron_id, canonical_neuron_id,
       method AS mapping_method, confidence AS confidence_score, mapped_at, evidence,
       review_status                  -- not in the diagram; needed to read a mapping correctly
FROM cc.neuron_mapping;

CREATE VIEW er.connection AS
SELECT c.connection_id,
       rf.release_id,                                                     -- CONTAINS
       mf.canonical_neuron_id AS from_neuron_id,                          -- SENDS
       mt.canonical_neuron_id AS to_neuron_id,                            -- RECEIVES
       c.synapse_count AS weight,     -- both sources publish weight as a synapse count
       c.synapse_count,
       rf.release_id AS valid_from,
       rt.release_id AS valid_to,
       lower(c.tx) AS transaction_from,
       upper(c.tx) AS transaction_to
FROM cc.connection c
JOIN cc.release rf ON rf.dataset_id = c.dataset_id AND rf.release_order = lower(c.valid_rel)
LEFT JOIN cc.release rt ON rt.dataset_id = c.dataset_id AND rt.release_order = upper(c.valid_rel)
LEFT JOIN cc.neuron_mapping mf ON mf.dataset_neuron_id = c.pre_neuron_id AND mf.review_status = 'approved'
LEFT JOIN cc.neuron_mapping mt ON mt.dataset_neuron_id = c.post_neuron_id AND mt.review_status = 'approved';

CREATE VIEW er.annotation AS
SELECT a.annotation_id,
       m.canonical_neuron_id,                                             -- HAS_ANNOTATION
       rf.release_id,                                                     -- PROVIDES
       a.annotation_key AS annotation_type,
       a.annotation_value,
       rf.release_id AS valid_from,
       rt.release_id AS valid_to,
       sf.archive_path || CASE WHEN sf.member_path <> '' THEN '!' || sf.member_path ELSE '' END
           || ':' || a.source_line AS source
FROM cc.annotation a
JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
JOIN cc.release rf ON rf.dataset_id = dn.dataset_id AND rf.release_order = lower(a.valid_rel)
LEFT JOIN cc.release rt ON rt.dataset_id = dn.dataset_id AND rt.release_order = upper(a.valid_rel)
LEFT JOIN cc.neuron_mapping m ON m.dataset_neuron_id = a.dataset_neuron_id AND m.review_status = 'approved'
JOIN cc.provenance p ON p.provenance_id = a.provenance_id
LEFT JOIN cc.source_file sf USING (source_file_id)
WHERE upper_inf(a.tx);

-- One row per provenance record (ingestion run x source file x transformation).
-- Row-level lineage lives on every fact (source_line / source_lines), so the
-- record id and row number are given by er.record_provenance below instead
-- of one provenance row per synapse edge.
CREATE VIEW er.provenance AS
SELECT p.provenance_id,
       p.dataset_id AS source_dataset_id,
       p.release_id AS source_release_id,
       sf.archive_path || CASE WHEN sf.member_path <> '' THEN '!' || sf.member_path ELSE '' END AS source_file,
       NULL::text AS source_record_id,
       NULL::integer AS row_number,
       p.transformation_name AS transformation,
       p.transformation_version AS pipeline_version,
       coalesce(r.finished_at, p.created_at) AS ingested_at
FROM cc.provenance p
LEFT JOIN cc.source_file sf USING (source_file_id)
LEFT JOIN cc.ingestion_run r ON r.run_id = p.ingestion_run_id;

-- SOURCED_FROM at record level: provenance of one dataset_neuron, annotation
-- or connection, with its source record id and row number(s).
CREATE FUNCTION er.record_provenance(p_entity text, p_id bigint)
RETURNS TABLE (provenance_id bigint, source_dataset_id smallint, source_release_id integer, source_file text,
               source_record_id text, row_number integer, transformation text, pipeline_version text,
               ingested_at timestamptz)
STABLE LANGUAGE sql AS $$
    WITH rec AS (
        SELECT provenance_id, source_neuron_id AS rid, ARRAY[source_line] AS lines
        FROM cc.dataset_neuron WHERE p_entity = 'dataset_neuron' AND dataset_neuron_id = p_id
        UNION ALL
        SELECT a.provenance_id, dn.source_neuron_id, ARRAY[a.source_line]
        FROM cc.annotation a JOIN cc.dataset_neuron dn USING (dataset_neuron_id)
        WHERE p_entity = 'annotation' AND a.annotation_id = p_id
        UNION ALL
        SELECT c.provenance_id, pre.source_neuron_id || '->' || post.source_neuron_id, c.source_lines
        FROM cc.connection c
        JOIN cc.dataset_neuron pre ON pre.dataset_neuron_id = c.pre_neuron_id
        JOIN cc.dataset_neuron post ON post.dataset_neuron_id = c.post_neuron_id
        WHERE p_entity = 'connection' AND c.connection_id = p_id)
    SELECT p.provenance_id, p.source_dataset_id, p.source_release_id, p.source_file,
           rec.rid, line, p.transformation, p.pipeline_version, p.ingested_at
    FROM rec JOIN er.provenance p USING (provenance_id), unnest(rec.lines) AS line
$$;
