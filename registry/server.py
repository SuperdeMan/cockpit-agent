"""Agent Registry gRPC 服务。"""
from __future__ import annotations

import inspect

from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc

from registry.store import Store


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

        # P1 语义路由：关键词匹配无结果或低分时，尝试 pgvector 语义检索
        if request.query and hasattr(self.store, "resolve_semantic"):
            best_score = max((s for _, s in recs), default=0)
            if best_score < 0.5:
                try:
                    semantic_recs = await self.store.resolve_semantic(
                        request.query, top_k=request.top_k or 3, granted=granted)
                    if semantic_recs:
                        # 合并结果，去重（语义结果追加到末尾）
                        seen = {r.manifest.agent_id for r, _ in recs}
                        for r, s in semantic_recs:
                            if r.manifest.agent_id not in seen:
                                recs.append((r, s))
                                seen.add(r.manifest.agent_id)
                except Exception as e:
                    print(f"[registry] semantic resolve failed: {e}", flush=True)

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
