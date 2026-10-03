"""The legacy evidence recount: occasions are distinct sessions of the same statement in a dimension."""
import json

from scripts import memory_evidence_recount as recount

revision, weighting = recount._load_rules()
CUTOFF = 1000


def _row(rid, text, session, predicate="climate.temperature", provenance="user_stated", subject="", count=12,
         superseded_by="", created_at=10):
    return {"id": rid, "user_id": "u1", "occupant_id": "primary", "subject": subject, "predicate": predicate,
            "text": text, "provenance": provenance, "source_session": session, "evidence_count": count,
            "weight": 1.0 if provenance == "user_stated" else 0.7, "superseded_by": superseded_by,
            "half_life_days": 0.0 if provenance == "user_stated" else 90.0, "created_at": created_at}


def _plan(rows, family=()):
    return {row["id"]: (count, weight)
            for row, count, weight in recount.plan(rows, list(family), revision, weighting, CUTOFF)}


def test_a_statement_from_one_session_counts_once_whatever_the_window():
    assert _plan([_row("a", "用户喜欢26度", "s1")]) == {"a": (1, 0.6)}


def test_paraphrases_from_several_sessions_count_each_session_once_per_source_class():
    rows = [_row("a", "用户喜欢26度", "s1"), _row("b", "空调开26度就好", "s2"), _row("c", "用户偏好26度", "s2"),
            _row("d", "用户可能喜欢凉快", "s3", provenance="agent_inferred")]
    assert _plan(rows) == {"a": (2, 0.7), "b": (2, 0.7), "c": (2, 0.7), "d": (1, 0.3)}


def test_replaced_statements_count_like_the_inherited_evidence_of_a_write():
    rows = [_row("old", "用户喜欢25度", "s1", superseded_by="new"), _row("new", "用户喜欢26度", "s2")]
    assert _plan(rows) == {"new": (2, 0.7)}


def test_multi_valued_dimensions_count_each_value_separately():
    rows = [_row("a", "用户喜欢吃川菜", "s1", predicate="taste.cuisine"),
            _row("b", "用户喜欢吃川菜啊", "s2", predicate="taste.cuisine"),
            _row("c", "用户喜欢吃粤菜", "s3", predicate="taste.cuisine")]
    assert _plan(rows) == {"a": (2, 0.7), "b": (2, 0.7), "c": (1, 0.6)}


def test_one_person_under_two_subjects_is_one_dimension():
    rows = [_row("a", "女儿不吃辣", "s1", predicate="taste.spicy", subject="女儿"),
            _row("b", "小雨不吃辣", "s2", predicate="taste.spicy", subject="小雨")]
    family = [{"user_id": "u1", "occupant_id": "primary", "subject": "小雨", "rel": "family", "object": "女儿"}]
    assert _plan(rows, family) == {"a": (2, 0.7), "b": (2, 0.7)}
    assert _plan(rows) == {"a": (1, 0.6), "b": (1, 0.6)}


def test_rows_written_under_the_new_rule_are_left_alone_unless_they_inherited_an_old_count():
    rows = [_row("fresh", "用户喜欢静音", "s9", predicate="media.volume", count=3, created_at=CUTOFF),
            _row("old", "用户喜欢24度", "s1", superseded_by="heir"),
            _row("heir", "用户喜欢26度", "s2", count=13, created_at=CUTOFF + 5)]
    assert _plan(rows) == {"heir": (2, 0.7)}


def test_rows_without_a_session_count_alone_and_correct_rows_are_left_alone():
    rows = [_row("a", "用户喜欢26度", ""), _row("b", "空调开26度", "")]
    assert _plan(rows) == {"a": (2, 0.7), "b": (2, 0.7)}
    settled = dict(_row("c", "用户喜欢静音", "s1", predicate="media.volume"), evidence_count=1, weight=0.6)
    assert _plan([settled]) == {}


def test_the_report_carries_counts_and_never_memory_text():
    rows = [_row("a", "女儿不吃辣的秘密", "s1", predicate="taste.spicy", subject="女儿")]
    changes = recount.plan(rows, [], revision, weighting, CUTOFF)
    out = json.dumps(recount.report(rows, changes, CUTOFF), ensure_ascii=False)
    assert "秘密" not in out and "女儿" not in out
    assert json.loads(out)["to_change"] == 1
