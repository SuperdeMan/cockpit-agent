"""WS4 场景回归：端侧降级矩阵关键路径。

不依赖 proto gen，直接测试 fast_intent + val + 降级逻辑。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../orchestrator/edge"))

from fast_intent import classify, is_local
from val import VAL
from edge_agents import edge_execute


# ─── 场景 1：车控指令始终本地（不依赖网络） ───

def test_scenario_vehicle_control_always_local():
    """车控指令无论网络状态如何都本地执行"""
    val = VAL()
    for text, expected_intent in [
        ("空调调到26度", "hvac.set"),
        ("关闭空调", "hvac.off"),
        ("下一首", "media.next"),
        ("把车窗关上", "window.close"),
    ]:
        intent = classify(text)
        assert intent is not None, f"'{text}' should be classified"
        assert is_local(intent["name"]), f"'{intent['name']}' should be local"
        speech, action = edge_execute(intent, val)
        assert speech, f"'{text}' should produce speech"
        assert action is not None, f"'{text}' should produce action"


# ─── 场景 2：慢意图不出端（降级） ───

def test_scenario_slow_intent_not_local():
    """慢意图（闲聊/导航/组合）应标记为非本地"""
    for text in ["讲个笑话", "附近的充电站", "找家川菜馆订位", "明天天气怎么样"]:
        intent = classify(text)
        if intent is not None:
            assert not is_local(intent["name"]), f"'{text}' should NOT be local"
        # None 也正确（未命中任何意图）


# ─── 场景 3：高速安全门控 ───

def test_scenario_speed_safety_gating():
    """高速行驶中开车窗应被安全门控拒绝"""
    val = VAL()
    val.state["speed_kmh"] = 130
    intent = classify("把车窗打开")
    assert intent is not None
    ok, msg = val.execute(intent["name"], intent["slots"])
    assert not ok, "高速行驶开车窗应被拒绝"
    assert "安全" in msg or "高速" in msg


# ─── 场景 4：正常速度允许车窗操作 ───

def test_scenario_normal_speed_allows_window():
    val = VAL()
    val.state["speed_kmh"] = 30
    intent = classify("把车窗打开")
    assert intent is not None
    ok, msg = val.execute(intent["name"], intent["slots"])
    assert ok, "低速行驶开车窗应允许"


# ─── 场景 5：edge_execute 产出正确 action 类型 ───

def test_scenario_action_type_mapping():
    val = VAL()
    # 车控 → vehicle.control
    intent = classify("空调26度")
    _, action = edge_execute(intent, val)
    assert action["type"] == "vehicle.control"

    # 媒体 → media.control
    intent2 = classify("下一首")
    _, action2 = edge_execute(intent2, val)
    assert action2["type"] == "media.control"
