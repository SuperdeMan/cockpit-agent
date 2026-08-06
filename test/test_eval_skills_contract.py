"""Strict CI schema checks for declarative Skill retrieval controls."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import eval_skills  # noqa: E402


def _write_guide(root: Path, value: str) -> None:
    guides = root / "guides"
    guides.mkdir()
    (guides / "sample.yaml").write_text(
        "name: sample\n"
        "type: guide\n"
        "description: sample description\n"
        f"semantic_min_score: {value}\n"
        "keywords: [sample]\n"
        "knowledge: |\n  sample knowledge\n",
        encoding="utf-8",
    )


def test_semantic_min_score_accepts_a_finite_unit_interval_number(tmp_path):
    _write_guide(tmp_path, "0.50")
    assert eval_skills._lane_files(tmp_path) == []


@pytest.mark.parametrize("value", ["true", "nope", ".nan", "-0.1", "1.1"])
def test_semantic_min_score_rejects_invalid_values_in_strict_lane(tmp_path, value):
    _write_guide(tmp_path, value)
    assert any("semantic_min_score" in row
               for row in eval_skills._lane_files(tmp_path))


def test_capability_dependencies_must_be_known_string_list(tmp_path):
    _write_guide(tmp_path, "0.50")
    path = tmp_path / "guides" / "sample.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text + "capability_dependencies: [charging.find, typo.missing]\n",
                    encoding="utf-8")
    errors = eval_skills._lane_files(tmp_path)
    assert any("capability_dependencies" in row and "typo.missing" in row
               for row in errors)


def test_configured_semantic_floor_turns_its_paraphrase_miss_into_a_failure():
    cases = [eval_skills.CaseResult(
        id="para-hyb::miss", bucket="paraphrase_hybrid", text="miss",
        expected="multi-day-trip", actual=[], passed=False,
    )]
    guides = [SimpleNamespace(
        name="multi-day-trip", semantic_min_score=0.45,
    )]

    assert eval_skills._semantic_floor_failures(cases, guides) == [
        "multi-day-trip ← miss",
    ]


def test_injected_wire_lane_fails_when_a_governed_doc_was_never_rendered():
    doc = eval_skills.sk.SkillDoc(
        name="blocked", type="guide", description="blocked",
        knowledge="只能调用 missing.intent。",
        capability_dependencies=("missing.intent",),
    )

    errors = eval_skills._lane_injected_wire([doc])

    assert any("未实际注入" in row for row in errors)


def test_live_skill_inventory_reuses_the_shared_production_facade(monkeypatch):
    sentinel = [object()]
    monkeypatch.setattr(eval_skills.eval_live, "load_agents",
                        lambda include_edge=True: sentinel)

    assert eval_skills._load_agents() is sentinel
