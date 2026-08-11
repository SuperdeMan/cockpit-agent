"""端侧车控 Agent。经 VAL 执行车控指令。

Phase 1 从 edge_agents.py 拆分独立，可独立测试。
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from val import VAL
from capability_meta import derive_edge_intents

# 车控意图白名单——**不再手写，从 VAL 知识库派生**（B4 §2.2）。
#
# 判据：新增一个车控能力时，「记得同时改这个集合」曾经是十来个同步点里的一个，
# 而漏写的后果是**能力不可达且没有任何报错**（除雾能力那次就是两头都缺：这里没有意图名、
# 端侧规则也不认，于是整句上云、planner 在 76 个语义不含除雾的工具里挑，
# `关闭强力前除雾` → `accompany_home.close` 被 VAL 照单执行）。
#
# 现在名字仍然人写，但写在 `knowledge/commands.yaml` 各对象的 `edge_intents` 里——
# 就挨着它的 operates / require_confirm / effect，改对象时看得见。
# ⚠ 方案原文说的是「从 对象×操作 机械派生」，那条路实测走不通（差集 38+196，还会复活
#   2026-08-04 刻意删掉的 `aircon.inc/dec` 同义名）；理由写在
#   `capability_meta.derive_edge_intents` 的 docstring 里。
#
# 迁移当次的一次性 diff 断言见 `tests/test_vehicle_intents_migration.py`：
# 派生结果与迁移前那 76 条**逐字相同**，本次切换零行为变化。
VEHICLE_INTENTS = derive_edge_intents()


class VehicleAgent:
    def __init__(self, val: VAL):
        self.val = val

    def can_handle(self, intent_name: str) -> bool:
        return intent_name in VEHICLE_INTENTS

    def execute(self, intent: dict) -> tuple[str, dict | None]:
        name = intent["name"]
        slots = intent["slots"]
        ok, msg = self.val.execute(name, slots)
        action = {
            "type": "vehicle.control",
            "payload": {"command": name, **slots},
            "require_confirm": False,
        }
        return msg, (action if ok else None)
