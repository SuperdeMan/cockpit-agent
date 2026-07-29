from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "test" / "e2e_voiceprint.py"


def test_voiceprint_e2e_consumes_run_fixture_without_external_tts_calls():
    source = SOURCE.read_text(encoding="utf-8")
    loader = source.split("def load_voiceprint_fixture()", 1)[1].split(
        "\n\nasync def enroll",
        1,
    )[0]

    assert "E2E_VOICEPRINT_FIXTURE_MANIFEST" in source
    assert "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256" in source
    assert "E2E_AUDIO_API_ORIGIN" in source
    assert "load_voiceprint_fixture" in source
    assert "read_verified_pcm" in loader
    assert ".read_bytes()" not in loader
    assert "/api/voices" not in source
    assert "/api/tts" not in source
    assert "def synth(" not in source
    assert "http://localhost:50059" not in source
    assert "AUDIO_API_URL" not in source


def test_voiceprint_e2e_requires_exact_accept_and_bilateral_isolation():
    source = SOURCE.read_text(encoding="utf-8")

    for case_id in (
        "identify_primary_accept",
        "identify_secondary_accept",
        "storage_isolation_a",
        "storage_isolation_b",
        "recall_isolation_a",
        "recall_isolation_b",
        "visible_recall_isolation_a",
        "visible_recall_isolation_b",
        "forget_secondary_all_zero",
        "forget_secondary_primary_survives",
        "forget_user_profile_zero",
        "forget_user_sessions_zero",
        "danger_confirm_primary",
        "danger_confirm_secondary",
        "danger_confirm_same_gate",
    ):
        assert f'"{case_id}"' in source
    assert 'get("decision") == "accept"' in source


def test_voiceprint_cleanup_fails_closed_for_profile_and_session_residue():
    source = SOURCE.read_text(encoding="utf-8")

    assert "cleanup ForgetUser returned ok=false" in source
    assert "cleanup left profile residue" in source
    assert "cleanup left session residue" in source
