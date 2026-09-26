"""The baseline must expose product failures without confusing missing evidence with success."""
import json
import asyncio

import pytest

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
