"""live 评测的公共装配：真实 catalog + 直连 llm-gateway 的 PlanBuilder（M5 P2 抽出）。

为什么单独一个文件而不是塞进 `eval_common.py`：那个文件自述「不 import 任何被测业务模块，
保持与『评测什么』完全解耦」——这里恰恰要 import agents/orchestrator。破它的契约不如新开
一个门面（`eval_common` 管报告与基线，`eval_live` 管真栈装配）。

在此之前 eval_skills / eval_exemplars / routing_bench 各抄了一份同样的 `_load_agents` +
`_make_llm_fn`，**三份抄写就是三份会各自漂移的「生产同构」承诺**。
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "gen" / "python"), str(_ROOT / "orchestrator" / "edge")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def load_agents(include_edge: bool = True) -> list:
    """真实云端 inventory（静态 manifests + builtin-tools）及可选端侧能力。

    与生产 catalog 同构是刻意的：少了 edge 车控，implicit-vehicle-control 之类的
    policy/范例就失去了断言对象；少了某个 manifest，它的 route_hints 也不参与评测。
    builtin-tools 由生产 `ToolRegistry().manifest` 周期 upsert，因此也必须显式纳入；若未来
    出现同 ID 静态 manifest，以运行时 ToolRegistry 为准替换且只保留一份。新增 Agent 落
    manifest 自动纳入。"""
    from agents._sdk.manifest import load_manifest
    from orchestrator.cloud.tools import ToolRegistry
    agents = []
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        m = load_manifest(path)
        _synth_admitted_caps(m, Path(path).parent)
        agents.append(SimpleNamespace(manifest=m, endpoint=f"{m.agent_id}:0"))
    builtin = ToolRegistry().manifest
    agents = [agent for agent in agents
              if agent.manifest.agent_id != builtin.agent_id]
    agents.append(SimpleNamespace(manifest=builtin, endpoint="tool://builtin"))
    if not include_edge:
        return agents
    # 必须复用生产注册的**整份** manifest。旧代码手抄 capability 名单并把
    # route_hints/verification/描述清空，导致生产新增门锁极性纠偏后，意图 gate
    # 永远看不见那两条规则；所谓「生产同构」只同构了 intent 名字。
    from capabilities import build_edge_manifests
    for manifest in build_edge_manifests():
        agents.append(SimpleNamespace(
            manifest=manifest,
            endpoint=f"edge://{manifest.agent_id}"))
    return agents


def _synth_admitted_caps(manifest, agent_dir: Path) -> None:
    """`capabilities: []` + 有 `servers.yaml` 的 Agent（mcp-bridge）：按**同一份准入清单**
    离线合成能力，与运行时 `_sync_capabilities()` 同源。

    为什么必须补这一步：mcp-bridge 的能力是启动期从 servers.yaml 合成的，静态 manifest 里
    `capabilities: []`——于是它的 `shop.order` route_hint 在离线评测里**结构性不可证伪**：
    RouteHintEngine 补出的步会因「intent 不在能力集」被 `_validated_steps` 丢掉，语料写了
    也永远不通过。`hint_retirement` 干跑盘点报「32 条里唯一没有命中语料的是 mcp-bridge#0」，
    根因就在这里——**不是没人写语料，是写了也测不了**。
    只读 YAML、不连任何 MCP server（准入清单本身就是唯一真相源，运行时的握手只做版本与
    schema 校验，不改变能力集合）。"""
    if list(getattr(manifest, "capabilities", []) or []):
        return
    servers = agent_dir / "servers.yaml"
    if not servers.is_file():
        return
    try:
        sys.path.insert(0, str(agent_dir / "src"))
        from admission import load_servers  # type: ignore
        try:
            from admission import load_local_capabilities  # type: ignore
        except ImportError:
            load_local_capabilities = None
        from cockpit.agent.v1 import agent_pb2
        loaded = {spec.id: spec for spec in load_servers(str(servers))}
        local_loaded = (load_local_capabilities(str(servers))
                        if load_local_capabilities is not None else None)
    except Exception:
        loaded = {}
        local_loaded = None
    try:
        from cockpit.agent.v1 import agent_pb2

        raw = yaml.safe_load(servers.read_text(encoding="utf-8")) or {}
        caps = []
        seen: set[str] = set()
        for server in raw.get("servers") or []:
            if not isinstance(server, dict):
                continue
            spec = loaded.get(str(server.get("id") or ""))
            declared_tools = {
                str(row.get("name") or "")
                for row in (server.get("tools") or [])
                if isinstance(row, dict) and row.get("name")
            }
            # ToolSpec 已存在很久，优先消费 loader 的规范化对象；loader 不可用时才读 YAML。
            tool_rows = getattr(spec, "tools", None) if spec is not None else None
            if tool_rows is None:
                tool_rows = server.get("tools") or []
            # WorkflowSpec 正在增量接入。旧 ServerSpec 没有 workflows 属性，此时安全回退
            # 到同一准入 YAML；新 loader 一旦拥有该字段，就以 loader 的最终结果为准。
            workflow_rows = (getattr(spec, "workflows", None)
                             if spec is not None else None)
            if workflow_rows is None:
                workflow_rows = server.get("workflows") or []
            for row, is_workflow in (
                    *((item, False) for item in (tool_rows or [])),
                    *((item, True) for item in (workflow_rows or []))):
                value = (lambda key, default=None:
                         row.get(key, default) if isinstance(row, dict)
                         else getattr(row, key, default))
                intent = str(value("intent", "") or "")
                if not intent or intent in seen or not bool(value("expose", True)):
                    continue
                required = {str(name) for name in (value("required_tools", []) or [])}
                handler = str(value("handler", "") or "")
                if is_workflow and (
                        not handler or not required or
                        not required <= declared_tools):
                    continue
                name = str(value("name", "") or intent)
                desc = str(value("description", "") or f"{server.get('id')} 的 {name}")
                if bool(server.get("demo", False)):
                    desc += "（演示商户，不产生真实交易）"
                caps.append(agent_pb2.Capability(
                    intent=intent, description=desc,
                    slots=list(value("slots", []) or []),
                    examples=list(value("examples", []) or []),
                    require_confirm=bool(value("require_confirm", False)
                                         or value("write", False))))
                seen.add(intent)
        local_rows = (local_loaded if local_loaded is not None
                      else raw.get("local_capabilities") or [])
        for row in local_rows:
            value = (lambda key, default=None:
                     row.get(key, default) if isinstance(row, dict)
                     else getattr(row, key, default))
            intent = str(value("intent", "") or "")
            if not intent or intent in seen or not bool(value("expose", True)):
                continue
            caps.append(agent_pb2.Capability(
                intent=intent,
                description=str(value("description", "") or ""),
                slots=list(value("slots", []) or []),
                examples=list(value("examples", []) or []),
                require_confirm=bool(value("require_confirm", False))))
            seen.add(intent)
        manifest.capabilities.extend(caps)
    except Exception:
        pass          # 合成失败不该带崩评测：退化成「该 Agent 无能力」，与今天行为一致


def known_intents() -> set[str]:
    """真实存在的 intent 全集 = `load_agents()` 实际准入的能力。

    其中既含端侧车控/媒体，也含生产 `ToolRegistry` 注册的 builtin-tools。刻意不再直接扫
    静态 manifest YAML：mcp-bridge 的能力由 `servers.yaml` 启动期合成，builtin-tools 则根本
    没有静态 manifest；漏掉任一动态来源都会让 active-intent 分母悄悄变小。统一复用
    `load_agents()`，让 catalog、L0 盘点与生产 inventory 不漂移。
    """
    return {
        str(cap.intent)
        for agent in load_agents(include_edge=True)
        for cap in (getattr(agent.manifest, "capabilities", None) or [])
        if getattr(cap, "intent", "")
    }


def make_llm_fns(caller: str, temperature: float = 0.3, timeout: int = 45,
                 model: str = ""):
    """→ (llm_fn, llm_tool_fn)，直连 llm-gateway gRPC（同生产的 submit_plan 通道）。

    model：档位 pin（`@primary`/`@fast`）。留空＝跟随网关默认，与生产规划轮同档——
    影子分诊靠的正是这一个变量的差分，**别的都必须逐字相同**。"""
    import grpc
    from google.protobuf.json_format import MessageToDict
    from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
    from orchestrator.cloud.clients import Clients
    ch = grpc.insecure_channel(os.getenv("LLM_GATEWAY_ADDR", "localhost:50052"))
    stub = llm_pb2_grpc.LLMGatewayStub(ch)

    def _req(messages):
        r = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role=m["role"], content=m["content"])
                      for m in messages], temperature=temperature, max_tokens=800)
        r.meta["caller_service"] = caller
        if model:
            r.model = model
        return r

    async def _llm(messages: list[dict]) -> str:
        return stub.Complete(_req(messages), timeout=timeout).content

    async def _llm_tools(messages: list[dict], tools: dict):
        req = _req(messages)
        req.tools.update(tools or {})
        resp = stub.Complete(req, timeout=timeout)
        calls = []
        if resp.HasField("tool_calls"):
            for tc in (MessageToDict(resp.tool_calls).get("tool_calls") or []):
                if isinstance(tc, dict) and tc.get("name"):
                    calls.append({"id": tc.get("id") or "", "name": tc["name"],
                                  "arguments": Clients._destruct_nums(
                                      tc.get("arguments") or {})})
        return resp.content, calls

    return _llm, _llm_tools


async def _registry_empty(query: str, top_k: int = 1):
    return []


def make_builder(caller: str, temperature: float = 0.3,
                 timeout: int = 45, model: str = ""):
    """现有两参数调用逐字不变；新增 timeout/model 只在显式传入时改变行为。"""
    from orchestrator.cloud.planning import PlanBuilder
    llm, llm_tools = make_llm_fns(caller, temperature, timeout, model)
    return PlanBuilder(llm_fn=llm, registry_fn=_registry_empty, llm_tool_fn=llm_tools)


async def warm_exemplars() -> int:
    """范例向量预热（评测进程活不到后台预热跑完；不预热＝只测了词法档）。"""
    from orchestrator.cloud import exemplars as ex
    if ex.mode() == "off" or ex.retrieval_mode() != "hybrid":
        return 0
    return await ex.default_store().warm_blocking()
