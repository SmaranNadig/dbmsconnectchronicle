SET search_path = cc, public;
COMMENT ON TABLE circuit_snapshot IS 'A content-addressed optic-column patch in one connectome release.';
COMMENT ON TABLE circuit_node IS 'A typed neuron and its eye coordinates in one frozen patch.';
COMMENT ON TABLE circuit_run IS 'A reproducible model and scene seed evaluated on one snapshot.';
ALTER TABLE circuit_edge ADD UNIQUE(snapshot_id, connection_id);
CREATE FUNCTION circuit_edge_source_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM cc.connection c
        JOIN cc.circuit_snapshot s ON s.snapshot_id=NEW.snapshot_id
        JOIN cc.release r ON r.release_id=s.release_id
        WHERE c.connection_id=NEW.connection_id AND c.dataset_id=r.dataset_id
        AND c.valid_rel @> r.release_order AND c.tx @> s.created_at
        AND (c.pre_neuron_id,c.post_neuron_id,c.synapse_count)=(NEW.pre_id,NEW.post_id,NEW.weight))
    THEN RAISE EXCEPTION 'Edge differs from the recorded release and source row' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER circuit_edge_source BEFORE INSERT OR UPDATE ON circuit_edge
FOR EACH ROW EXECUTE FUNCTION circuit_edge_source_check();
CREATE VIEW circuit_paired_diff AS
SELECT a.run_id, b.condition, count(*) AS paired_trials,
    sum(((sign(a.score)=a.direction) AND (sign(b.score)<>b.direction))::int) AS lost_correct,
    sum(((sign(a.score)<>a.direction) AND (sign(b.score)=b.direction))::int) AS gained_correct,
    avg(b.heading_error-a.heading_error) AS added_drift
FROM circuit_trial a JOIN circuit_trial b USING(run_id,trial)
WHERE a.condition='intact' AND b.condition<>'intact'
GROUP BY a.run_id,b.condition;

