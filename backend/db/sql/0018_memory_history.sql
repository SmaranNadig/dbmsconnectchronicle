SET search_path = cc, public;

-- Make the shared stimulus vectors and encounter order queryable by trial key.
ALTER TABLE memory_run ADD COLUMN channels text[];
UPDATE memory_run SET channels = ARRAY(SELECT jsonb_array_elements_text(protocol->'channels'));
ALTER TABLE memory_run ALTER COLUMN channels SET NOT NULL;
ALTER TABLE memory_run ADD CHECK (cardinality(channels) >= 4);

CREATE TABLE memory_stimulus (
    run_id bigint REFERENCES memory_run,
    odor text CHECK (odor IN ('A', 'B')),
    activity double precision[] NOT NULL CHECK (
        cardinality(activity) >= 4 AND array_position(activity, NULL) IS NULL
        AND 0 <= ALL(activity) AND 1 >= ALL(activity)),
    PRIMARY KEY (run_id, odor)
);

CREATE TABLE memory_schedule (
    run_id bigint REFERENCES memory_run,
    step integer CHECK (step BETWEEN 1 AND 80),
    phase text NOT NULL CHECK (phase IN ('baseline', 'conditioning', 'retention', 'retraining', 'final')),
    presented smallint NOT NULL CHECK (presented IN (0, 1)),
    left_position smallint NOT NULL CHECK (left_position IN (0, 1)),
    unpaired boolean NOT NULL,
    draw double precision NOT NULL CHECK (draw >= 0 AND draw < 1),
    noise bigint NOT NULL CHECK (noise >= 0 AND noise < 2147483648),
    PRIMARY KEY (run_id, step)
);

INSERT INTO memory_stimulus
SELECT run_id, CASE n WHEN 1 THEN 'A' ELSE 'B' END,
       ARRAY(SELECT jsonb_array_elements_text(vector)::double precision)
FROM memory_run CROSS JOIN LATERAL jsonb_array_elements(protocol->'odors') WITH ORDINALITY AS o(vector, n);

INSERT INTO memory_schedule
SELECT run_id, n, e->>'phase', (e->>'presented')::smallint, (e->>'left')::smallint,
       (e->>'unpaired')::boolean, (e->>'draw')::double precision, (e->>'noise')::bigint
FROM memory_run CROSS JOIN LATERAL jsonb_array_elements(protocol->'events') WITH ORDINALITY AS s(e, n);

ALTER TABLE memory_run DROP COLUMN protocol;

ALTER TABLE memory_snapshot ADD CHECK (digest ~ '^[0-9a-f]{64}$');
ALTER TABLE memory_branch ADD CHECK (
    intervention IN ('paired_A', 'paired_B', 'frozen', 'unpaired', 'rewired', 'silent_output')
    AND (parent_id IS NULL AND fork_step = 0 AND intervention = 'paired_A' OR parent_id < branch_id AND parent_id IS NOT NULL));
ALTER TABLE memory_trial ADD CHECK (state_hash ~ '^[0-9a-f]{64}$');
ALTER TABLE memory_trial ADD CHECK (
    (phase = 'checkpoint' AND NOT reward AND presented IS NULL
        AND left_odor IS NULL AND choice IS NULL AND p_a IS NULL)
    OR (phase IN ('baseline', 'conditioning', 'retention', 'retraining', 'final')
        AND left_odor IS NOT NULL AND left_odor IN ('A', 'B')
        AND choice IS NOT NULL AND choice IN ('A', 'B') AND p_a IS NOT NULL
        AND (phase IN ('conditioning', 'retraining') AND presented IS NOT NULL AND presented IN ('A', 'B')
            OR phase IN ('baseline', 'retention', 'final') AND presented IS NULL AND NOT reward)));

CREATE FUNCTION cc.memory_validate_insert() RETURNS trigger LANGUAGE plpgsql
SET search_path = cc, pg_catalog AS $$
DECLARE
    previous integer;
    branch memory_branch%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME = 'memory_edge' THEN
        IF NOT EXISTS (
            SELECT FROM memory_snapshot s JOIN release r USING (release_id)
            JOIN connection c ON c.connection_id = NEW.connection_id AND c.dataset_id = r.dataset_id
            WHERE s.snapshot_id = NEW.snapshot_id AND c.valid_rel @> r.release_order
              AND upper_inf(c.tx) AND c.synapse_count >= NEW.regional_weight
        ) THEN
            RAISE EXCEPTION 'Edge must reference a current, release-valid connection' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'memory_change' THEN
        IF NOT EXISTS (
            SELECT FROM memory_branch b JOIN memory_run r USING (run_id)
            JOIN memory_edge e USING (snapshot_id) JOIN memory_trial t ON t.branch_id = b.branch_id
            WHERE (b.branch_id, t.step, e.connection_id) = (NEW.branch_id, NEW.step, NEW.connection_id)
              AND e.pathway = 'output' AND t.phase IN ('conditioning', 'retraining')
              AND b.intervention <> 'frozen'
        ) THEN
            RAISE EXCEPTION 'Weight change must target a plastic output edge during training' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'memory_checkpoint' THEN
        IF NOT EXISTS (
            SELECT FROM memory_trial t WHERE (t.branch_id, t.step) = (NEW.branch_id, NEW.step)
              AND t.state_hash = encode(sha256(NEW.payload), 'hex')
        ) THEN
            RAISE EXCEPTION 'Checkpoint must match its committed trial digest' USING ERRCODE = '23514';
        END IF;
    ELSE
        SELECT * INTO STRICT branch FROM memory_branch WHERE branch_id = NEW.branch_id FOR UPDATE;
        SELECT max(step) INTO previous FROM memory_trial WHERE branch_id = NEW.branch_id;
        IF previous IS NULL THEN
            IF NEW.step <> branch.fork_step OR NEW.phase <> 'checkpoint' OR
                (branch.parent_id IS NOT NULL AND NEW.state_hash IS DISTINCT FROM (
                    SELECT state_hash FROM memory_trial WHERE branch_id = branch.parent_id AND step = branch.fork_step
                )) THEN
                RAISE EXCEPTION 'Branch must start at its exact fork state' USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.step <> previous + 1 OR NOT EXISTS (
            SELECT FROM memory_schedule s WHERE s.run_id = branch.run_id AND s.step = NEW.step
              AND s.phase = NEW.phase AND NEW.left_odor = substr('AB', s.left_position + 1, 1)
              AND (NEW.presented IS NULL OR NEW.presented = substr('AB', s.presented + 1, 1))
        ) THEN
            RAISE EXCEPTION 'Trial must append the next scheduled encounter' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER memory_edge_source BEFORE INSERT ON memory_edge
FOR EACH ROW EXECUTE FUNCTION cc.memory_validate_insert();
CREATE TRIGGER memory_change_output BEFORE INSERT ON memory_change
FOR EACH ROW EXECUTE FUNCTION cc.memory_validate_insert();
CREATE TRIGGER memory_checkpoint_hash BEFORE INSERT ON memory_checkpoint
FOR EACH ROW EXECUTE FUNCTION cc.memory_validate_insert();
CREATE TRIGGER memory_trial_order BEFORE INSERT ON memory_trial
FOR EACH ROW EXECUTE FUNCTION cc.memory_validate_insert();

CREATE FUNCTION cc.memory_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Committed memory history is append-only (%)', TG_TABLE_NAME USING ERRCODE = '23000';
END $$;

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['memory_snapshot', 'memory_edge', 'memory_run', 'memory_stimulus',
        'memory_schedule', 'memory_branch', 'memory_trial', 'memory_change', 'memory_checkpoint']
    LOOP
        EXECUTE format('CREATE TRIGGER memory_history BEFORE UPDATE OR DELETE ON cc.%I '
                       'FOR EACH ROW EXECUTE FUNCTION cc.memory_append_only()', table_name);
    END LOOP;
END $$;

CREATE FUNCTION cc.memory_state_at(target_branch bigint, target_step integer)
RETURNS TABLE (connection_id bigint, gain integer)
LANGUAGE sql STABLE SET search_path = cc, pg_catalog AS $$
    WITH checkpoint AS MATERIALIZED (
        SELECT step, payload FROM memory_checkpoint
        WHERE branch_id = target_branch AND step <= target_step
          AND EXISTS (SELECT FROM memory_trial WHERE branch_id = target_branch AND step = target_step)
        ORDER BY step DESC LIMIT 1
    ), output_edges AS (
        SELECT e.connection_id, (row_number() OVER (ORDER BY e.connection_id)::int - 1) * 2 AS byte_index
        FROM memory_branch b JOIN memory_run r USING (run_id) JOIN memory_edge e USING (snapshot_id)
        WHERE b.branch_id = target_branch AND pathway = 'output'
    ), changes AS (
        SELECT DISTINCT ON (c.connection_id) c.connection_id, c.gain
        FROM memory_change c, checkpoint k
        WHERE branch_id = target_branch AND c.step > k.step AND c.step <= target_step
        ORDER BY c.connection_id, c.step DESC
    )
    SELECT e.connection_id, coalesce(c.gain, get_byte(k.payload, e.byte_index)
                                + 256 * get_byte(k.payload, e.byte_index + 1))
    FROM output_edges e CROSS JOIN checkpoint k LEFT JOIN changes c USING (connection_id)
    ORDER BY e.connection_id
$$;

CREATE VIEW memory_wiring AS
SELECT e.*, CASE e.pathway WHEN 'input' THEN 'MB_CA_R' ELSE 'MB_ML_R' END AS neuropil,
       a.source_neuron_id AS pre_root, b.source_neuron_id AS post_root,
       f.archive_path, f.sha256
FROM memory_edge e JOIN memory_snapshot s USING (snapshot_id)
JOIN connection c USING (connection_id)
JOIN dataset_neuron a ON a.dataset_neuron_id = c.pre_neuron_id
JOIN dataset_neuron b ON b.dataset_neuron_id = c.post_neuron_id
LEFT JOIN source_file f ON f.release_id = s.release_id AND f.archive_path = 'flywire/783/connections.csv.gz'
                       AND f.member_path = '' AND f.sha256 = s.sources->>'connections';

CREATE VIEW memory_paired_probes AS
SELECT b.branch_id, b.parent_id, child.phase, count(*) AS paired_trials,
       avg(child.p_a - parent.p_a) AS preference_change,
       count(*) FILTER (WHERE child.choice <> parent.choice) AS changed_choices
FROM memory_branch b JOIN memory_trial child ON child.branch_id = b.branch_id AND child.step > b.fork_step
JOIN memory_trial parent ON parent.branch_id = b.parent_id AND parent.step = child.step
WHERE child.phase IN ('baseline', 'retention', 'final') GROUP BY b.branch_id, b.parent_id, child.phase;

CREATE VIEW memory_reversal AS
WITH moving AS (
    SELECT t.branch_id, b.fork_step, t.step,
           count(*) OVER recent AS observations, max(p_a) OVER recent AS max_p_a
    FROM memory_trial t JOIN memory_branch b USING (branch_id)
    WHERE b.intervention = 'paired_B' AND t.phase IN ('conditioning', 'retraining', 'final')
    WINDOW recent AS (PARTITION BY t.branch_id ORDER BY t.step ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
)
SELECT branch_id, min(step) AS first_sustained_b, min(step) - fork_step AS encounters_after_fork
FROM moving WHERE observations = 3 AND max_p_a < 0.5 GROUP BY branch_id, fork_step;

COMMENT ON TABLE memory_run IS 'One versioned odor experiment, its channel order and visual replay reference.';
COMMENT ON TABLE memory_stimulus IS 'The input vector for one odor in a versioned experiment.';
COMMENT ON TABLE memory_schedule IS 'The shared sensory and randomization schedule for all branches of an experiment.';
COMMENT ON FUNCTION cc.memory_state_at(bigint, integer) IS
    'Reconstruct ordered model gains from the nearest checkpoint and latest intervening change per edge.';

