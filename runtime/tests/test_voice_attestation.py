"""CA2-15 S2: an occupant is taken only from a signed, fresh voiceprint result for this owner."""
import base64
import json

import pytest

from runtime import memory_projection as mp
from runtime import voice_attestation as va

KEY = va.derive_key(b"k" * 64)
NOW = 1_800_000_000


def _token(**kw):
    claims = {"user_id": "u1", "occupant_id": "occ-2", "display_name": "小雨", "now": NOW}
    claims.update(kw)
    return va.issue(KEY, **claims)


def test_a_fresh_token_for_this_owner_names_the_occupant():
    got = va.verify(_token(), KEY, user_id="u1", now=NOW + 10)
    assert got == va.VoiceAttestation("u1", "occ-2", "小雨", NOW + va.TTL_S)


@pytest.mark.parametrize("case", ["other_owner", "expired", "future", "other_key", "no_key", "tampered",
                                  "not_a_token", "too_long"])
def test_anything_wrong_gives_no_occupant(case):
    token, key, owner, now = _token(), KEY, "u1", NOW + 10
    if case == "other_owner":
        owner = "u2"
    elif case == "expired":
        now = NOW + va.TTL_S
    elif case == "future":
        token = _token(now=NOW + 120)
    elif case == "other_key":
        key = va.derive_key(b"x" * 64)
    elif case == "no_key":
        key = None
    elif case == "tampered":
        head, payload, sig = token.rsplit(".", 2)
        claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
        claims["occupant_id"] = "occ-9"
        forged = base64.urlsafe_b64encode(
            json.dumps(claims, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
        token = f"{head}.{forged}.{sig}"
    elif case == "not_a_token":
        token = "occ-2"
    elif case == "too_long":
        token = token + "A" * va.MAX_TOKEN_CHARS
    assert va.verify(token, key, user_id=owner, now=now) is None


def test_issue_refuses_an_empty_owner_or_occupant_and_ttl_is_bounded():
    with pytest.raises(ValueError):
        va.issue(KEY, user_id="", occupant_id="occ-2")
    with pytest.raises(ValueError):
        va.issue(KEY, user_id="u1", occupant_id="")
    long_lived = va.issue(KEY, user_id="u1", occupant_id="occ-2", now=NOW, ttl_s=va.TTL_S + 1)
    assert va.verify(long_lived, KEY, user_id="u1", now=NOW + 1) is None


def test_the_key_comes_from_the_mounted_mesh_key_and_is_absent_without_it(tmp_path):
    material = tmp_path / "server.key"
    material.write_bytes(b"m" * 64)
    assert va.load_key(environ={"GRPC_TLS_KEY": str(material)}) == va.derive_key(b"m" * 64)
    assert va.load_key(environ={"GRPC_TLS_KEY": str(tmp_path / "missing")}) is None
    short = tmp_path / "short.key"
    short.write_bytes(b"s")
    assert va.load_key(environ={"GRPC_TLS_KEY": str(short)}) is None


PREF = {"kind": "semantic", "privacy_level": "normal", "predicate": "climate.temperature",
        "scope": "profile.comfort", "text": "喜欢 26 度"}


@pytest.mark.parametrize("item,shown", [
    (PREF, True),
    ({**PREF, "predicate": "taste.spicy", "scope": "profile.taste"}, True),
    ({**PREF, "privacy_level": "sensitive"}, False),
    ({**PREF, "privacy_level": "highly_sensitive", "predicate": "place.home", "scope": "profile.places"}, False),
    ({**PREF, "predicate": "identity.name", "scope": "profile.identity"}, False),
    ({**PREF, "predicate": "person.child", "scope": "profile.person"}, False),
    ({**PREF, "kind": "episodic", "scope": "episodic.general"}, False),
    ({**PREF, "kind": "procedural", "predicate": "routine.commute"}, False),
    ({**PREF, "subject": "爸爸"}, False),
])
def test_unrecognized_voices_only_see_ordinary_preferences(item, shown):
    assert mp.visible(item, mp.NORMAL_ONLY) is shown
    assert mp.visible(item, mp.NONE) is True


def test_scopes_and_unknown_projection_values():
    assert mp.scope_visible("profile.taste", mp.NORMAL_ONLY)
    assert not mp.scope_visible("profile.places", mp.NORMAL_ONLY)
    assert mp.normalize("") == mp.NONE and mp.normalize(None) == mp.NONE
    assert mp.normalize("something-else") == mp.NORMAL_ONLY          # unknown ⇒ strictest
