"""Memory gRPC 服务。"""
from __future__ import annotations

import json
import logging

from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc

from store import MemoryStore

logger = logging.getLogger("memory.server")


class MemoryServicer(memory_pb2_grpc.MemoryServicer):
    def __init__(self):
        self.store = MemoryStore()

    async def GetContext(self, request, context):
        values = await self.store.get_context(
            request.session_id, request.user_id, request.vehicle_id, list(request.scopes))
        return memory_pb2.GetContextResponse(values=values)

    async def AppendTurn(self, request, context):
        await self.store.append_turn(request.session_id, request.role, request.text)
        return memory_pb2.AppendTurnResponse(ok=True)

    async def GetSession(self, request, context):
        turns = await self.store.get_session(request.session_id, request.last_n or 6)
        return memory_pb2.GetSessionResponse(turns=[
            memory_pb2.Turn(role=t["role"], text=t["text"], ts=t["ts"]) for t in turns
        ])

    async def UpsertProfile(self, request, context):
        """写用户画像字段（如常用地点 places）。value_json 非法则拒绝，不写脏数据。"""
        if not request.user_id or not request.key:
            return memory_pb2.UpsertProfileResponse(ok=False)
        try:
            value = json.loads(request.value_json) if request.value_json else None
        except json.JSONDecodeError as e:
            logger.warning("UpsertProfile bad json (%s/%s): %s",
                           request.user_id, request.key, e)
            return memory_pb2.UpsertProfileResponse(ok=False)
        await self.store.upsert_profile(request.user_id, request.key, value)
        return memory_pb2.UpsertProfileResponse(ok=True)
