"""B3 §2.4 Registry 静态 admission 契约测试。

反向验证两头做：
- **开启后能挡住**：无 token / 错 token / 错 agent_id 三种形态各判 PERMISSION_DENIED；
- **关闭时逐字如前**：不带 token 也能注册（这是本批「只加档不改默认」的硬约束）。
"""
from __future__ import annotations

import pytest

from cockpit.registry.v1 import registry_pb2

from registry.server import RegistryServicer
from runtime import admission

TOKENS = "tok-nav:navigation,tok-plan:charging-planner|scene-orchestrator"


class _Aborted(Exception):
    def __init__(self, code, details):
        super().__init__(details)
        self.code = code
        self.details = details


class FakeContext:
    """够用的 grpc.aio ServicerContext 替身：只实现被 Register 用到的两个方法。"""

    def __init__(self, metadata=()):
        self._metadata = tuple(metadata)

    def invocation_metadata(self):
        return self._metadata

    async def abort(self, code, details):
        raise _Aborted(code, details)


class FakeStore:
    def __init__(self):
        self.registered: list[str] = []

    def register(self, manifest, endpoint):
        self.registered.append(manifest.agent_id)
        return "lease-1"


def _request(agent_id: str):
    req = registry_pb2.RegisterRequest(endpoint="agent:50060")
    req.manifest.agent_id = agent_id
    return req


# ── 解析 ───────────────────────────────────────────────────────────────────

def test_parse_tokens():
    table = admission.parse_tokens(TOKENS)
    assert table == {"tok-nav": {"navigation"},
                     "tok-plan": {"charging-planner", "scene-orchestrator"}}


@pytest.mark.parametrize("raw", [
    None, "", "   ", ",,", "no-colon", "tok-only:", ":agent-only", " : ",
])
def test_malformed_entries_are_dropped_not_widened(raw):
    """配错的规则被**丢掉**，不被补成「允许一切」——后者比丢掉危险得多。"""
    assert admission.parse_tokens(raw) == {}


def test_partial_config_keeps_only_the_valid_entries():
    table = admission.parse_tokens("tok-ok:navigation,broken,tok-empty:")
    assert table == {"tok-ok": {"navigation"}}


def test_enabled_reflects_parsed_table_not_raw_string():
    """一行全是垃圾的配置**不算开启**——否则 admission 会「开着但谁也过不去」。"""
    assert admission.enabled({admission.TOKENS_ENV: "garbage"}) is False
    assert admission.enabled({admission.TOKENS_ENV: TOKENS}) is True
    assert admission.enabled({}) is False


# ── 判定 ───────────────────────────────────────────────────────────────────

def test_disabled_by_default_allows_anything():
    ok, reason = admission.check(None, "navigation", env={})
    assert ok and reason == ""


@pytest.mark.parametrize("metadata,agent_id", [
    ((), "navigation"),                                   # 无 token
    ((("x-agent-token", "tok-unknown"),), "navigation"),  # 错 token
    ((("x-agent-token", "tok-nav"),), "charging-planner"),  # 越权申报
])
def test_enabled_rejects(metadata, agent_id):
    ok, reason = admission.check(metadata, agent_id, env={admission.TOKENS_ENV: TOKENS})
    assert not ok and reason


def test_enabled_allows_matching_pair():
    for token, agent_id in (("tok-nav", "navigation"),
                            ("tok-plan", "charging-planner"),
                            ("tok-plan", "scene-orchestrator")):
        ok, reason = admission.check(((admission.METADATA_KEY, token),), agent_id,
                                     env={admission.TOKENS_ENV: TOKENS})
        assert ok and reason == "", (token, agent_id, reason)


def test_metadata_key_is_case_insensitive():
    ok, _ = admission.check((("X-Agent-Token", "tok-nav"),), "navigation",
                            env={admission.TOKENS_ENV: TOKENS})
    assert ok


def test_denial_reason_never_contains_the_token():
    """审计行要回答「谁申报了什么」，token 本身写进日志就是新的泄漏面。"""
    secret = "tok-super-secret"  # release-secret-fixture
    _, reason = admission.check(((admission.METADATA_KEY, secret),), "navigation",
                                env={admission.TOKENS_ENV: TOKENS})
    assert secret not in reason
    assert "navigation" in reason


def test_client_metadata_is_empty_when_unset():
    assert admission.client_metadata({}) == []
    assert admission.client_metadata({admission.AGENT_TOKEN_ENV: "  "}) == []
    assert admission.client_metadata({admission.AGENT_TOKEN_ENV: "tok-nav"}) == [
        (admission.METADATA_KEY, "tok-nav")]


# ── 服务端契约 ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_without_token_passes_when_disabled(monkeypatch):
    monkeypatch.delenv(admission.TOKENS_ENV, raising=False)
    store = FakeStore()
    servicer = RegistryServicer(store=store)
    resp = await servicer.Register(_request("navigation"), FakeContext())
    assert resp.ok and store.registered == ["navigation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata,agent_id", [
    ((), "navigation"),
    ((("x-agent-token", "tok-unknown"),), "navigation"),
    ((("x-agent-token", "tok-nav"),), "charging-planner"),
])
async def test_register_denied_when_enabled(monkeypatch, metadata, agent_id):
    monkeypatch.setenv(admission.TOKENS_ENV, TOKENS)
    store = FakeStore()
    servicer = RegistryServicer(store=store)
    with pytest.raises(_Aborted) as ei:
        await servicer.Register(_request(agent_id), FakeContext(metadata))
    assert ei.value.code.name == "PERMISSION_DENIED"
    assert store.registered == []          # 拒绝要在**写入之前**，不能先覆盖再报错


@pytest.mark.asyncio
async def test_register_allowed_pair_when_enabled(monkeypatch):
    monkeypatch.setenv(admission.TOKENS_ENV, TOKENS)
    store = FakeStore()
    servicer = RegistryServicer(store=store)
    resp = await servicer.Register(
        _request("navigation"), FakeContext(((admission.METADATA_KEY, "tok-nav"),)))
    assert resp.ok and store.registered == ["navigation"]
