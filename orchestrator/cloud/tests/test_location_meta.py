from types import SimpleNamespace

from orchestrator.cloud.engine import PlannerEngine


def test_context_forwards_browser_location_only_with_location_scope():
    engine = object.__new__(PlannerEngine)
    request = SimpleNamespace(
        request_id="r1", session_id="s1", is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
        meta={
            "granted_scopes": "location.read,navigation.control",
            "current_lat": "39.92", "current_lng": "116.41",
            "current_accuracy_m": "12",
        },
    )

    ctx = engine._build_context(request)

    assert ctx.prefs["current_lat"] == "39.92"
    assert ctx.prefs["current_lng"] == "116.41"


def test_context_does_not_forward_browser_location_without_location_scope():
    engine = object.__new__(PlannerEngine)
    request = SimpleNamespace(
        request_id="r1", session_id="s1", is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
        meta={"granted_scopes": "navigation.control", "current_lat": "39.92", "current_lng": "116.41"},
    )

    assert "current_lat" not in engine._build_context(request).prefs
