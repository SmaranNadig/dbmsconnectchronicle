"""Data flow diagram rules (backend/design/dfd.py): the project's own model
in config/dfd.yaml satisfies every rule, and a model broken in one way is
reported under the right rule."""
from __future__ import annotations

import copy

import pytest

from backend.design import dfd


@pytest.fixture(scope="module")
def spec():
    return dfd.load()


def diagram(s, did):
    return next(d for d in s["diagrams"] if d["id"] == did)


def rules_broken(s):
    return {e["rule"] for e in dfd.check(s)["errors"]}


def test_model_satisfies_every_rule(spec):
    result = dfd.check(spec)
    assert result["ok"], result["errors"]
    levels = sorted({d["level"] for d in spec["diagrams"]})
    assert levels == [0, 1, 2]


def test_every_diagram_renders(spec):
    for d in spec["diagrams"]:
        svg = dfd.render(d["id"], spec)
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        for pid, p in d["processes"].items():
            assert p["name"].split()[0] in svg


def break_label(s):
    diagram(s, "level1")["flows"][0]["label"] = ""


def break_store_to_store(s):
    d = diagram(s, "level1")
    stores = [e for e in d["place"] if e in s["stores"]]
    d["flows"].append({"from": stores[0], "to": stores[1], "label": "copied rows"})


def break_black_hole(s):
    d = diagram(s, "level1")
    d["flows"] = [f for f in d["flows"] if f["from"] != "1"]


def break_numbering(s):
    d = diagram(s, "level2-2")
    pid = next(iter(d["processes"]))
    d["processes"]["7.1"] = d["processes"].pop(pid)
    for f in d["flows"]:
        f["from"] = "7.1" if f["from"] == pid else f["from"]
        f["to"] = "7.1" if f["to"] == pid else f["to"]


def break_verb(s):
    d = diagram(s, "level1")
    d["processes"]["1"]["name"] = "Release files"


def break_balance(s):
    d = diagram(s, "level2-2")
    ext = next(e for e in d["place"] if e in s["externals"])
    d["flows"].append({"from": ext, "to": next(iter(d["processes"])), "label": "a flow the parent does not have"})


def break_context_store(s):
    d = diagram(s, "level0")
    d["place"]["D1"] = [600, 600]


def break_unread_store(s):
    for d in s["diagrams"]:
        d["flows"] = [f for f in d["flows"] if f["from"] != "D8"]


def break_duplicate(s):
    d = diagram(s, "level1")
    d["flows"].append(dict(d["flows"][0]))


@pytest.mark.parametrize("breaker, rule", [
    (break_label, "R1"), (break_store_to_store, "R2"), (break_black_hole, "R3"), (break_numbering, "R4"),
    (break_verb, "R5"), (break_balance, "R6"), (break_context_store, "R7"), (break_unread_store, "R8"),
    (break_duplicate, "R9"),
])
def test_broken_model_is_reported(spec, breaker, rule):
    s = copy.deepcopy(spec)
    breaker(s)
    assert rule in rules_broken(s)
