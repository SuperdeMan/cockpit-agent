"""终态账本词表（评审 W13）：每个 kind 有类、执行类终态按结果集算。"""
from __future__ import annotations

from types import SimpleNamespace

from runtime import outcome
from runtime.outcome import (CATEGORY_OF, OUTCOME_KINDS, category_of,
                             outcome_of_results)


def _r(status: str, refused: bool = False):
    return SimpleNamespace(status=SimpleNamespace(value=status),
                           data={"_refused": True} if refused else {})


def test_every_kind_has_a_review_category():
    categories = {v for v in CATEGORY_OF.values()}
    assert categories <= {
        outcome.CAT_NOT_ADDRESSED, outcome.CAT_AMBIGUOUS, outcome.CAT_UNSUPPORTED,
        outcome.CAT_PERMISSION, outcome.CAT_POLICY_BLOCKED, outcome.CAT_FAILURE,
        outcome.CAT_PROGRESS, outcome.CAT_SESSION}
    assert category_of("not_addressed") == outcome.CAT_NOT_ADDRESSED
    assert category_of("nope") == ""
    assert "completed" in OUTCOME_KINDS and "planner_failure" in OUTCOME_KINDS


def test_results_all_ok_is_completed():
    assert outcome_of_results([_r("ok"), _r("ok")]) == "completed"


def test_results_mixed_is_partial_and_refused_counts_as_undone():
    assert outcome_of_results([_r("ok"), _r("failed")]) == "partial"
    assert outcome_of_results([_r("ok"), _r("ok", refused=True)]) == "partial"


def test_results_all_failed_or_empty_is_failed():
    assert outcome_of_results([_r("failed")]) == "failed"
    assert outcome_of_results([]) == "failed"
    assert outcome_of_results([_r("skipped")]) == "failed"


def test_a_pending_result_is_never_counted_as_done():
    assert outcome_of_results([_r("ok"), _r("need_slot")]) == "partial"


def test_status_may_be_a_plain_string():
    assert outcome_of_results([SimpleNamespace(status="ok", data={})]) == "completed"


# ── 批 5：`_refused="unsupported"` ⇒ unsupported（不是 failed）──

def test_all_unsupported_refusals_are_unsupported_not_failed():
    from types import SimpleNamespace
    ok = SimpleNamespace(value="ok")
    unsupported = SimpleNamespace(status=ok, data={"_refused": "unsupported"})
    generic = SimpleNamespace(status=ok, data={"_refused": True})
    done = SimpleNamespace(status=ok, data={})
    assert outcome_of_results([unsupported]) == "unsupported"
    assert outcome_of_results([unsupported, unsupported]) == "unsupported"
    assert outcome_of_results([generic]) == "failed"            # 泛拒绝仍是 failed
    assert outcome_of_results([unsupported, generic]) == "failed"
    assert outcome_of_results([unsupported, done]) == "partial"  # 做成一半照旧 partial
    assert category_of("unsupported") == "unsupported"


# ── 批 7 ①：「这一批全是能力做不到的声明式拒绝」= 该诉求有终态，T2 不再为它换能力再试 ──

def test_all_refused_unsupported_is_true_only_for_a_nonempty_all_unsupported_batch():
    from runtime.outcome import all_refused_unsupported, refused_unsupported
    ok = SimpleNamespace(value="ok")
    unsupported = SimpleNamespace(status=ok, data={"_refused": "unsupported"})
    generic = SimpleNamespace(status=ok, data={"_refused": True})
    done = SimpleNamespace(status=ok, data={})
    assert refused_unsupported(unsupported) is True
    assert refused_unsupported(generic) is False and refused_unsupported(done) is False
    assert all_refused_unsupported([unsupported]) is True
    assert all_refused_unsupported([unsupported, unsupported]) is True
    assert all_refused_unsupported([unsupported, done]) is False      # 混合批：别的诉求还在
    assert all_refused_unsupported([generic]) is False                # 泛拒绝可再规划
    assert all_refused_unsupported([]) is False                       # 空批什么都不证明
