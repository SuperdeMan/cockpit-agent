"""MCP 准入（M3 P2）——**人工准入 + 版本锁定**，不是动态放行。

`servers.yaml` 是唯一准入依据：server 与 tool 都要在里面，且 server 版本与 tool 的
schema 指纹都要**逐字对得上**，否则拒载并告警。母提案 §4.F 的「MCP 动态注册过宽」
就是这条要收紧的东西——外部服务改了接口，我们必须重新审一遍，而不是自动接受。

新增工具 = 改 servers.yaml + 人工审，**零编排核心改动**（route_hints/verification 同款哲学）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("agent.mcp_bridge.admission")

REJECT_VERSION = "version_mismatch"
REJECT_SCHEMA = "schema_mismatch"
REJECT_NOT_ALLOWED = "not_in_allowlist"
REJECT_MISSING = "tool_missing_on_server"


@dataclass
class ToolSpec:
    name: str                    # MCP 侧 tool 名
    intent: str                  # 映射成的 capability intent
    write: bool = False
    require_confirm: bool = False
    description: str = ""
    examples: list = field(default_factory=list)
    slots: list = field(default_factory=list)
    schema_sha: str = ""         # 声明的 inputSchema 指纹（空=首次接入，不校验只记录）
    timeout_ms: int = 15000
    idempotency_key_arg: str = ""
    compensate_tool: str = ""   # 补偿工具（退款/取消）；写操作没有它就不许标 write
    # 二次确认时给用户看的那句话（`{args}` 占位）。**用户正要点头同意的就是这句**，
    # 说错动作比说得笨拙严重得多——真栈实测抓到取消订单被问成「准备下单：DC…」。
    # 放在声明里而不是桥核心：动词是领域语义，桥不该认识「下单」和「取消」。
    confirm_prompt: str = ""
    # 槽位名 → 工具参数名。**规划期看到的词表与工具的参数名解耦**：LLM 自然会填
    # `item`（"点一杯拿铁"），而商户的参数叫 `sku`——硬要求 LLM 用商户词表是自找的
    # 槽位缺失。映射写在准入清单里，桥不含任何领域词。
    arg_map: dict = field(default_factory=dict)


@dataclass
class ServerSpec:
    id: str
    command: list
    version: str
    tools: list                  # list[ToolSpec]
    demo: bool = False
    trust: str = "third_party"
    startup_timeout_ms: int = 20000


def schema_fingerprint(schema) -> str:
    """inputSchema 的稳定指纹：键排序后 sha256 前 12 位。schema 变了=接口变了=要重审。"""
    try:
        return hashlib.sha256(
            json.dumps(schema or {}, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:12]
    except (TypeError, ValueError):
        return ""


def load_servers(path: str) -> list:
    import yaml
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out = []
    for s in data.get("servers", []) or []:
        tools = [ToolSpec(
            name=t["name"], intent=t["intent"], write=bool(t.get("write", False)),
            require_confirm=bool(t.get("require_confirm", False)),
            description=t.get("description", ""), examples=t.get("examples", []) or [],
            slots=t.get("slots", []) or [],
            schema_sha=str(t.get("schema_sha", "") or ""),
            confirm_prompt=str(t.get("confirm_prompt", "") or ""),
            timeout_ms=int(t.get("timeout_ms", 15000) or 15000),
            idempotency_key_arg=str(t.get("idempotency_key_arg", "") or ""),
            compensate_tool=str(t.get("compensate_tool", "") or ""),
            arg_map={str(k): str(v) for k, v in (t.get("arg_map") or {}).items()},
        ) for t in (s.get("tools") or [])]
        out.append(ServerSpec(
            id=s["id"], command=list(s.get("command") or []),
            version=str(s.get("version", "")), tools=tools,
            demo=bool(s.get("demo", False)), trust=s.get("trust", "third_party"),
            startup_timeout_ms=int(s.get("startup_timeout_ms", 20000) or 20000)))
    return out


def check_version(spec: ServerSpec, server_info: dict) -> str:
    """版本锁定：声明版本与 server 自报版本必须逐字相等。不等 → 拒载理由。"""
    actual = str((server_info or {}).get("version", ""))
    if spec.version and actual != spec.version:
        return (f"{REJECT_VERSION}: 声明 {spec.version!r} ≠ 实际 {actual!r}")
    return ""


def admit(spec: ServerSpec, offered_tools: list) -> tuple[list, list]:
    """allowlist ∩ server 实际提供的工具。返回 (准入的 (ToolSpec, schema) 对, 拒绝理由)。

    - server 多提供的工具**直接忽略**（记日志）——动态放行正是要防的事。
    - allowlist 里有、server 没有 → 拒绝并记录（配置与现实脱节要看得见）。
    - schema 指纹对不上 → 拒绝（接口变了要重审，不自动接受）。
    - `write: true` 却没声明 `compensate_tool` → 拒绝（母提案 §4.F 强制项：
      没有补偿路径的写操作不许接）。
    """
    offered = {t.get("name"): t for t in offered_tools if isinstance(t, dict)}
    admitted, rejected = [], []
    for t in spec.tools:
        found = offered.get(t.name)
        if found is None:
            rejected.append(f"{t.name}: {REJECT_MISSING}")
            continue
        actual_sha = schema_fingerprint(found.get("inputSchema"))
        if t.schema_sha and actual_sha != t.schema_sha:
            rejected.append(f"{t.name}: {REJECT_SCHEMA}（声明 {t.schema_sha} ≠ "
                            f"实际 {actual_sha}）")
            continue
        if t.write and not t.compensate_tool:
            rejected.append(f"{t.name}: 写操作未声明 compensate_tool（缺补偿路径不许接）")
            continue
        admitted.append((t, found))
    extra = sorted(set(offered) - {t.name for t in spec.tools})
    if extra:
        logger.info("[mcp:%s] 忽略未准入的工具 %s（接入永远是人工准入）", spec.id, extra)
    return admitted, rejected
