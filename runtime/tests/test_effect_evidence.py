"""CA2-10 evidence vocabulary: one constructor, strict receipts, derived verification."""
from __future__ import annotations

import pytest

from runtime import effect_evidence as E


def test_references_are_fresh_lowercase_hex():
    refs = {E.new_ref() for _ in range(64)}
    assert len(refs) == 64 and all(E.valid_ref(r) for r in refs)
    for bad in ("", "A" * 32, "a" * 31, "a" * 33, "g" * 32, None, 1, "a" * 31 + "\n"):
        assert not E.valid_ref(bad)


def test_receipts_echo_only_valid_references_and_sort_keys():
    assert E.make_receipt("a" * 32, ["window", "hvac_on", "hvac_on"]) == {
        "observation_ref": "a" * 32, "changed": ["hvac_on", "window"]}
    assert E.make_receipt("not-a-ref", ["hvac_on", "", 3, "bad key"])["observation_ref"] == ""
    assert E.make_receipt("", ["hvac_on", "", 3, "bad key"])["changed"] == ["hvac_on"]


@pytest.mark.parametrize("raw", [
    None, [], {"observation_ref": "a" * 32}, {"observation_ref": "a" * 32, "changed": "hvac_on"},
    {"observation_ref": "short", "changed": []}, {"observation_ref": "a" * 32, "changed": [1]},
    {"observation_ref": "a" * 32, "changed": ["bad key"]},
    {"observation_ref": "a" * 32, "changed": [], "verified": True},
    {"observation_ref": "a" * 32, "changed": ["k"] * (E.MAX_CHANGED + 1)},
])
def test_malformed_receipts_read_as_absent(raw):
    assert E.read_receipt({E.RECEIPT: raw}) is None


def test_receipt_round_trip():
    receipt = E.read_receipt({E.RECEIPT: E.make_receipt("a" * 32, ["hvac_on"])})
    assert receipt == E.Receipt("a" * 32, frozenset({"hvac_on"}))
    assert E.read_receipt({}) is None and E.read_receipt("x") is None


def test_verified_is_derived_and_never_supplied():
    for state in E.STATES:
        for observed in E.OBSERVATIONS:
            record = E.summarize(ack="unknown", state=state, observed=observed)
            assert record["verified"] is (state == "satisfied" and observed == "attributed")
    with pytest.raises(TypeError):
        E.summarize(ack="acknowledged", state="satisfied", observed="attributed", verified=False)


@pytest.mark.parametrize("kw", [
    {"ack": "sent"}, {"state": "done"}, {"observed": "caused"}, {"reasons": ["because"]},
    {"source_kind": "car"},
])
def test_vocabulary_is_closed(kw):
    base = {"ack": "acknowledged", "state": "satisfied", "observed": "attributed"}
    with pytest.raises(ValueError):
        E.summarize(**{**base, **kw})


def test_authentication_requires_a_source():
    assert not E.summarize(ack="acknowledged", state="unknown", observed="unattributed",
                           authenticated=True)["authenticated"]


def test_public_projection_rederives_and_rejects_inconsistency():
    record = E.summarize(ack="acknowledged", state="satisfied", observed="attributed",
                         source_kind="vehicle", authenticated=True)
    public = E.public(record)
    assert public == {k: v for k, v in record.items() if k != "version"}
    assert E.public({**record, "verified": False}) is None
    assert E.public({**record, "version": 2}) is None
    assert E.public({**record, "reasons": "already_satisfied"}) is None
    assert E.public("verified") is None
    assert E.public({**record, "keys": {"hvac_on": {}}}) == public       # extra internals never leave


def test_actions_after_the_reply_are_explicitly_unknown():
    record = E.dispatched_after_reply()
    assert (record["ack"], record["state"], record["observed"], record["verified"]) == (
        "unknown", "unknown", "unattributed", False)
    assert record["reasons"] == ["dispatched_after_reply"]


def test_weakest_source_wins():
    assert E.weakest_source({"vehicle", "simulated"}) == "simulated"
    assert E.weakest_source({"vehicle", "sandbox"}) == "sandbox"
    assert E.weakest_source({"elsewhere"}) == "" and E.weakest_source(()) == ""
