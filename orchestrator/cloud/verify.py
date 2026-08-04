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


# ── 动态期望：`$slot:<name>` 取本步槽位的值（2026-08-04）────────────────────
#
# 起因是一条**一直在报绿**的缺陷：「设定为 N 度」这个动作的 state_match 只声明了
# 「空调开着」，于是「set 了但没设成」在 Verifier 面前是成功的（journeys `B3-3`：
# 终态 20、期望 26、verdict `sat`）。
#
# > **判据：验证的强度必须匹配主张的强度。** 核了一个比主张弱的东西等于没核，
# > 而且它比「挂点漏了执行路径」更难发现——漏挂是没有 span，核错是一路绿灯。
#
# 期望值写成 `$slot:<槽名>` 时，求值前用**本步 slots** 里同名槽的值替换。声明侧
# 由能力自己写（`Verification.expect`），中央零领域分支不变。
#
# **取不到时是 UNKNOWN 不是 UNSAT。** 槽缺失说明这一步压根没主张那个值，
# 「没声明温度」不等于「温度没设成」——那是另一条账（planner 把值算进 goal
# 却没写进 slots），归另一个检测器管。把它算成 UNSAT 等于用一条断言去证两件事。
_SLOT_REF_PREFIX = "$slot:"


class _Unresolved:
    """声明引用了一个本步没有的槽：这一键**核不了**，不是核不过。"""

    __slots__ = ()

    def __repr__(self) -> str:            # pragma: no cover - 仅诊断可读性
        return "<unresolved slot ref>"


UNRESOLVED = _Unresolved()


def resolve_expect_keys(keys, slots) -> dict:
    """把 `keys` 里的 `$slot:<名>` 换成本步槽值；取不到 → `UNRESOLVED` 占位。

    只认**整值**引用，不做字符串插值——期望值是要拿去和世界状态逐值比对的，
    支持插值等于把一个可被模型输出影响的语法面塞进对账层。
    """
    if not isinstance(keys, dict):
        return {}
    slots = slots if isinstance(slots, dict) else {}
    resolved = {}
    for k, want in keys.items():
        if isinstance(want, str) and want.startswith(_SLOT_REF_PREFIX):
            name = want[len(_SLOT_REF_PREFIX):].strip()
            value = slots.get(name)
            resolved[k] = (UNRESOLVED
                           if value is None or str(value).strip() == ""
                           else value)
        else:
            resolved[k] = want
    return resolved


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


def eval_state_match(expect: dict, snapshot: dict | None, slots=None) -> str:
    """对共享状态镜像逐键比对。UNSAT 优先于 UNKNOWN——有硬证据说明没做成就该报，
    只有"全都读不到"时才是真的不知道。

    镜像为空（无 NATS / 冷启动没收到过快照）→ UNKNOWN：这是"我看不见"，不是"没做成"。
    `$slot:` 动态期望取不到值 → 那一键计入 UNKNOWN，与"镜像里没这个键"同一条通道。
    """
    keys = resolve_expect_keys(expect.get("keys"), slots)
    if not keys:
        return UNKNOWN
    if not snapshot:
        return UNKNOWN
    unknown = False
    for k, want in keys.items():
        if want is UNRESOLVED:
            unknown = True
            continue
        if k not in snapshot or snapshot[k] is None:
            unknown = True
            continue
        if not _values_equal(snapshot[k], want):
            return UNSAT
    return UNKNOWN if unknown else SAT


# ── 调度：按 mode 选求值器 ───────────────────────────────────────────────

async def evaluate(verification: dict, data: dict, mirror=None, slots=None) -> str:
    """按声明的 mode 求值。未知 mode → UNKNOWN（前向兼容：新 mode 在旧编排上不定罪）。

    `state_match` 在 `timeout_ms` 内轮询等收敛——车控生效有毫秒到秒级延迟（动作到端 →
    VAL 执行 → state diff 经 NATS 回来），立刻断言必然误报。

    `slots` 是**本步已解析完的槽位**（`slot_refs` 填过之后），供 `$slot:` 动态期望取值。
    """
    mode = str(verification.get("mode") or "")
    expect = verification.get("expect") or {}
    if mode == MODE_SCHEMA:
        return eval_schema(expect, data)
    if mode == MODE_STATE_MATCH:
        return await _eval_state_with_wait(expect, verification, mirror, slots)
    return UNKNOWN


async def _eval_state_with_wait(expect: dict, verification: dict, mirror,
                                slots=None) -> str:
    if mirror is None:
        return UNKNOWN
    timeout_ms = int(verification.get("timeout_ms") or 0) or DEFAULT_TIMEOUT_MS
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000.0
    verdict = UNKNOWN
    while True:
        verdict = eval_state_match(expect, mirror.snapshot(), slots)
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
