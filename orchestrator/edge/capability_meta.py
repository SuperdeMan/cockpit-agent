"""能力元数据：`effect`（声明）与 `risk`（派生）。B4 §2.2 的 schema 增量落地。

## `effect: read | write` —— 声明字段

对象级的「这东西是查的还是控的」。它**不是** `fast_intent._is_write_action` 的重复：
那一处判的是**这一次分类结果**是不是写操作（`intent == "control"`），是**轮级**判断；
这里判的是**对象本身**只提供查询（胎压/电量/天气/航班…）还是能被控制。混合对象
（既能开关又能查，如 `dashcam` / `media`）一律记 `write`——判据是「它**能**改状态」。

消费点（不是死字段）：能力完整性门禁的「验证定义」车道据此判定「查询类对象本来就没有
可对账的状态键」，把原先手写在台账里的那条豁免变成机械推导。

## `risk: low | medium | high` —— **派生**，不落盘声明

方案 §2.2 原文要求把 `risk` 也做成声明字段（`require_confirm=true ⇒ risk ≥ high`）。
落地时改成派生，两条理由：

1. **B1 刚把「危险与否」收敛成 `require_confirm` 这一个权威**（确认闸下沉 VAL，
   `docs/conventions.md` §9.15）。再手写一个 `risk` 就是第二份危险声明——两份声明会漂移，
   而漂移的方向没人能预测。同 B1 的判据：安全不变量要放在唯一出口。
2. `risk` 唯一声明中的消费方是 B6（ActionabilityClassifier），而 B6 是**条件启动**、
   尚未开工。先落一个没有消费方的字段就是死字段（方案 §2.3 自己写着这条纪律）。

派生给出的是同一份信息的另一种视图，B6 开工时直接调 :func:`risk_of` 即可；真到了那时
若发现派生规则不够用，**再**加声明字段，那时它有真消费方。
"""
from __future__ import annotations

import os

import yaml

#: 只查不改的操作。对象的 operates 全落在这里 ⇒ 它是 read。
_READ_ONLY_OPERATES = frozenset({"query", "locate"})

EFFECTS = ("read", "write")
RISKS = ("low", "medium", "high")


def derive_effect(obj_def: dict) -> str:
    """从 `operates` 推出 effect。声明缺失时的兜底，也是门禁比对声明的参照。"""
    operates = set(obj_def.get("operates") or [])
    if operates and operates <= _READ_ONLY_OPERATES:
        return "read"
    return "write"


def effect_of(obj_def: dict) -> str:
    """取声明的 `effect`；未声明则派生（门禁另有一条断言要求它必须声明）。"""
    declared = str((obj_def or {}).get("effect") or "").strip().lower()
    return declared if declared in EFFECTS else derive_effect(obj_def or {})


def risk_of(obj_def: dict) -> str:
    """派生风险档。

    - `require_confirm` / `voice_forbidden` ⇒ **high**：前者是 CLAUDE.md §5 的危险动作，
      后者是「压根不许语音操作」，都属最高档；
    - `drive_restricted` / `drive_restricted_off` ⇒ **medium**：行车中受限，说明它会影响
      行车安全，只是不到需要二次确认；
    - 其余 ⇒ **low**。查询类对象恒 low（它不改状态）。
    """
    d = obj_def or {}
    if d.get("require_confirm") or d.get("voice_forbidden"):
        return "high"
    if d.get("drive_restricted") or d.get("drive_restricted_off"):
        return "medium"
    return "low"


# ── `edge_intents` —— 端侧意图名单的单一声明源（B4 §2.2）────────────────────

_KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "knowledge")


class CapabilityKnowledgeError(RuntimeError):
    """知识库读不出任何端侧意图。**fail-closed**：宁可起不来，也不要一个空能力面。"""


def _load_objects(knowledge_dir: str | None = None) -> dict:
    path = os.path.join(knowledge_dir or _KNOWLEDGE_DIR, "commands.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("objects") or {}


def derive_edge_intents(objects: dict | None = None,
                        knowledge_dir: str | None = None) -> set[str]:
    """端侧车控意图全集 = 各对象声明的 ``edge_intents`` 之并。

    ## 为什么是「声明」而不是「从 对象×操作 推」

    方案 §2.2 原文写的是「运行时派生自 commands.yaml（对象×操作）」。实测这条路走不通，
    差集 38 + 196（派生 234 条 vs 手工 76 条），原因是**意图名承载了 commands.yaml 里
    根本没有的四类判断**：

    1. **用哪个对象别名**——`hvac.*` 对 `aircon`、`tire_pressure.query` 对
       `tire_pressure_monitoring`；
    2. **哪个 mode/attr 值得单独占一个意图名**——有 `seat.heating.on` 却没有
       `seat.recline.on`；
    3. **操作动词用哪套**——`on/off` 还是 `open/close`；
    4. **这个对象该不该出现在端侧能力面**——`commands.yaml` 是《公版语音指令表》整表
       导出，含 weather/flight/hotel 等云侧域对象，机械派生会把它们变成端侧意图。

    而且机械派生会**复活 2026-08-04 刻意删掉的同义名**（`aircon.inc/dec` 与 `hvac.inc/dec`
    解出逐字相同的执行数据，两个名字让 planner 只能掷硬币）——「一个动作只能有一个名字」
    这条判据是推不出来的。

    所以改成：名字仍然人写，但**写在 commands.yaml 对象定义里**而不是 `vehicle.py` 的
    Python 集合里。「新增能力要同时记得改 vehicle.py」这个事故面照样消失，代价只是把
    76 行 Python 挪成 27 处 YAML 声明——而它们就挨着对象的 operates/require_confirm，
    改的时候看得见。
    """
    objs = _load_objects(knowledge_dir) if objects is None else objects
    intents = {str(i) for d in objs.values()
               for i in ((d or {}).get("edge_intents") or []) if str(i).strip()}
    if not intents:
        raise CapabilityKnowledgeError(
            "commands.yaml 里一条 edge_intents 都没读到——端侧能力面为空会让云侧 planner "
            "看不到任何车控工具。这里 fail-closed，不静默回落空集。")
    return intents
