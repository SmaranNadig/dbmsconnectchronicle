SET search_path = cc,public;
CREATE TABLE memory_snapshot (
 snapshot_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 release_id integer NOT NULL REFERENCES release,
 digest text NOT NULL UNIQUE,
 sources jsonb NOT NULL
);
CREATE TABLE memory_edge (
 snapshot_id bigint REFERENCES memory_snapshot,
 connection_id bigint REFERENCES connection,
 pathway text NOT NULL CHECK(pathway IN ('input','output','teaching')),
 channel text NOT NULL,
 regional_weight integer NOT NULL CHECK(regional_weight>0),
 source_line integer NOT NULL CHECK(source_line>0),
 PRIMARY KEY(snapshot_id,connection_id)
);
CREATE TABLE memory_run (
 run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 snapshot_id bigint NOT NULL REFERENCES memory_snapshot,
 seed integer NOT NULL CHECK(seed BETWEEN 0 AND 999999),
 version text NOT NULL,
 protocol jsonb NOT NULL,
 visual_run_id bigint REFERENCES circuit_run,
 UNIQUE(snapshot_id,seed,version)
);
CREATE TABLE memory_branch (
 branch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 run_id bigint NOT NULL REFERENCES memory_run,
 parent_id bigint, fork_step integer NOT NULL CHECK(fork_step BETWEEN 0 AND 80),
 intervention text NOT NULL,
 UNIQUE NULLS NOT DISTINCT(run_id,parent_id,fork_step,intervention),
 UNIQUE(run_id,branch_id),
 FOREIGN KEY(run_id,parent_id) REFERENCES memory_branch(run_id,branch_id)
);
CREATE TABLE memory_trial (
 branch_id bigint REFERENCES memory_branch,
 step integer CHECK(step BETWEEN 0 AND 80),
 phase text NOT NULL, presented text, reward boolean NOT NULL,
 left_odor text, choice text, p_a double precision CHECK(p_a BETWEEN 0 AND 1),
 state_hash text NOT NULL,
 PRIMARY KEY(branch_id,step)
);
ALTER TABLE memory_branch ADD FOREIGN KEY(parent_id,fork_step) REFERENCES memory_trial(branch_id,step);
CREATE TABLE memory_change (
 branch_id bigint, step integer, connection_id bigint REFERENCES connection,
 gain integer NOT NULL CHECK(gain BETWEEN 0 AND 10000),
 PRIMARY KEY(branch_id,step,connection_id),
 FOREIGN KEY(branch_id,step) REFERENCES memory_trial
);
CREATE TABLE memory_checkpoint (
 branch_id bigint, step integer, payload bytea NOT NULL,
 PRIMARY KEY(branch_id,step),
 FOREIGN KEY(branch_id,step) REFERENCES memory_trial
);
CREATE VIEW memory_preference AS SELECT branch_id,phase,count(*) AS encounters,
 avg((choice='A')::int) AS choice_a,avg(p_a) AS preference_a
 FROM memory_trial WHERE step>0 AND choice IS NOT NULL GROUP BY branch_id,phase;
CREATE VIEW memory_divergence AS SELECT b.branch_id,b.parent_id,
 min(x.step) FILTER(WHERE x.choice<>p.choice) AS first_changed_choice,
 min(x.step) FILTER(WHERE x.state_hash<>p.state_hash) AS first_changed_state
 FROM memory_branch b JOIN memory_trial x ON x.branch_id=b.branch_id AND x.step>b.fork_step
 JOIN memory_trial p ON p.branch_id=b.parent_id AND p.step=x.step GROUP BY b.branch_id,b.parent_id;

