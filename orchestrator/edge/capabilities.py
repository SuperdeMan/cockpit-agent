"""Registry manifests exposed by the in-vehicle fast executor.

## 判别化描述（M5 P3 收尾，2026-08-01）

在此之前，78 个 capability 共用**两句**描述（74 个车控一句、4 个媒体一句）。
修法不是手写 74 条，是从 VAL 知识库（`knowledge/commands.yaml`）机械生成：对象名取
`display_name`、动作取 intent 名末段、限定语取中间段，解码走 `edge_call.decode_intent`
——**与 executor 待会儿真执行的是同一个解码器**。于是描述永远说的是这条 intent 真会
干的事，知识库改了描述跟着改，两边不会各自漂移。

**受益方是 registry 语义兜底，不是 planner——这一条是被实测掰过来的。**
本改动的起因是 P3a 影子抓到 `关闭强力前除雾` 被规划成 `accompany_home.close` 并执行，
当时归因写的是「planner 看到 74 个文本等价的工具只能靠名字猜」。实测双臂差分推翻了
这个归因：把描述渲进 planner catalog，25 条 canonical+口语语料 ×2 轮 ×2 provider
（minimax / deepseek）**Δ=0、100 次对照零翻面**——intent 名本身就是判别性文本，
`lane_departure_assistance.open` 与 `lane_assistance.open` 两档都分得开。故 catalog
维持只渲染意图名（判据与护栏见 `orchestrator/cloud/context.py::_catalog_item`）。
真正有效应的是 **registry**：它按 capability 粒度做 embedding，泛化描述让 74 条向量
毫无判别力——`打开空调` 的 top-1 是 **scene-orchestrator（0.517）**，而 registry Resolve
正是 LLM 失败时的兜底规划路径。换判别化描述后 edge-vehicle 0.675 回到 top-1
（`test/eval_registry_resolve.py` 新增两条肯定断言守住）。
那条原始 badcase 的真根因另有其人：**能力面根本没有除雾意图**，描述治不了缺能力。

⚠ `examples` 仍为空：catalog 对任何 agent 都不渲染 examples，registry 逐字打分才用它。
补 examples 属另一件事，本期不做。
"""
from __future__ import annotations

import logging
import os

import grpc

from cockpit.agent.v1 import agent_pb2
from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc

from runtime.grpcio import aio_channel
from edge_agents_mod.media import MEDIA_INTENTS
from edge_agents_mod.vehicle import VEHICLE_INTENTS
from edge_call import decode_intent
from val import VAL

logger = logging.getLogger("edge.capabilities")


# M2 Outcome Verifier 首批声明（车控面试点）：VAL 说"执行成功"但状态没落地是**真实
# 发生过**的静默失败（scene Verify 首跑抓到 ambient_light.set 的亮度分支被提前 return
# 吞掉，四个预置场景的亮度从没生效过）。这里按 intent 声明期望态，云侧 executor 的通用
# 求值器对 NATS 车况镜像对账；镜像读不到 → UNKNOWN 不定罪。
# 状态键与 `orchestrator/edge/val.py` 的 `self.state` 同源。
# on_fail 一律 report：车控是副作用动作，**永不自动重放**（retry 只对查询步开放）。
_VERIFICATION = {
    "hvac.set": {"mirror": "vehicle_state", "keys": {"hvac_on": "true"}},
    "hvac.on": {"mirror": "vehicle_state", "keys": {"hvac_on": "true"}},
    "hvac.off": {"mirror": "vehicle_state", "keys": {"hvac_on": "false"}},
}


def _verification_for(intent: str):
    expect = _VERIFICATION.get(intent)
    if not expect:
        return None
    from google.protobuf.struct_pb2 import Struct
    s = Struct()
    s.update(expect)
    return agent_pb2.Verification(mode="state_match", timeout_ms=2500,
                                  on_fail="report", max_attempts=1, expect=s)


# ── 判别化描述生成 ────────────────────────────────────────────────────────────

# 动词取 intent 名的**最后一段原文**，刻意不过 `_normalize_operation`：归一化会把
# on/off 与 open/close 合并，也会把 `steering_wheel.heating.open` 与 `.close` 一起
# 压成 `set`（差异落在 `enabled` 里）——那正好把判别力抹掉。描述要的是语义不是执行形态。
_VERB = {
    "open": "打开", "on": "打开", "close": "关闭", "off": "关闭",
    "set": "设置", "inc": "调高", "dec": "调低", "query": "查询",
    "play": "播放", "pause": "暂停", "fold": "折叠", "unfold": "展开",
    "next": "切换到下一个", "prev": "切换到上一个",
}
# 这两个动词读起来必须后置：「媒体切换到下一个」通顺，「切换到下一个媒体」不通。
_SUFFIX_VERBS = {"next", "prev"}

# intent 名 `<object>.<path>.<verb>` 里 path 段的中文；外加「默认属性」的中文
# （path 为空且对象声明了 attrs 时取 attrs[0]——与 VAL `_simulate` 的默认行为一致：
# aircon 的裸 inc/dec 调的就是 temperature）。
_QUALIFIER = {
    "heating": "加热", "ventilation": "通风", "massage": "按摩",
    "lumbar_support": "腰托", "wind_speed": "风速",
    "brightness": "亮度", "height": "高度", "speed": "速度",
    "temperature": "温度", "level": "档位",
}

# 模式取值只在**对象本身就是一个模式选择器**时进 描述（`attrs` 为空 = 没有可调的量，
# 它的 set 就是在选 mode）。这一条挡住了 aircon：它有 temperature/speed 两个属性，
# `hvac.set` 是设温度，把 19 个 aircon mode 灌进描述纯属噪声还费预算。
_MODE_VOCAB_FALLBACK = {"scene_mode": "scene_modes"}   # commands.yaml 里 modes 为空，取 entities

# 两个 producer 造成的**真别名**：云端 planner 产 `hvac.*`（架构 2026-06-14「隐式车控」
# 映射），端侧 fast_intent 产 `aircon.*`，两者解码到同一条 VAL 命令。两个名字都得留着
# （各自的执行路径在用），但描述必须点明等价——否则 catalog 里会出现两个**文本完全相同**
# 的工具，那正是本次要消灭的病。契约测试断言 78 条描述两两不同，这里漏一条就红。
_ALIAS_OF = {"aircon.inc": "hvac.inc", "aircon.dec": "hvac.dec"}


def _knowledge() -> tuple[dict, dict]:
    """(objects, entities)——来自 VAL 知识库，与执行侧同一份 `commands.yaml`。"""
    val = VAL()
    return (val.commands or {}).get("objects") or {}, val.entities or {}


def _mode_options(obj: str, defs: dict, entities: dict) -> str:
    """该对象的中文模式取值，如 `标准/运动/经济`；无则空串。"""
    if defs.get("attrs"):
        return ""
    modes = [m for m in (defs.get("modes") or []) if not m.isascii()]
    if not modes:
        cat = entities.get(_MODE_VOCAB_FALLBACK.get(obj, ""), {}) or {}
        seen, modes = set(), []
        for cn, en in cat.items():          # 中文→英文，取每个英文值的第一个中文说法
            if en not in seen:
                seen.add(en)
                modes.append(cn)
    return "/".join(modes[:6])


def _describe(intent: str, objects: dict, entities: dict) -> str:
    """intent → 判别化中文描述。无法解码/缺词条时返回空串（契约测试会红，不静默降级）。"""
    decoded = decode_intent(intent, set(objects) or None)
    if not decoded:
        return ""
    obj = decoded["data"].get("object", "")
    defs = objects.get(obj) or {}
    noun = defs.get("display_name") or ""
    parts = [p for p in intent.split(".") if p]
    verb_key = parts[-1]
    verb = _VERB.get(verb_key, "")
    if not noun or not verb:
        return ""

    path = ".".join(parts[1:-1])
    if path:
        qual = _QUALIFIER.get(path, "")
        if not qual:
            return ""                       # 新增中间段没配中文 → 不许悄悄落回泛化描述
    else:
        attrs = defs.get("attrs") or []
        qual = (_QUALIFIER.get(attrs[0], "")
                if attrs and verb_key in ("set", "inc", "dec") else "")

    text = (f"{noun}{qual}{verb}" if verb_key in _SUFFIX_VERBS
            else f"{verb}{noun}{qual}")
    if verb_key == "set":
        options = _mode_options(obj, defs, entities)
        if options:
            text += f"（{options}）"
    canonical = _ALIAS_OF.get(intent)
    if canonical:
        text += f"（等同 {canonical}，端侧快路径的同义意图名）"
    return text


def _capabilities(intents: set[str], fallback: str):
    objects, entities = _knowledge()
    return [
        agent_pb2.Capability(
            intent=intent,
            description=_describe(intent, objects, entities) or fallback,
            examples=[],
            verification=_verification_for(intent),
        )
        for intent in sorted(intents)
    ]


def build_edge_manifests() -> list[agent_pb2.AgentManifest]:
    return [
        agent_pb2.AgentManifest(
            agent_id="edge-vehicle",
            version="1.0.0",
            display_name="车端快思考-车控",
            category="core",
            trust_level="system",
            deployment="edge",
            latency_budget_ms=800,
            kind="edge_fast",
            capabilities=_capabilities(
                VEHICLE_INTENTS, "通过车端 VAL 执行确定性车控意图"),
            requires_permissions=["vehicle.control"],
            edge_intents=sorted(VEHICLE_INTENTS),
        ),
        agent_pb2.AgentManifest(
            agent_id="edge-media",
            version="1.0.0",
            display_name="车端快思考-媒体",
            category="core",
            trust_level="system",
            deployment="edge",
            latency_budget_ms=500,
            kind="edge_fast",
            capabilities=_capabilities(
                MEDIA_INTENTS, "通过车端执行器控制本地媒体"),
            requires_permissions=["media.control"],
            edge_intents=sorted(MEDIA_INTENTS),
        ),
    ]


async def register_edge_capabilities():
    """Best-effort capability registration; execution still requires an active vehicle stream."""
    addr = os.getenv("REGISTRY_ADDR", "registry:50051")
    channel = aio_channel(addr)
    stub = registry_pb2_grpc.RegistryStub(channel)
    try:
        for manifest in build_edge_manifests():
            await stub.Register(
                registry_pb2.RegisterRequest(
                    manifest=manifest,
                    endpoint="edge://vehicle",
                ),
                timeout=5,
            )
            logger.info("Registered edge capability %s", manifest.agent_id)
    finally:
        await channel.close()
