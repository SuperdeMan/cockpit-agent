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
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger("agent.mcp_bridge.admission")

REJECT_VERSION = "version_mismatch"
REJECT_SCHEMA = "schema_mismatch"
REJECT_NOT_ALLOWED = "not_in_allowlist"
REJECT_MISSING = "tool_missing_on_server"
REJECT_ENV = "env_var_missing"
REJECT_COMPENSATE = "compensate_invalid"

_ENV_REF = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
COMPENSATE_POLICIES = ("tool", "abandon_unpaid", "terminal")
IDEMPOTENCY_MODES = ("upstream", "local_at_most_once")


@dataclass
class ToolSpec:
    name: str                    # MCP 侧 tool 名
    intent: str                  # 映射成的 capability intent
    write: bool = False
    require_confirm: bool = False
    expose: bool = True
    forward_owner: bool = False
    required_scopes: list = field(default_factory=list)
    description: str = ""
    examples: list = field(default_factory=list)
    slots: list = field(default_factory=list)
    schema_sha: str = ""         # 声明的 inputSchema 指纹（空=首次接入，不校验只记录）
    timeout_ms: int = 15000
    idempotency_key_arg: str = ""
    idempotency_mode: str = "none"
    retry_policy: str = "safe"
    compensate_tool: str = ""   # 补偿工具（退款/取消）；policy=tool 时必填且须在白名单
    # 补偿形态：`tool`（下单即扣款型——补偿=退款/取消工具）|
    # `abandon_unpaid`（创建**未支付**订单、扫码才付钱型——天然补偿=不支付+商户
    # 自动过期）| `terminal`（取消等生命周期终态，不再向下补偿）。
    compensate_policy: str = "tool"
    # 商户下单响应里支付链接的点路径（如 "payH5Url" / "order.payUrl"），从
    # structuredContent 提取——声明式，桥核心零领域词（§9.9 支付链接闭环）。
    pay_url_locator: str = ""
    # 常量参数（麦当劳激活时新增，2026-08-11）：真实商户工具常有 required 枚举
    # （beType=1/searchType=1 这类场景选择器），LLM 的自然语言槽位填不了也不该填
    # ——在准入清单里**声明死**。组装序：const_args 打底 → 槽位映射值覆盖 →
    # 幂等键/owner 系统键最后（系统键永不被声明覆盖）。
    const_args: dict = field(default_factory=dict)
    # 话术形态（商户端到端取证后新增，2026-08-11）：`raw`（默认，text 原样进话术
    # ——demo 商户的中文短回执适用）| `summarize`（text/data 交 LLM 用中文一两句
    # 直接回答用户——真实商户返回的是「给 LLM 读的 API 文档+原始数据」，逐字念
    # 6638 字英文文档是实测抓到的体验事故）。LLM 不可用时回落截断+「详情见屏幕」。
    speech_mode: str = "raw"
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
    # 批 3（§9.9 transport 行）：stdio（默认）| streamable_http（仅官方商户远程端点）
    transport: str = "stdio"
    url: str = ""
    headers: dict = field(default_factory=dict)   # 值支持 ${ENV_VAR}，token 不进 yaml
    pay_url_hosts: list = field(default_factory=list)  # 支付链接域名白名单（第一层）
    env_error: str = ""          # ${VAR} 展开失败的具名原因——bootstrap 据此整台拒载


def schema_fingerprint(schema) -> str:
    """inputSchema 的稳定指纹：键排序后 sha256 前 12 位。schema 变了=接口变了=要重审。"""
    try:
        return hashlib.sha256(
            json.dumps(schema or {}, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:12]
    except (TypeError, ValueError):
        return ""


def _expand_env(value: str) -> tuple[str, str]:
    """`${VAR}` 展开。返回 (展开值, 缺失变量名)——缺 env 是**具名拒载理由**，
    不静默留空 token 出站吃 401（§9.9 transport 行）。"""
    missing = ""

    def _sub(m: re.Match) -> str:
        nonlocal missing
        v = os.getenv(m.group(1), "")
        if not v:
            missing = m.group(1)
        return v

    return _ENV_REF.sub(_sub, value or ""), missing


def load_servers(path: str) -> list:
    import yaml
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out = []
    for s in data.get("servers", []) or []:
        tools = [ToolSpec(
            name=t["name"], intent=t["intent"], write=bool(t.get("write", False)),
            require_confirm=bool(t.get("require_confirm", False)),
            expose=bool(t.get("expose", True)),
            forward_owner=bool(t.get("forward_owner", False)),
            required_scopes=[str(scope) for scope in
                             (t.get("required_scopes") or [])],
            description=t.get("description", ""), examples=t.get("examples", []) or [],
            slots=t.get("slots", []) or [],
            schema_sha=str(t.get("schema_sha", "") or ""),
            confirm_prompt=str(t.get("confirm_prompt", "") or ""),
            timeout_ms=int(t.get("timeout_ms", 15000) or 15000),
            idempotency_key_arg=str(t.get("idempotency_key_arg", "") or ""),
            idempotency_mode=str(t.get("idempotency_mode", "none") or "none"),
            retry_policy=str(t.get("retry_policy", "safe") or "safe"),
            compensate_tool=str(t.get("compensate_tool", "") or ""),
            compensate_policy=str(t.get("compensate_policy", "tool") or "tool"),
            pay_url_locator=str(t.get("pay_url_locator", "") or ""),
            const_args=dict(t.get("const_args") or {}),
            speech_mode=str(t.get("speech_mode", "raw") or "raw"),
            arg_map={str(k): str(v) for k, v in (t.get("arg_map") or {}).items()},
        ) for t in (s.get("tools") or [])]
        headers: dict[str, str] = {}
        env_error = ""
        for k, v in (s.get("headers") or {}).items():
            expanded, missing = _expand_env(str(v))
            if missing:
                env_error = f"{REJECT_ENV}: headers.{k} 引用的 ${{{missing}}} 未配置"
            headers[str(k)] = expanded
        out.append(ServerSpec(
            id=s["id"], command=list(s.get("command") or []),
            version=str(s.get("version", "")), tools=tools,
            demo=bool(s.get("demo", False)), trust=s.get("trust", "third_party"),
            startup_timeout_ms=int(s.get("startup_timeout_ms", 20000) or 20000),
            transport=str(s.get("transport", "stdio") or "stdio"),
            url=str(s.get("url", "") or ""),
            headers=headers,
            pay_url_hosts=[str(h).lower() for h in (s.get("pay_url_hosts") or [])],
            env_error=env_error))
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
    - 写工具必须显式确认、幂等模式与补偿政策；tool 型补偿目标自身也必须准入。
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
        if t.write:
            if not t.require_confirm:
                rejected.append(
                    f"{t.name}: 写操作 require_confirm 必须为 true")
                continue
            if t.idempotency_mode not in IDEMPOTENCY_MODES:
                rejected.append(
                    f"{t.name}: 写操作 idempotency_mode 必须显式为 "
                    "upstream 或 local_at_most_once")
                continue
            if t.compensate_policy not in COMPENSATE_POLICIES:
                rejected.append(f"{t.name}: {REJECT_COMPENSATE}"
                                f"（未知 compensate_policy={t.compensate_policy!r}）")
                continue
            if t.compensate_policy == "tool":
                if not t.compensate_tool:
                    rejected.append(f"{t.name}: 写操作未声明 compensate_tool"
                                    f"（缺补偿路径不许接）")
                    continue
            elif t.compensate_policy == "abandon_unpaid":
                prompt = t.confirm_prompt
                if ("不支付" not in prompt or "自动" not in prompt or
                        not any(term in prompt for term in ("取消", "失效", "过期"))):
                    rejected.append(
                        f"{t.name}: {REJECT_COMPENSATE}（abandon_unpaid 必须给 "
                        f"confirm_prompt 且说明「不支付」会「自动取消/失效/过期」）")
                    continue
            # terminal：该动作本身就是生命周期终态，不再要求下一跳补偿工具。

            if t.idempotency_mode == "upstream":
                properties = ((found.get("inputSchema") or {}).get("properties")
                              if isinstance(found.get("inputSchema"), dict) else {})
                if (not t.idempotency_key_arg or
                        t.idempotency_key_arg not in (properties or {})):
                    rejected.append(
                        f"{t.name}: upstream idempotency_key_arg="
                        f"{t.idempotency_key_arg!r} 不在实时 inputSchema.properties")
                    continue
            if (t.idempotency_mode == "local_at_most_once" and
                    t.retry_policy != "never"):
                rejected.append(
                    f"{t.name}: local_at_most_once 要求 retry_policy=never")
                continue
        if t.pay_url_locator and not spec.pay_url_hosts:
            rejected.append(
                f"{t.name}: pay_url_locator 已声明但 pay_url_hosts 为空")
            continue
        admitted.append((t, found.get("inputSchema") or {}))

    # 二阶段校验：补偿目标必须自己通过全部准入，并且是另一件 terminal 写工具。
    # 这样 schema 漂移、只读伪补偿、自引用与 A↔B 环都不能替 create 背书。
    admitted_by_name = {tool.name: tool for tool, _ in admitted}
    kept = []
    for tool, schema in admitted:
        if tool.write and tool.compensate_policy == "tool":
            target = admitted_by_name.get(tool.compensate_tool)
            if (target is None or target.name == tool.name or not target.write or
                    target.compensate_policy != "terminal"):
                rejected.append(
                    f"{tool.name}: {REJECT_COMPENSATE}（compensate_tool="
                    f"{tool.compensate_tool!r} 必须是已准入的独立 terminal 写工具）")
                continue
        kept.append((tool, schema))
    admitted = kept
    extra = sorted(set(offered) - {t.name for t in spec.tools})
    if extra:
        logger.info("[mcp:%s] 忽略未准入的工具 %s（接入永远是人工准入）", spec.id, extra)
    return admitted, rejected
