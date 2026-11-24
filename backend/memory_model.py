"""Bounded associative rate model with independent streams for cues, positions and choices."""

import hashlib
import numpy as np

VERSION = "mb-gamma5-v2"
LAST = 80
CHECKPOINT_EVERY = 10
RETRAINING_START = 36
FINAL_PROBE_START = 68


def fingerprint(state):
    return hashlib.sha256(state.astype("<u2").tobytes()).hexdigest()


def protocol(channels, seed):
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(channels))
    n = max(2, len(channels) // 4)
    odor = np.full((2, len(channels)), 0.05)
    for i, group in enumerate([order[:n], order[n - 1 : 2 * n - 1]]):
        odor[i, group] = 1
    events = []
    for name, count in [
        ("baseline", 8),
        ("conditioning", 16),
        ("retention", 12),
        ("retraining", 32),
        ("final", 12),
    ]:
        presented = rng.permutation(np.tile([0, 1], count // 2))
        # Give both odors the same number of rewards in each training block.
        shuffled = np.empty(count, dtype=bool)
        for cue in (0, 1):
            shuffled[presented == cue] = rng.permutation(np.tile([False, True], count // 4))
        left = rng.permutation(np.tile([0, 1], count // 2))
        for i in range(count):
            events.append(
                dict(
                    phase=name,
                    presented=int(presented[i]),
                    left=int(left[i]),
                    unpaired=bool(shuffled[i]),
                    draw=float(rng.random()),
                    noise=int(rng.integers(2**31)),
                )
            )
    return {"channels": channels, "odors": odor.tolist(), "events": events}


class Pathway:
    def __init__(self, edges, seed, mode):
        inputs = [e for e in edges if e[1] == "input"]
        outputs = [e for e in edges if e[1] == "output"]
        self.channels = sorted({e[2] for e in inputs})
        cells = sorted({e[3] for e in outputs})
        cell_index = {key: i for i, key in enumerate(cells)}
        channel_index = {key: i for i, key in enumerate(self.channels)}
        self.matrix = np.zeros((len(cells), len(self.channels)))
        targets = [cell_index[e[4]] for e in inputs]
        if mode == "rewired":
            targets = np.random.default_rng(seed + 907).permutation(targets)
        for target, edge in zip(targets, inputs):
            self.matrix[target, channel_index[edge[2]]] += edge[5]
        self.matrix /= np.maximum(1, self.matrix.sum(axis=1, keepdims=True))
        self.ids = [e[0] for e in outputs]
        self.index = {k: i for i, k in enumerate(self.ids)}
        self.post = [cell_index[e[3]] for e in outputs]
        self.weights = np.array([e[5] for e in outputs])
        teaching = sum(e[5] for e in edges if e[1] == "teaching")
        self.gate = teaching / (10 + teaching)
        self.initial = np.full(len(outputs), 10000, dtype="<u2")

    def activity(self, patterns):
        drive = np.clip(patterns, 0, 1) @ self.matrix.T
        return np.maximum(0, drive - np.quantile(drive, 0.75, axis=1, keepdims=True))[:, self.post]

    def preference(self, state, activity, mode):
        weighted_activity = activity * self.weights
        depression = (
            weighted_activity
            @ (1 - state.astype(float) / 10000)
            / np.maximum(weighted_activity.sum(axis=1), 1e-9)
        )
        contrast = depression[:-1] - depression[-1]
        if mode == "silent_output":
            contrast[:] = 0
        return 1 / (1 + np.exp(-6 * contrast))

    def mixtures(self, state, odors, mode):
        ratios = np.linspace(0, 1, 5)
        a, b = np.asarray(odors)
        # Last row is the reference. An independent neural response is computed for each cue.
        patterns = np.vstack((ratios[:, None] * a + (1 - ratios[:, None]) * b, b))
        probabilities = self.preference(state, self.activity(patterns), mode)
        return [
            {"percent_a": int(100 * ratio), "probability": float(p)}
            for ratio, p in zip(ratios, probabilities)
        ]

    def encounter(self, state, spec, step, mode):
        event = spec["events"][step - 1]
        # Independently perturbed presentations, with greater variation in unrewarded probes.
        training = event["phase"] in ("conditioning", "retraining")
        noisy = np.array(spec["odors"]) + np.random.default_rng(event["noise"]).normal(
            0, 0.09 if training else 0.18, (2, len(self.channels))
        )
        active = self.activity(noisy)
        p_a = float(self.preference(state, active, mode)[0])
        left = event["left"]
        choose_left = event["draw"] < (p_a if left == 0 else 1 - p_a)
        choice = left if choose_left else 1 - left
        cue = event["presented"]
        reward = training and (event["unpaired"] if mode == "unpaired" else cue == int(mode == "paired_B"))
        after = state.copy()
        if training and mode != "frozen":
            activity = active[cue] / max(active[cue].max(), 1e-9)
            target = 1800 if reward else 10000
            after = np.rint(
                state + (0.48 if reward else 0.22) * self.gate * activity * (target - state.astype(float))
            ).astype("<u2")
        return after, (
            event["phase"],
            "AB"[cue] if training else None,
            reward,
            "AB"[left],
            "AB"[choice],
            p_a,
        )
