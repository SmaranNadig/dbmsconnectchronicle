# ConnectomeChronicle

The interactive workspace includes a release-aware neuron explorer, visual
ConnectomeDiff, explainable change-quality analysis, correspondence review,
durable jobs, and snapshot-aware shard generations. Read
**[PROJECT_GUIDE.md](PROJECT_GUIDE.md)** for the concise explanation, tab guide,
methodology, interpretation and setup.

**Synthetic demo on Windows:** with Python 3.12, uv, Node.js and Docker Desktop
available, run `powershell -File scripts/start-demo.ps1`. It creates separate demo
databases and explicitly synthetic releases. The original public-data results
below are historical; current fixture reports are labelled separately.

A temporal, federated and distributed database for the evolution of neural
connectomes — RVCE Database Management Systems (CD252IA), Team 03.

ConnectomeChronicle ingests public *Drosophila* connectome releases
(hemibrain, FlyWire), keeps every source identifier and source line traceable,
stores the data **bitemporally** (which release a fact is valid in, and when
the database recorded it), and compares releases with **ConnectomeDiff**. All
of the relational integrity, temporal reconstruction and diff logic lives in
PostgreSQL (constraints, triggers and SQL functions). Python handles source
adapters, analysis, query routing, background jobs and the API.

It combines three kinds of database work in one system:

| Part | Technology | What it does |
|---|---|---|
| Relational core | PostgreSQL 16 | normalised schema from the team ER diagram; keys, CHECK, composite foreign keys and GiST exclusion constraints; append-only history trigger; bitemporal queries; views, a materialized view, SQL and PL/pgSQL functions |
| Document store | MongoDB 7 | one document per neuron holding every original source row (columns differ by dataset and release), linked to PostgreSQL by key, with a sync log and consistency check |
| Distributed storage | 3 PostgreSQL shards | hash, anatomical (brain region) and community (Louvain) partitioning, verified copies, a query router, benchmarks |
| Dashboard | npm + Express, plain HTML/JS | DBMS concepts with live evidence; read-only SQL and MongoDB query consoles; the SQL and NoSQL implementation; routed shard queries |

Results on the real data (2 datasets, 5 releases, 182,274 neurons): 9.0 M
connection rows stored instead of 15.8 M as full copies per release (43% fewer);
182,274 MongoDB documents; edge cut 66.7% (hash), 28.0% (anatomical), 27.1%
(community); 492 routed answers checked against the single server with 0
mismatches; 74 automated tests passing.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and ER model,
[DATASETS.md](DATASETS.md) for sources, [IMPLEMENTATION.md](IMPLEMENTATION.md)
for what is done and what is next, and [TESTING.md](TESTING.md) for the tests.

### Repository layout

```
backend/            Python: ETL, API (FastAPI), MongoDB sync, sharding, query consoles
  db/sql/           the schema: SQL migrations 0001-0010 (run by Alembic)
  etl/              source adapters, release loader (one transaction per release)
  distributed/      partitioning, shard deployment, query router, benchmark
alembic/            migration runner
config/             dataset catalog and per-dataset schema mappings
frontend/           dashboard (npm start; public/ holds the page and examples.json)
scripts/            dataset download, demo SQL
tests/              pytest suite + small synthetic fixtures (tests/fixtures/toy)
results/            latest benchmark JSON and test report
data/metadata/      download manifest with SHA-256 checksums (raw data is not in git)
```

The raw datasets (about 240 MB) are not committed. Download them with the
script below; the manifest records each file's checksum, so the download can
be checked.

## Prerequisites

- Windows, macOS or Linux with **Docker Desktop** running
- **Python 3.12** (pinned in `.python-version`)
- ~2 GB free disk for raw data, more for the database volume

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows (Git Bash: source .venv/Scripts/activate)
pip install -r requirements.lock
cp .env.example .env            # then set real passwords in .env
docker compose up -d --wait     # coordinator, 3 shards, MongoDB
alembic upgrade head            # create schemas cc and er (migrations 0001-0010)
```

Then either load the data yourself (next sections), or restore a snapshot
from a teammate who already has it loaded.

## Quick start from a snapshot (skip downloading and loading)

On the machine that already has the data loaded:

```bash
python scripts/snapshot.py export
```

This writes `snapshot/` (git-ignored): a PostgreSQL dump, a MongoDB archive,
and `snapshot.json` with SHA-256 checksums and the exact row count of every
table. Send the whole folder (e.g. through Google Drive). Do not send `.env`
or Docker containers: the other person uses their own `.env`, and Docker
downloads the same images from `docker-compose.yml`.

On the new machine, after **Setup** above, copy the folder into the project
root as `snapshot/` and run:

```bash
python scripts/snapshot.py import --with-shards
```

The import checks the file checksums, replaces the PostgreSQL and MongoDB
databases named in `.env`, re-grants the console's read-only role, and then
compares every table's row count and the MongoDB document count with the
exporting machine; it stops with a list of differences if anything does not
match. `--with-shards` then re-deploys the three sharding strategies (the
partition assignments are part of the dump) and verifies each shard against
the coordinator. The raw source files are not part of the snapshot; download
them only if you want to re-run the loaders.

## Data

```bash
python scripts/download_datasets.py --dataset all --dry-run   # show what will be fetched
python scripts/download_datasets.py --dataset all             # ~260 MB, recorded in data/metadata/dataset_manifest.json
```

## Load releases (always in release order per dataset)

```bash
python -m backend.cli inspect --dataset hemibrain --release v1.0
python -m backend.cli ingest  --dataset hemibrain --release v1.0
python -m backend.cli ingest  --dataset hemibrain --release v1.1
python -m backend.cli ingest  --dataset hemibrain --release v1.2
python -m backend.cli ingest  --dataset flywire   --release 630
python -m backend.cli ingest  --dataset flywire   --release 783
python -m backend.cli status
```

## Derived data: brain regions and MongoDB source records

```bash
python -m backend.cli regions  --dataset hemibrain --release v1.0   # per release, after ingest
python -m backend.cli metadata sync  --dataset hemibrain --release v1.0
python -m backend.cli metadata check --dataset hemibrain            # cross-store reference check
```

## Distributed storage (3 shards)

```bash
python -m backend.cli partition --strategy hash         # also: anatomical, community
python -m backend.cli deploy    --strategy hash         # copy to shards, verify against coordinator
python -m backend.cli bench                             # routed queries vs coordinator -> results/benchmarks/
```

## Query

```bash
python -m backend.cli neuron --dataset hemibrain --id 1688868386
python -m backend.cli diff   --dataset hemibrain --from v1.0 --to v1.1
docker exec -i cc_coordinator psql -U chronicle -d connectome < scripts/demo_queries.sql
```

The team's ER diagram can be queried directly through the `er` views
(`er.dataset`, `er.release`, `er.dataset_neuron`, `er.canonical_neuron`,
`er.neuron_mapping`, `er.connection`, `er.annotation`, `er.provenance`,
`er.record_provenance(entity, id)`); see ARCHITECTURE.md.

Entity resolution (in psql):

```sql
SELECT cc.assign_source_identity('hemibrain');
SELECT cc.assign_source_identity('flywire');
SELECT cc.build_type_crosswalk();
SELECT cc.propose_crosswalk_mappings(<flywire 783 release_id>, <hemibrain v1.2 release_id>);
```

## Dashboard (HTML, served with npm)

```bash
uvicorn backend.main:app --port 8000      # the API (from the project root, venv active)
npm --prefix frontend install             # once
npm --prefix frontend start               # http://localhost:3000
```

One page, organised around DBMS concepts so evaluators can check each one:

- **Query console**: type SQL (PostgreSQL) or a MongoDB find / count / aggregate
  and get live results; EXPLAIN / EXPLAIN ANALYZE shows the query plan. SQL runs
  as role `cc_reader` (SELECT only, migration 0010) in a READ ONLY transaction
  that is rolled back, as a single prepared statement, with a 10 s timeout and
  500-row cap. MongoDB refuses `$where`, `$function`, `$accumulator`, `$out`, `$merge`.
- **DBMS concepts**: ER model, relational queries, constraints and triggers,
  transactions, bitemporal data, indexing, views and functions, provenance,
  ConnectomeDiff, entity resolution, NoSQL, distribution. Each concept has live
  evidence and "Try it" queries (`frontend/public/examples.json`), plus a button
  that sends 9 invalid writes, all rejected by PostgreSQL and rolled back.
- **SQL implementation**: the migration files and the live system catalog
  (tables, constraints, indexes, triggers, functions).
- **NoSQL implementation**: the document model, indexes, sync log, and the same
  neuron in both stores.
- **Distributed**: strategy comparison, and one query routed to the shards
  next to the same query on the coordinator (shards touched, SQL sent, same answer).
- **Results**: test run, benchmark, requirement status.

Plain HTML/CSS/JS served by Express (no build step); every number is fetched
live from the API. Set `CC_WARM_ROUTERS=1` for the API to build the shard
routers at startup, so the first routed query does not wait for them.

## Tests

```bash
python -m pytest        # also writes results/tests/junit.xml (shown on the dashboard)
```

Tests create and use separate `connectome_test` databases (coordinator and
each shard) and a `connectome_meta_test` MongoDB database, with small
synthetic fixtures (`tests/fixtures/toy`, clearly labelled); the real
databases are never touched.

## Troubleshooting

- `docker: command not found` in Git Bash: add
  `%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin` to `PATH`, or use PowerShell.
- `load earlier release(s) first`: releases of a dataset must be loaded in order.
- A failed load is rolled back completely; fix the cause and run `ingest` again.
