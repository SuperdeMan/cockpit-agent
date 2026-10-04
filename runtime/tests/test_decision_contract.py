"""Jev 判别纯契约（JV01）：规格自检、payload 校验、供应商答案整批校验。"""
import math

from runtime import decision_contract as dc
from runtime.decision_contract import QuestionSpec, TaskSpec

NOUL = QuestionSpec(id="q_noul", type="noul", instructions="Is state.text a request?",
                    criteria={"true": "yes", "false": "no"})
CHOICE = QuestionSpec(id="q_choice", type="choice", instructions="Which kind is state.text?",
                      criteria={"act": "an action", "ask": "a question", "other": "neither"})
SCORE = QuestionSpec(id="q_score", type="score", instructions="How urgent is state.text?",
                     criteria=["not urgent", "somewhat", "urgent"])
SPEC = TaskSpec(task_id="t", rubric_version="1", payload_fields={"text": "str", "items": "list[str]"},
                required=frozenset({"text"}), questions=(NOUL, CHOICE, SCORE))
MODEL = "jev-1.13.0"


def _good():
    return {
        "q_noul": {"type": "noul", "noul": 0.8},
        "q_choice": {"type": "choice", "choice": "ask", "probabilities": {"act": 0.2, "ask": 0.7, "other": 0.1},
                     "confidence": 0.7},
        "q_score": {"type": "score", "score": 0.4, "probabilities": {"0": 0.7, "1": 0.2, "2": 0.1},
                    "confidence": 0.7},
    }


def _check(answers, model_used=MODEL):
    return dc.validate_answers(SPEC, answers, model_requested=MODEL, model_used=model_used)


def test_spec_self_check():
    assert dc.validate_spec(SPEC) == []
    dup = TaskSpec(task_id="t", rubric_version="1", questions=(NOUL, NOUL))
    assert "duplicate_question_id" in dc.validate_spec(dup)
    bad = TaskSpec(task_id="t", rubric_version="1", questions=(
        QuestionSpec(id="a", type="choice", instructions="x", criteria={"only": "one"}),
        QuestionSpec(id="b", type="score", instructions="x", criteria=["one"]),
        QuestionSpec(id="c", type="maybe", instructions="x"),
        QuestionSpec(id="d", type="noul", instructions="x", criteria={"yes": "y"})))
    assert dc.validate_spec(bad) == ["bad_choice_criteria:a", "bad_score_criteria:b", "bad_question:c",
                                     "bad_noul_criteria:d"]
    assert "required_not_declared:x" in dc.validate_spec(
        TaskSpec(task_id="t", rubric_version="1", required=frozenset({"x"}), questions=(NOUL,)))


def test_payload_is_checked_against_the_spec():
    assert dc.validate_payload(SPEC, {"text": "打开空调", "items": ["a", "b"]}) == []
    assert dc.validate_payload(SPEC, {"text": "x", "owner": "u1"}) == ["unknown_field:owner"]
    assert dc.validate_payload(SPEC, {"items": []}) == ["missing_field:text"]
    assert dc.validate_payload(SPEC, {"text": 3}) == ["bad_field:text"]
    assert dc.validate_payload(SPEC, {"text": "x" * (dc.MAX_TEXT_CHARS + 1)}) == ["bad_field:text"]
    assert dc.validate_payload(SPEC, {"text": "x", "items": ["a", 1]}) == ["bad_field:items"]
    assert dc.validate_payload(SPEC, ["text"]) == ["payload_not_object"]


def test_vendor_questions_shape():
    q = dc.vendor_questions(SPEC)
    assert q["q_noul"] == {"type": "noul", "instructions": NOUL.instructions, "criteria": {"true": "yes", "false": "no"}}
    assert q["q_score"]["criteria"] == ["not urgent", "somewhat", "urgent"]
    assert set(q) == {"q_noul", "q_choice", "q_score"}


def test_a_complete_valid_batch_is_normalised():
    status, answers, reason = _check(_good())
    assert (status, reason) == (dc.OK, "")
    assert answers["q_noul"] == {"type": "noul", "p_true": 0.8}
    assert answers["q_choice"]["choice"] == "ask" and answers["q_score"]["score"] == 0.4


def test_any_bad_item_rejects_the_whole_batch():
    cases = {
        "missing": lambda a: a.pop("q_score"),
        "extra": lambda a: a.update(q_new={"type": "noul", "noul": 0.1}),
        "nan": lambda a: a["q_noul"].update(noul=math.nan),
        "range": lambda a: a["q_noul"].update(noul=1.2),
        "bool": lambda a: a["q_noul"].update(noul=True),
        "type": lambda a: a["q_noul"].update(type="choice"),
        "unknown_option": lambda a: a["q_choice"].update(choice="maybe"),
        "not_argmax": lambda a: a["q_choice"].update(choice="act"),
        "sum": lambda a: a["q_choice"]["probabilities"].update(ask=0.9),
        "score_range": lambda a: a["q_score"].update(score=3.5),
        "score_levels": lambda a: a["q_score"]["probabilities"].update({"7": 0.0}),
        "confidence": lambda a: a["q_score"].update(confidence=-0.1),
    }
    for name, mutate in cases.items():
        answers = _good()
        mutate(answers)
        status, normalised, reason = _check(answers)
        assert status == dc.INVALID_RESPONSE and normalised == {} and reason, name


def test_model_drift_and_shapes():
    assert _check(_good(), model_used="jev-1.14.0")[2] == "model_drift"
    assert _check(None)[2] == "answers_not_object"
    one_based = _good()
    one_based["q_score"] = {"type": "score", "score": 2.0, "probabilities": {"1": 0.2, "2": 0.6, "3": 0.2},
                            "confidence": 0.6}
    assert _check(one_based)[0] == dc.OK      # 等级从 1 开始的写法同样接受，但分布要和等级数一致
