"""常驻 policy 不得把最相关的 guide 挤出预算。

`render_skills_block` 先无条件铺 policy、再按检索相关度序塞 guide，两者**共用一个
`SKILL_BUDGET`**。于是「新增一条 policy」这个看起来纯加法的动作，会静默把当轮最相关的
guide 记成 `!clipped`——名单是诚实的，但没人会去看名单，直到某条 badcase 复发。

2026-08-02 实测命中：negation-and-deferral 落库后 policies 合计 1047 + 块头 14 +
navigation-with-stop 1428 = 2489 > 2400，`ki.navigation-with-stop.hit` 当场红。

本测试把那次的算术钉成红线：**常驻总量 + 最大的那条 guide 必须放得进预算**。
它守的不是某个具体数字，是「加 policy 要连带看 guide 的头寸」这条纪律。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud import skills


def _docs():
    return skills.SkillStore().load(force=True)


def _governed_capability_refs(docs):
    """Build opaque request refs for every pair used by structured few-shots."""
    pairs = set()
    for doc in docs:
        for shot in doc.few_shots:
            plan = shot.get("plan")
            if isinstance(plan, str):
                plan = json.loads(plan)
            assert isinstance(plan, dict) and isinstance(plan.get("steps"), list), doc.name
            for step in plan["steps"]:
                pair = (
                    str(step.get("agent_id") or step.get("agent") or "").strip(),
                    str(step.get("intent") or "").strip(),
                )
                assert all(pair), (doc.name, step)
                pairs.add(pair)
    refs = {pair: f"cap_{index:04d}"
            for index, pair in enumerate(sorted(pairs), 1)}
    for doc in docs:
        if doc.few_shots:
            rendered = skills._render_doc(doc, refs)
            assert rendered != doc.knowledge and "cap_" in rendered, (
                f"{doc.name} structured few-shots did not render through opaque refs")
    return refs


def test_residents_plus_largest_guide_fit_in_budget():
    docs = _docs()
    refs = _governed_capability_refs(docs)
    policies = [d for d in docs if d.type == "policy"]
    guides = [d for d in docs if d.type == "guide"]
    assert policies and guides, "skills/ 里没读到 policy/guide，先看加载器"

    largest = max(guides, key=lambda d: len(skills._render_doc(d, refs)))
    block, injected, clipped = skills.render_skills_block(
        policies, [largest], budget=10**9, capability_refs=refs)
    need = len(block)
    assert need <= skills.SKILL_BUDGET, (
        f"动态渲染后的常驻 policy + 最大 guide {largest.name} = {need} "
        f"> SKILL_BUDGET "
        f"{skills.SKILL_BUDGET}——它会被静默裁成 !clipped。"
        f"要么把 policy 写短，要么连带调预算，不要只加不看。")
    assert [d.name for d in injected] == [largest.name] and clipped == []


def test_render_actually_injects_the_first_guide():
    """算术之外再验一次真渲染——预算是渲染器花的，不是算出来的。"""
    docs = _docs()
    refs = _governed_capability_refs(docs)
    policies = [d for d in docs if d.type == "policy"]
    guides = sorted((d for d in docs if d.type == "guide"),
                    key=lambda d: len(skills._render_doc(d, refs)), reverse=True)
    block, injected, clipped = skills.render_skills_block(
        policies, guides[:1], capability_refs=refs)
    assert len(block) <= skills.SKILL_BUDGET
    assert [d.name for d in injected] == [guides[0].name], (
        f"最大的 guide {guides[0].name} 没能注入；clipped={[d.name for d in clipped]}")


def test_navigation_guide_survives_real_three_guide_mix():
    """真实检索候选混合里，最相关的 navigation 必须先放得进预算。

    2026-08-04 否定 policy 加 mixed few-shot 后净增 48 字，恰好让两条 stable
    navigation knowledge-injection 契约 3/3 变红。只守“最大一条能进”看不见第二条。
    """
    docs = _docs()
    refs = _governed_capability_refs(docs)
    policies = [d for d in docs if d.type == "policy"]
    by_name = {d.name: d for d in docs if d.type == "guide"}
    _block, injected, clipped = skills.render_skills_block(
        policies,
        [by_name["navigation-with-stop"], by_name["charging-strategy"],
         by_name["weather-outing"]],
        capability_refs=refs,
    )
    assert "navigation-with-stop" in [d.name for d in injected]
    assert "navigation-with-stop" not in [d.name for d in clipped]
