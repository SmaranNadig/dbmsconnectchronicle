"""Cheap revision keys: temporal rows are append-only and source loads are versioned."""

import hashlib
import json


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def source_revision(conn):
    # Independent of row_factory; aggregate one JSON value.
    row = conn.execute("""SELECT jsonb_build_array(
        (SELECT jsonb_agg(jsonb_build_array(release_id, loaded_at, manifest_hash) ORDER BY release_id)
         FROM cc.release WHERE ingestion_status = 'loaded'),
        (SELECT max(neuron_version_id) FROM cc.neuron_version),
        (SELECT max(annotation_id) FROM cc.annotation),
        (SELECT max(connection_id) FROM cc.connection),
        (SELECT max(provenance_id) FROM cc.neuron_region_synapses)) AS revision""").fetchone()
    return digest(row["revision"] if isinstance(row, dict) else row[0])


def placement_revision(conn, strategy_id):
    row = conn.execute(
        """SELECT jsonb_build_array(count(*), max(assigned_at)) AS revision
                          FROM cc.partition_assignment WHERE strategy_id = %s""",
        (strategy_id,),
    ).fetchone()
    return digest(row["revision"] if isinstance(row, dict) else row[0])
