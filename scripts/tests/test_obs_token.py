"""Tools get a collector operator token without children ever holding the signing secret."""
from pathlib import Path

from runtime import obs_access
from scripts import obs_token, run_e2e

SECRET = "tool-side-test-secret-" + "q" * 40
KEY = obs_access.derive_key(SECRET)


def _write_env(root: Path, text: str) -> Path:
    (root / ".env").write_text(text, encoding="utf-8")
    return root


def test_a_runner_child_only_uses_the_token_it_was_handed(tmp_path):
    root = _write_env(tmp_path, f"{obs_access.SECRET_ENV}={SECRET}\n")
    handed = obs_access.issue(KEY)
    assert obs_token.operator_token({obs_access.TOKEN_ENV: handed}, root=root) == handed
    # a runner child without a handed token never falls back to a secret
    assert obs_token.operator_token({"E2E_RUN_ID": "e2e-run", obs_access.SECRET_ENV: SECRET}, root=root) == ""


def test_manual_tools_sign_with_the_process_or_root_env_secret(tmp_path):
    from_env = obs_token.operator_token({obs_access.SECRET_ENV: SECRET}, root=tmp_path)
    assert obs_access.verify(KEY, from_env)
    root = _write_env(tmp_path, f"OTHER=1\n{obs_access.SECRET_ENV}={SECRET}\n")
    assert obs_access.verify(KEY, obs_token.operator_token({}, root=root))
    assert obs_token.collector_headers({obs_access.SECRET_ENV: SECRET})["Authorization"].startswith("Bearer obs.v1.")


def test_without_any_secret_there_is_no_token(tmp_path):
    assert obs_token.operator_token({}, root=tmp_path) == ""
    assert obs_token.operator_token({obs_access.SECRET_ENV: "too-short"}, root=tmp_path) == ""


def test_the_runner_hands_children_a_token_signed_with_the_effective_secret():
    env = run_e2e._with_collector_token({obs_access.SECRET_ENV: SECRET})
    assert obs_access.verify(KEY, env[obs_access.TOKEN_ENV])
    assert run_e2e._with_collector_token({}) == {}
