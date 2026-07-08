"""R4.4 planner 受话判定 + 澄清解析单测（T1）。

覆盖：addressed=false 短路且不重试、fail-open（缺省/垃圾值=True）、clarify 解析（合法/坏格式）、
clarify 与非空 steps 互斥（steps 优先）、clarify 命中 route_hints 被兜底填 steps（D6-2）、
_planner_system() 按 CLARIFY_ENABLED 拼澄清段。全部进程内 stub，不依赖 gRPC/真 LLM。
"""
from __future__ import annotations
import asyncio
import json
import os

from unittest.mock import MagicMock

from orchestrator.cloud.planning import PlanBuilder, _planner_system
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.context import WorkingSet
from agents._sdk.manifest import load_manifest

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")
_ROUTE_HINT_MANIFESTS = {"deep-research": "agents/deep_research/manifest.yaml"}


def _load_route_hints(agent_id):
    rel = _ROUTE_HINT_MANIFESTS.get(agent_id)
    if not rel:
        return []
    return list(load_manifest(os.path.join(_REPO_ROOT, rel)).route_hints)


class MockAgent:
    def __init__(self, agent_id, intents):
        self.manifest = MagicMock()
        self.manifest.agent_id = agent_id
        self.manifest.capabilities = []
        self.manifest.latency_budget_ms = 5000
        self.manifest.kind = "agent"
        self.manifest.deployment = "cloud"
        self.manifest.requires_permissions = []
        self.manifest.trust_level = "first_party"
        for intent in intents:
            cap = MagicMock()
            cap.intent = intent
            cap.slots = []
            cap.description = ""
            cap.examples = []
            cap.heavy = False
            self.manifest.capabilities.append(cap)
        self.manifest.route_hints = _load_route_hints(agent_id)
        self.endpoint = f"localhost:{hash(agent_id) % 1000 + 50060}"


def _builder(llm_returns, resolve_returns=None):
    """llm_returns: str | list[str]（多次调用按序返回，用尽后重复末项）。"""
    seq = [llm_returns] if isinstance(llm_returns, str) else list(llm_returns)
    calls = {"n": 0}

    async def mock_llm(messages):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    async def mock_resolve(query, top_k=1):
        return resolve_returns or []

    return PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve), calls


def _build(builder, text, agents):
    return asyncio.run(builder.build(text, WorkingSet(catalog=agents), PlanContext(session_id="t")))


# ── _parse_clarify 纯函数 ────────────────────────────────────────────────────

def test_parse_clarify_valid():
    out = PlanBuilder._parse_clarify({
        "question": "您是想找附近的，还是导航过去？",
        "options": [{"label": "找附近的", "send_text": "帮我找附近的川菜馆"},
                    {"label": "导航过去", "send_text": "导航去最近的川菜馆"}]})
    assert out["question"].startswith("您是想")
    assert len(out["options"]) == 2
    assert out["options"][0]["send_text"] == "帮我找附近的川菜馆"


def test_parse_clarify_rejects_bad():
    # 非 dict
    assert PlanBuilder._parse_clarify("nope") is None
    # 缺 question
    assert PlanBuilder._parse_clarify({"options": [{"label": "a", "send_text": "x"},
                                                   {"label": "b", "send_text": "y"}]}) is None
    # options 只 1 个（消歧至少 2 选）
    assert PlanBuilder._parse_clarify({"question": "?", "options": [{"label": "a", "send_text": "x"}]}) is None
    # 某项缺 send_text
    assert PlanBuilder._parse_clarify({"question": "?", "options": [
        {"label": "a", "send_text": "x"}, {"label": "b"}]}) is None


def test_parse_clarify_truncates_to_three():
    out = PlanBuilder._parse_clarify({"question": "?", "options": [
        {"label": str(i), "send_text": str(i)} for i in range(5)]})
    assert len(out["options"]) == 3


# ── _parse_and_validate 短路 ─────────────────────────────────────────────────

def test_addressed_false_short_circuits():
    b, _ = _builder("x")
    plan = b._parse_and_validate('{"addressed":false,"steps":[]}', {}, "妈你到哪了")
    assert plan is not None
    assert plan.addressed is False
    assert plan.steps == []


def test_addressed_missing_is_fail_open_true():
    b, _ = _builder("x")
    agents = [MockAgent("nearby", ["nearby.search"])]
    amap = {a.manifest.agent_id: a for a in agents}
    plan = b._parse_and_validate(
        '{"steps":[{"id":"s1","agent_id":"nearby","intent":"nearby.search","slots":{}}]}',
        amap, "找川菜")
    assert plan.addressed is True


def test_addressed_garbage_is_fail_open_true():
    b, _ = _builder("x")
    # addressed 是垃圾字符串（非 bool false）→ 视作 True，且无 steps → None（走重试/fallback）
    plan = b._parse_and_validate('{"addressed":"maybe","steps":[]}', {}, "嗯")
    assert plan is None      # 非 false + 无 steps + 无 clarify → 解析失败语义（现状）


def test_clarify_without_steps_parsed():
    b, _ = _builder("x")
    raw = json.dumps({"addressed": True, "clarify": {
        "question": "找附近还是导航？",
        "options": [{"label": "找附近", "send_text": "找附近川菜"},
                    {"label": "导航", "send_text": "导航去川菜馆"}]}})
    plan = b._parse_and_validate(raw, {}, "华润大厦")
    assert plan is not None
    assert plan.steps == []
    assert plan.clarify and plan.clarify["question"].startswith("找附近")


def test_clarify_ignored_when_steps_present():
    b, _ = _builder("x")
    agents = [MockAgent("nearby", ["nearby.search"])]
    amap = {a.manifest.agent_id: a for a in agents}
    raw = json.dumps({
        "steps": [{"id": "s1", "agent_id": "nearby", "intent": "nearby.search", "slots": {}}],
        "clarify": {"question": "?", "options": [{"label": "a", "send_text": "x"},
                                                 {"label": "b", "send_text": "y"}]}})
    plan = b._parse_and_validate(raw, amap, "找川菜")
    assert len(plan.steps) == 1
    assert plan.clarify is None      # 互斥：steps 非空则 clarify 忽略


# ── build() 集成：不重试 / route_hints 优先 ──────────────────────────────────

def test_build_addressed_false_no_retry():
    agents = [MockAgent("nearby", ["nearby.search"])]
    b, calls = _builder('{"addressed":false,"steps":[]}')
    plan = _build(b, "他昨天跟我说那个项目黄了", agents)
    assert plan.addressed is False
    assert plan.steps == []
    assert calls["n"] == 1           # 合法空计划即刻放行，不触发第二次 LLM


def test_build_clarify_no_retry():
    agents = [MockAgent("nearby", ["nearby.search"])]
    raw = json.dumps({"addressed": True, "clarify": {
        "question": "找附近还是导航？",
        "options": [{"label": "找附近", "send_text": "找附近川菜"},
                    {"label": "导航", "send_text": "导航去川菜馆"}]}})
    b, calls = _builder(raw)
    plan = _build(b, "华润大厦", agents)
    assert plan.clarify is not None
    assert plan.steps == []
    assert calls["n"] == 1


def test_build_clarify_overridden_by_route_hint():
    """clarify 计划被 route_hints 命中 → steps 被兜底填充，clarify 让位（母卡 D6-2）。"""
    agents = [MockAgent("deep-research", ["research.run"])]
    raw = json.dumps({"addressed": True, "clarify": {
        "question": "要调研什么？",
        "options": [{"label": "电池", "send_text": "调研电池"},
                    {"label": "别的", "send_text": "调研别的"}]}})
    b, _ = _builder(raw)
    plan = _build(b, "帮我深入调研一下固态电池", agents)
    assert [s.intent for s in plan.steps] == ["research.run"]   # route_hint 兜底填 steps


# ── _fallback 语义 top-1 分数门槛（P1 T5）────────────────────────────────────

def _resolved(agent_id, intent, score):
    """构造带 .score 的 registry ResolvedAgent（fake）。"""
    m = MagicMock()
    m.agent_id = agent_id
    cap = MagicMock(); cap.intent = intent
    m.capabilities = [cap]
    m.kind = "agent"; m.deployment = "cloud"; m.requires_permissions = []; m.trust_level = "first_party"
    a = MagicMock(); a.manifest = m; a.endpoint = "stub:50099"; a.score = score
    return a


def test_fallback_low_score_honest_degrades():
    """LLM 两次失败 + 语义 top-1 分数 < 门槛 → 空计划（不硬执行 capabilities[0]）。"""
    agents = [MockAgent("nearby", ["nearby.search"])]        # 无 chitchat 兜底 → 走语义 top-1
    b, calls = _builder(["nope", "still nope"], resolve_returns=[_resolved("info", "info.weather", 0.4)])
    plan = _build(b, "含混不清的一句", agents)
    assert plan.steps == []          # 低分诚实降级
    assert calls["n"] == 2           # LLM 两次都失败才走 fallback


def test_fallback_high_score_executes_top1():
    """语义 top-1 分数 ≥ 门槛 → 现状：执行该 Agent capabilities[0]。"""
    agents = [MockAgent("nearby", ["nearby.search"])]
    b, _ = _builder(["nope", "still nope"], resolve_returns=[_resolved("info", "info.weather", 0.7)])
    plan = _build(b, "含混不清的一句", agents)
    assert [s.intent for s in plan.steps] == ["info.weather"]


# ── _planner_system() env 拼接 ───────────────────────────────────────────────

def test_planner_system_addressed_always_present(monkeypatch):
    monkeypatch.delenv("CLARIFY_ENABLED", raising=False)
    sys_text = _planner_system()
    assert "受话判定" in sys_text and "addressed" in sys_text


def test_planner_system_clarify_gated_by_env(monkeypatch):
    monkeypatch.setenv("CLARIFY_ENABLED", "off")
    assert "路由歧义澄清" not in _planner_system()
    monkeypatch.setenv("CLARIFY_ENABLED", "on")
    assert "路由歧义澄清" in _planner_system()
