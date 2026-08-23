from __future__ import annotations

from scripts import probe_qa_long_sessions as long_qa


def test_five_personas_each_have_a_continuous_50_to_100_turn_plan():
    plans = long_qa.build_persona_plans()

    assert set(plans) == {
        "vehicle", "family", "merchant", "information", "adversarial",
    }
    for name, cases in plans.items():
        turns = sum(len(case["turns"]) for case in cases)
        assert 50 <= turns <= 100, (name, turns)
        assert all(int(turn.get("sid", 0)) == 0
                   for case in cases for turn in case["turns"]), name


def test_long_session_plan_never_authorizes_dangerous_or_merchant_writes():
    plans = long_qa.build_persona_plans()
    forbidden = ("付款", "支付", "确认下单", "确认开门", "确认解锁")

    for cases in plans.values():
        for case in cases:
            for turn in case["turns"]:
                assert not turn.get("confirm")
                text = str(turn.get("say") or "")
                assert not any(word in text for word in forbidden), text


def test_collector_audit_accepts_minimax_and_rejects_any_other_llm_provider():
    good = {
        "turn": {"trace_id": "t1", "status": "ok", "path": "cloud",
                 "intents": "info.weather"},
        "spans": [{"service": "cloud-orchestrator", "node": "agent.call",
                   "attrs": {"agent_id": "info", "intent": "info.weather"}}],
        "llm_calls": [{"provider": "minimax", "model": "MiniMax-M3",
                       "status": "ok", "fallback": 0}],
        "logs": [],
    }
    summary, failures = long_qa.audit_trace_detail(good, "t1")
    assert failures == []
    assert summary["providers"] == ["minimax:MiniMax-M3"]
    assert summary["agents"] == ["info"]

    bad = {**good, "llm_calls": [
        {"provider": "deepseek", "model": "deepseek-v4-flash",
         "status": "ok", "fallback": 1},
    ]}
    _, failures = long_qa.audit_trace_detail(bad, "t1")
    assert failures and "非 MiniMax" in failures[0]


def test_missing_collector_trace_is_never_counted_as_pass():
    _, failures = long_qa.audit_trace_detail({"error": "not found"}, "lost")
    assert failures == ["collector 缺少 trace lost"]


def test_card_provenance_is_collected_and_mock_is_rejected():
    card_text = '''{
      "type": "weather",
      "_prov": {"provider": "qweather", "mode": "real"},
      "items": [{"_prov": {"provider": "amap", "mode": "degraded"}}]
    }'''
    entries, failures = long_qa.audit_card_provenance(card_text)
    assert entries == [
        {"provider": "qweather", "mode": "real"},
        {"provider": "amap", "mode": "degraded"},
    ]
    assert failures == []

    entries, failures = long_qa.audit_card_provenance(
        '{"_prov":{"provider":"qweather","mode":"mock"}}')
    assert entries == [{"provider": "qweather", "mode": "mock"}]
    assert failures == ["真栈卡片出现 mock provenance: qweather"]
