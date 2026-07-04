"""Agent Registry gRPC 服务。"""
from __future__ import annotations

import inspect

from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc

from registry.store import Store, SEMANTIC_MIN_SIM, SEMANTIC_PROMOTE_SIM


class RegistryServicer(registry_pb2_grpc.RegistryServicer):
    def __init__(self, store: Store | None = None):
        self.store = store or Store()

    async def Register(self, request, context):
        result = self.store.register(request.manifest, request.endpoint)
        lease = await result if inspect.isawaitable(result) else result
        print(f"[registry] + {request.manifest.agent_id} @ {request.endpoint} "
              f"({len(request.manifest.capabilities)} caps)", flush=True)
        return registry_pb2.RegisterResponse(ok=True, lease_id=lease)

    async def Deregister(self, request, context):
        result = self.store.deregister(request.agent_id)
        if inspect.isawaitable(result):
            await result
        print(f"[registry] - {request.agent_id}", flush=True)
        return registry_pb2.DeregisterResponse(ok=True)

    async def ResolveAgents(self, request, context):
        granted = list(request.granted_permissions)
        recs = self.store.resolve(
            request.intent, request.query, request.top_k, granted)

        # R4.1 语义重排：无精确 intent 命中（关键词 best<1.0）且有 query、store 支持语义时，
        # 总是跑语义（ResolveAgents 是 planning._fallback 降级路径，延迟可接受；query 向量有缓存）。
        # 语义足够自信（top sim ≥ SEMANTIC_PROMOTE_SIM）→ 语义排序在前，纠正关键词字符打分对中文
        # 的噪声 top-1（实测纯语义 20/20 全对）；否则语义（过 SEMANTIC_MIN_SIM 下限）去重追加在
        # 关键词之后（保守，关键词 top 不变，仍修 §1.1 无下限追加 bug）。无 embedding 源/PG 不可达
        # → resolve_semantic 返回 [] → recs 原样（关键词路径，byte 一致，nightly 纯 mock 零感知）。
        best_kw = max((s for _, s in recs), default=0)
        if request.query and best_kw < 1.0 and hasattr(self.store, "resolve_semantic"):
            try:
                sem_recs = await self.store.resolve_semantic(
                    request.query, top_k=request.top_k or 3, granted=granted)
            except Exception as e:
                print(f"[registry] semantic resolve failed: {e}", flush=True)
                sem_recs = []
            # SEMANTIC_MIN_SIM 下限双保险：先统一过一道（防未来弱向量源绕开 store 过滤，
            # 修 §1.1「无相似度下限地追加」bug 的另一半）——提升/追加两分支都受保护。
            sem_recs = [(r, s) for r, s in sem_recs if s >= SEMANTIC_MIN_SIM]
            if sem_recs:
                sem_ids = {r.manifest.agent_id for r, _ in sem_recs}
                if sem_recs[0][1] >= SEMANTIC_PROMOTE_SIM:
                    # 语义自信：语义排序在前 + 关键词残余去重接后
                    recs = sem_recs + [(r, s) for r, s in recs
                                       if r.manifest.agent_id not in sem_ids]
                else:
                    # 语义不自信：关键词在前 + 语义去重追加
                    seen = {r.manifest.agent_id for r, _ in recs}
                    for r, s in sem_recs:
                        if r.manifest.agent_id not in seen:
                            recs.append((r, s))
                            seen.add(r.manifest.agent_id)

        if request.top_k:
            recs = recs[:request.top_k]
        return registry_pb2.ResolveResponse(agents=[
            registry_pb2.ResolvedAgent(manifest=r.manifest, endpoint=r.endpoint, score=s)
            for r, s in recs
        ])

    async def ListAgents(self, request, context):
        recs = self.store.list(request.category)
        return registry_pb2.ListResponse(agents=[
            registry_pb2.ResolvedAgent(manifest=r.manifest, endpoint=r.endpoint, score=1.0)
            for r in recs
        ])
