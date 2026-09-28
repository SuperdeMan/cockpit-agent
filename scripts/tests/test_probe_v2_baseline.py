"""The baseline must expose product failures without confusing missing evidence with success."""
import json
import asyncio
import hashlib

import pytest
import yaml

from scripts import probe_v2_baseline as probe


def obs(**kwargs):
    return {"speech": "空调有自动模式", "actions": [], "card_text": "{}", **kwargs}


def test_planned_manual_is_not_dispatched_or_presented():
    detail = {"turn": {"intents": "manual.query"}, "spans": [
        {"node": "cloud.planning", "attrs": {"plan": "manual.query"}}]}
    result = probe.judge({"manual": True}, obs(), detail)
    assert result["failures"] == ["manual_not_dispatched", "manual_not_presented"]


def test_successful_agent_without_card_is_a_presentation_failure():
    detail = {"spans": [{"node": "agent.call", "attrs": {"agent_id": "manual-rag"}}]}
    result = probe.judge({"manual": True}, obs(), detail)
    assert result["manual_dispatched"]
    assert result["failures"] == ["manual_not_presented"]


def test_manual_in_group_is_visible_but_action_still_fails():
    detail = {"spans": [{"attrs": {"agent": "manual-rag"}}]}
    card = {"type": "card_group", "items": [{"type": "weather"}, {"type": "manual"}]}
    result = probe.judge({"manual": True}, obs(card_text=json.dumps(card), actions=["hvac.on"]), detail)
    assert result["failures"] == ["unexpected_action"]


@pytest.mark.parametrize("changes", [{"need_confirm": True}, {"operation_id": "op-1"}])
def test_confirmation_requires_both_visible_prompt_and_address(changes):
    assert "pending_missing" in probe.judge({"need_confirm": True}, obs(**changes), {})["failures"]


def test_internal_error_cannot_pass_as_speech():
    assert "technical_failure" in probe.judge({}, obs(speech="Agent 内部错误：RuntimeError"), {})["failures"]


def test_an_unrequested_confirmation_is_not_success_even_without_actions():
    assert probe.judge({}, obs(need_confirm=True, operation_id="op-1"), {})["failures"] == ["unexpected_confirmation"]


def test_freeze_refuses_dirty_inputs(monkeypatch):
    monkeypatch.setattr(probe, "_git", lambda *args: " M runtime/x.py")
    with pytest.raises(ValueError, match="clean"):
        probe.freeze("a"*40, "minimax", "MiniMax-M3")


def test_recursive_redaction_keeps_image_evidence_without_payload():
    source = {"items": [{"images": [{"data_uri": "data:private", "sha256": "proof"}]}]}
    result = probe._redact(source)
    assert result["items"][0]["images"][0] == {"data_uri": "[image:12 chars]", "sha256": "proof"}
    assert source["items"][0]["images"][0]["data_uri"] == "data:private"


def test_seed_is_regression_and_never_automatically_confirms():
    cases = probe.load_cases()
    assert len(cases) == 20
    assert len({c["family"] for c in cases}) >= 6
    assert not {"merchant.write", "payment.invoke"} & set(probe.SCOPES)
    assert all(t["say"] == "取消" for c in cases for t in c["turns"] if t.get("cancel_pending"))


def test_full_state_probe_does_not_drop_a_new_vehicle_signal(monkeypatch):
    state = {"trunk": "closed", "rear_view_mirror_heating": False}
    async def read(_):
        return dict(state)
    monkeypatch.setattr(probe.audit, "_vehicle_state", read)
    good = asyncio.run(probe.audit._settled_vehicle_state(
        "stub", attempts=2, expected=state, include_unmanaged=True))
    assert good.settled and good.value == state
    wrong = asyncio.run(probe.audit._settled_vehicle_state(
        "stub", attempts=2, expected={**state, "rear_view_mirror_heating": True}, include_unmanaged=True))
    assert not wrong.settled
    assert wrong.reachable and not wrong.missing


@pytest.fixture
def source_card(tmp_path, monkeypatch):
    source = "仅在压力恢复且显示未更新时才复位；仍报警则停止并检查。"
    document = {"document_id": "synthetic", "source_sha256": "a"*64, "content_sha256": "b"*64}
    resource = {"schema_version": 1, "documents": {"synthetic": {
        "source_sha256": "a"*64, "content_sha256": "b"*64,
        "guards": [{"id": "guard", "subjects": ["胎压"], "parts": [{
            "id": "condition", "label": "原文", "page": 1, "start": 0, "end": len(source),
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
        }]}]}}}
    path = tmp_path / "source.yaml"
    path.write_text(yaml.safe_dump(resource, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(probe, "SOURCE_EVIDENCE", path)
    card = {"type": "manual", "_prov": {"mode": "real"}, "document": document,
            "chunks": [{"page_start": 1, "content": source}]}
    return card, source


def test_source_probe_compares_whole_condition_instead_of_shared_keywords(source_card):
    card, source = source_card
    detail = {"spans": [{"attrs": {"agent_id": "manual-rag"}}], "llm_calls": []}
    good = probe.judge({"manual": True, "source_evidence": True},
                       obs(speech=source, card_text=json.dumps(card)), detail, query="胎压灯该如何复位？")
    assert not good["failures"]
    bad = probe.judge({"manual": True, "source_evidence": True},
                      obs(speech=source.replace("且显示未更新", ""), card_text=json.dumps(card)),
                      detail, query="胎压灯该如何复位？")
    assert bad["failures"] == ["source_condition_lost:condition"]


@pytest.mark.parametrize("mutation", ["hash", "source", "model_rewrite"])
def test_source_probe_rejects_unproven_or_regenerated_evidence(source_card, mutation):
    card, source = source_card
    detail = {"llm_calls": []}
    if mutation == "hash":
        card["document"]["source_sha256"] = "c"*64
    elif mutation == "source":
        card["chunks"][0]["content"] = "无需任何前提，直接复位。"
    else:
        detail["llm_calls"] = [{"caller": "manual-rag"}]
    assert probe.judge_source_evidence("胎压灯复位？", obs(speech=source), detail, card)


def test_custom_source_corpus_is_read_only_and_does_not_change_frozen_twenty():
    from scripts.probe_manual_rag_full_coverage import validate_live_query_safety
    corpus = probe.CORPUS.with_name("manual_source_evidence.yaml")
    cases = probe.load_cases(corpus)
    result = validate_live_query_safety([
        {"id": case["id"], "query": turn["say"]} for case in cases for turn in case["turns"]])
    assert result["fast_intent_none"] == 6
    assert all(turn["expect"]["source_evidence"] for case in cases for turn in case["turns"])
    assert len(probe.load_cases()) == 20


def test_image_assertion_rejects_an_illustration_in_place_of_the_controlled_icon():
    expected = {"image_assets": ["manual:p192:warning"]}
    card = {"type": "manual", "images": [{"asset_id": "manual:p257:illustration"}]}
    assert probe.judge(expected, obs(card_text=json.dumps(card)), {})["failures"] == [
        "manual_image_missing:manual:p192:warning"]
    card["images"] = [{"asset_id": "manual:p192:warning"}]
    assert not probe.judge(expected, obs(card_text=json.dumps(card)), {})["failures"]


def test_source_visual_questions_pass_the_unchanged_read_only_preflight():
    from scripts.probe_manual_rag_full_coverage import validate_live_query_safety
    cases = probe.load_cases(probe.CORPUS.with_name("manual_source_visual.yaml"))
    result = validate_live_query_safety([
        {"id": case["id"], "query": turn["say"]} for case in cases for turn in case["turns"]])
    assert result["fast_intent_none"] == 2
