"""MongoDB source records: the heterogeneous, source-specific side of a neuron.

One document per dataset neuron in collection `neuron_source_record`:

    {_id: "<dataset>:<source id>",          # = dataset_neuron.metadata_doc_id
     dataset, source_neuron_id, dataset_neuron_id,
     releases: {"<release_id>": {release, ingestion_run_id, synced_at,
                                 records: [{file, source_file_id, sha256, line, fields}]}}}

`fields` is the original row with every column the source file has, mapped or
not (e.g. FlyWire's per-transmitter prediction scores), typed as pandas reads
them; empty cells are left out, so documents differ in shape by source and
release. PostgreSQL stays authoritative: a release is synced only after it is
loaded there, only from files whose SHA-256 still matches `source_file`, and
only for neurons that exist in `dataset_neuron`. Re-running a sync replaces
that release's entry, so it is idempotent.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache

import pandas as pd
from pymongo import MongoClient, UpdateOne
from pymongo.database import Database

from backend.config import PIPELINE_VERSION, get_settings
from backend.db import connect
from backend.etl.adapters import ValidationError, get_adapter
from backend.etl.sources import file_sha256, open_source

log = logging.getLogger("cc.metadata")

COLLECTION = "neuron_source_record"
BATCH = 5_000


@lru_cache
def _client(uri: str) -> MongoClient:
    # one pooled, thread-safe client per process
    return MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)


def mongo_db() -> Database:
    s = get_settings()
    return _client(s.mongo_uri)[s.mongo_db]


def ensure_indexes(mdb: Database) -> None:
    coll = mdb[COLLECTION]
    coll.create_index([("dataset", 1), ("source_neuron_id", 1)], unique=True)
    coll.create_index("dataset_neuron_id", unique=True)


def _clean(value):
    # numpy scalars are not BSON; NaN means the cell was empty
    if pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def _read_rows(ref, id_column: str):
    """Yield (source_id, line, fields) for every row of a neuron file."""
    sep = "\t" if ref.display.endswith(".tsv") else ","
    line = 2
    with open_source(ref) as fh:
        for chunk in pd.read_csv(fh, sep=sep, dtype={id_column: str}, chunksize=200_000):
            for rec in chunk.to_dict("records"):
                fields = {k: v for k, v in ((k, _clean(v)) for k, v in rec.items()) if v is not None}
                yield str(rec[id_column]).strip(), line, fields
                line += 1


def sync_release(dataset: str, release: str, mdb: Database | None = None) -> dict:
    mdb = mdb if mdb is not None else mongo_db()
    ensure_indexes(mdb)
    coll = mdb[COLLECTION]
    with connect() as conn:
        row = conn.execute("""SELECT r.release_id, r.dataset_id, r.ingestion_status
                              FROM release r JOIN dataset d USING (dataset_id)
                              WHERE d.code = %s AND r.version_label = %s""", (dataset, release)).fetchone()
        if row is None:
            raise ValidationError(f"{dataset} {release}: release is not registered")
        rel_id, ds_id, status = row
        if status != "loaded":
            raise ValidationError(f"{dataset} {release}: release is {status}; load it into PostgreSQL first")
        run_id = conn.execute("""SELECT max(run_id) FROM ingestion_run
                                 WHERE release_id = %s AND status = 'succeeded'""", (rel_id,)).fetchone()[0]
        sync_id = conn.execute(
            """INSERT INTO metadata_sync (release_id, mongo_database, mongo_collection, pipeline_version)
               VALUES (%s, %s, %s, %s) RETURNING metadata_sync_id""",
            (rel_id, mdb.name, COLLECTION, PIPELINE_VERSION)).fetchone()[0]
        conn.commit()
        try:
            stats = _sync(conn, coll, dataset, release, rel_id, ds_id, run_id)
        except Exception as exc:
            conn.rollback()
            conn.execute("""UPDATE metadata_sync SET status = 'failed', finished_at = clock_timestamp(),
                                   error_message = %s WHERE metadata_sync_id = %s""",
                         (f"{type(exc).__name__}: {exc}"[:2000], sync_id))
            conn.commit()
            raise
        conn.execute("""UPDATE metadata_sync SET status = 'completed', finished_at = clock_timestamp(),
                               documents_written = %s, records_written = %s, stats = %s
                        WHERE metadata_sync_id = %s""",
                     (stats["documents"], stats["records"], json.dumps(stats), sync_id))
        conn.commit()
    stats["metadata_sync_id"] = sync_id
    return stats


def _sync(conn, coll, dataset, release, rel_id, ds_id, run_id) -> dict:
    neurons = dict(conn.execute("SELECT source_neuron_id, dataset_neuron_id FROM dataset_neuron "
                                "WHERE dataset_id = %s", (ds_id,)).fetchall())
    files = {(a, m): (sf, sha) for sf, a, m, sha in conn.execute(
        "SELECT source_file_id, archive_path, member_path, sha256 FROM source_file WHERE release_id = %s",
        (rel_id,)).fetchall()}
    adapter = get_adapter(dataset, release)
    records: dict[str, list[dict]] = {}
    stats: dict = {"files": [], "rows_for_unknown_neurons": 0}
    for nf in adapter.neuron_files:
        key = (nf.ref.archive_rel, nf.ref.member)
        if key not in files:
            raise ValidationError(f"{nf.ref.display} was not part of the PostgreSQL load")
        sf_id, sha = files[key]
        if file_sha256(nf.ref.archive) != sha:
            raise ValidationError(f"{nf.ref.display} changed since it was loaded (SHA-256 mismatch)")
        n = unknown = 0
        for sid, line, fields in _read_rows(nf.ref, nf.id_column):
            if sid not in neurons:
                unknown += 1        # e.g. rows dropped by an unknown_neuron_policy: skip
                continue
            records.setdefault(sid, []).append(
                {"file": nf.ref.display, "source_file_id": sf_id, "sha256": sha, "line": line, "fields": fields})
            n += 1
        stats["files"].append({"file": nf.ref.display, "records": n, "rows_for_unknown_neurons": unknown})
        stats["rows_for_unknown_neurons"] += unknown
    now = datetime.now(timezone.utc)
    ops = [UpdateOne({"_id": f"{dataset}:{sid}"},
                     {"$set": {f"releases.{rel_id}": {"release": release, "ingestion_run_id": run_id,
                                                      "synced_at": now, "records": recs},
                               "updated_at": now},
                      "$setOnInsert": {"dataset": dataset, "source_neuron_id": sid,
                                       "dataset_neuron_id": neurons[sid]}},
                     upsert=True)
           for sid, recs in records.items()]
    for i in range(0, len(ops), BATCH):
        coll.bulk_write(ops[i:i + BATCH], ordered=False)
    stats["documents"] = len(ops)
    stats["records"] = sum(len(r) for r in records.values())
    log.info("%s %s: %d documents, %d records", dataset, release, stats["documents"], stats["records"])
    return stats


def check_consistency(dataset: str, mdb: Database | None = None) -> dict:
    """Cross-database reference check (no foreign key spans the two stores).

    - every dataset_neuron has a document with the same _id and dataset_neuron_id
    - every document belongs to an existing dataset_neuron
    - every neuron present in a synced release has that release in its document
    """
    mdb = mdb if mdb is not None else mongo_db()
    coll = mdb[COLLECTION]
    with connect() as conn:
        pg = {doc_id: nid for doc_id, nid in conn.execute(
            """SELECT dn.metadata_doc_id, dn.dataset_neuron_id FROM dataset_neuron dn
               JOIN dataset d USING (dataset_id) WHERE d.code = %s""", (dataset,)).fetchall()}
        synced = [r[0] for r in conn.execute(
            """SELECT DISTINCT s.release_id FROM metadata_sync s JOIN release r USING (release_id)
               JOIN dataset d USING (dataset_id) WHERE d.code = %s AND s.status = 'completed'""",
            (dataset,)).fetchall()]
        present = {rid: {r[0] for r in conn.execute(
            """SELECT dn.metadata_doc_id FROM neuron_version nv JOIN dataset_neuron dn USING (dataset_neuron_id)
               JOIN release r ON r.release_id = %s AND r.dataset_id = dn.dataset_id
               WHERE nv.valid_rel @> r.release_order AND upper_inf(nv.tx)""",
            (rid,)).fetchall()} for rid in synced}
    docs = {d["_id"]: d for d in coll.find({"dataset": dataset},
                                           {"dataset_neuron_id": 1, **{f"releases.{r}.release": 1 for r in synced}})}
    report = {
        "dataset": dataset,
        "postgres_neurons": len(pg),
        "mongo_documents": len(docs),
        # neurons of releases not synced yet are not expected to have a document
        "neurons_without_document": sorted(set().union(*present.values()) - set(docs))[:20],
        "documents_without_neuron": sorted(set(docs) - set(pg))[:20],
        "dataset_neuron_id_mismatch": sorted(k for k in set(pg) & set(docs)
                                             if docs[k].get("dataset_neuron_id") != pg[k])[:20],
        "missing_release_entries": {str(rid): sum(1 for k in ids if str(rid) not in docs.get(k, {}).get("releases", {}))
                                    for rid, ids in present.items()},
    }
    report["consistent"] = not (report["neurons_without_document"] or report["documents_without_neuron"]
                                or report["dataset_neuron_id_mismatch"]
                                or any(report["missing_release_entries"].values()))
    return report


def source_record(doc_id: str, mdb: Database | None = None) -> dict | None:
    mdb = mdb if mdb is not None else mongo_db()
    return mdb[COLLECTION].find_one({"_id": doc_id})
