"""Collector operator tokens: only the derived key signs them; nothing else passes for one."""
import hashlib
import hmac

import pytest

from runtime import obs_access as oa

SECRET = "e2e-root-secret-" + "s" * 40
KEY = oa.derive_key(SECRET)
NOW = 1_800_000_000


def test_a_token_round_trips_and_carries_the_operator():
    claims = oa.verify(KEY, oa.issue(KEY, now=NOW), now=NOW + 60)
    assert claims["sub"] == "operator" and claims["exp"] - claims["iat"] == oa.MAX_TTL_S


@pytest.mark.parametrize("case", ["other_key", "tampered", "expired", "future", "too_long_lived", "e2e_prefix",
                                  "raw_secret_key", "no_key", "not_a_string"])
def test_untrusted_tokens_are_refused(case):
    token = oa.issue(KEY, now=NOW)
    key, at = KEY, NOW + 60
    if case == "other_key":
        token = oa.issue(oa.derive_key("another-root-secret-" + "t" * 40), now=NOW)
    elif case == "tampered":
        token = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    elif case == "expired":
        at = NOW + oa.MAX_TTL_S
    elif case == "future":
        at = NOW - oa.MAX_FUTURE_S - 1
    elif case == "too_long_lived":
        payload = oa._encode(oa._canonical({"v": 1, "sub": "operator", "iat": NOW, "exp": NOW + oa.MAX_TTL_S + 1}))
        token = f"{oa.PREFIX}{payload}.{oa._encode(oa._signature(KEY, payload))}"
    elif case == "e2e_prefix":
        token = "e2e.v1." + token[len(oa.PREFIX):]
    elif case == "raw_secret_key":
        # signed straight with the e2e root secret (what an e2e identity token uses): domain separation refuses it
        payload = token[len(oa.PREFIX):].split(".")[0]
        raw = hmac.new(SECRET.encode(), oa._SIGN_CONTEXT + payload.encode(), hashlib.sha256).digest()
        token = f"{oa.PREFIX}{payload}.{oa._encode(raw)}"
    elif case == "no_key":
        key = None
    elif case == "not_a_string":
        token = None
    with pytest.raises(oa.ObsTokenError):
        oa.verify(key, token, now=at)


def test_a_short_or_missing_root_secret_configures_nothing():
    assert oa.derive_key("") is None and oa.derive_key("short") is None
    assert oa.key_from_env({}) is None
    assert oa.key_from_env({oa.SECRET_ENV: SECRET}) == KEY
    with pytest.raises(oa.ObsTokenError):
        oa.issue(None)
    with pytest.raises(oa.ObsTokenError):
        oa.issue(KEY, ttl_s=oa.MAX_TTL_S + 1)


def test_header_and_frame_helpers():
    assert oa.bearer("Bearer abc") == "abc" and oa.bearer("bearer  abc ") == "abc"
    assert oa.bearer("Basic abc") == "" and oa.bearer("") == ""
    assert oa.headers("") == {} and oa.headers("t") == {"Authorization": "Bearer t"}
    assert oa.token_from_frame(oa.auth_frame("t")) == "t"
    assert oa.token_from_frame('{"type":"hello","token":"t"}') == "" and oa.token_from_frame("not json") == ""
