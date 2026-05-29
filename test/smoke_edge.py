"""端侧纯逻辑 smoke 测试：Fast Intent 分类 + 模拟 VAL。

不依赖 gen 代码与 docker，可直接 `python test/smoke_edge.py` 运行。
验证"车控快路径"的核心判定与执行逻辑。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "orchestrator", "edge"))

from fast_intent import classify, is_local  # noqa: E402
from val import VAL  # noqa: E402
from edge_agents import edge_execute  # noqa: E402

_passed = 0
_failed = 0


def check(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {msg}")
    else:
        _failed += 1
        print(f"  FAIL  {msg}")


print("== Fast Intent ==")
i = classify("空调调到26度")
check(i and i["name"] == "hvac.set" and i["slots"].get("temp") == "26", "『空调调到26度』-> hvac.set temp=26")
i = classify("打开空调")
check(i and i["name"] == "hvac.on", "『打开空调』-> hvac.on")
# 注：『有点热』这类隐式表达规则版不命中（避免闲聊误触发），保守上云，Phase1 由端侧小模型增强
check(classify("今天有点热") is None, "『今天有点热』-> 隐式表达不误触发(保守上云)")
i = classify("关闭空调")
check(i and i["name"] == "hvac.off", "『关闭空调』-> hvac.off")
i = classify("下一首")
check(i and i["name"] == "media.next", "『下一首』-> media.next")
i = classify("把车窗关上")
check(i and i["name"] == "window.close", "『把车窗关上』-> window.close")
check(classify("讲个笑话") is None, "『讲个笑话』-> 非本地意图(应上云)")
check(classify("附近的充电站") is None, "『附近的充电站』-> 非本地意图(应上云)")
check(is_local("hvac.set") and not is_local("chitchat.talk"), "is_local 判定正确")

print("== VAL（模拟车控抽象层）==")
v = VAL()
ok, msg = v.execute("hvac.set", {"temp": "26"})
check(ok and v.state["hvac_temp"] == 26, f"hvac.set -> {msg}")
ok, msg = v.execute("window.close", {})
check(ok and v.state["window"] == "closed", f"window.close -> {msg}")
v.state["speed_kmh"] = 130
ok, msg = v.execute("window.open", {})
check(not ok, f"高速行驶安全门控 -> {msg}")

print("== Edge 执行链（intent -> VAL -> action）==")
v2 = VAL()
speech, action = edge_execute(classify("空调26度"), v2)
check(action and action["type"] == "vehicle.control" and action["payload"]["command"] == "hvac.set",
      f"edge_execute 产出 vehicle.control 动作；话术：{speech}")

print(f"\n结果：{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
