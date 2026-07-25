"""Outcome Verifier：执行后对账（M2 P1）。

**动机（真缺陷）**：步骤 OK ≠ 结果达成。车控步 VAL 层可能没落地（scene 首跑就抓到过一个：
`ambient_light.set` 同时带 color+brightness 时设色分支提前 return，亮度被静默丢弃），
查询步可能拿了空数据却照样 OK——两者今天都以"成功"落地，用户听到的是"已为您打开"。

**声明式是铁律**：领域期望全部由 `capability.verification` 声明（proto Capability field 7），
本模块与两个求值器**不得出现任何 agent_id/intent 字面量分支**——否则会长成下一个
fast_intent.py（v1.2 评审既定）。加一个能力的对账 = 在它自己的 manifest 写 5 行 YAML，
编排核心零改动，与 route_hints 把领域路由知识搬回 Agent 是同一条哲学。契约测试锁死这一点。

**三态语义**（照搬 scene 求值器的思想，不搬代码）：
- `SAT`   期望达成 → 透传
- `UNSAT` **确凿**未达成 → 进 on_fail（report 诚实告知 / retry 重试一次）
- `UNKNOWN` 观测缺失（镜像里没这个键 / 镜像没数据）→ **不定罪**，只记观测。
  「读不到」不等于「没做成」；把观测缺失当失败会制造假警，比不验更伤信任。
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("planner.verify")

SAT, UNSAT, UNKNOWN = "sat", "unsat", "unknown"

MODE_SCHEMA, MODE_STATE_MATCH = "schema", "state_match"
ON_FAIL_REPORT, ON_FAIL_RETRY = "report", "retry"

DEFAULT_TIMEOUT_MS = 2000
_POLL_INTERVAL_S = 0.1


# ── 求值器一：schema（查询步「拿到了真东西」）────────────────────────────

def eval_schema(expect: dict, data: dict) -> str:
    """对 `StepResult.data` 做**纯结构断言**：`expect.data_keys` 列出的键存在且非空。

    列表/字典键要求非空容器（空列表 = 没查到，正是"空结果假 OK"的形态）；
    数字 0 与布尔 False 视为**有值**（0 度、false 都是真实答案，不是缺失）。

    刻意不判语义质量（答得好不好是 eval 的事，不是运行期对账的事）。
    没声明 data_keys → UNKNOWN（声明不完整时不定罪）。
    """
    keys = expect.get("data_keys")
    if not isinstance(keys, (list, tuple)) or not keys:
        return UNKNOWN
    if not isinstance(data, dict):
        return UNSAT
    for k in keys:
        if str(k) not in data:
            return UNSAT
        v = data[str(k)]
        if v is None:
            return UNSAT
        if isinstance(v, (str, list, tuple, dict, set)) and len(v) == 0:
            return UNSAT
    return SAT


# ── 求值器二：state_match（车控步「世界真的变了」）──────────────────────

def _to_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "on", "yes")


def _values_equal(actual, expect) -> bool:
    """跨类型等值：镜像里的 22 与声明里的 "22"、True 与 "true" 应当相等
    （YAML/Struct 一律是字符串，镜像里是原生类型）。"""
    if isinstance(actual, bool) or str(expect).strip().lower() in ("true", "false"):
        return _to_bool(actual) == _to_bool(expect)
    try:
        return float(actual) == float(expect)
    except (TypeError, ValueError):
        return str(actual).strip().lower() == str(expect).strip().lower()


def eval_state_match(expect: dict, snapshot: dict | None) -> str:
    """对共享状态镜像逐键比对。UNSAT 优先于 UNKNOWN——有硬证据说明没做成就该报，
    只有"全都读不到"时才是真的不知道。

    镜像为空（无 NATS / 冷启动没收到过快照）→ UNKNOWN：这是"我看不见"，不是"没做成"。
    """
    keys = expect.get("keys")
    if not isinstance(keys, dict) or not keys:
        return UNKNOWN
    if not snapshot:
        return UNKNOWN
    unknown = False
    for k, want in keys.items():
        if k not in snapshot or snapshot[k] is None:
            unknown = True
            continue
        if not _values_equal(snapshot[k], want):
            return UNSAT
    return UNKNOWN if unknown else SAT


# ── 调度：按 mode 选求值器 ───────────────────────────────────────────────

async def evaluate(verification: dict, data: dict, mirror=None) -> str:
    """按声明的 mode 求值。未知 mode → UNKNOWN（前向兼容：新 mode 在旧编排上不定罪）。

    `state_match` 在 `timeout_ms` 内轮询等收敛——车控生效有毫秒到秒级延迟（动作到端 →
    VAL 执行 → state diff 经 NATS 回来），立刻断言必然误报。
    """
    mode = str(verification.get("mode") or "")
    expect = verification.get("expect") or {}
    if mode == MODE_SCHEMA:
        return eval_schema(expect, data)
    if mode == MODE_STATE_MATCH:
        return await _eval_state_with_wait(expect, verification, mirror)
    return UNKNOWN


async def _eval_state_with_wait(expect: dict, verification: dict, mirror) -> str:
    if mirror is None:
        return UNKNOWN
    timeout_ms = int(verification.get("timeout_ms") or 0) or DEFAULT_TIMEOUT_MS
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000.0
    verdict = UNKNOWN
    while True:
        verdict = eval_state_match(expect, mirror.snapshot())
        if verdict == SAT:
            return SAT
        if asyncio.get_event_loop().time() >= deadline:
            return verdict          # 等到超时仍未达成：UNSAT 报、UNKNOWN 不定罪
        await asyncio.sleep(_POLL_INTERVAL_S)


def enabled() -> bool:
    """总开关：`VERIFY_OUTCOME=off` 一键回到 M2 之前（声明照读、只是不执行对账）。"""
    return os.getenv("VERIFY_OUTCOME", "on").strip().lower() != "off"


def retry_allowed(verification: dict, require_confirm: bool, attempts: int) -> bool:
    """能不能重试这一步。

    **副作用步永不重试**：`require_confirm=true` 的能力（后备箱/支付/场景创建…）重放
    等于二次执行副作用，而用户只确认过一次。这条不是配置项，是硬约束——契约测试锁死。
    """
    if str(verification.get("on_fail") or ON_FAIL_REPORT) != ON_FAIL_RETRY:
        return False
    if require_confirm:
        return False
    return attempts < (int(verification.get("max_attempts") or 0) or 1)
