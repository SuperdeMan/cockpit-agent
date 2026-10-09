"""Visual v2 read contracts: honest counts, filtering and legacy response shapes."""
import time

import pytest
from fastapi.testclient import TestClient

from observability.collector.db import ObsDB
from observability.collector.server import create_app
from runtime import obs_access
from runtime.outcome import CATEGORY_OF, category_of


@pytest.fixture
def client():
    key = obs_access.derive_key("dashboard-query-test-" + "x" * 32)
    db = ObsDB(":memory:")
    with TestClient(create_app(db=db, operator_key=key)) as value:
        value.headers.update(obs_access.headers(obs_access.issue(key)))
        yield value
    db.close()


def _turn(db, trace_id, *, session="demo-one", ts=1000, outcome="completed",
          mode="toolcall", edge="", actionability="", **kw):
    db.insert_turn({"trace_id": trace_id, "session_id": session, "ts": ts,
                    "user_text": "打开空调", "status": "ok", "duration_ms": 100, **kw})
    if outcome:
        db.insert_span({"trace_id": trace_id, "node": "cloud.outcome", "attrs": {"kind": outcome}})
    db.insert_span({"trace_id": trace_id, "node": "cloud.planning", "attrs": {
        "plan_mode": mode, "edge_nlu": edge, "actionability": actionability,
    }})


def test_search_filters_run_before_pagination_and_count_the_same_rows(client):
    db = client.app.state.db
    for i in range(6):
        _turn(db, f"t{i}", ts=1000 + i, session="app-one" if i % 2 else "demo-one",
              outcome="partial" if i % 2 else "completed", duration_ms=100 + i)
    _turn(db, "excluded", session="e2e-run", ts=9999)
    params = {"paginated": 1, "limit": 2, "offset": 1,
              "origin": "hmi,app", "category": "progress,session_control",
              "outcome": "partial,completed", "min_duration_ms": 102,
              "since": 1001, "until": 1005}
    response = client.get("/api/search", params=params)
    assert response.status_code == 200
    page = response.json()
    assert {key: page[key] for key in ("total", "limit", "offset")} == {
        "total": 4, "limit": 2, "offset": 1,
    }
    assert [row["trace_id"] for row in page["items"]] == ["t4", "t3"]
    assert {row["origin"] for row in page["items"]} == {"app", "hmi"}
    assert {row["outcome_category"] for row in page["items"]} == {"progress"}
    exhausted = client.get("/api/search", params={**params, "offset": 200}).json()
    assert exhausted["items"] == [] and exhausted["total"] == 4
    legacy = client.get("/api/search", params={"q": "打开", "limit": 2}).json()
    assert isinstance(legacy, list) and len(legacy) == 2


@pytest.mark.parametrize("flag", [
    "edge_disagreement", "actionability_disagreement", "degraded", "has_warnings", "labeled",
])
def test_flags_support_positive_negative_and_omitted_filters(client, flag):
    db = client.app.state.db
    _turn(db, "plain")
    _turn(db, "marked", ts=2000, mode="toolcall_salvage_kept",
          edge="media.play|0.9!=", actionability="clarify|0.8!=")
    db.set_gold("marked", "media.play")
    for level in ("WARNING", "ERROR", "INFO"):
        db.insert_log({"trace_id": "marked", "level": level})
    yes = client.get("/api/search", params={flag: "true", "paginated": 1}).json()
    no = client.get("/api/search", params={flag: "false", "paginated": 1}).json()
    assert yes["total"] == 1 and yes["items"][0]["trace_id"] == "marked"
    assert yes["items"][0]["warning_count"] == 2
    assert no["total"] == 1 and no["items"][0]["trace_id"] == "plain"
    assert len(client.get("/api/search").json()) == 2


def test_warning_counts_do_not_count_other_traces_or_duplicate_join_rows(client):
    db = client.app.state.db
    _turn(db, "t1")
    _turn(db, "t2")
    for level in ("WARNING", "WARN", "error", "CRITICAL", "FATAL", "INFO", "DEBUG"):
        db.insert_log({"trace_id": "t1", "level": level})
    db.insert_log({"trace_id": "unrelated", "level": "WARNING"})
    result = client.get("/api/search", params={"has_warnings": 1, "paginated": 1}).json()
    assert result["total"] == 1
    assert result["items"][0]["warning_count"] == 5
    assert client.get("/api/turns/t1").json()["turn"]["warning_count"] == 5


def test_every_outcome_projection_and_filter_use_runtime_declaration(client, monkeypatch):
    db = client.app.state.db
    monkeypatch.setitem(CATEGORY_OF, "future_test_kind", "progress")
    kinds = [*CATEGORY_OF, "unrecognized_kind", ""]
    for i, kind in enumerate(kinds):
        _turn(db, f"outcome-{i}", outcome=kind, ts=i + 1)
    rows = db.search_turns(limit=100)
    assert {row["outcome"]: row["outcome_category"] for row in rows} == {
        kind: category_of(kind) for kind in kinds
    }
    for category in set(CATEGORY_OF.values()):
        page = db.search_turns(category=category, limit=100, paginated=True)
        assert {row["outcome"] for row in page["items"]} == {
            kind for kind, expected in CATEGORY_OF.items() if expected == category
        }
        assert page["total"] == len(page["items"])
    assert db.turn_detail("outcome-0")["turn"]["outcome_category"] == category_of(kinds[0])
    assert {row["origin"] for row in db.session_turns("demo-one")} == {"hmi"}


def test_sessions_keep_full_group_and_first_utterance_after_text_filter(client):
    db = client.app.state.db
    _turn(db, "a1", ts=1000, user_text="首句", session="demo-a")
    _turn(db, "a2", ts=4000, user_text="命中机场", session="demo-a")
    _turn(db, "b1", ts=2000, user_text="命中机场", session="app-b")
    page = client.get("/api/sessions", params={"q": "机场", "paginated": 1, "limit": 1}).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    row = page["items"][0]
    assert row["session_id"] == "demo-a" and row["turns"] == 2
    assert row["first_user_text"] == "首句" and row["origin"] == "hmi"
    second = client.get("/api/sessions", params={"q": "机场", "paginated": 1,
                                                "limit": 1, "offset": 1}).json()
    assert second["total"] == 2 and second["items"][0]["session_id"] == "app-b"
    filtered = client.get("/api/sessions", params={"origin": "app", "paginated": 1}).json()
    assert filtered["total"] == 1 and filtered["items"][0]["origin"] == "app"
    assert isinstance(client.get("/api/sessions").json(), list)


def test_detail_hides_metadata_after_join_and_normalizes_without_rewriting_evidence(client):
    db = client.app.state.db
    _turn(db, "one")
    db.insert_span({"trace_id": "one", "node": "http.request", "status": "error"})
    for pinned, tier in ((True, "test-tier"), (False, "")):
        db.insert_llm({"trace_id": "one", "pinned": pinned, "requested_tier": tier})
    changes = db._conn.total_changes
    schema = db._conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    for url in ("/api/turns/one", "/api/export/one"):
        detail = client.get(url).json()
        assert not any(span["node"] == "llm.call.meta" for span in detail["spans"])
        assert next(span for span in detail["spans"] if span["node"] == "http.request")["status"] == "err"
        assert [(call["pinned"], call["requested_tier"]) for call in detail["llm_calls"]] == [
            (True, "test-tier"), (False, ""),
        ]
    assert db._conn.total_changes == changes
    assert db._conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == schema
    assert db._conn.execute("SELECT status FROM spans WHERE node='http.request'").fetchone()[0] == "error"
    assert db._conn.execute("SELECT COUNT(*) FROM spans WHERE node='llm.call.meta'").fetchone()[0] == 2


def test_meta_is_protected_and_reports_configuration_without_guessing_expiry(client, monkeypatch):
    monkeypatch.setenv("OBS_CONTENT_CAPTURE", "off")
    monkeypatch.setenv("OBS_RETENTION_DAYS", "3.5")
    monkeypatch.setenv("DEBUG_VEHICLE_CONTROL", "false")
    meta = client.get("/api/meta").json()
    assert meta == {"content_capture": False, "retention_days": 3.5,
                    "debug_vehicle_control": False,
                    "query_features": ["turn_filters", "turn_pagination", "session_pagination"]}
    assert client.get("/api/turns/missing").json() == {"error": "not found"}
    client.headers.pop("authorization")
    assert client.get("/api/meta").status_code == 401
    client.app.state.operator_key = None
    assert client.get("/api/meta").status_code == 503


def test_usage_counts_separate_errors_fallback_and_zero_reported_usage(client):
    db = client.app.state.db
    now_ms = int(time.time() * 1000)
    for extra in ({"status": "err", "fallback": True},
                  {"status": "ok"},
                  {"status": "ok", "fallback": True, "completion_tokens": 1},
                  {"status": "ok", "prompt_tokens": 4}):
        db.insert_llm({"ts": now_ms, "caller": "", "model": "test", **extra})
    db.insert_llm({"ts": now_ms - 48 * 3600 * 1000, "model": "old", "fallback": True})
    groups = client.get("/api/llm/summary").json()["groups"]
    assert len(groups) == 1
    assert {key: groups[0][key] for key in ("caller", "calls", "errors", "fallback_calls", "zero_usage_calls")} == {
        "caller": "(未归属)", "calls": 4, "errors": 1, "fallback_calls": 2, "zero_usage_calls": 1,
    }


@pytest.mark.parametrize("params", [
    {"offset": -1}, {"min_duration_ms": -1},
])
def test_search_rejects_invalid_bounds(client, params):
    assert client.get("/api/search", params=params).status_code == 422
