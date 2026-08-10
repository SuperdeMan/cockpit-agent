"""B3 DEPLOY_PROFILE 三档 × 满足/不满足矩阵。

反向验证两头都做（B1 那一课）：
- **注入缺陷会红**：强制表每一项都有至少一次「只破这一项」的突变，且必须拒绝启动；
- **对照仍绿**：合规 env 在 prod 档零 violation——证明没修过头。

只做前一半的话，一个恒红的检查也能通过验收。
"""
from __future__ import annotations

import pytest

from runtime import profile as P


def _prod_ok_env() -> dict[str, str]:
    """一份**逐项满足**强制表的 prod env。所有突变都从它派生，只改一个键。"""
    return {
        P.PROFILE_ENV: P.PROD,
        "AUTH_REQUIRED": "true",
        "PERMISSIONS_FAIL_OPEN": "false",
        "GRPC_TLS": "on",
        "AUTH_TOKENS": "prod-tok-1:u1:v1:vehicle.control,media.control",
        "CLOUD_CHANNEL_TOKEN": "prod-channel-1",
        "CLOUD_CHANNEL_TOKENS": "prod-channel-1,prod-channel-2",
        "OBS_CONTENT_CAPTURE": "off",
        "REQUIRE_REAL_PROVIDERS": "on",
        "POSTGRES_PASSWORD": "pg-not-the-default",
        "POSTGRES_DSN": "postgresql://cockpit:pg-not-the-default@postgres:5432/cockpit",
        "DEBUG_VEHICLE_CONTROL": "false",
        "GRAFANA_ADMIN_PASSWORD": "grafana-not-the-default",
    }


@pytest.fixture(autouse=True)
def _reset_announce():
    P._reset_announce_state()
    yield
    P._reset_announce_state()


# ── 档位解析 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, P.DEV), ("", P.DEV), ("  ", P.DEV),
    ("dev", P.DEV), ("DEV", P.DEV), ("demo", P.DEMO), ("prod", P.PROD),
    (" Prod ", P.PROD),
])
def test_resolve_profile(raw, expected):
    env = {} if raw is None else {P.PROFILE_ENV: raw}
    assert P.resolve_profile(env) == expected


@pytest.mark.parametrize("raw", ["production", "PRODUCTION", "stage", "prod1", "true"])
def test_unknown_profile_refuses_to_fall_back_to_dev(raw):
    """未知档位**不静默回落 dev**——拼错 profile 却按零校验跑正是本闸要消灭的形态。"""
    with pytest.raises(P.DeployProfileError):
        P.resolve_profile({P.PROFILE_ENV: raw})


def test_unknown_profile_exits_at_enforce():
    with pytest.raises(SystemExit) as ei:
        P.enforce_deploy_profile({P.PROFILE_ENV: "production"})
    assert ei.value.code == P.EXIT_CONFIG


# ── dev 档：零校验（本方案的硬约束）────────────────────────────────────────

@pytest.mark.parametrize("env", [
    {},                                   # 完全裸的进程环境
    {P.PROFILE_ENV: "dev"},
    {P.PROFILE_ENV: "dev", "AUTH_REQUIRED": "false",
     "PERMISSIONS_FAIL_OPEN": "true", "GRPC_TLS": "off",
     "OBS_CONTENT_CAPTURE": "on", "DEBUG_VEHICLE_CONTROL": "true"},
])
def test_dev_profile_checks_nothing(env, capsys):
    assert P.enforce_deploy_profile(env) == P.DEV
    assert capsys.readouterr().err == ""      # 一个字都不打：dev 档逐字保持现状


# ── prod 档：合规即通过（对照组，证明没修过头）────────────────────────────

def test_prod_compliant_env_passes():
    env = _prod_ok_env()
    assert P.audit(env) == []
    assert P.enforce_deploy_profile(env) == P.PROD


def test_demo_compliant_env_is_silent(capsys):
    env = {**_prod_ok_env(), P.PROFILE_ENV: P.DEMO}
    assert P.enforce_deploy_profile(env) == P.DEMO
    assert capsys.readouterr().err == ""


# ── prod 档：单项不满足 → 拒绝启动（验收判据 §4.2 的矩阵）──────────────────

#: (用例名, 覆盖到的强制表项, env 突变)。突变**只破一项**，其余保持合规。
_SINGLE_FAULTS: list[tuple[str, int, dict[str, str | None]]] = [
    ("auth_required_off", 1, {"AUTH_REQUIRED": "false"}),
    ("auth_required_unset", 1, {"AUTH_REQUIRED": None}),
    # 「看起来是真」的值在 Go 侧其实是关的——通用真值判断会在这里报绿。
    ("auth_required_truthy_but_not_true", 1, {"AUTH_REQUIRED": "1"}),
    ("auth_required_yes", 1, {"AUTH_REQUIRED": "yes"}),
    ("permissions_fail_open_on", 2, {"PERMISSIONS_FAIL_OPEN": "true"}),
    ("permissions_fail_open_unset", 2, {"PERMISSIONS_FAIL_OPEN": None}),
    ("permissions_fail_open_off_typo", 2, {"PERMISSIONS_FAIL_OPEN": "0"}),
    ("grpc_tls_off", 3, {"GRPC_TLS": "off"}),
    ("grpc_tls_unset", 3, {"GRPC_TLS": None}),
    # Go 侧是 switch 精确匹配、不 lower——大写 ON 那边读成关。
    ("grpc_tls_uppercase", 3, {"GRPC_TLS": "ON"}),
    ("auth_tokens_empty", 4, {"AUTH_TOKENS": ""}),
    ("auth_tokens_unset", 4, {"AUTH_TOKENS": None}),
    ("auth_tokens_sample_literal", 4,
     {"AUTH_TOKENS": "demo-u1:u1:v1:vehicle.control"}),
    # 畸形条目被消费方跳过 ⇒ 实际 token 表仍是空的，这里也不能算数。
    ("auth_tokens_malformed_only", 4, {"AUTH_TOKENS": "justatoken"}),
    ("channel_token_unset", 5, {"CLOUD_CHANNEL_TOKEN": None}),
    ("channel_allowlist_empty", 5, {"CLOUD_CHANNEL_TOKENS": ""}),
    ("channel_token_not_in_allowlist", 5, {"CLOUD_CHANNEL_TOKEN": "other"}),
    ("channel_token_sample_literal", 5,
     {"CLOUD_CHANNEL_TOKEN": "demo-channel-v1",
      "CLOUD_CHANNEL_TOKENS": "demo-channel-v1"}),
    ("obs_capture_on", 6, {"OBS_CONTENT_CAPTURE": "on"}),
    ("obs_capture_unset", 6, {"OBS_CONTENT_CAPTURE": None}),
    ("require_real_off", 7, {"REQUIRE_REAL_PROVIDERS": "off"}),
    ("require_real_unset", 7, {"REQUIRE_REAL_PROVIDERS": None}),
    ("pg_password_default", 8, {"POSTGRES_PASSWORD": "cockpit"}),
    ("pg_password_unset", 8, {"POSTGRES_PASSWORD": None}),
    ("pg_dsn_still_default", 8,
     {"POSTGRES_DSN": "postgresql://cockpit:cockpit@postgres:5432/cockpit"}),
    ("debug_vehicle_on", 11, {"DEBUG_VEHICLE_CONTROL": "true"}),
    ("debug_vehicle_unset", 11, {"DEBUG_VEHICLE_CONTROL": None}),
    ("grafana_default", 12, {"GRAFANA_ADMIN_PASSWORD": "admin"}),
    ("grafana_unset", 12, {"GRAFANA_ADMIN_PASSWORD": None}),
]


def _mutate(patch: dict[str, str | None]) -> dict[str, str]:
    env = _prod_ok_env()
    for key, value in patch.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


@pytest.mark.parametrize("name,idx,patch",
                         _SINGLE_FAULTS, ids=[c[0] for c in _SINGLE_FAULTS])
def test_prod_single_fault_refuses_to_start(name, idx, patch):
    env = _mutate(patch)
    violations = P.audit(env)
    assert [v.idx for v in violations] == [idx], (
        f"{name}: 应只破第 {idx} 项，实测 {[v.idx for v in violations]}")
    with pytest.raises(SystemExit) as ei:
        P.enforce_deploy_profile(env)
    assert ei.value.code == P.EXIT_CONFIG


def test_every_check_has_a_fault_case():
    """强制表里不许有「谁也测不到」的项——加一项就得配一条突变。"""
    covered = {idx for _, idx, _ in _SINGLE_FAULTS}
    assert covered == {c.idx for c in P.CHECKS}


# ── demo 档：告警但不阻断 ──────────────────────────────────────────────────

def test_demo_warns_once_and_does_not_block(capsys):
    env = {**_prod_ok_env(), P.PROFILE_ENV: P.DEMO,
           "AUTH_REQUIRED": "false", "GRPC_TLS": "off"}
    assert P.enforce_deploy_profile(env) == P.DEMO
    first = capsys.readouterr().err
    assert "AUTH_REQUIRED" in first and "GRPC_TLS" in first
    # 聚合成一段（两项在同一次输出里），且第二次建 server 不再刷屏
    assert P.enforce_deploy_profile(env) == P.DEMO
    assert capsys.readouterr().err == ""


def test_prod_decision_is_never_cached():
    """幂等只作用在打印上；判定缓存了就等于「第二次调用时闸是开的」。"""
    env = _mutate({"AUTH_REQUIRED": "false"})
    for _ in range(3):
        with pytest.raises(SystemExit):
            P.enforce_deploy_profile(env)


# ── 报错可读性与脱敏 ──────────────────────────────────────────────────────

def test_report_lists_key_actual_expected_and_why():
    env = _mutate({"AUTH_REQUIRED": "false"})
    report = P.format_report(P.PROD, P.audit(env))
    assert "AUTH_REQUIRED" in report
    assert "要求：" in report and "原因：" in report


def test_report_never_echoes_credentials(capsys):
    """密钥/token 不进日志（CLAUDE.md 红线）——只回显形状。"""
    secret_pwd = "super-secret-pg-password"
    env = _mutate({"POSTGRES_PASSWORD": secret_pwd,
                   "POSTGRES_DSN": f"postgresql://cockpit:{secret_pwd}@postgres:5432/cockpit",
                   "AUTH_REQUIRED": "false"})
    env["AUTH_TOKENS"] = "very-secret-session-token:u1:v1:vehicle.control"
    with pytest.raises(SystemExit):
        P.enforce_deploy_profile(env)
    err = capsys.readouterr().err
    assert secret_pwd not in err
    assert "very-secret-session-token" not in err


def test_sample_literal_is_named_in_the_report(capsys):
    """示例 token 是 .env.example 里的公开值——配错的人需要看到自己抄了它。"""
    env = _mutate({"AUTH_TOKENS": "demo-u1:u1:v1:vehicle.control"})
    with pytest.raises(SystemExit):
        P.enforce_deploy_profile(env)
    assert "demo-u1" in capsys.readouterr().err


def test_show_redacts_secret_keys_but_keeps_shape():
    env = {"AUTH_TOKENS": "abcdefghij", "GRPC_TLS": "off"}
    assert P._show("AUTH_TOKENS", env) == "<已设，10 字符>"
    assert P._show("GRPC_TLS", env) == "'off'"          # 非凭据键回显原值
    assert P._show("POSTGRES_PASSWORD", env) == "<未设>"
    assert P._show("AUTH_TOKENS", {"AUTH_TOKENS": ""}) == "<空>"
