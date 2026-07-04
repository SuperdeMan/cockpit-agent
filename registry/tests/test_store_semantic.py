"""registry PgStore 语义路由单测（R4.1 P0，不依赖真实 PostgreSQL / llm-gateway）。

覆盖设计 §3.3 要求：mock embed 的 per-cap 写入 / text_hash 去重 / SEMANTIC_MIN_SIM 下限
过滤 / 无源降级；外加探测三态收敛与「llm-gateway 晚于 registry 就绪」的按需重探兜底。

用 FakeConn/FakePool 模拟 asyncpg（只记录 execute、按 SQL 回放 fetch），embedding 经
monkeypatch `_llm_embed`/`_embed` 注入确定性向量——完全离线、确定性、秒级。
"""
import asyncio
import hashlib
from types import SimpleNamespace

from cockpit.agent.v1 import agent_pb2
from registry.server import RegistryServicer
from registry.store import PgStore, Record, EMBED_DIM, SEMANTIC_MIN_SIM


# ── fakes ────────────────────────────────────────────────────────────────

class FakeConn:
    """记录所有 execute；按 SQL 片段回放 fetch（仅 agent_capability_vec 既有行查询）。"""

    def __init__(self, cap_rows=None):
        self._cap_rows = cap_rows or {}      # {agent_id: [{"intent":..,"text_hash":..}, ...]}
        self.executed = []                   # [(sql, args), ...]

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetch(self, sql, *args):
        if "FROM agent_capability_vec WHERE agent_id" in sql:
            return self._cap_rows.get(args[0], [])
        return []

    async def fetchrow(self, sql, *args):
        return None

    # 便捷断言辅助
    def inserts(self):
        return [(sql, args) for sql, args in self.executed
                if "INSERT INTO agent_capability_vec" in sql]

    def deletes(self):
        return [(sql, args) for sql, args in self.executed
                if "DELETE FROM agent_capability_vec" in sql]


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


def _cap(intent, desc="", examples=()):
    return agent_pb2.Capability(intent=intent, description=desc, examples=list(examples))


def _manifest(agent_id, caps):
    return agent_pb2.AgentManifest(agent_id=agent_id, capabilities=caps)


def _vec(seed=0.1):
    return [seed] * EMBED_DIM


def _hash_of(cap):
    return hashlib.sha256(PgStore._capability_text(cap).encode()).hexdigest()


def _llm_store(conn, source="llm", probe_done=True):
    """构造一个「PG 就绪 + embedding 源已定论」的 PgStore（不触真实 IO）。"""
    store = PgStore("postgresql://fake")
    store._pg_ok = True
    store._pool = FakePool(conn)
    store._embed_source = source
    store._embed_probe_done = probe_done

    async def fake_llm_embed(texts, timeout):
        return [_vec() for _ in texts]

    store._llm_embed = fake_llm_embed
    return store


# ── _capability_text ─────────────────────────────────────────────────────

def test_capability_text_concats_intent_desc_examples():
    txt = PgStore._capability_text(_cap("info.weather", "查天气", ["今天天气", "会下雨吗"]))
    assert txt == "info.weather 查天气 今天天气 会下雨吗"
    # 空字段不产生多余空格
    assert PgStore._capability_text(_cap("a.b")) == "a.b"


# ── 探测三态收敛 ───────────────────────────────────────────────────────────

def test_probe_correct_dim_sets_llm_source():
    store = PgStore("postgresql://fake")
    store._llm_addr = "llm-gateway:50052"

    async def ok(texts, timeout):
        return [_vec()]
    store._llm_embed = ok

    asyncio.run(store._probe_embedder())
    assert store._embed_source == "llm"
    assert store._embed_probe_done is True


def test_probe_wrong_dim_gives_up_no_reprobe():
    """llm-gateway 无 embed key 回退 384 维 mock → 定论无源、停止重探（nightly 零后续感知）。"""
    store = PgStore("postgresql://fake")
    store._llm_addr = "llm-gateway:50052"

    async def mockdim(texts, timeout):
        return [[0.1] * (EMBED_DIM + 7)]     # 维度不符
    store._llm_embed = mockdim

    asyncio.run(store._probe_embedder())
    assert store._embed_source is None
    assert store._embed_probe_done is True   # 定论：不再重探


def test_probe_unreachable_keeps_reprobe_open():
    """llm-gateway 暂不可达 → 不判死刑，probe_done 保持 False 以便按需重探。"""
    store = PgStore("postgresql://fake")
    store._llm_addr = "llm-gateway:50052"

    async def down(texts, timeout):
        return None
    store._llm_embed = down

    asyncio.run(store._probe_embedder())
    assert store._embed_source is None
    assert store._embed_probe_done is False  # 可重探


def test_probe_no_addr_disables_silently():
    store = PgStore("postgresql://fake")
    store._llm_addr = ""
    asyncio.run(store._probe_embedder())
    assert store._embed_source is None
    assert store._embed_probe_done is True


# ── per-capability 增量写入 ────────────────────────────────────────────────

def test_embed_capabilities_writes_one_row_per_capability():
    conn = FakeConn(cap_rows={})            # 无既有向量 → 全部新写
    store = _llm_store(conn)
    m = _manifest("info", [_cap("info.weather", "查天气"), _cap("info.stock", "查股价")])

    asyncio.run(store._embed_capabilities(m))

    inserts = conn.inserts()
    assert len(inserts) == 2                 # 每 capability 一行
    written_intents = {args[1] for _sql, args in inserts}
    assert written_intents == {"info.weather", "info.stock"}
    assert not conn.deletes()                # 无 stale 删除


def test_embed_capabilities_no_source_writes_nothing():
    """无 embedding 源：静默不写（语义路由缺席，行为回落关键词路径）。"""
    conn = FakeConn(cap_rows={})
    store = _llm_store(conn, source=None, probe_done=True)
    m = _manifest("info", [_cap("info.weather", "查天气")])

    asyncio.run(store._embed_capabilities(m))
    assert conn.executed == []               # 连 fetch 都不发（早退）


# ── text_hash 去重 ─────────────────────────────────────────────────────────

def test_embed_capabilities_dedup_skips_unchanged():
    weather = _cap("info.weather", "查天气")
    stock = _cap("info.stock", "查股价")
    # 既有行：info.weather 的 text_hash 与当前一致 → 跳过；info.stock 不存在 → 新写。
    conn = FakeConn(cap_rows={"info": [{"intent": "info.weather", "text_hash": _hash_of(weather)}]})
    store = _llm_store(conn)

    embedded = []

    async def spy_embed(text, timeout):
        embedded.append(text)
        return _vec()
    store._embed = spy_embed

    asyncio.run(store._embed_capabilities(_manifest("info", [weather, stock])))

    assert embedded == [PgStore._capability_text(stock)]   # 只 embed 了变化的那条
    inserts = conn.inserts()
    assert {args[1] for _s, args in inserts} == {"info.stock"}


def test_embed_capabilities_changed_hash_reembeds():
    weather = _cap("info.weather", "查天气")
    # 既有行 hash 与当前不一致（manifest 描述变了）→ 必须重 embed。
    conn = FakeConn(cap_rows={"info": [{"intent": "info.weather", "text_hash": "stale-hash"}]})
    store = _llm_store(conn)

    embedded = []

    async def spy_embed(text, timeout):
        embedded.append(text)
        return _vec()
    store._embed = spy_embed

    asyncio.run(store._embed_capabilities(_manifest("info", [weather])))
    assert embedded == [PgStore._capability_text(weather)]


def test_embed_capabilities_cascades_stale_intents():
    """manifest 删掉的 capability → 级联删该 intent 行。"""
    weather = _cap("info.weather", "查天气")
    conn = FakeConn(cap_rows={"info": [
        {"intent": "info.weather", "text_hash": _hash_of(weather)},
        {"intent": "info.legacy", "text_hash": "whatever"},   # 新 manifest 里没有 → stale
    ]})
    store = _llm_store(conn)

    asyncio.run(store._embed_capabilities(_manifest("info", [weather])))

    deletes = conn.deletes()
    assert len(deletes) == 1
    _sql, args = deletes[0]
    assert args[0] == "info" and "info.legacy" in args[1]


# ── 启动时序：按需重探兜底 ─────────────────────────────────────────────────

def test_embed_capabilities_reprobes_when_source_pending():
    """init 探测时 llm-gateway 未就绪（source=None, probe_done=False）；注册路径按需重探，
    此时 llm-gateway 已就绪 → source 翻 llm 并完成写入。"""
    conn = FakeConn(cap_rows={})
    store = PgStore("postgresql://fake")
    store._pg_ok = True
    store._pool = FakePool(conn)
    store._llm_addr = "llm-gateway:50052"
    store._embed_source = None
    store._embed_probe_done = False          # 尚未定论 → 允许重探

    async def now_up(texts, timeout):
        return [_vec() for _ in texts]
    store._llm_embed = now_up

    asyncio.run(store._embed_capabilities(_manifest("info", [_cap("info.weather", "查天气")])))

    assert store._embed_source == "llm"      # 重探成功
    assert len(conn.inserts()) == 1


# ── resolve_semantic 降级 ─────────────────────────────────────────────────

def test_resolve_semantic_degrades_without_pg():
    store = PgStore("postgresql://fake")
    store._pg_ok = False
    store._embed_source = "llm"
    assert asyncio.run(store.resolve_semantic("茅台股价")) == []


def test_resolve_semantic_degrades_without_embed_source():
    store = PgStore("postgresql://fake")
    store._pg_ok = True
    store._embed_source = None               # 无源 → 语义分支静默缺席
    assert asyncio.run(store.resolve_semantic("茅台股价")) == []


def test_resolve_semantic_degrades_on_empty_query():
    store = PgStore("postgresql://fake")
    store._pg_ok = True
    store._embed_source = "llm"
    assert asyncio.run(store.resolve_semantic("")) == []


# ── query 向量缓存 ─────────────────────────────────────────────────────────

def test_query_embedding_is_cached():
    store = PgStore("postgresql://fake")
    store._embed_source = "llm"
    calls = []

    async def counting_embed(text, timeout):
        calls.append(text)
        return _vec()
    store._embed = counting_embed

    first = asyncio.run(store._embed_query_cached("附近的川菜馆"))
    second = asyncio.run(store._embed_query_cached("附近的川菜馆"))
    assert first == second
    assert calls == ["附近的川菜馆"]         # 第二次命中缓存，不再 embed


# ── server 侧 SEMANTIC_MIN_SIM 下限过滤（修 §1.1 bug 的另一半）──────────────

class _FakeStore:
    """keyword resolve 空 → best_score 0 <0.5 触发语义；semantic 返回含低于下限的候选。"""

    def __init__(self, semantic_recs):
        self._sem = semantic_recs

    def resolve(self, intent, query, top_k, granted):
        return []

    async def resolve_semantic(self, query, top_k=3, granted=None):
        return self._sem


def _rec(agent_id):
    return Record(manifest=agent_pb2.AgentManifest(
        agent_id=agent_id, capabilities=[_cap(f"{agent_id}.do")]),
        endpoint=f"{agent_id}:50000", lease_id="")


def test_server_filters_below_semantic_min_sim():
    hi, lo = SEMANTIC_MIN_SIM + 0.2, SEMANTIC_MIN_SIM - 0.1
    servicer = RegistryServicer(store=_FakeStore([(_rec("food-ordering"), hi),
                                                  (_rec("random-agent"), lo)]))
    req = SimpleNamespace(intent="", query="帮我找个川菜馆订位", top_k=1, granted_permissions=[])
    resp = asyncio.run(servicer.ResolveAgents(req, None))

    ids = [a.manifest.agent_id for a in resp.agents]
    assert "food-ordering" in ids            # 高于下限：保留
    assert "random-agent" not in ids         # 低于下限：被 server 二次过滤丢弃
