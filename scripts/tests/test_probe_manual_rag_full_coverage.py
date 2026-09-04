import json

from scripts.probe_manual_rag_full_coverage import (
    failure_ids_from_artifact,
    merge_finals,
    sanitize_message,
    summarize,
    validate_live_query_safety,
)


def test_merge_finals_preserves_all_actions_cards_and_confirmation():
    first = {
        "speech": "first",
        "actions": [{"type": "vehicle.control"}],
        "need_confirm": False,
        "ui_card": {"type": "manual", "chunks": []},
    }
    later = {
        "speech": "later",
        "actions": [],
        "need_confirm": True,
        "ui_card": {"type": "weather"},
    }

    merged = merge_finals(first, later)

    assert merged["speech"] == "first\nlater"
    assert merged["actions"] == [{"type": "vehicle.control"}]
    assert merged["need_confirm"] is True
    assert merged["ui_card"] == {
        "type": "card_group",
        "items": [first["ui_card"], later["ui_card"]],
    }


def test_sanitize_message_replaces_image_payload_with_length():
    message = {
        "ui_card": {
            "type": "manual",
            "images": [{"caption": "图", "data_uri": "data:image/png;base64,eA=="}],
        },
    }

    sanitized = sanitize_message(message)

    assert "data_uri" not in sanitized["ui_card"]["images"][0]
    assert sanitized["ui_card"]["images"][0]["data_uri_chars"] == 26
    assert "data_uri" in message["ui_card"]["images"][0]


def test_summary_keeps_failures_actions_confirmation_and_state_separate():
    payload = {
        "scheduled": 2,
        "state_before": {"wiper": False},
        "state_after": {"wiper": True},
        "rows": [
            {"id": "a", "kind": "section", "passed": True,
             "actions": [], "need_confirm": False, "probe_error": ""},
            {"id": "b", "kind": "visual", "passed": False,
             "actions": [{"type": "vehicle.control"}], "need_confirm": True,
             "probe_error": "TimeoutError"},
        ],
    }

    summary = summarize(payload)

    assert summary["passed"] == 1 and summary["failed"] == 1
    assert summary["by_kind"]["section"] == {"total": 1, "passed": 1}
    assert summary["failed_ids"] == ["b"]
    assert summary["action_rows"] == ["b"]
    assert summary["confirmation_rows"] == ["b"]
    assert summary["probe_errors"] == ["b"]
    assert summary["state_diff"] == {
        "wiper": {"before": False, "after": True},
    }


def test_live_query_safety_rejects_edge_route_and_non_question():
    safe = [{
        "id": "safe",
        "query": "我想知道 SU7 用户手册里车辆保养 空调滤芯更换？",
    }]

    assert validate_live_query_safety(safe) == {
        "total": 1,
        "question_shape_passed": 1,
        "fast_intent_none": 1,
    }

    unsafe = [{"id": "unsafe", "query": "打开空调"}]
    try:
        validate_live_query_safety(unsafe)
    except RuntimeError as exc:
        assert "live query safety preflight failed" in str(exc)
    else:
        raise AssertionError("unsafe edge query passed preflight")


def test_failure_retry_source_is_release_bound(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({
        "expected_release_sha": "a" * 40,
        "rows": [
            {"id": "pass", "passed": True},
            {"id": "fail", "passed": False},
        ],
    }), encoding="utf-8")

    assert failure_ids_from_artifact(
        path, expected_release="a" * 40) == {"fail"}

    try:
        failure_ids_from_artifact(path, expected_release="b" * 40)
    except RuntimeError as exc:
        assert "release mismatch" in str(exc)
    else:
        raise AssertionError("cross-release failure retry was accepted")
