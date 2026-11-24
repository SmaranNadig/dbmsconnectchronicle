# Circuit Lab

Open **Circuit Lab** from the real-data dashboard. The default experiment uses FlyWire release 783, a right-eye patch of radius 4, and scene seed 42. Choose **Run experiment** to compute a new seed or reopen a matching saved run. Select an intervention and a trial to replay the intact and changed circuit together. The SQL console can inspect the same results shown by the website.

## Reproduce on a fresh installation

Start the project databases and load FlyWire 783 using the real-data procedure in the project guide. From the repository root in PowerShell, run:

```powershell
.venv/Scripts/python -m alembic upgrade head
curl.exe --fail --location https://storage.googleapis.com/flywire-data/codex/data/fafb/783/column_assignment.csv.gz -o data/raw/flywire/783/column_assignment.csv.gz
.venv/Scripts/python -m backend.circuit_snapshot --radius 4
.venv/Scripts/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8200
```

Run the frontend in a second terminal. Existing servers only need restarting after backend code changes.

```powershell
$env:API_URL='http://127.0.0.1:8200'
$env:PORT='3000'
node frontend/server.js
```

The website is at [Circuit Lab](http://127.0.0.1:3000/circuit.html). The importer verifies the pinned column-file SHA-256. Radii 2 through 6 are supported. Repeating the same import reuses its digest. Importing another snapshot keeps previous results available. The site runs offline after data preparation.

## What the database does

The importer joins column root IDs to dataset-scoped identities and release-valid, transaction-current cell annotations. It joins the resulting patch to source connections under the same PostgreSQL repeatable-read transaction. Exact types must agree. The included edges have positive synapse count and connect either L1/L2 to one of eight intermediates or an intermediate to a T4/T5 subtype. The published right-eye convention gives horizontal position proportional to q minus p. A radius specifies the bound on the absolute value of each hex coordinate.

Foreign keys connect each frozen edge to both snapshot neurons and the canonical connection. A trigger checks the source weight, endpoints and bitemporal validity when an edge is written. The canonical connection supplies source file and line lineage. A snapshot digest covers coordinates, weights and database record identities. The same dataset reloaded into a different database can have different internal IDs and digest.

Experiments run on the coordinator. A unique constraint on snapshot, model version and seed deduplicates requests. Each completed run and all 192 trials commit together. PostgreSQL stores compact replay frames in JSONB, computes the metrics and compares interventions with the same intact trial. The existing read-only console exposes these views.

```sql
SELECT s.radius, r.seed, m.condition, m.trials, m.accuracy, m.heading_improvement
FROM cc.circuit_results m
JOIN cc.circuit_run r USING (run_id)
JOIN cc.circuit_snapshot s USING (snapshot_id)
ORDER BY s.radius, r.seed, m.condition;

SELECT * FROM cc.circuit_paired_diff
WHERE run_id = 1 ORDER BY added_drift DESC;
```

## Model and interpretation

The input adapter takes positive or negative brightness changes for L1 and L2. The eight intervening types are Mi1, Mi4, Mi9, Tm1, Tm2, Tm3, Tm4 and Tm9. Each postsynaptic unit normalizes the incoming synapse counts across its selected inputs. The model assigns two temporal filters to intermediates and multiplies faster and slower afferent activity at T4/T5 outputs. These choices are engineering assumptions. A regularized linear readout uses eight mean subtype activities. Calibration uses 48 scenes and evaluation uses 24 separate balanced scenes with randomized phase, period, contrast and speed. The simulator uses ground truth for generating motion and scoring it. The steering controller receives the model readout.

The controls use the same scenes and frozen decoder. Local rewiring permutes destination stubs within source/target type pairs and target-coordinate blocks, preserving stub degrees before parallel edges are summed. Random silencing matches the T4 cell count. Shuffled weights permute raw weights within each layer before normalization. The disconnected output abstains and receives zero credit. Replay direction scores come from open-loop testing and heading error comes from the separate feedback rollout. A bright/dark moving-edge probe reports mean T4 and T5 activity independently of the decoder.

The model operates on horizontal gratings in one eye. Experimental physiology, natural movies, receptor-specific signs, native fly motor commands and equivalence of cells across releases remain outside the measured result. Functional comparison currently pairs an intact 783 circuit with controlled interventions. A release-to-release functional comparison requires independently verified spatial correspondences.

The approach draws on [Lappalainen et al.](https://doi.org/10.1038/s41586-024-07939-3), [fixed-decoder scale and silencing experiments](https://pubmed.ncbi.nlm.nih.gov/42627449/), [Maisak et al. on ON/OFF motion channels](https://pubmed.ncbi.nlm.nih.gov/23925246/), and the [FlyWire optic-column definitions](https://github.com/hsseung/OpticLobe.jl/blob/3352e97c37b0f96ab08f27b70b4420f3d4ac2726/src/columnassignment.jl).

