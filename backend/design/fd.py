"""Functional-dependency algebra for the normalisation analysis.

Pure functions over attribute sets: attribute closure, candidate keys, prime
attributes, the 2NF / 3NF / BCNF tests, a minimal cover, BCNF decomposition,
the lossless-join (chase) test and the dependency-preservation test. They are
used by backend/design/normalisation.py on the declared dependencies of every
relation (backend/design/relations.py) and are unit-tested on textbook
examples (tests/unit/test_fd.py).

An FD is a pair (lhs, rhs) of frozensets of attribute names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

FD = tuple[frozenset, frozenset]


def parse(text: str) -> FD:
    """'a, b -> c, d' -> (frozenset{a, b}, frozenset{c, d})."""
    lhs, rhs = text.split("->")
    split = lambda s: frozenset(a.strip() for a in s.split(",") if a.strip())
    return split(lhs), split(rhs)


def fmt(fd: FD) -> str:
    return f"{', '.join(sorted(fd[0]))} → {', '.join(sorted(fd[1]))}"


def closure(attrs, fds) -> frozenset:
    """X⁺ under fds."""
    result = set(attrs)
    changed = True
    while changed:
        changed = False
        for lhs, rhs in fds:
            if lhs <= result and not rhs <= result:
                result |= rhs
                changed = True
    return frozenset(result)


def project(fds, attrs) -> list[FD]:
    """The FDs that hold on a subset of attributes (projection of F⁺), as
    X → (X⁺ ∩ attrs) − X for every non-trivial X ⊆ attrs, then minimised.

    Enumerating every X is exponential, so attribute sets are handled as bit
    masks here (same algorithm, far fewer allocations than frozensets)."""
    attrs = frozenset(attrs)
    universe = sorted(attrs.union(*(l | r for l, r in fds)) if fds else attrs)
    bit = {a: 1 << i for i, a in enumerate(universe)}
    mask = lambda xs: sum(bit[x] for x in xs)
    rules = [(mask(l), mask(r)) for l, r in fds]

    def clo(m: int) -> int:
        changed = True
        while changed:
            changed = False
            for lm, rm in rules:
                if lm & ~m == 0 and rm & ~m:
                    m |= rm
                    changed = True
        return m

    whole = mask(attrs)
    bits = [bit[a] for a in sorted(attrs)]
    out = []                    # (lhs, rhs, closure of lhs) as masks: each closure computed once
    for n in range(1, len(bits) + 1):
        for combo in combinations(bits, n):
            lm = sum(combo)
            if any(p & ~lm == 0 and lm & ~c == 0 for p, _, c in out):
                continue        # implied by a smaller left side that already covers it
            c = clo(lm)
            rm = c & whole & ~lm
            # keep only what the dependencies found so far do not already imply
            if rm and (rm & ~_clo(lm, [(a, b) for a, b, _ in out])):
                out.append((lm, rm, c))
    names = lambda m: frozenset(a for a in universe if bit[a] & m)
    return minimal_cover([(names(lm), names(rm)) for lm, rm, _ in out])


def _masks(fds):
    """Attribute sets as bit masks, for the exponential-size inputs of
    minimal_cover and project: (bit of each attribute, mask function)."""
    universe = sorted(frozenset().union(*(l | r for l, r in fds))) if fds else []
    bit = {a: 1 << i for i, a in enumerate(universe)}
    return universe, bit, (lambda xs: sum(bit[x] for x in xs))


def _clo(m: int, rules) -> int:
    changed = True
    while changed:
        changed = False
        for lm, rm in rules:
            if lm & ~m == 0 and rm & ~m:
                m |= rm
                changed = True
    return m


def minimal_cover(fds) -> list[FD]:
    """Canonical cover: single-attribute right sides, no extraneous left
    attributes, no redundant FDs; then right sides with the same left side merged."""
    # deterministic order (set iteration order changes between runs), so the same
    # input always gives the same one of its equivalent covers
    single = [(l, frozenset([a])) for l, r in fds for a in sorted(r) if a not in l]
    universe, bit, mask = _masks(single)
    rules = [(mask(l), mask(r)) for l, r in single]
    # extraneous left-side attributes
    reduced = []
    for (lhs, rhs), (_, rm) in zip(single, rules):
        lm = mask(lhs)
        for a in sorted(lhs):
            if bin(lm).count("1") > 1 and lm & bit[a] and rm & ~_clo(lm & ~bit[a], rules) == 0:
                lm &= ~bit[a]
        reduced.append((lm, rm))
    reduced = list(dict.fromkeys(reduced))
    # redundant FDs
    cover = list(reduced)
    for fd in reduced:
        rest = [g for g in cover if g != fd]
        if fd[1] & ~_clo(fd[0], rest) == 0:
            cover = rest
    names = lambda m: frozenset(a for a in universe if bit[a] & m)
    merged: dict[frozenset, set] = {}
    for lm, rm in cover:
        merged.setdefault(names(lm), set()).update(names(rm))
    return sorted(((l, frozenset(r)) for l, r in merged.items()), key=lambda f: (len(f[0]), sorted(f[0])))


def _minimise(superkey: frozenset, attrs: frozenset, fds) -> frozenset:
    """Drop attributes from a superkey while it stays a superkey: a candidate key."""
    key = superkey
    for a in sorted(superkey):
        if closure(key - {a}, fds) >= attrs:
            key = key - {a}
    return key


def candidate_keys(attrs, fds) -> list[frozenset]:
    """All candidate keys (Lucchesi and Osborn, 1978). Start from one key
    found by minimising the whole schema; for every key K and every FD X → Y,
    X ∪ (K − Y) is again a superkey, and minimising it yields any key not yet
    found. Runs in time polynomial in the number of keys, instead of trying
    every subset of attributes."""
    attrs = frozenset(attrs)
    keys = [_minimise(attrs, attrs, fds)]
    i = 0
    while i < len(keys):
        k = keys[i]
        for lhs, rhs in fds:
            s = lhs | (k - rhs)
            if not any(x <= s for x in keys):
                keys.append(_minimise(s, attrs, fds))
        i += 1
    return sorted(keys, key=lambda k: (len(k), sorted(k)))


def is_superkey(x, attrs, fds) -> bool:
    return closure(x, fds) >= frozenset(attrs)


@dataclass
class Violation:
    form: str              # '2NF', '3NF' or 'BCNF': the first normal form this FD breaks
    fd: FD
    reason: str


@dataclass
class Analysis:
    attrs: list[str]
    fds: list[FD]
    keys: list[frozenset]
    prime: frozenset
    violations: list[Violation] = field(default_factory=list)

    @property
    def highest(self) -> str:
        forms = {v.form for v in self.violations}
        if "2NF" in forms:
            return "1NF"
        if "3NF" in forms:
            return "2NF"
        if "BCNF" in forms:
            return "3NF"
        return "BCNF"


def analyse(attrs, fds) -> Analysis:
    """Keys, prime attributes and every dependency that breaks 2NF, 3NF or
    BCNF. Testing the FDs of a minimal cover is sufficient for 3NF and BCNF;
    2NF is tested against every proper subset of every candidate key."""
    attrs = list(attrs)
    cover = minimal_cover(fds)
    keys = candidate_keys(attrs, cover)
    prime = frozenset().union(*keys)
    a = Analysis(attrs, cover, keys, prime)
    seen = set()
    # 2NF: a non-prime attribute depends on part of a candidate key
    for key in keys:
        for n in range(1, len(key)):
            for part in combinations(sorted(key), n):
                part = frozenset(part)
                dep = (closure(part, cover) - part) - prime
                # report only minimal parts: a larger part repeats what a smaller one implies
                dep -= frozenset().union(*(d for p, d in seen if p < part)) if seen else frozenset()
                if dep and (part, frozenset(dep)) not in seen:
                    seen.add((part, frozenset(dep)))
                    a.violations.append(Violation(
                        "2NF", (part, frozenset(dep)),
                        f"partial dependency: {', '.join(sorted(dep))} depend(s) on {', '.join(sorted(part))}, "
                        f"a proper part of the key ({', '.join(sorted(key))})"))
    partial_lhs = {v.fd[0] for v in a.violations}
    for lhs, rhs in cover:
        if is_superkey(lhs, attrs, cover) or lhs in partial_lhs:
            continue
        nonprime = rhs - prime
        if nonprime:
            a.violations.append(Violation(
                "3NF", (lhs, frozenset(nonprime)),
                f"transitive dependency: {', '.join(sorted(lhs))} is not a superkey and "
                f"{', '.join(sorted(nonprime))} is non-prime"))
        if rhs & prime:
            a.violations.append(Violation(
                "BCNF", (lhs, frozenset(rhs & prime)),
                f"{', '.join(sorted(lhs))} is not a superkey (allowed in 3NF because "
                f"{', '.join(sorted(rhs & prime))} is prime)"))
    return a


def bcnf_decompose(attrs, fds) -> list[tuple[frozenset, list[FD]]]:
    """Standard BCNF decomposition: split R on a violating X → Y into
    (X ∪ Y) and (R − (Y − X)) until every part is in BCNF. Returns each part
    with its projected FDs. The result is always lossless."""
    todo = [frozenset(attrs)]
    done = []
    while todo:
        r = todo.pop()
        local = project(fds, r)
        bad = next(((l, (closure(l, local) & r) - l) for l, _ in local
                    if not closure(l, local) >= r), None)
        if bad is None:
            done.append((r, local))
            continue
        x, y = bad
        todo += [x | y, r - y]
    return sorted(done, key=lambda p: sorted(p[0]))


def lossless(attrs, parts, fds) -> bool:
    """Chase test: the decomposition is lossless iff the tableau gets a row
    of all 'distinguished' symbols."""
    attrs = sorted(attrs)
    rows = [{a: ("a", a) if a in part else ("b", i, a) for a in attrs} for i, part in enumerate(parts)]
    changed = True
    while changed:
        changed = False
        for lhs, rhs in fds:
            groups: dict[tuple, list[dict]] = {}
            for row in rows:
                groups.setdefault(tuple(row[a] for a in sorted(lhs)), []).append(row)
            for group in groups.values():
                if len(group) < 2:
                    continue
                for a in rhs:
                    values = {row[a] for row in group}
                    if len(values) > 1:
                        best = ("a", a) if ("a", a) in values else min(values)
                        for row in group:
                            if row[a] != best:
                                row[a] = best
                                changed = True
    return any(all(row[a] == ("a", a) for a in attrs) for row in rows)


def preserved(parts, fds) -> list[FD]:
    """The FDs of fds that are NOT preserved by the decomposition (empty =
    dependency preserving). Uses the restricted closure, no F⁺ needed."""
    lost = []
    for lhs, rhs in fds:
        z = set(lhs)
        changed = True
        while changed:
            changed = False
            for part in parts:
                t = closure(z & part, fds) & part
                if not t <= z:
                    z |= t
                    changed = True
        if not rhs <= z:
            lost.append((lhs, rhs))
    return lost
