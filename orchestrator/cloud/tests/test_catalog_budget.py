"""D1 契约测试（数据飞轮 P0）：catalog 预算裁剪对**真实 manifests** 的实际行为。

固化两个事实（docs/design/2026-07-28-intent-accuracy-data-flywheel.md §3-D1）：
1. 旧默认 8000 字符下，满栈（14 云 manifest + 2 端）渲染超预算，被裁的恰是全部
   「无 route_hints」的 agent——含核心域 navigation。保护判据（有无 hint）与领域
   重要性无关，被裁 agent 对 planner 完全不可见且步骤校验会拒它的 intent。
2. 当前默认 16000 下全量放得下、零裁剪。若能力面继续增长让本测试转红，说明预算
   又被追上——正确动作是启用 catalog 检索化预筛（P2），不是回到静默丢域。
"""
from __future__ import annotations

import glob
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
_HINTLESS = {"navigation", "manual-rag", "parking-payment", "road-safety"}


def _full_stack_agents() -> list:
    agents = []
    for path in sorted(glob.glob(os.path.join(_ROOT, "agents", "*", "manifest.yaml"))):
        agents.append(SimpleNamespace(manifest=load_manifest(path), endpoint="x:1"))
    for manifest in build_edge_manifests():
        agents.append(SimpleNamespace(manifest=manifest, endpoint="edge:1"))
    assert len(agents) >= 16, f"满栈应为 14 云 + 2 端，实际 {len(agents)}"
    return agents


def test_old_8000_budget_drops_all_hintless_agents(monkeypatch):
    monkeypatch.setattr(ctxmod, "_CATALOG_BUDGET", 8000)
    stats: dict = {}
    WorkingSet.render_catalog(_full_stack_agents(), stats)
    # 「正常情况下根本不触发裁剪」的旧假设已随 M3/M4 能力面增长失效
    assert stats["chars_full"] > 8000
    # 被裁集合 = 全部无 route_hints 的 agent（含核心域 navigation）——保护资格
    # 是「有没有声明 hint」这个巧合，不是领域重要性
    assert set(stats["dropped"]) == _HINTLESS


def test_current_default_budget_holds_full_stack(monkeypatch):
    monkeypatch.setattr(ctxmod, "_CATALOG_BUDGET", 16000)   # 显式=代码默认，免受宿主 env 影响
    stats: dict = {}
    WorkingSet.render_catalog(_full_stack_agents(), stats)
    assert stats["dropped"] == []
    assert stats["chars_full"] == stats["chars_final"]
