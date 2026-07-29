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

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "gen" / "python"), str(_ROOT / "orchestrator" / "edge")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def load_agents(include_edge: bool = True) -> list:
    """真实 `agents/*/manifest.yaml` + 端侧车控/媒体 → ResolvedAgent 形状。

    与生产 catalog 同构是刻意的：少了 edge 车控，implicit-vehicle-control 之类的
    policy/范例就失去了断言对象；少了某个 manifest，它的 route_hints 也不参与评测。
    新增 Agent 落 manifest 自动纳入——不硬编码列表。"""
    from agents._sdk.manifest import load_manifest
    agents = []
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        m = load_manifest(path)
        agents.append(SimpleNamespace(manifest=m, endpoint=f"{m.agent_id}:0"))
    if not include_edge:
        return agents
    from edge_agents_mod.media import MEDIA_INTENTS
    from edge_agents_mod.vehicle import VEHICLE_INTENTS
    for aid, intents, perm in (("edge-vehicle", VEHICLE_INTENTS, "vehicle.control"),
                               ("edge-media", MEDIA_INTENTS, "media.control")):
        caps = [SimpleNamespace(intent=i, description="", slots=[], examples=[],
                                heavy=False, require_confirm=False)
                for i in sorted(intents)]
        agents.append(SimpleNamespace(
            manifest=SimpleNamespace(
                agent_id=aid, kind="edge_fast", deployment="edge", category="core",
                trust_level="system", latency_budget_ms=800,
                requires_permissions=[perm], context_scopes=[], route_hints=[],
                capabilities=caps),
            endpoint=f"edge://{aid}"))
    return agents


def known_intents() -> set[str]:
    """真实存在的 intent 全集：manifests ∪ 端侧车控/媒体（expect_* typo 守卫共用）。"""
    import yaml
    intents: set[str] = set()
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        m = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for c in m.get("capabilities") or []:
            if c.get("intent"):
                intents.add(str(c["intent"]))
    from edge_agents_mod.media import MEDIA_INTENTS
    from edge_agents_mod.vehicle import VEHICLE_INTENTS
    return intents | VEHICLE_INTENTS | MEDIA_INTENTS


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


def make_builder(caller: str, temperature: float = 0.3):
    from orchestrator.cloud.planning import PlanBuilder
    llm, llm_tools = make_llm_fns(caller, temperature)
    return PlanBuilder(llm_fn=llm, registry_fn=_registry_empty, llm_tool_fn=llm_tools)


async def warm_exemplars() -> int:
    """范例向量预热（评测进程活不到后台预热跑完；不预热＝只测了词法档）。"""
    from orchestrator.cloud import exemplars as ex
    if ex.mode() == "off" or ex.retrieval_mode() != "hybrid":
        return 0
    return await ex.default_store().warm_blocking()
