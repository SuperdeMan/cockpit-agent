"""BaseAgent: 业务 Agent 的基类。子类只需实现 handle()。

Phase 1：注入 AgentClient（跨 Agent 协作）。
护栏跨进程修复：server.Execute 在调 handle 前把 request.meta 中的
call_depth/call_stack 写入 _current_meta contextvar，agents 属性读取
它构造正确深度的 AgentClient，使 MAX_DEPTH/环检测跨进程生效。

ws2 P0：注入 RegistryClient，AgentClient 经 Registry 动态解析 endpoint。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from runtime import context_access, memory_read

from .clients import LLMClient, MemoryClient, RegistryClient
from .ledger import TaskLedger
from .manifest import load_manifest
from .result import AgentResult
# contextvar 抽到 _ctx（中立模块），解 base↔clients 循环依赖；此处保留同名再导出向后兼容。
from ._ctx import _current_meta, set_current_meta as _set_current_meta, get_current_meta  # noqa: F401


@dataclass
class IntentView:
    """传给业务的意图视图（已从 proto 解包）。"""
    name: str
    slots: dict[str, str]
    raw_text: str
    confidence: float


class Context:
    """会话上下文句柄。按需向 Memory 拉取声明的 scopes（隐私最小化）。"""
    def __init__(self, session_id: str, user_id: str, vehicle_id: str, memory: MemoryClient,
                 occupant_id: str = "primary", *, meta: dict | None = None):
        self.session_id = session_id
        self.user_id = user_id
        self.vehicle_id = vehicle_id
        # M4 P4：本轮说话人（声纹）。默认 "primary"=P4 之前的行为。记忆读写自动按它隔离。
        # **只用于记忆归属**——权限判定在编排层按 granted_scopes，与本字段无关（RFC §6.1 红线）。
        self.occupant_id = occupant_id or "primary"
        self._memory = memory
        authority = dict(meta if meta is not None else get_current_meta() or {})
        self.granted_permissions = [s.strip() for s in authority.get("granted_scopes", "").split(",") if s.strip()]
        self.prefs = {"memory_enabled": authority.get("memory_enabled", "true")}
        # CA2-15 S1b：云端在本回合读记忆之前取的 owner 代际（服务端自有 meta）。写记忆原样带回，
        # 回合进行中记忆被删过 ⇒ Memory 拒收。
        self.memory_epoch = str(authority.get("memory_epoch") or "")
        # CA2-15 S2：云端按声音证明算出的读取投影（服务端自有 meta）。车机免唤醒语音没认出说话人时为
        # "normal_only"——这一轮只读得到普通偏好；读记忆一律原样带给 Memory，由 Memory 执行。
        self.memory_projection = str(authority.get("memory_projection") or "")
        self.read_states = {}
        self._projection_meta = {}
        self._read_disabled = set()
        self._read_binding = (self.session_id, self.user_id, self.vehicle_id, self.occupant_id)
        self._read_domains = {d for d in context_access.READ_SCOPES if context_access.allowed(self, d)}

    def _can_read(self, domain="profile"):
        bound = (self.session_id, self.user_id, self.vehicle_id, self.occupant_id) == self._read_binding
        allowed = domain in self._read_domains and domain not in self._read_disabled and bound and context_access.allowed(self, domain)
        if not allowed:
            self._read_disabled.add(domain)
            self.read_states[domain] = memory_read.OFF
        return allowed


    def vehicle_reading(self, key: str):
        """车况读数连同时效与来源（CA2-19 S1，`runtime.vehicle_reading.Reading`）。

        `key` 是车况信号名（battery / speed_kmh / gear）。没有读权限、没读到或已过期 ⇒ `value is None`——
        调用方不得自己补缺省值；`fetch` 只给裸数值，要说出口或上卡片的读数用这里。
        """
        from runtime import vehicle_reading
        if not self._can_read("vehicle_state"):
            return vehicle_reading.Reading(key)
        reading = vehicle_reading.from_projection(self._projection_meta, self.vehicle_id, key)
        self.read_states["vehicle_state"] = memory_read.FOUND if reading.known else memory_read.UNAVAILABLE
        return reading

    async def fetch(self, *scopes: str) -> dict:
        # The legacy KV interface remains a dict; read_states keeps denial/off
        # distinct from an authoritative empty result.
        values = {}
        vehicle_keys = {"vehicle.battery": "battery", "vehicle.speed": "speed_kmh", "vehicle.gear": "gear"}
        if any(s.startswith("vehicle.") for s in scopes):
            if self._can_read("vehicle_state"):
                observed, _ = context_access.vehicle_projection(self._projection_meta, self.vehicle_id)
                values = {s: str(observed[vehicle_keys[s]]) for s in scopes
                          if s in vehicle_keys and vehicle_keys[s] in observed}
                self.read_states["vehicle_state"] = memory_read.FOUND if values else memory_read.UNAVAILABLE
        requested = [s for s in scopes if s.startswith("profile.") and self._can_read("profile")]
        if not requested:
            return values
        profile = await self._memory.get_context(
            self.session_id, self.user_id, self.vehicle_id, requested,
            occupant_id=self.occupant_id, projection=self.memory_projection)
        if not self._can_read():
            return {}
        self.read_states["profile"] = memory_read.read_state(profile)
        values.update({k: v for k, v in profile.items() if k in requested})
        return values

    async def history(self, last_n: int = 6) -> list[dict]:
        if not self._can_read():
            return []
        values = await self._memory.get_session(
            self.session_id, last_n, user_id=self.user_id, occupant_id=self.occupant_id)
        if not self._can_read():
            return []
        self.read_states["profile"] = memory_read.read_state(values)
        return values

    async def save_profile(self, key: str, value) -> bool:
        """写用户画像字段（如常用地点 places）。value 为可 JSON 序列化对象。
        无 user_id 时静默跳过（PoC 单用户由网关注入 user_id）。"""
        if not self.user_id:
            return False
        import json
        return await self._memory.upsert_profile(
            self.user_id, key, json.dumps(value, ensure_ascii=False),
            occupant_id=self.occupant_id, memory_epoch=self.memory_epoch)

    async def save_shared_state(self, key: str, value) -> bool:
        """写跨 Agent 会话状态（Agent 无状态化，临时会话态落 profile KV）。

        key 用 `agents._sdk.shared_state` 常量（NEWS_ACTIVE/RESEARCH_ACTIVE/TRIP_ACTIVE），
        权威登记见 `docs/conventions.md`「跨 Agent 状态键」。写走 profile；读经 load_shared_state。
        """
        return await self.save_profile(key, value)

    async def load_shared_state(self, key: str):
        """读跨 Agent 会话状态（写走 `save_profile(<key>)`、读经 `profile.<key>` 命名空间——
        本封装消除该读写前缀不对称）。返回存储的原始值（可能是 JSON 字符串，调用方按需 json.loads）；
        无 / 失败 → None。"""
        try:
            vals = await self.fetch(f"profile.{key}")
        except Exception as e:
            import logging
            logging.getLogger("agent.sdk").debug("load_shared_state %s skipped: %s", key, e)
            return None
        return vals.get(f"profile.{key}") if vals else None

    async def recall(self, query: str = "", *, scopes: list[str] | None = None,
                     kinds: list[str] | None = None, top_k: int = 5,
                     predicate_prefix: str = "", min_score: float = 0.0,
                     min_confidence: float = 0.0, max_age_days: int = 0,
                     subject: str = "") -> list[dict]:
        """语义召回与当前问题相关的偏好/事件（如点餐前取口味）。无 user_id 返回空。
        精确画像读取传 predicate_prefix（如 "place." "taste."）走谓词精确而非向量。
        subject 非空=只取「关于该人」的记忆（G6，如 subject="老婆" 取老婆的口味）。"""
        if not self._can_read():
            return []
        values = await self._memory.recall(
            self.user_id, query, occupant_id=self.occupant_id, scopes=scopes,
            kinds=kinds, top_k=top_k,
            predicate_prefix=predicate_prefix, min_score=min_score,
            min_confidence=min_confidence, max_age_days=max_age_days, subject=subject,
            projection=self.memory_projection)
        return values if self._can_read() else []

    async def recall_read(self, query: str = "", **kw) -> tuple[list[dict], str]:
        """`recall` 的三态版（批 5 W17）→ `(items, state)`；无 user_id 返回 `([], "off")`。
        state ∈ `runtime.memory_read`：found / none / unavailable / off。"""
        from runtime import memory_read
        if not self._can_read():
            return [], memory_read.OFF
        kw.setdefault("occupant_id", self.occupant_id)
        kw["projection"] = self.memory_projection        # 不由调用方决定
        values, state = await self._memory.recall_read(self.user_id, query, **kw)
        if not self._can_read():
            return [], memory_read.OFF
        self.read_states["profile"] = state
        return values, state

    async def resolve_person_place(self, person_word: str) -> dict | None:
        """人称词 → 常去地点（M2 记忆图谱 P1 关系边一跳，「去接孩子放学」）。

        返回 `{person, place, object_ref}`；**查不到或有歧义返回 None**——调用方必须
        诚实追问，绝不用相似度猜（导航到错学校比查不到更糟）。
        """
        if not self._can_read() or not person_word:
            return None
        try:
            value = await self._memory.resolve_person_place(
                self.user_id, person_word, occupant_id=self.occupant_id,
                projection=self.memory_projection)
            return value if self._can_read() else None
        except Exception as e:
            import logging
            logging.getLogger("agent.sdk").debug("resolve_person_place skipped: %s", e)
            return None

    async def remember(self, text: str, *, predicate: str = "", kind: str = "semantic",
                       scope: str = "", value=None, provenance: str = "user_stated",
                       confidence: float = 1.0, privacy_level: str = "normal",
                       vehicle_id: str = "", memory_level: str = "user",
                       expires_at: int = 0, review_status: str = "user_confirmed",
                       source_turn_ids: str = "") -> bool:
        """显式写一条记忆。无 user_id 或空文本时静默跳过。
        家/公司等高敏地点用 privacy_level="highly_sensitive"；车级偏好传 vehicle_id+memory_level。"""
        if not self.user_id or not text:
            return False
        import json
        item = {"user_id": self.user_id, "occupant_id": self.occupant_id,
                "kind": kind, "predicate": predicate,
                "text": text, "scope": scope, "provenance": provenance,
                "confidence": confidence, "privacy_level": privacy_level,
                "vehicle_id": vehicle_id, "memory_level": memory_level,
                "expires_at": expires_at, "review_status": review_status,
                "source_turn_ids": source_turn_ids,
                # 证据按场合（会话）计：同一会话里重复写不加分，换了会话再写才算又一次（CA2-15 遗留）。
                "source_session": self.session_id,
                "value_json": json.dumps(value, ensure_ascii=False) if value is not None else ""}
        ids = await self._memory.remember([item], memory_epoch=self.memory_epoch)
        return bool(ids)


class BaseAgent(ABC):
    def __init__(self, manifest_path: str):
        self.manifest = load_manifest(manifest_path)
        self.llm = LLMClient()
        self.memory = MemoryClient()
        self.registry = RegistryClient()  # ws2: 供 AgentClient 动态解析 endpoint
        # M2 P0：跨轮持久任务账本。构造只读 env（不建连），首次调用惰性 init；
        # 无 POSTGRES_DSN / 无 asyncpg → 全部操作静默返回空，Agent 照常干活（诚实降级）。
        # 接入长任务 = 调 open/heartbeat/close 三个函数，编排核心零改动（RFC §2.1）。
        self.ledger = TaskLedger()
        # 跨 Agent 协作客户端（延迟初始化，避免循环依赖）
        self._agents = None
        # 跨 Agent 调用的 channel 缓存：按 endpoint 复用 keepalive 连接，
        # 避免每次协作调用新建且不关闭导致的连接泄漏（AgentClient 每请求新建，故缓存在此长生命周期对象上）
        self._agent_channels: dict = {}

    @property
    def agents(self):
        """跨 Agent 协作客户端。从当前请求 meta 读取 call_depth/call_stack，
        使 MAX_DEPTH/环检测跨进程生效。ws2: 注入 RegistryClient。"""
        from .agent_client import AgentClient
        meta = _current_meta.get()
        if meta is not None:
            depth = int(meta.get("call_depth", 0))
            stack = [s for s in meta.get("call_stack", "").split(",") if s]
            return AgentClient(caller=self, call_depth=depth, call_stack=stack,
                               registry=self.registry, parent_meta=meta)
        # 无 meta（本地测试 / 非 gRPC 调用）→ 默认深度 0
        if self._agents is None:
            self._agents = AgentClient(caller=self, registry=self.registry)
        return self._agents

    @abstractmethod
    async def handle(self, intent: IntentView, ctx: Context, meta: dict) -> AgentResult:
        """处理一个意图，返回 AgentResult。这是业务唯一必须实现的方法。"""
        ...

    async def handle_stream(self, intent: IntentView, ctx: Context, meta: dict):
        """流式执行。默认调 handle 并包成单个 final 事件；需要流式话术的 Agent 可重写。
        yield 形如 ("speech", str) 或 ("final", AgentResult)。
        """
        res = await self.handle(intent, ctx, meta)
        yield ("final", res)

    async def on_start(self) -> None:
        """可选生命周期钩子：serve() 启动 gRPC 服务后调用一次（后台任务）。

        响应式 Agent（如订阅 NATS 做主动播报的 road-safety）在此启动后台循环；
        默认无操作。失败由 serve() 静默吞掉，不影响 Agent 正常请求-响应服务。
        """
        return None
