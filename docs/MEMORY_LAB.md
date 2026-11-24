# Odor Memory Lab

Open [Odor Memory Lab](http://127.0.0.1:3000/memory.html) on the real-data dashboard. Run conditioning and controls with a seed, then move **Inspect encounter** to trial 36. Select **Reward B** and **Fork from here**. The fork starts with the same gain hash. Advance the slider to trial 80 to see the final decision and retrieve the changed connections. The arena shows how the visual circuit corrects drift after the fly chooses an arm.

## Prepare the real data

Start the Compose databases and load FlyWire 783 using the [project guide](../PROJECT_GUIDE.md). From the project root, run the additive schema migrations and freeze the learning circuit.

```powershell
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m backend.memory_snapshot
```

The importer uses the three existing 783 archives under `data/raw/flywire/783` and verifies their SHA-256 digests against the coordinator’s source registry. It reuses the snapshot if the selected edges and source hashes match. To enable the two-arm arena, prepare a radius-four snapshot using [Circuit Lab setup](CIRCUIT_LAB.md) and save a visual run before creating the odor experiment. The run pins a visual reference from the same release and prefers seed 42 when available. Start or restart FastAPI and the existing frontend using the project guide. The lab runs without further data downloads.

## Circuit and behavioral interpretation

The snapshot has 47 olfactory channel labels, 115 Kenyon cells and 626 regional connections. The importer follows listed projection-neuron inputs in the right calyx to `KCg-m` cells, then joins their outputs to the `MBON01` arbor in the right medial lobe. The target cell body is annotated on the left. Eight `PAM01 → MBON01` connections in the same neuropil provide an anatomical scalar for the modeled reinforcement gate. Projection types and synapse locations support a γ5-associated pathway. The source neuropil covers the medial lobe at coarser resolution than an individual compartment.

The model uses two generated odor patterns with independent noise at each encounter. A rank threshold retains the strongest quarter of Kenyon-cell drive, with ties at threshold contributing zero activity. The readout compares the modeled reduction in MBON01 drive for the two odor cues. During reinforced presentations the active output gains move toward a lower bound. Unreinforced presentations of a training cue allow partial recovery. Both rules and the fixed choice readout are modeling assumptions. The choices are recorded before reinforcement. During baseline, retention and final probes, the schedule has no gain updates. The odor-mixture table varies the input pattern leading into the same circuit and holds the selected state fixed.

The standard controls include frozen gains from the start, shuffled reward timing, and rewired input connections. The current schedule gives each cue the same number of rewards within each shuffled block. Rewiring permutes target stubs while keeping the source and target stub counts. Parallel stubs can land on the same pair. The simple odor-value baseline uses nearest-template classification and two learned values. The output-silenced branch and its rescue start at trial 68. Their gains match while behavior changes due to the readout. A custom fork inherits the exact stored gains and applies the selected intervention preset to subsequent encounters.

In the arena, learned odor preference selects the arm. A matched control chooses sides with a fixed probability of one half and uses the same stored random draw. The visual circuit receives wall luminance at its eye coordinates at every frame. Its decoder drives the lateral steering correction, and the resulting offset shifts the next retinal input. Forward motion and the nominal path down the selected arm are prescribed. The four conditions share their trial indices, texture parameters and crosswinds. The endpoint reports arm choice and arrival error separately from success, which requires the target arm and an arrival offset within three arena units. Trajectories can be reproduced from the two referenced circuit states, the protocol and the arena seed.

The task draws on published work on olfactory coding, dopamine-mediated memory and reversal. The simplified assay establishes properties of the implemented model under generated stimuli. [Mushroom-body connectome](https://doi.org/10.7554/eLife.62576) · [Olfactory input wiring](https://pmc.ncbi.nlm.nih.gov/articles/PMC12124391/) · [Dopamine and memory dynamics](https://doi.org/10.1038/s41586-024-07819-w) · [A reversal-learning experiment](https://doi.org/10.1038/s41467-021-21388-w)

## Database design and reproducibility

- The circuit edges reference the project's bitemporal connection records. The `memory_wiring` view adds the release-783 regional row, neuropil and archive checksum. A canonical connection may have originally entered the store in an earlier release.
- The run, its two stimulus vectors and its shared encounter schedule commit together. Every branch begins with a checkpoint whose SHA-256 matches its parent's exact trial. PostgreSQL enforces eligible source and output edges, valid next encounters and append-only history.
- Each encounter, next set of gains and scheduled checkpoint commit in one transaction. A branch row lock serializes simultaneous retries. Resuming continues at the first uncommitted encounter. A fork can use any committed encounter in its parent's own timeline.
- `cc.memory_state_at(branch, step)` starts at the latest checkpoint, takes the most recent intervening gain for each edge and reconstructs the state. The API verifies the reconstructed state against its stored digest before showing it. The run and branch JSON exports include the inputs and choices needed for independent replay.
- The SQL views compare matching encounters across branches. Reversal pace is the number of encounters from the fork to the third consecutive scored training or final encounter with probability of choosing A below one half. The actual first change in sampled choice is reported separately.

```sql
SELECT branch_id, parent_id, phase, paired_trials, preference_change, changed_choices
FROM cc.memory_paired_probes
ORDER BY branch_id, phase;

SELECT d.branch_id, b.intervention, b.fork_step, d.first_changed_state,
       d.first_changed_choice, r.encounters_after_fork
FROM cc.memory_divergence d
JOIN cc.memory_branch b USING (branch_id)
LEFT JOIN cc.memory_reversal r USING (branch_id)
ORDER BY d.branch_id;
```

Use the console link in the lab to inspect the actual gains and source rows at the selected trial. `memory_stimulus` and `memory_schedule` expose the inputs and reward randomization to the existing query console.

## Storage experiment

**Benchmark PostgreSQL replay** independently simulates each state and compares two temporary relational representations. One stores a compact two-byte gain vector per trial. The other stores periodic vectors and individual changed gains. The benchmark measures PostgreSQL table and index allocation, bulk insertion time, median read time, and latency for the first and last trials between checkpoints. It checks every reconstruction against the stored trial hash. The temporary tables share the same session and hardware. Their writes omit the durability overhead of permanent tables, so use the measurements for comparisons within this workload.

