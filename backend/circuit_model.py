"""Small CPU rate model. All units, delays and controller gains are modeling assumptions."""
import numpy as np
from scipy.sparse import csr_matrix

from backend.circuit_snapshot import INPUT, MID, OUTPUT

VERSION = "motion-rate-1"
CONDITIONS = ("intact", "silence_T4", "silence_T5", "random_silence", "rewired", "shuffled_weights",
              "disconnected", "pixel_correlator")
STEPS = 32


def wiring(nodes, edges, condition, seed):
    rng = np.random.default_rng(seed)
    groups = [[n for n in nodes if n[1] in types] for types in (INPUT, MID, OUTPUT)]
    types = [np.array([n[1] for n in group]) for group in groups]
    matrices = []
    for layer in range(2):
        src = {n[0]: i for i, n in enumerate(groups[layer])}
        dst = {n[0]: i for i, n in enumerate(groups[layer + 1])}
        rows = [(dst[b], src[a], weight) for a, b, weight in edges if a in src and b in dst]
        post, pre, weight = np.array(rows, dtype=int).T
        if condition == "rewired":
            # Permute destination stubs within type pair and spatial block. Preserves in/out degrees.
            blocks = {}
            for i, (a, b) in enumerate(zip(pre, post)):
                _, typ, p, q = groups[layer + 1][b]
                blocks.setdefault((types[layer][a], typ, p // 3, q // 3), []).append(i)
            for indices in blocks.values():
                post[indices] = rng.permutation(post[indices])
        if condition == "shuffled_weights":
            rng.shuffle(weight)
        mat = csr_matrix((weight.astype(float), (post, pre)), shape=(len(dst), len(src)))
        norm = np.asarray(mat.sum(axis=1)).ravel().clip(1)
        matrices.append(mat.multiply((1 / norm)[:, None]).tocsr())
    keep = np.ones(len(types[2]))
    if condition in ("silence_T4", "silence_T5"):
        keep[np.char.startswith(types[2], condition[-2:])] = 0
    if condition == "random_silence":
        keep[rng.choice(len(keep), np.char.startswith(types[2], "T4").sum(), replace=False)] = 0
    if condition == "disconnected":
        keep[:] = 0
    x = np.array([n[3] - n[2] for n in groups[0]])[:, None]
    return matrices, types, x, keep


def scenes(seed, count):
    rng = np.random.default_rng(seed)
    return (np.tile([-1, 1], count // 2), rng.uniform(0, 2*np.pi, count),
            rng.uniform(6, 14, count), rng.uniform(.26, .52, count),
            rng.uniform(.25, .45, count))


class MotionCircuit:
    """Advance the same retinal, intermediate and output dynamics once per frame."""

    def __init__(self, graph, batch):
        self.matrices, types, self.coordinates, self.keep = graph
        self.on_channel = (types[0] == "L1")[:, None]
        self.slow = np.isin(types[1], ["Mi4", "Mi9", "Tm1", "Tm9"])[:, None]
        self.alpha = np.where(self.slow, .18, .8)
        self.mid = np.zeros((len(self.slow), batch))
        self.readout_groups = [types[2] == typ for typ in OUTPUT]
        self.previous = None

    def advance(self, frame):
        delta = np.zeros_like(frame) if self.previous is None else frame - self.previous
        self.previous = frame
        on_off = np.maximum(np.where(self.on_channel, delta, -delta), 0)
        inputs, outputs = self.matrices
        self.mid += self.alpha * (inputs @ on_off - self.mid)
        response = (outputs @ (self.mid * self.slow)) * (outputs @ (self.mid * ~self.slow))
        response *= self.keep[:, None]
        return np.stack([response[mask].mean(axis=0) for mask in self.readout_groups], axis=1)


def grating(coordinates, position, angle, phase, contrast):
    theta = (coordinates - position) * angle + phase
    return .5 + contrast * (.75 * np.sin(theta) + .25 * np.sin(2 * theta + .5))


def rollout(graph, scene, decoder=None, pixel=False, edge_polarity=None):
    direction, phase, period, speed, contrast = scene
    batch = len(speed)
    circuit = MotionCircuit(graph, batch)
    position = np.zeros(batch)
    action = np.zeros(batch)
    heading_sum = np.zeros(batch)
    features = []
    replay = []
    x = circuit.coordinates
    grid = np.arange(int(x.min()), int(x.max()) + 1)[:, None]
    previous_grid = None
    pixel_scores = []
    for t in range(STEPS):
        position += direction * speed - action
        heading_sum += abs(position)
        angle = 2*np.pi / period
        def light(coords):
            if edge_polarity is not None:
                # A single bright or dark front sweeps the same retinal columns.
                distance = direction * (coords - position) + (grid[-1] - grid[0]) / 2
                return .5 - edge_polarity * contrast * np.tanh(distance)
            return grating(coords, position, angle, phase, contrast)
        frame = light(x)
        strip = light(grid)
        feature = circuit.advance(frame)
        features.append(feature)
        old = strip if previous_grid is None else previous_grid
        centered, prior = strip - .5, old - .5
        corr = (prior[:-1]*centered[1:] - prior[1:]*centered[:-1]).mean(axis=0)
        pixel_scores.append(corr)
        if decoder is not None and t >= 4:
            prediction = corr if pixel else feature @ decoder
            action = np.clip(prediction * (20 if pixel else .32), -.45, .45)
        previous_grid = strip
        replay.append((position.copy(), action.copy(), strip.T.copy(), feature.copy()))
    return (np.mean(features[5:], axis=0), np.mean(pixel_scores[5:], axis=0),
            heading_sum / STEPS, replay)


def fit_decoder(graph, train_rng):
    train = scenes(train_rng, 48)
    calibration = rollout(graph, train)[0]
    scale = calibration.std(axis=0).clip(1e-12)
    design = calibration / scale
    return np.linalg.solve(design.T @ design + .05*np.eye(len(OUTPUT)), design.T @ train[0]) / scale


def experiment_seeds(seed):
    return [int(s.generate_state(1)[0]) for s in np.random.SeedSequence(seed).spawn(3)]


def experiment(nodes, edges, seed):
    train_rng, test_rng, control_rng = experiment_seeds(seed)
    test = scenes(test_rng, 24)
    intact = wiring(nodes, edges, "intact", control_rng)
    decoder = fit_decoder(intact, train_rng)
    rows = []
    for condition in CONDITIONS:
        graph = intact if condition in ("intact", "pixel_correlator") else wiring(nodes, edges, condition, control_rng)
        feat, pixel, baseline, _ = rollout(graph, test)
        reference = condition == "pixel_correlator"
        scores = pixel if reference else feat @ decoder
        _, _, heading, trace = rollout(graph, test, decoder, reference)
        for j, direction in enumerate(test[0]):
            frames = [dict(position=round(float(pos[j]), 4), action=round(float(action[j]), 4),
                           pixels=np.round(strip[j], 3).tolist(), activity=np.round(activity[j], 10).tolist())
                      for pos, action, strip, activity in trace]
            rows.append((condition, j, int(direction), float(scores[j]), float(heading[j]),
                         float(baseline[j]), frames))
    return rows

