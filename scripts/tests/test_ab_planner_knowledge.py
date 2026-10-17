"""规划知识 A/B 工具（`scripts/ab_planner_knowledge.py` + 容器载荷）：配置装配、送进容器的程序、B 臂描述补丁与汇总。

不连云主机：远端执行由真跑核对（第十三轮 M10 / A1-5 两次 A/B，后续批 §20.3），这里钉住两臂只差那一处知识、配置不丢。
"""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from scripts import ab_planner_knowledge as ab
from scripts import ab_planner_knowledge_payload as payload


def _spec(**arm_b):
    return {"name": "m10", "n": 2, "texts": ["停车时有人刮车能录下来吗", "我有哪些提醒"], "arm_b": arm_b}


def test_a_description_spec_becomes_a_config_with_the_pinned_model():
    config = ab.build_config(_spec(description={"agent_id": "manual-rag", "intent": "manual.query", "text": "新描述"}))
    assert config["provider"] == "minimax" and config["model"] == "MiniMax-M3"
    assert config["description_b"] == {"agent_id": "manual-rag", "intent": "manual.query", "description": "新描述"}
    assert "guide_name" not in config


def test_a_guide_spec_reads_the_modified_guide_file(tmp_path):
    guide = tmp_path / "weather-outing.yaml"
    guide.write_text("name: weather-outing\nknowledge: 改过的\n", encoding="utf-8")
    config = ab.build_config(_spec(guide={"name": "weather-outing", "file": str(guide)}))
    assert config["guide_name"] == "weather-outing" and "改过的" in config["guide_yaml_b"]


@pytest.mark.parametrize("spec", [
    {"name": "x", "n": 1, "texts": [], "arm_b": {"description": {"agent_id": "a", "intent": "b", "text": "c"}}},
    {"name": "x", "n": 0, "texts": ["t"], "arm_b": {"description": {"agent_id": "a", "intent": "b", "text": "c"}}},
    {"name": "x", "n": 1, "texts": ["t"], "arm_b": {}},
    {"name": "x", "n": 1, "texts": ["t"], "arm_b": {"description": {"agent_id": "a", "intent": "b", "text": "c"},
                                                  "guide": {"name": "g", "file": "f"}}},
])
def test_an_incomplete_or_ambiguous_spec_is_refused(spec):
    with pytest.raises(ValueError):
        ab.build_config(spec)


def test_the_host_program_carries_payload_and_config_to_the_planner_container():
    config = ab.build_config(_spec(description={"agent_id": "manual-rag", "intent": "manual.query", "text": "新描述"}))
    program = ab.host_program("print('hi')", config)
    assert "label=com.docker.compose.service=cloud-planner" in program
    assert base64.b64encode(b"print('hi')").decode() in program
    encoded = base64.b64encode(json.dumps(config, ensure_ascii=False).encode()).decode()
    assert encoded in program and payload.decode_config(encoded) == config


def _agent(agent_id, *intents):
    caps = [SimpleNamespace(intent=i, description="旧描述") for i in intents]
    return SimpleNamespace(manifest=SimpleNamespace(agent_id=agent_id, capabilities=caps))


def test_only_arm_b_patches_the_one_description_once_per_agent():
    manual = _agent("manual-rag", "manual.query", "manual.claim")
    ws = SimpleNamespace(catalog=[manual, _agent("chitchat", "chitchat.talk")], registry_agents=[manual])
    config = {"description_b": {"agent_id": "manual-rag", "intent": "manual.query", "description": "新描述"}}
    assert payload.patch_catalog(ws, config, "A") == 0
    assert manual.manifest.capabilities[0].description == "旧描述"
    assert payload.patch_catalog(ws, config, "B") == 1          # 预筛目录与完整注册表里是同一个对象，只算一次
    assert [c.description for c in manual.manifest.capabilities] == ["新描述", "旧描述"]


def test_the_summary_counts_landings_per_text_and_arm_and_reports_injection():
    rows = [{"arm": "A", "text": "t", "intents": ["chitchat.talk"], "guide_sha": "", "patched": 0},
            {"arm": "B", "text": "t", "intents": ["manual.query"], "guide_sha": "", "patched": 1},
            {"arm": "B", "text": "t", "error": "TimeoutError"}]
    lines = ab.summarize(rows)
    assert "A: {'chitchat.talk': 1}" in lines[0] and "'manual.query': 1" in lines[0] and "'error:TimeoutError': 1" in lines[0]
    assert lines[-1].startswith("B: rows 2, errors 1") and "patched 1" in lines[-1]
