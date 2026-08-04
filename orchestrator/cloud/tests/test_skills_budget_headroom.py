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

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud import skills


def _docs():
    return skills.SkillStore().load(force=True)


def test_residents_plus_largest_guide_fit_in_budget():
    docs = _docs()
    policies = [d for d in docs if d.type == "policy"]
    guides = [d for d in docs if d.type == "guide"]
    assert policies and guides, "skills/ 里没读到 policy/guide，先看加载器"

    resident = sum(len(d.body) for d in policies)
    header = len("== 规划知识（按需注入）==")
    largest = max(guides, key=lambda d: len(d.body))

    need = resident + header + len(largest.body)
    assert need <= skills.SKILL_BUDGET, (
        f"常驻 policy {resident} 字 + 块头 {header} + 最大 guide "
        f"{largest.name} {len(largest.body)} 字 = {need} > SKILL_BUDGET "
        f"{skills.SKILL_BUDGET}——它会被静默裁成 !clipped。"
        f"要么把 policy 写短，要么连带调预算，不要只加不看。")


def test_render_actually_injects_the_first_guide():
    """算术之外再验一次真渲染——预算是渲染器花的，不是算出来的。"""
    docs = _docs()
    policies = [d for d in docs if d.type == "policy"]
    guides = sorted((d for d in docs if d.type == "guide"),
                    key=lambda d: len(d.body), reverse=True)
    _block, injected, clipped = skills.render_skills_block(policies, guides[:1])
    assert [d.name for d in injected] == [guides[0].name], (
        f"最大的 guide {guides[0].name} 没能注入；clipped={[d.name for d in clipped]}")


def test_navigation_guide_survives_real_three_guide_mix():
    """真实检索候选混合里，最相关的 navigation 必须先放得进预算。

    2026-08-04 否定 policy 加 mixed few-shot 后净增 48 字，恰好让两条 stable
    navigation knowledge-injection 契约 3/3 变红。只守“最大一条能进”看不见第二条。
    """
    docs = _docs()
    policies = [d for d in docs if d.type == "policy"]
    by_name = {d.name: d for d in docs if d.type == "guide"}
    _block, injected, clipped = skills.render_skills_block(
        policies,
        [by_name["navigation-with-stop"], by_name["charging-strategy"],
         by_name["weather-outing"]],
    )
    assert "navigation-with-stop" in [d.name for d in injected]
    assert "navigation-with-stop" not in [d.name for d in clipped]
