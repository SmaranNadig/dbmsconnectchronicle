# Datasets

All files were located by listing the public Google Cloud Storage buckets
`hemibrain` and `flywire-data` and the GitHub repository
`flyconnectome/flywire_annotations` on 2026-11-03, then opened to verify
columns. No login is required. Exact SHA-256 hashes, sizes, URLs and the
server's Last-Modified header of every downloaded file are in
`data/metadata/dataset_manifest.json`. Raw files are stored unmodified in
`data/raw/<dataset>/<release>/` and are not committed to Git.

## Hemibrain (Janelia FlyEM) — 3 real releases

| release | file | contents |
|---|---|---|
| v1.0 | `v1.0/conn_summary.tgz` (47 MB) | `exported-traced-adjacencies/` |
| v1.1 | `v1.1/exported-traced-adjacencies-v1.1.tar.gz` (46 MB) | same layout |
| v1.2 | `v1.2/exported-traced-adjacencies-v1.2.tar.gz` (46 MB) | same layout |

Base URL `https://storage.googleapis.com/hemibrain/`. Each archive holds

- `traced-neurons.csv` — `bodyId`, `type`, `instance` (~21.7k neurons).
  **Column order differs**: v1.0 is `bodyId,instance,type`, v1.1+ is `bodyId,type,instance`.
- `traced-total-connections.csv` — `bodyId_pre`, `bodyId_post`, `weight` (3.41M / 3.52M / 3.55M pairs)
- `traced-roi-connections.csv` — per brain region (not loaded yet; planned for anatomical partitioning)

Neuron ids are stable: 21,576 bodyIds occur in all three releases; v1.1 and
v1.2 have identical neuron sets. Release-to-release change is therefore
mostly reclassification and synapse-weight revision — real temporal evolution.

License: Janelia publishes the hemibrain data openly (CC-BY 4.0 per the
FlyEM release); cite Scheffer et al. 2020. Verify before redistribution.

## FlyWire (FAFB) via FlyWire Codex — 2 real materializations

Base URL `https://storage.googleapis.com/flywire-data/codex/data/fafb/<version>/`

| file | 630 | 783 |
|---|---|---|
| `neurons.csv.gz` (root_id, group, nt_type, nt scores) | 127,978 rows | 139,255 rows |
| `connections.csv.gz` (pre_root_id, post_root_id, neuropil, syn_count, nt_type) | 3,794,615 rows | 3,869,878 rows |
| `classification.csv.gz` | includes `cell_type`, `hemibrain_type` | those two columns removed |
| `consolidated_cell_types.csv.gz` (root_id, primary_type, additional_type(s)) | — | 139,255 rows |
| `fw_and_hemibrain_types.csv.gz` (root_id, cell_type, hemibrain_type) | — | 139,255 rows |

`connections.csv.gz` has one row per (pre, post, neuropil) and only pairs with
≥ 5 synapses per neuropil (Codex default threshold). ConnectomeChronicle stores
the pair total and keeps every contributing source line.

**Root ids are not stable**: only 106,785 ids occur in both 630 and 783
because FlyWire issues a new root id whenever a neuron is edited. The
database therefore never assumes that equal-looking ids across releases or
datasets are the same neuron, and a 630→783 diff reports id churn, which must
not be read as biological change.

License: FlyWire Codex terms of use; cite Dorkenwald et al. 2024 and
Schlegel et al. 2024.

## Reference correspondences — Schlegel et al. 2024

`https://raw.githubusercontent.com/flyconnectome/flywire_annotations/main/supplemental_files/Supplemental_file1_neuron_annotations.tsv`
(32 MB). Contains a `hemibrain_type` per FlyWire neuron: published
**cell-type-level** correspondences, not neuron-to-neuron identities. Used as
the reference for evaluating the planned graph-embedding matcher. Note that
FlyWire's own `hemibrain_type` column (used by the type crosswalk) derives
from the same work, so evaluating the crosswalk against it would be circular.

## Not used (yet)

- **MaleCNS, MANC** — neuPrint datasets requiring an API token. Adapter
  templates exist (`backend/etl/adapters.py`); adding them needs a mapping
  YAML and a catalog entry, not schema changes (tested with fixture datasets).

## Synthetic data

Only in `tests/fixtures/toy/` (hand-written, labelled) and only loaded into
the separate `connectome_test` database by the test suite.
