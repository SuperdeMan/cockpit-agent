"""scene-orchestrator Agent 契约测试。

覆盖：scene.activate / scene.deactivate / scene.list。
验证 NEED_CONFIRM（危险动作）、模糊匹配、场景展开。
"""
import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from agents._sdk.testing import make_context, run_handle
from agents.scene_orchestrator.src.agent import SceneOrchestratorAgent


def test_list_scenes():
    """scene.list → OK + 可用场景列表"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        SceneOrchestratorAgent(), "scene.list",
        slots={}, raw_text="有哪些场景模式", ctx=ctx))
    assert res.status == "ok"
    assert "回家" in res.speech or "露营" in res.speech


def test_activate_go_home():
    """scene.activate 回家模式 → OK（无危险动作，直接执行）"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        SceneOrchestratorAgent(), "scene.activate",
        slots={"scene": "回家模式"}, raw_text="开启回家模式", ctx=ctx))
    assert res.status == "ok"
    assert "回家" in res.speech


def test_activate_camping_needs_confirm():
    """scene.activate 露营模式 → NEED_CONFIRM（座椅放平需确认）"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        SceneOrchestratorAgent(), "scene.activate",
        slots={"scene": "露营模式"}, raw_text="露营模式", ctx=ctx))
    assert res.status == "need_confirm"
    assert res.actions and res.actions[0].get("require_confirm") is True


def test_activate_nap_needs_confirm():
    """scene.activate 午休模式 → NEED_CONFIRM（座椅放平需确认）"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        SceneOrchestratorAgent(), "scene.activate",
        slots={"scene": "午休"}, raw_text="午休模式", ctx=ctx))
    assert res.status == "need_confirm"


def test_activate_unknown_scene():
    """scene.activate 未知场景 → 提示可用场景"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        SceneOrchestratorAgent(), "scene.activate",
        slots={"scene": "蹦迪模式"}, raw_text="蹦迪模式", ctx=ctx))
    assert res.status == "ok"
    assert "没有找到" in res.speech


def test_activate_missing_scene_slot():
    """scene.activate 无场景名 → NEED_SLOT"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        SceneOrchestratorAgent(), "scene.activate",
        slots={}, raw_text="开启场景", ctx=ctx))
    assert res.status == "need_slot"


def test_deactivate():
    """scene.deactivate → OK"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        SceneOrchestratorAgent(), "scene.deactivate",
        slots={"scene": "回家模式"}, raw_text="关闭回家模式", ctx=ctx))
    assert res.status == "ok"


def test_unsupported_intent():
    """不支持的意图 → FAILED"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        SceneOrchestratorAgent(), "scene.unknown",
        slots={}, raw_text="xxx", ctx=ctx))
    assert res.status == "failed"
