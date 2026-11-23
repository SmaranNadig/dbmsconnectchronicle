"""Functional dependency algebra (backend/design/fd.py) on textbook cases:
closure, candidate keys, minimal cover, normal-form tests, BCNF
decomposition, the lossless-join (chase) test and dependency preservation."""
from __future__ import annotations

import pytest

from backend.design import fd as F


def fds(*texts):
    return [F.parse(t) for t in texts]


def keys(attrs, deps):
    return sorted("".join(sorted(k)) for k in F.candidate_keys(list(attrs), deps))


def test_closure():
    deps = fds("A -> B", "B -> C", "C, D -> E")
    assert F.closure({"A"}, deps) == {"A", "B", "C"}
    assert F.closure({"A", "D"}, deps) == {"A", "B", "C", "D", "E"}
    assert F.closure({"E"}, deps) == {"E"}


@pytest.mark.parametrize("attrs, deps, expected", [
    ("ABCD", ["A -> B", "B -> C", "C -> A"], ["AD", "BD", "CD"]),          # cyclic: three keys
    ("ABCDE", ["A, B -> C", "C -> D", "D -> A"], ["ABE", "BCE", "BDE"]),
    ("ABC", ["A -> B", "B -> A"], ["AC", "BC"]),
    ("ABCDEF", ["A -> B", "B -> C", "C -> A", "D -> E", "E -> F", "F -> D"],
     ["AD", "AE", "AF", "BD", "BE", "BF", "CD", "CE", "CF"]),
    ("ABC", [], ["ABC"]),                                                  # no FDs: the whole relation
])
def test_candidate_keys(attrs, deps, expected):
    assert keys(attrs, fds(*deps)) == expected


def test_candidate_keys_are_minimal_superkeys():
    deps = fds("A, B -> C", "C -> D", "D -> A", "E -> F")
    attrs = list("ABCDEF")
    found = F.candidate_keys(attrs, deps)
    for k in found:
        assert F.closure(k, deps) >= set(attrs)
        assert all(not F.closure(k - {a}, deps) >= set(attrs) for a in k)


def test_minimal_cover_removes_extraneous_and_redundant():
    cover = F.minimal_cover(fds("A -> B, C", "B -> C", "A -> B", "A, B -> C"))
    assert sorted(F.fmt(f) for f in cover) == ["A → B", "B → C"]
    # deterministic: the same input always gives the same cover
    assert [F.fmt(f) for f in F.minimal_cover(fds("A -> B, C", "B -> C"))] == \
           [F.fmt(f) for f in F.minimal_cover(fds("A -> B, C", "B -> C"))]


def test_minimal_cover_is_equivalent():
    original = fds("A -> B, C", "C, D -> E", "B -> D", "E -> A")
    cover = F.minimal_cover(original)
    implies = lambda f, g: all(r <= F.closure(l, g) for l, r in f)
    assert implies(original, cover) and implies(cover, original)


@pytest.mark.parametrize("attrs, deps, highest", [
    ("ABCD", ["A, B -> C, D"], "BCNF"),
    ("ABCD", ["A, B -> D", "B -> C"], "1NF"),            # partial dependency on part of the key
    ("ABC", ["A -> B", "B -> C"], "2NF"),                # transitive dependency
    ("ABC", ["A, B -> C", "C -> B"], "3NF"),             # C -> B: C not a superkey, B prime
])
def test_highest_normal_form(attrs, deps, highest):
    assert F.analyse(list(attrs), fds(*deps)).highest == highest


def test_bcnf_decomposition_is_lossless():
    attrs = list("ABCDE")
    deps = fds("A -> B", "B -> C", "C, D -> E")
    parts = F.bcnf_decompose(attrs, deps)
    for p, local in parts:
        assert F.analyse(sorted(p), local).highest == "BCNF"
    assert F.lossless(attrs, [p for p, _ in parts], deps)


def test_lossless_and_lossy_splits():
    deps = fds("A -> B")
    assert F.lossless(list("ABC"), [frozenset("AB"), frozenset("AC")], deps)       # common A is a key of AB
    assert not F.lossless(list("ABC"), [frozenset("AB"), frozenset("BC")], deps)   # common B is a key of neither


def test_dependency_preservation():
    # the classic: R(street, city, zip) with street, city -> zip and zip -> city
    deps = fds("S, C -> Z", "Z -> C")
    lost = F.preserved([frozenset("SZ"), frozenset("ZC")], deps)
    assert [F.fmt(f) for f in lost] == ["C, S → Z"]
    assert F.preserved([frozenset("SCZ")], deps) == []


def test_projection():
    proj = F.project(fds("A -> B", "B -> C"), ["A", "C"])
    assert [F.fmt(f) for f in proj] == ["A → C"]
