# Synthetic test fixtures — NOT biological data

Hand-written toy connectomes used only by the automated tests to check exact
expected results. They are never loaded into the real `connectome` database.

| dataset | purpose |
|---|---|
| `toyfly` r1 → r2 → r3 | temporal storage, reconstruction, ConnectomeDiff. r2 removes neuron 4, adds 5, retypes 2 (B→B2), types 3, changes 1→2 from 5 to 8 synapses, removes 3→4, adds 3→5 and a zero-weight 5→1. r3 restores neuron 4 and 1→2 = 5. Neuron `720575940596125868` checks lossless 64-bit ids. |
| `toyorphan` r1 | an edge to unknown neuron 99 → load must fail and roll back |
| `toydup` r1 | the pair 1→2 appears twice with `duplicate_pairs: error` → load must fail |
| `toyfly` `edges_roi.csv` | per-region synapses: r1 and r2 aggregate to exact per-neuron region counts; r3 contains the unknown region code `XYZ(R)`, so its region load must fail and leave r1/r2 untouched |
