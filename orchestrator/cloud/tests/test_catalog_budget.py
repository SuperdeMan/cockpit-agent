"""D1 契约测试（数据飞轮 P0）：catalog 预算裁剪对**真实 manifests** 的实际行为。

固化三个事实（docs/design/2026-07-28-intent-accuracy-data-flywheel.md §3-D1）：
1. 旧默认 8000 字符下，满栈（14 云 manifest + 2 端）渲染仍超预算——「正常情况下根本
   不触发裁剪」的旧假设已随 M3/M4 能力面增长失效。
2. **P0 时被裁的恰是全部「无 route_hints」的 agent，含核心域 navigation**——保护资格
   是「有没有声明 hint」这个巧合，与领域重要性无关。**M5 P2 已修**：保护判据补上
   `category: core`（见 `context.py::_always_include`），navigation / road-safety 自此
   不再被裁；本测试相应改断言「被裁集合 = 非 core 且无 hint 的 agent」，**并显式断言
   核心域不在其中**——那条才是当初真正想守的性质。
3. 当前默认 16000 下全量放得下、零裁剪。若能力面继续增长让本测试转红，说明预算
   又被追上——正确动作是启用 catalog 检索化预筛，不是回到静默丢域。
"""
from __future__ import annotations

import glob
import json
import os
import sys
from types import SimpleNamespace

# 端侧模块按服务内裸名导入（edge tests 同款惯例）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "edge"))

import orchestrator.cloud.context as ctxmod
from agents._sdk.manifest import load_manifest
from capabilities import build_edge_manifests
from orchestrator.cloud.context import WorkingSet

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
# 既非 core、又没声明 route_hints 的 agent——预算不够时只有它们该被裁
#
# 2026-07-30 `nearby` 加入本集合：它的两条 route_hints 已由数据退役（跨两档全覆盖双臂裸跑），
# 而它是 `category: ecosystem`（trust_level third_party，高德 POI）——**保护是随 hint 一起
# 没的**。这正是 P2 记过的那个副作用「hint 退役会顺手删掉那个 Agent 的 catalog 保护」，
# 本次是**预期发生**，故改断言而不是回避：
#   ① 现默认预算 16000 下全量零裁剪，生产无影响（下一个测试守着）；
#   ② **绝不为了拿保护而留一条规则**——那就是本期在清理的巧合耦合，方向反了；
#   ③ 也不把 nearby 改标 `core` 去骗保护：`ecosystem` 是它的诚实分类，为副作用改字段
#      等于把 category 变成第二个「有 hint 就保护」；
#   ④ 但要记一笔风险：若预算再被追上，被裁的将包含**周边发现这个高频功能**。
#      正确动作是启用 catalog 检索化预筛（RFC §5-P2-4），不是回填规则或改分类。
_UNPROTECTED = {"manual-rag", "parking-payment", "nearby"}
# 核心域：无论预算多紧都不许被裁（P0 时 navigation/road-safety 恰恰会被裁，那是 D1 根因）
_CORE_MUST_SURVIVE = {"navigation", "road-safety", "info", "reminder",
                      "scene-orchestrator", "vision", "charging-planner"}


def _full_stack_agents() -> list:
    agents = []
    for path in sorted(glob.glob(os.path.join(_ROOT, "agents", "*", "manifest.yaml"))):
        agents.append(SimpleNamespace(manifest=load_manifest(path), endpoint="x:1"))
    for manifest in build_edge_manifests():
        agents.append(SimpleNamespace(manifest=manifest, endpoint="edge:1"))
    assert len(agents) >= 16, f"满栈应为 14 云 + 2 端，实际 {len(agents)}"
    return agents


def test_tight_budget_only_drops_unprotected_and_never_core(monkeypatch):
    """预算再紧也只裁「非 core 且无 hint」的 agent；**核心域一个都不许掉**。

    这条断言是 M5 P2 的成果：同样的 8000 预算下，P0 时 navigation 与 road-safety
    会被整域裁出 prompt（planner 从此看不见它们、步骤校验还会拒它们的 intent），
    现在它们由 `category: core` 保住。"""
    monkeypatch.setattr(ctxmod, "_CATALOG_BUDGET", 8000)
    stats: dict = {}
    WorkingSet.render_catalog(_full_stack_agents(), stats)
    assert stats["chars_full"] > 8000
    dropped = set(stats["dropped"])
    assert dropped == _UNPROTECTED
    assert not (dropped & _CORE_MUST_SURVIVE), f"核心域被裁：{dropped & _CORE_MUST_SURVIVE}"


def test_current_default_budget_holds_full_stack(monkeypatch):
    monkeypatch.setattr(ctxmod, "_CATALOG_BUDGET", 16000)   # 显式=代码默认，免受宿主 env 影响
    stats: dict = {}
    WorkingSet.render_catalog(_full_stack_agents(), stats)
    assert stats["dropped"] == []
    assert stats["chars_full"] == stats["chars_final"]


def test_edge_capabilities_stay_name_only_in_catalog(monkeypatch):
    """端侧能力（2026-08-04 起 76 条）在 catalog 里**只出名字**——判别化描述有意不进 planner prompt。

    这条是 M5 P3 收尾的**负结果护栏**。`capabilities.py` 已经能机械生成逐条判别化
    描述了，把它们渲进 catalog 看起来天经地义；实测双臂差分（唯一变量=这一个渲染分支，
    25 条 canonical+口语语料 ×2 轮 ×2 provider）**Δ=0、100 次对照零翻面**，而代价是每次
    规划 +1462 字符。判别化描述真正的受益方是 registry 语义兜底（按 capability 粒度
    embed），不是 planner。

    所以本条守的不是「省字符」，是**不许在没有证据的情况下把成本加进每一次规划**。
    要推翻它，请附上跨 provider 的双臂数据，而不是「描述当然比没描述好」。
    """
    monkeypatch.setattr(ctxmod, "_CATALOG_BUDGET", 16000)
    stats: dict = {}
    rendered = json.loads(WorkingSet.render_catalog(_full_stack_agents(), stats))
    edge = [i for i in rendered if i["agent_id"].startswith("edge-")]
    assert len(edge) == 2
    caps = [c for item in edge for c in item["capabilities"]]
    assert len(caps) >= 70, "端侧能力面塌了？"     # 不钉死数字：新增车控意图是正常演进
    assert all(set(c) == {"intent"} for c in caps), "端侧能力多渲染了字段，请附 A/B 证据"
    assert stats["dropped"] == []
