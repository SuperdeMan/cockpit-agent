"""candidate 导入器：来源、冲突、去重、脱敏与生命周期。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402
import yaml  # noqa: E402

from build_intent_adversarial_candidates import (  # noqa: E402
    deduplicate_candidates, import_eval_corpus, import_manifest_examples,
    privacy_violations, split_manual_review, write_candidates,
)


def _manifest(tmp_path: Path, intent: str, examples: list[str]) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({
        "agent_id": "info",
        "capabilities": [{"intent": intent, "examples": examples}],
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _candidate(text: str, intent: str) -> dict:
    return {
        "id": f"candidate.{intent}", "family_id": f"asset.{intent}",
        "status": "candidate", "input": {"utterance": text, "context": {}},
        "required_intent_groups": [{"any_of": [intent]}],
        "provenance": {"kind": "test"},
    }


def test_imported_assets_are_candidate_and_keep_source_reference(tmp_path):
    rows = import_manifest_examples(_manifest(tmp_path, "info.weather", ["今天天气怎么样"]))
    assert rows[0]["status"] == "candidate"
    assert rows[0]["provenance"]["kind"] == "manifest_example"
    assert rows[0]["provenance"]["source_ref"].endswith("manifest.yaml")
    assert "reviewed_by" not in rows[0]["provenance"]


def test_same_input_with_conflicting_gold_goes_to_conflict_queue():
    accepted, conflicts = deduplicate_candidates([
        _candidate("有没有天气预警", "info.alerts"),
        _candidate("有没有天气预警", "safety.weather_alert"),
    ])
    assert accepted == []
    assert conflicts[0]["reason"] == "conflicting_gold"


def test_identical_gold_from_two_sources_is_merged_not_duplicated():
    a = _candidate("今天天气怎么样", "info.weather")
    b = _candidate("今天天气怎么样。", "info.weather")
    a["provenance"]["source_ref"] = "manifest"
    b["provenance"]["source_ref"] = "exemplar"
    accepted, conflicts = deduplicate_candidates([a, b])
    assert conflicts == []
    assert len(accepted) == 1
    assert accepted[0]["provenance"]["merged_sources"] == ["exemplar", "manifest"]


def test_negation_and_multi_intent_rows_need_a_human():
    rows = [_candidate("今天天气怎么样", "info.weather"),
            _candidate("别提醒我了", "reminder.cancel")]
    multi = _candidate("查天气顺便订个位子", "info.weather")
    multi["required_intent_groups"] = [{"any_of": ["info.weather", "nearby.order"]}]
    auto, manual = split_manual_review(rows + [multi])
    assert [row["input"]["utterance"] for row in auto] == ["今天天气怎么样"]
    reasons = {row["manual_reason"] for row in manual}
    assert reasons == {"negation_or_reference", "multi_intent_gold"}


def test_privacy_detector_catches_machine_recognisable_pii():
    assert "phone" in privacy_violations("打给我 13800138000")
    assert "plate" in privacy_violations("我的车牌是粤B12345")
    assert "token" in privacy_violations("key 是 sk-abcdefghijklmnop1234")
    assert privacy_violations("今天天气怎么样") == []


def test_eval_corpus_rows_without_intent_gold_are_skipped(tmp_path):
    (tmp_path / "clarify_cases.yaml").write_text(
        yaml.safe_dump({"cases": [
            {"text": "华润大厦", "expect_clarify": True},
            {"text": "查天气", "expect_intents": ["info.weather"]},
        ]}, allow_unicode=True), encoding="utf-8")
    rows = import_eval_corpus(tmp_path)
    assert [row["input"]["utterance"] for row in rows] == ["查天气"]


def test_builder_never_writes_inside_committed_corpus(tmp_path):
    with pytest.raises(ValueError, match="review queue"):
        write_candidates([], tmp_path / "test/eval_corpus/intent_adversarial/cases/a.yaml")


def test_builder_writes_a_loadable_review_queue(tmp_path):
    out = write_candidates([_candidate("查天气", "info.weather")],
                           tmp_path / "_ci-run-intent-candidates.yaml")
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["candidates"][0]["status"] == "candidate"
