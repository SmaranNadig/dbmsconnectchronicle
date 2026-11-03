-- Per-release neuron and connection counts, materialized. Counting a release
-- scans millions of versioned rows (~25 s on the real data), too slow for an
-- API. The loader refreshes this view inside the load transaction of every
-- release, so the counts commit (or roll back) together with the data.
-- release_overview remains the live, always-exact version.
SET search_path = cc, public;

CREATE MATERIALIZED VIEW release_stats AS
SELECT release_id, neurons, connections, now() AS computed_at
FROM release_overview;

CREATE UNIQUE INDEX release_stats_pk ON release_stats (release_id);
