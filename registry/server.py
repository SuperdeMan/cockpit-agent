"""Agent Registry gRPC 服务。"""
from __future__ import annotations

from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc

from registry.store import Store


class RegistryServicer(registry_pb2_grpc.RegistryServicer):
    def __init__(self):
        self.store = Store()

    async def Register(self, request, context):
        lease = self.store.register(request.manifest, request.endpoint)
        print(f"[registry] + {request.manifest.agent_id} @ {request.endpoint} "
              f"({len(request.manifest.capabilities)} caps)", flush=True)
        return registry_pb2.RegisterResponse(ok=True, lease_id=lease)

    async def Deregister(self, request, context):
        self.store.deregister(request.agent_id)
        print(f"[registry] - {request.agent_id}", flush=True)
        return registry_pb2.DeregisterResponse(ok=True)

    async def ResolveAgents(self, request, context):
        recs = self.store.resolve(
            request.intent, request.query, request.top_k,
            list(request.granted_permissions))
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
