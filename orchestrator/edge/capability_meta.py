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
