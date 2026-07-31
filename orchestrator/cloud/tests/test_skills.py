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

from orchestrator.cloud import embedding as _embedding
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
            "charging-strategy", "freshness-and-depth",
            "implicit-vehicle-control"} <= names
    assert len(store.guides()) >= 4 and len(store.policies()) >= 2
    for d in docs:
        assert d.type in ("guide", "policy", "workflow")
        assert d.description and d.knowledge
        assert d.body.startswith(d.knowledge)   # body=注入文本（knowledge+few_shots 渲染）


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


def test_conditional_reminder_guide_covers_parallel_unconditional_boundary():
    """A1-3 真栈：『八点提醒我…再看天气』是并列，不是『下雨就提醒』。
    同一 guide 必须把边界和双 intent 金标一起交给 planner。"""
    store = sk.SkillStore()
    guide = next(d for d in store.guides() if d.name == "conditional-reminder")
    parallel = next(
        g for g in guide.golden
        if g.get("text") == "明天早上八点提醒我带伞，再看下明天深圳会不会下雨"
    )

    assert "明确时间" in guide.knowledge
    assert parallel["expect_intents"] == [
        "reminder.create",
        "info.weather|info.forecast",
    ]
    assert "expect_not" not in parallel


# ── 渲染 ──────────────────────────────────────────────────────────────────────

def test_render_block_has_policies_and_guides_within_budget():
    store = sk.SkillStore()
    guides = sk.top_guides("周末去杭州玩两天带老人", store.guides(), k=3)
    block, injected, clipped = sk.render_skills_block(store.policies(), guides)
    assert "时效判据" in block                      # policy 常驻
    assert "多日出行必出行程规划" in block          # 命中 guide 的 knowledge
    assert len(block) <= sk.SKILL_BUDGET + 200      # 预算约束（含区头小富余）
    assert [d.name for d in injected] and not clipped


def test_few_shots_field_renders_into_block(tmp_path):
    """few_shots 实装（2026-07-26 验收立卡关卡）：照 README 写的 few_shots 必须进注入块
    ——此前是「文档有、代码不读」的空契约，作者以为示例生效了，实际被静默丢弃。"""
    gdir = tmp_path / "guides"
    gdir.mkdir()
    (gdir / "demo-shots.yaml").write_text(
        "name: demo-shots\ntype: guide\ndescription: few_shots 渲染契约样例\n"
        "keywords: [示例词]\nknowledge: |\n  规则正文。\n"
        "few_shots:\n"
        "  - user: 来个示例词\n"
        "    plan: {\"steps\":[{\"id\":\"s1\",\"agent_id\":\"demo\",\"intent\":\"demo.run\"}]}\n"
        "owner: orchestrator\nversion: 1\n", encoding="utf-8")
    store = sk.SkillStore(root=str(tmp_path))
    doc = store.guides()[0]
    assert doc.few_shots and "示例——用户：『来个示例词』" in doc.body
    assert '"intent":"demo.run"' in doc.body        # plan dict → 紧凑 JSON（输出形态示范）
    block, injected, _ = sk.render_skills_block([], [doc])
    assert "示例——用户：『来个示例词』" in block and injected == [doc]


def test_unknown_top_level_key_warns_not_silent(tmp_path, caplog):
    """未知顶层键（few_shot/keyword 等拼写错误）必须告警——静默忽略会让作者以为知识生效。"""
    import logging
    gdir = tmp_path / "guides"
    gdir.mkdir()
    (gdir / "typo.yaml").write_text(
        "name: typo\ntype: guide\ndescription: 拼写错误告警样例\n"
        "knowledge: |\n  正文。\nfew_shot:\n  - user: x\n    plan: y\n"
        "owner: o\nversion: 1\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="cloud.skills"):
        docs = sk.SkillStore(root=str(tmp_path)).load()
    assert len(docs) == 1                            # fail-open：仍加载
    assert any("未知顶层字段" in r.message and "few_shot" in r.message
               for r in caplog.records)


def test_clipped_guides_reported_honestly(tmp_path):
    """归因诚实（2026-07-26 修）：超预算被裁的 guide 绝不能出现在「已注入」名单里。"""
    gdir = tmp_path / "guides"
    gdir.mkdir()
    (gdir / "big.yaml").write_text(
        "name: big\ntype: guide\ndescription: 超长知识样例\npriority: 90\n"
        "keywords: [超长]\nknowledge: |\n  " + "长" * 300 + "\n"
        "owner: o\nversion: 1\n", encoding="utf-8")
    (gdir / "small.yaml").write_text(
        "name: small\ntype: guide\ndescription: 短知识样例\npriority: 10\n"
        "keywords: [超长]\nknowledge: |\n  短规则。\n"
        "owner: o\nversion: 1\n", encoding="utf-8")
    store = sk.SkillStore(root=str(tmp_path))
    guides = store.guides()
    block, injected, clipped = sk.render_skills_block([], guides, budget=120)
    assert [d.name for d in injected] == ["small"]   # big 超预算被裁、small 补上
    assert [d.name for d in clipped] == ["big"]
    assert "短规则" in block and "长长长" not in block


# ── 语义通道（hybrid）：fail-open 回词法 / 语义补位词法漏召 ────────────────────

def _mini_skill_dir(tmp_path):
    gdir = tmp_path / "guides"
    gdir.mkdir()
    (gdir / "charge.yaml").write_text(
        "name: charge\ntype: guide\ndescription: 长途补能策略的分流判据\n"
        "keywords: [充电]\nknowledge: |\n  补能规则。\nowner: o\nversion: 1\n",
        encoding="utf-8")
    (gdir / "fish.yaml").write_text(
        "name: fish\ntype: guide\ndescription: 钓鱼出行的组合规划知识\n"
        "keywords: [钓鱼]\nknowledge: |\n  钓鱼规则。\nowner: o\nversion: 1\n",
        encoding="utf-8")
    return sk.SkillStore(root=str(tmp_path))


def test_hybrid_semantic_supplements_lexical_miss(monkeypatch, tmp_path):
    """paraphrase（keywords 盲区）：词法漏召、语义按 description 余弦补位，通道记 @vec。"""
    monkeypatch.setenv("SKILLS_RETRIEVAL", "hybrid")
    _embedding.reset_cooldown()      # 冷却已随 M5 P1 移到共享出口 embedding.py
    store = _mini_skill_dir(tmp_path)
    query = "去惠州中间要不要补个电"                 # 无「充电/钓鱼」字面 → 词法双漏

    async def fake_embed(texts):
        table = {query: (1.0, 0.0),
                 "长途补能策略的分流判据": (0.9, 0.1),
                 "钓鱼出行的组合规划知识": (0.0, 1.0)}
        return [table[t] for t in texts], "fake-embed"

    monkeypatch.setattr(sk, "_embed_texts", fake_embed)
    assert sk.top_guides(query, store.guides()) == []          # 词法确实漏
    pairs = asyncio.run(sk.retrieve_guides(query, store))
    assert [(d.name, ch) for d, ch, _ in pairs] == [("charge", "vec")]
    assert pairs[0][2] > 0.9                                   # 归因带余弦分（取证用）


def test_hybrid_fails_open_to_lexical(monkeypatch, tmp_path):
    """embedding 不可用（超时/无源）→ 该轮纯词法，绝不堵规划。"""
    monkeypatch.setenv("SKILLS_RETRIEVAL", "hybrid")
    _embedding.reset_cooldown()      # 冷却已随 M5 P1 移到共享出口 embedding.py
    store = _mini_skill_dir(tmp_path)

    async def dead_embed(texts):
        return None

    monkeypatch.setattr(sk, "_embed_texts", dead_embed)
    pairs = asyncio.run(sk.retrieve_guides("附近找个充电桩", store))
    assert [(d.name, ch) for d, ch, _ in pairs] == [("charge", "lex")]  # 词法命中原样保留


# ── 运行时容错（2026-07-27 评审缺口 1）：坏文件绝不崩规划 ──────────────────────

def test_toplevel_list_and_bad_priority_do_not_crash(tmp_path):
    """评审复现的两个真崩溃：顶层数组 AttributeError / priority 非数字 ValueError——
    都发生在 fail-open try 之外，一个坏文件就打穿「坏文件跳过不崩规划」承诺。"""
    gdir = tmp_path / "guides"
    gdir.mkdir()
    (gdir / "bad-list.yaml").write_text("- a\n- b\n", encoding="utf-8")
    (gdir / "bad-pri.yaml").write_text(
        "name: bad-pri\ntype: guide\ndescription: d\npriority: high\n"
        "keywords: [x]\nknowledge: |\n  k\n", encoding="utf-8")
    docs = sk.SkillStore(root=str(tmp_path)).load()      # 不得 raise
    by_name = {d.name: d for d in docs}
    assert "bad-list" not in str(by_name)                # 结构性坏文件跳过
    assert by_name["bad-pri"].priority == 50             # 非法 priority 宽容回默认（知识保活）


def test_duplicate_names_first_wins(tmp_path, caplog):
    import logging
    gdir = tmp_path / "guides"
    gdir.mkdir()
    body = "name: dup\ntype: guide\ndescription: d{i}\nkeywords: [关键词]\nknowledge: |\n  k{i}\n"
    (gdir / "a-dup.yaml").write_text(body.format(i=1), encoding="utf-8")
    (gdir / "b-dup.yaml").write_text(body.format(i=2), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="cloud.skills"):
        docs = sk.SkillStore(root=str(tmp_path)).load()
    assert len(docs) == 1 and docs[0].description == "d1"   # glob 有序=先到者胜，确定性
    assert any("重名" in r.message for r in caplog.records)


def test_last_known_good_survives_broken_reload(tmp_path):
    """热更新改坏文件 → 沿用上一版好文档（LKG）；删除文件 → 正常下线（删除是意图）。"""
    gdir = tmp_path / "guides"
    gdir.mkdir()
    f = gdir / "lkg.yaml"
    f.write_text("name: lkg\ntype: guide\ndescription: 好版本\nkeywords: [钓鱼]\n"
                 "knowledge: |\n  好知识。\n", encoding="utf-8")
    store = sk.SkillStore(root=str(tmp_path))
    assert [d.name for d in store.load(force=True)] == ["lkg"]
    f.write_text("- 顶层写成了数组\n", encoding="utf-8")     # 改坏
    docs = store.load(force=True)
    assert [d.name for d in docs] == ["lkg"] and docs[0].knowledge == "好知识。"
    f.unlink()                                              # 删除=下线
    assert store.load(force=True) == []


def test_dir_type_mismatch_warns_but_honors_type(tmp_path, caplog):
    import logging
    gdir = tmp_path / "guides"
    gdir.mkdir()
    (gdir / "misplaced.yaml").write_text(
        "name: misplaced\ntype: policy\ndescription: 放错目录的 policy\n"
        "knowledge: |\n  正文。\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="cloud.skills"):
        store = sk.SkillStore(root=str(tmp_path))
        docs = store.load()
    assert docs[0].type == "policy" and store.policies()    # 语义按 type 生效
    assert any("应放" in r.message for r in caplog.records)  # 但告警要求归位


def test_env_range_clamps(monkeypatch):
    """越界 env 不崩但会**静默**改行为（阈值>1=语义全关/超时<0=必超时/min_score<0=
    词法全量放行）——钳制+告警（2026-07-27 评审三批）。"""
    monkeypatch.setenv("SKILL_SEM_THRESHOLD", "1.5")
    assert sk._sem_threshold() == 1.0
    monkeypatch.setenv("SKILL_SEM_THRESHOLD", "-0.2")
    assert sk._sem_threshold() == 0.0
    monkeypatch.setenv("SKILL_EMBED_TIMEOUT", "-2")
    assert sk._embed_timeout() == 0.1
    monkeypatch.setenv("X_SKILL_TEST_INT", "-5")
    assert sk._env_int("X_SKILL_TEST_INT", 10, min_val=0) == 0
    monkeypatch.setenv("X_SKILL_TEST_INT", "oops")
    assert sk._env_int("X_SKILL_TEST_INT", 10, min_val=0) == 10
    monkeypatch.setenv("X_SKILL_TEST_INT", "")
    assert sk._env_int("X_SKILL_TEST_INT", 10) == 10        # compose ${VAR:-} 空串透传


# ── T2 再规划知识继承（2026-07-27 评审缺口 4） ────────────────────────────────

def test_render_for_names_reinjects_only_actually_injected(monkeypatch):
    monkeypatch.setattr(sk, "_default_store", sk.SkillStore())   # 真仓库 skills/
    names = ["full:freshness-and-depth",                 # policy：应注入
             "full:multi-day-trip@lex!clipped",          # 初规划被裁：不注入
             "shadow:navigation-with-stop@vec"]          # shadow 轮：从未注入
    block = sk.render_for_names(names)
    assert "时效判据" in block                            # freshness 的 knowledge
    assert "多日出行必出行程规划" not in block
    assert "单个" not in block                            # navigation 的 knowledge 不在
    assert sk.render_for_names([]) == "" and sk.render_for_names(None) == ""


def test_replan_inherits_skill_block(monkeypatch):
    """replan 的 user prompt 必须带初规划注入的知识块（conditional-reminder 类
    「看结果再决定」的决策恰好发生在再规划轮——那一轮失忆等于知识白教）。"""
    monkeypatch.setattr(sk, "_default_store", sk.SkillStore())
    seen = {}

    async def mock_llm(messages, **kw):
        seen["user"] = messages[-1]["content"]
        return '{"done": true}'

    async def mock_resolve(query, top_k):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    asyncio.run(builder.replan(
        "查天气并视结果决定是否建提醒", [{"step": "s1", "ok": True}],
        [_mock_agent("info", ["info.weather"])], PlanContext(session_id="t"),
        skill_names=["full:conditional-reminder@lex", "full:freshness-and-depth"]))
    assert "条件依赖" in seen["user"]                     # guide knowledge 进了再规划
    assert "时效判据" in seen["user"]                     # policy 同样继承
    assert seen["user"].index("当前日期") < seen["user"].index("条件依赖")  # 顺序契约


def test_plan_skills_names_carry_channel_and_clip_markers(monkeypatch, tmp_path):
    """plan.skills 名单契约：guide 记 mode:name@通道、被裁加 !clipped、policy 记 mode:name。"""
    monkeypatch.setenv("SKILLS_MODE", "full")
    monkeypatch.setenv("SKILLS_RETRIEVAL", "lexical")
    monkeypatch.setenv("SKILLS_DIR", "")               # 防宿主 env 泄漏
    gdir = tmp_path / "guides"
    pdir = tmp_path / "policies"
    gdir.mkdir()
    pdir.mkdir()
    (gdir / "big.yaml").write_text(
        "name: big\ntype: guide\ndescription: 超长知识\npriority: 90\n"
        "keywords: [充电]\nknowledge: |\n  " + "长" * 3000 + "\nowner: o\nversion: 1\n",
        encoding="utf-8")
    (gdir / "small.yaml").write_text(
        "name: small\ntype: guide\ndescription: 短知识\npriority: 10\n"
        "keywords: [充电]\nknowledge: |\n  短规则。\nowner: o\nversion: 1\n",
        encoding="utf-8")
    (pdir / "pol.yaml").write_text(
        "name: pol\ntype: policy\ndescription: 常驻策略\n"
        "knowledge: |\n  策略正文。\nowner: o\nversion: 1\n", encoding="utf-8")
    monkeypatch.setattr(sk, "_default_store", sk.SkillStore(root=str(tmp_path)))
    mode, names, block = asyncio.run(sk.plan_skills("附近找个充电桩"))
    assert mode == "full"
    # 归因带检索分（评审四批：@lex:整数 / @vec:两位小数——边缘共召回的取证靠它）
    assert any(n.startswith("full:small@lex:") and not n.endswith("!clipped")
               for n in names)
    assert any(n.startswith("full:big@lex:") and n.endswith("!clipped")
               for n in names)                         # 被裁诚实标注，不谎称已注入
    assert "full:pol" in names
    assert "短规则" in block and "策略正文" in block and "长长长" not in block


def test_render_preserves_caller_relevance_order():
    """渲染/裁剪按调用方传入的检索相关度序（评审四批）：高 priority 但弱相关的 guide
    不得排到强相关 guide 前面——priority 只在检索同分时定序。"""
    strong = sk.SkillDoc(name="strong", type="guide", description="d1",
                         knowledge="强相关知识", body="强相关知识", priority=10)
    weak = sk.SkillDoc(name="weak", type="guide", description="d2",
                       knowledge="弱相关知识", body="弱相关知识", priority=90)
    block, injected, _ = sk.render_skills_block([], [strong, weak])
    assert block.index("强相关知识") < block.index("弱相关知识")
    assert [d.name for d in injected] == ["strong", "weak"]


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
    """shadow=研究档：记录检索名单但不注入。Full Migration 后 base 唯一且不含领域知识。"""
    plan, seen = _run_build(monkeypatch, "shadow")
    assert any(s.startswith("shadow:") for s in plan.skills)          # 记录检索名单
    assert "== 规划知识" not in seen["user"]                          # 不注入
    assert "多日出行必出行程规划" not in seen["system"]                # 单 base，无领域知识


def test_mode_canary_injects_without_duplication(monkeypatch):
    plan, seen = _run_build(monkeypatch, "canary")
    assert any(s.startswith("canary:") for s in plan.skills)
    assert "== 规划知识" in seen["user"]                              # 注入块
    assert "多日出行必出行程规划" in seen["user"]                      # guide 进了 user msg
    assert "多日出行必出行程规划" not in seen["system"]                # 知识不双份（单 base）
    assert "时效判据" in seen["user"]                                 # policy 常驻
    assert "当前日期" in seen["user"].split("== 规划知识")[0]          # date 锚在 skills 块之前


def test_mode_off_is_debug_no_injection(monkeypatch):
    """off=debug 档：注入关。Full Migration 后此档缺领域知识，仅排障用。"""
    plan, seen = _run_build(monkeypatch, "off")
    assert plan.skills == []
    assert "== 规划知识" not in seen["user"]
    assert "多日出行必出行程规划" not in seen["system"]


def test_default_mode_is_full_injection(monkeypatch):
    """缺省（未设 SKILLS_MODE）= full：注入生效——Full Migration 后的默认交付形态。"""
    monkeypatch.delenv("SKILLS_MODE", raising=False)
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
    plan = asyncio.run(builder.build("周末去杭州玩两天带老人", WorkingSet(catalog=agents),
                                     PlanContext(session_id="t")))
    assert any(s.startswith("full:") for s in plan.skills)
    assert "== 规划知识" in seen["user"] and "多日出行必出行程规划" in seen["user"]


def test_migrated_domain_knowledge_never_returns_to_central_base():
    """Full Migration 契约的另一半（验收补口）：「零领域字面量」此前没有任何源码级
    护栏——skills 里的领域知识若被人顺手抄回 `_PLANNER_BASE`，行为测试全绿、
    双份知识静默漂移。动态取每个 skill 文件 knowledge 的实句做指纹查中央：
    新 skill 落库即自动纳入保护，不维护硬编码黑名单（那挡不住增量）。
    """
    import pathlib
    import yaml
    from orchestrator.cloud import planning as planning_mod
    import inspect

    central = inspect.getsource(planning_mod)
    root = pathlib.Path(__file__).resolve().parents[3] / "skills"
    checked = 0
    for f in sorted(root.glob("*/*.yaml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for line in (doc.get("knowledge") or "").splitlines():
            line = line.strip().lstrip("-•").strip()
            if len(line) < 12:            # 短句/空行没有指纹价值
                continue
            assert line not in central, (
                f"skill「{f.name}」的知识句回潮进了 planning.py：{line[:40]}…——"
                "加规划知识只投 skill 文件，中央不得倒灌（M0b Full Migration 契约）")
            checked += 1
            break                          # 每文件取第一条实句即可（存在性指纹）
    assert checked >= 5, f"指纹句仅 {checked} 条——skills 目录结构变了请更新本测试"
