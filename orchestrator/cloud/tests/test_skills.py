"""M0b Skill 层测试：加载/词法检索/渲染/即插即用契约 + planning 四态注入。

契约（skills/README.md + 设计稿 §4.A）：
- guide 预筛注入（top-N + 预算）、policy 常驻注入；
- SKILLS_MODE=off|shadow|canary|full：shadow 只检索记录不改 prompt（零行为变化）；
  canary/full 用瘦身 base + 注入块；
- 「加规划知识 = 只投一个 skill 文件」——不改任何中央代码。
"""
import asyncio
import os
from unittest.mock import MagicMock

import pytest

from orchestrator.cloud import skills as sk
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.planning import PlanBuilder


# ── 加载 ──────────────────────────────────────────────────────────────────────

def test_store_loads_repo_skills():
    store = sk.SkillStore()
    docs = store.load()
    names = {d.name for d in docs}
    assert {"multi-day-trip", "navigation-with-stop", "conditional-reminder",
            "freshness-and-depth", "implicit-vehicle-control"} <= names
    assert len(store.guides()) >= 3 and len(store.policies()) >= 2
    for d in docs:
        assert d.type in ("guide", "policy", "workflow")
        assert d.description and d.knowledge


# ── 词法检索（零网络、确定性；embedding 升级由 shadow 召回数据决定） ─────────────

@pytest.mark.parametrize("text,expect", [
    ("周末去杭州玩两天带老人不要太累", "multi-day-trip"),
    ("下周三日游去成都带爸妈", "multi-day-trip"),
    ("导航去东方之门，附近找个吃饭的地方", "navigation-with-stop"),
    ("查下明天会不会下雨，要是下雨就提醒我带伞", "conditional-reminder"),
])
def test_retrieval_hits_expected_guide(text, expect):
    store = sk.SkillStore()
    top = sk.top_guides(text, store.guides(), k=3)
    assert expect in [d.name for d in top], f"{text} 未命中 {expect}"


def test_retrieval_stays_quiet_on_plain_queries():
    """普通单域句不应召回 guide（阈值挡噪声）。"""
    store = sk.SkillStore()
    assert sk.top_guides("今天天气怎么样", store.guides(), k=3) == []
    assert sk.top_guides("把空调调到24度", store.guides(), k=3) == []


# ── 渲染 ──────────────────────────────────────────────────────────────────────

def test_render_block_has_policies_and_guides_within_budget():
    store = sk.SkillStore()
    guides = sk.top_guides("周末去杭州玩两天带老人", store.guides(), k=3)
    block = sk.render_skills_block(store.policies(), guides)
    assert "时效判据" in block                      # policy 常驻
    assert "多日出行必出行程规划" in block          # 命中 guide 的 knowledge
    assert len(block) <= sk.SKILL_BUDGET + 200      # 预算约束（含区头小富余）


# ── 即插即用契约：加规划知识=只投一个文件 ─────────────────────────────────────

def test_new_skill_file_is_plug_and_play(tmp_path):
    gdir = tmp_path / "guides"
    gdir.mkdir()
    (gdir / "fishing-trip.yaml").write_text(
        "name: fishing-trip\ntype: guide\ndescription: 钓鱼出行的组合规划知识\n"
        "priority: 50\nkeywords: [钓鱼, 鱼竿]\n"
        "knowledge: |\n  钓鱼出行要同时考虑天气窗口与装备提醒。\n"
        "owner: orchestrator\nversion: 1\n", encoding="utf-8")
    store = sk.SkillStore(root=str(tmp_path))
    docs = store.load()
    assert [d.name for d in docs] == ["fishing-trip"]
    top = sk.top_guides("周末想去钓鱼，帮我看看", store.guides(), k=3)
    assert [d.name for d in top] == ["fishing-trip"]


# ── planning 四态注入 ─────────────────────────────────────────────────────────

def _mock_agent(agent_id, intents):
    a = MagicMock()
    a.manifest.agent_id = agent_id
    a.manifest.latency_budget_ms = 5000
    a.manifest.kind = "agent"
    a.manifest.deployment = "cloud"
    a.manifest.requires_permissions = []
    a.manifest.trust_level = "first_party"
    a.manifest.route_hints = []
    a.manifest.context_scopes = []
    caps = []
    for it in intents:
        c = MagicMock()
        c.intent = it
        c.description = it
        c.slots = []
        c.examples = []
        c.heavy = False
        c.require_confirm = False
        caps.append(c)
    a.manifest.capabilities = caps
    a.endpoint = "stub:1"
    return a


def _run_build(monkeypatch, mode, text="周末去杭州玩两天带老人"):
    monkeypatch.setenv("SKILLS_MODE", mode)
    seen = {}

    async def mock_llm(messages, **kw):
        seen["system"] = messages[0]["content"]
        seen["user"] = messages[-1]["content"]
        return ('{"complexity":"simple","goal":"g","steps":[{"id":"s1",'
                '"agent_id":"trip-planner","intent":"trip.plan","slots":{},'
                '"depends_on":[],"slot_refs":{}}]}')

    async def mock_resolve(query, top_k):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    agents = [_mock_agent("trip-planner", ["trip.plan"])]
    plan = asyncio.run(builder.build(text, WorkingSet(catalog=agents),
                                     PlanContext(session_id="t")))
    return plan, seen


def test_mode_shadow_records_but_does_not_inject(monkeypatch):
    plan, seen = _run_build(monkeypatch, "shadow")
    assert any(s.startswith("shadow:") for s in plan.skills)          # 记录检索名单
    assert "多日出行必出行程规划" in seen["system"]                    # 完整 base 未瘦身
    assert "== 规划知识" not in seen["user"]                          # 不注入


def test_mode_canary_injects_and_slims_base(monkeypatch):
    plan, seen = _run_build(monkeypatch, "canary")
    assert any(s.startswith("canary:") for s in plan.skills)
    assert "== 规划知识" in seen["user"]                              # 注入块
    assert "多日出行必出行程规划" in seen["user"]                      # guide 进了 user msg
    assert "多日出行必出行程规划" not in seen["system"]                # base 已瘦身（知识不双份）
    assert "时效判据" in seen["user"]                                 # policy 常驻
    assert "当前日期" in seen["user"].split("== 规划知识")[0]          # date 锚在 skills 块之前


def test_mode_off_is_legacy_behavior(monkeypatch):
    plan, seen = _run_build(monkeypatch, "off")
    assert plan.skills == []
    assert "== 规划知识" not in seen["user"]
    assert "多日出行必出行程规划" in seen["system"]
