"""M2 记忆图谱真栈验证（子 RFC §6 P0/P1/P2 DoD）。

场景：
  ① 偏好加权：同一偏好说三次 → weight 上升、evidence_count 累加、条目不重复
  ② 关系边：「我女儿叫小雨，她在XX小学上学」→ 两条边入图（不进 memory_item）
  ③ 人称地点解析：「去接孩子放学」→ 导航到 XX小学
  ④ 未知人称诚实追问：没登记过的人称 → 追问而不是猜
  ⑤ **GDPR 级联删除（红线）**：ForgetUser 后关系边必须一起没

前置：全栈已起（含 postgres + 真 LLM，抽取需要）。依赖：pip install websockets httpx
用法：python test/e2e_memory_graph.py
"""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_CONFIGURED_STACK_ROOT = Path(os.getenv("E2E_STACK_ROOT", ""))
STACK_ROOT = (
    _CONFIGURED_STACK_ROOT.resolve()
    if _CONFIGURED_STACK_ROOT.is_absolute()
    else ROOT
)
sys.path.insert(0, str(ROOT))

from runtime import privacy_registry
from scripts.e2e_contract import (
    load_manifest,
    validate_runtime_privacy_sync,
)
from scripts.privacy_bootstrap import run_deletion_bootstrap
from support.e2e import (
    CaseRecorder,
    assert_persistent_source_contract,
)


def _source_contract() -> None:
    assert_persistent_source_contract(Path(__file__).read_text(encoding="utf-8"))


if "--source-contract" in sys.argv:
    _source_contract()
    print("source contract: PASS")
    raise SystemExit(0)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import httpx
    import websockets
except ImportError:
    print("请先：pip install websockets httpx")
    sys.exit(1)

sys.path.insert(0, str(ROOT / "gen" / "python"))
import grpc  # noqa: E402
from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc  # noqa: E402
from google.protobuf.json_format import MessageToDict  # noqa: E402

WS = ""
AUDIO_API = "http://localhost:50059"
MEM_ADDR = os.getenv("MEM_ADDR", "localhost:50053")
TIMEOUT = 90
PG = ["docker", "exec", "car-agent-postgres-1", "psql", "-U", "cockpit", "-d", "cockpit", "-tAc"]
REDIS = [
    "docker",
    "compose",
    "-f",
    str(STACK_ROOT / "compose.yaml"),
    "exec",
    "-T",
    "redis",
    "redis-cli",
    "--raw",
]

_recorder: CaseRecorder | None = None


def check(case_id: str, ok: bool, label: str, detail: str = "") -> bool:
    if _recorder is None:
        raise RuntimeError("CaseRecorder is not initialized")
    print(f"  {'✓' if ok else '✗'} {label}" + (f" — {detail}" if detail else ""))
    if ok:
        _recorder.pass_case(case_id)
    else:
        _recorder.fail_case(case_id, "assertion_failed", detail or label)
    return ok


def sql(q: str) -> str:
    try:
        out = subprocess.run(
            PG + [q],
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("memory graph SQL command timed out") from exc
    if out.returncode != 0:
        raise RuntimeError("memory graph SQL command failed")
    return (out.stdout or "").strip()


def quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def parse_count(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("memory graph namespace count is invalid") from exc
    if value < 0 or str(value) != raw:
        raise RuntimeError("memory graph namespace count is invalid")
    return value


def namespace_count(user: str) -> int:
    owner = quoted(user)
    raw = sql(
        "SELECT "
        f"(SELECT count(*) FROM memory_item WHERE user_id={owner}) + "
        f"(SELECT count(*) FROM memory_relation WHERE user_id={owner})",
    )
    return parse_count(raw)


def cleanup_namespace(user: str) -> None:
    with grpc.insecure_channel(MEM_ADDR) as channel:
        memory_pb2_grpc.MemoryStub(channel).ForgetUser(
            memory_pb2.ForgetUserRequest(user_id=user),
            timeout=15,
        )
    remaining = namespace_count(user)
    if remaining:
        raise RuntimeError(f"memory graph cleanup left {remaining} rows")


async def append_extract(
    *,
    session: str,
    user: str,
    text: str,
    e2e_memory_capability: str,
) -> None:
    async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
        await memory_pb2_grpc.MemoryStub(channel).AppendTurn(
            memory_pb2.AppendTurnRequest(
                session_id=session,
                role="user",
                text=text,
                user_id=user,
                e2e_memory_capability=e2e_memory_capability,
            ),
        )


async def ask(text: str, session: str) -> dict:
    """带 runner identity 的一轮，session 仅使用非 bearer namespace ID。"""
    async with websockets.connect(WS) as ws:
        await ws.send(json.dumps({"text": text, "session_id": session}))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            if msg.get("type") in ("final", "error"):
                return msg


def pref_row(predicate_like: str, user: str) -> dict:
    owner = quoted(user)
    predicate = quoted(predicate_like + "%")
    raw = sql("SELECT row_to_json(t) FROM (SELECT weight, evidence_count, text, id "
              f"FROM memory_item WHERE user_id={owner} AND predicate LIKE {predicate} "
              "AND superseded_by IS NULL ORDER BY valid_from DESC LIMIT 1) t")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def redis_raw(*args: str) -> str:
    try:
        result = subprocess.run(
            REDIS + list(args),
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("memory graph Redis command timed out") from exc
    if result.returncode != 0:
        raise RuntimeError("memory graph Redis command failed")
    return result.stdout or ""


def redis_count(*args: str) -> int:
    return parse_count(redis_raw(*args).strip())


def parse_snapshot_json(raw: str) -> object:
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("memory privacy snapshot is not valid JSON") from exc


def proto_snapshot(message) -> dict:
    return MessageToDict(
        message,
        always_print_fields_with_no_presence=True,
        preserving_proto_field_name=True,
    )


class MemoryPrivacyAdapter:
    def __init__(self):
        self._session_by_user: dict[str, str] = {}

    def session_id(self, user: str) -> str:
        return self._session_by_user.setdefault(
            user,
            f"{user}-gdpr-session",
        )

    async def seed(self, target, user: str, marker: str) -> None:
        async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
            stub = memory_pb2_grpc.MemoryStub(channel)
            if target.id == "memory_item":
                response = await stub.Remember(
                    memory_pb2.RememberRequest(items=[memory_pb2.MemoryItem(
                        user_id=user,
                        occupant_id="primary",
                        kind="semantic",
                        text=marker,
                        predicate="e2e.gdpr.memory_item",
                        scope="profile.test",
                        provenance="user_stated",
                        confidence=1.0,
                    )]),
                    timeout=15,
                )
                if not response.ok or len(response.ids) != 1:
                    raise RuntimeError("GDPR memory item seed failed")
                return
            if target.id == "voiceprint":
                sample = memory_pb2.VoiceVector(
                    values=[1.0, 0.0, 0.0, 0.0],
                )
                response = await stub.EnrollVoiceprint(
                    memory_pb2.EnrollVoiceprintRequest(
                        user_id=user,
                        occupant_id="primary",
                        display_name=marker,
                        samples=[sample, sample, sample],
                        model="gdpr-bootstrap-v1",
                    ),
                    timeout=15,
                )
                if not response.ok:
                    raise RuntimeError("GDPR voiceprint seed failed")
                return
            if target.id == "profile_identity":
                response = await stub.UpsertProfile(
                    memory_pb2.UpsertProfileRequest(
                        user_id=user,
                        key="identity",
                        value_json=json.dumps(
                            {"marker": marker},
                            ensure_ascii=False,
                        ),
                    ),
                    timeout=15,
                )
                if not response.ok:
                    raise RuntimeError("GDPR profile seed failed")
                return
            if target.id == "session_history":
                response = await stub.AppendTurn(
                    memory_pb2.AppendTurnRequest(
                        session_id=self.session_id(user),
                        role="user",
                        text=marker,
                        user_id=user,
                        occupant_id="primary",
                    ),
                    timeout=15,
                )
                if not response.ok:
                    raise RuntimeError("GDPR session seed failed")
                return
        if target.id == "memory_relation":
            relation_id = f"{user}-gdpr-relation"
            sql(
                "INSERT INTO memory_relation "
                "(id,tenant_id,user_id,occupant_id,subject,rel,object,"
                "object_ref,confidence,provenance,privacy_level,consent,"
                "source_turn_ids,valid_from,superseded_by,created_at) VALUES "
                f"({quoted(relation_id)},'default',{quoted(user)},'primary',"
                f"'gdpr-subject','prefers_brand',{quoted(marker)},'',1.0,"
                "'user_stated','sensitive','','',0,NULL,0) "
                "ON CONFLICT (id) DO NOTHING",
            )
            return
        raise RuntimeError("GDPR memory adapter target is unsupported")

    async def count(self, target, user: str) -> int:
        owner = quoted(user)
        if target.id == "memory_item":
            return parse_count(sql(
                f"SELECT count(*) FROM memory_item WHERE user_id={owner}",
            ))
        if target.id == "memory_relation":
            return parse_count(sql(
                f"SELECT count(*) FROM memory_relation WHERE user_id={owner}",
            ))
        if target.id == "voiceprint":
            return parse_count(sql(
                f"SELECT count(*) FROM voiceprint WHERE user_id={owner}",
            ))
        if target.id == "profile_identity":
            return redis_count("EXISTS", f"profile:{user}")
        if target.id == "session_history":
            return (
                redis_count("EXISTS", f"user_sessions:{user}")
                + redis_count("EXISTS", f"sess:{self.session_id(user)}")
            )
        raise RuntimeError("GDPR memory adapter target is unsupported")

    async def read_contains(self, target, user: str, marker: str) -> bool:
        async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
            stub = memory_pb2_grpc.MemoryStub(channel)
            if target.id == "memory_item":
                response = await stub.Recall(
                    memory_pb2.RecallRequest(
                        user_id=user,
                        occupant_id="primary",
                        query="",
                        predicate_prefix="e2e.gdpr.memory_item",
                        top_k=5,
                    ),
                    timeout=15,
                )
                return any(marker in item.text for item in response.items)
            if target.id == "memory_relation":
                response = await stub.QueryRelations(
                    memory_pb2.QueryRelationsRequest(
                        user_id=user,
                        occupant_id="primary",
                        object=marker,
                        limit=5,
                    ),
                    timeout=15,
                )
                return any(edge.object == marker for edge in response.edges)
            if target.id == "voiceprint":
                response = await stub.ListVoiceprints(
                    memory_pb2.ListVoiceprintsRequest(user_id=user),
                    timeout=15,
                )
                return any(
                    voice.display_name == marker
                    for voice in response.occupants
                )
            if target.id == "profile_identity":
                response = await stub.GetContext(
                    memory_pb2.GetContextRequest(
                        user_id=user,
                        session_id=self.session_id(user),
                        scopes=["profile.identity"],
                    ),
                    timeout=15,
                )
                return marker in response.values.get("profile.identity", "")
            if target.id == "session_history":
                response = await stub.GetSession(
                    memory_pb2.GetSessionRequest(
                        session_id=self.session_id(user),
                        last_n=50,
                    ),
                    timeout=15,
                )
                return any(marker in turn.text for turn in response.turns)
        raise RuntimeError("GDPR memory adapter target is unsupported")

    def _persistent_snapshot(self, target, user: str) -> object:
        owner = quoted(user)
        # SELECT * is intentional: the deletion control includes every stored
        # field, especially embedding, value_json, metadata-like JSON columns,
        # provenance, timestamps and supersession state.
        if target.id == "memory_item":
            raw = sql(
                "SELECT COALESCE("
                "jsonb_agg(to_jsonb(memory_item_row) "
                "ORDER BY memory_item_row.id), '[]'::jsonb)::text "
                "FROM (SELECT * FROM memory_item "
                f"WHERE user_id={owner} ORDER BY id) AS memory_item_row",
            )
            return {"rows": parse_snapshot_json(raw)}
        if target.id == "memory_relation":
            raw = sql(
                "SELECT COALESCE("
                "jsonb_agg(to_jsonb(memory_relation_row) "
                "ORDER BY memory_relation_row.id), '[]'::jsonb)::text "
                "FROM (SELECT * FROM memory_relation "
                f"WHERE user_id={owner} ORDER BY id) AS memory_relation_row",
            )
            return {"rows": parse_snapshot_json(raw)}
        if target.id == "voiceprint":
            raw = sql(
                "SELECT COALESCE("
                "jsonb_agg(to_jsonb(voiceprint_row) "
                "ORDER BY voiceprint_row.id), '[]'::jsonb)::text "
                "FROM (SELECT * FROM voiceprint "
                f"WHERE user_id={owner} ORDER BY id) AS voiceprint_row",
            )
            return {"rows": parse_snapshot_json(raw)}
        if target.id == "profile_identity":
            key = f"profile:{user}"
            raw = redis_raw("GET", key).rstrip("\r\n")
            return {
                "key": key,
                "value": None if not raw else parse_snapshot_json(raw),
            }
        if target.id == "session_history":
            index_key = f"user_sessions:{user}"
            session_ids = sorted(
                item
                for item in redis_raw("SMEMBERS", index_key).splitlines()
                if item
            )
            sessions = []
            for session_id in session_ids:
                turn_lines = redis_raw(
                    "LRANGE",
                    f"sess:{session_id}",
                    "0",
                    "-1",
                ).splitlines()
                sessions.append({
                    "id": session_id,
                    "turns": [
                        parse_snapshot_json(turn)
                        for turn in turn_lines
                        if turn
                    ],
                })
            return {
                "index_key": index_key,
                "session_ids": session_ids,
                "sessions": sessions,
            }
        raise RuntimeError("GDPR memory adapter target is unsupported")

    async def _consumer_snapshot(
        self,
        target,
        user: str,
        marker: str,
    ) -> object:
        async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
            stub = memory_pb2_grpc.MemoryStub(channel)
            if target.id == "memory_item":
                response = await stub.Recall(
                    memory_pb2.RecallRequest(
                        user_id=user,
                        occupant_id="primary",
                        query="",
                        predicate_prefix="e2e.gdpr.memory_item",
                        top_k=100,
                    ),
                    timeout=15,
                )
                return proto_snapshot(response)
            if target.id == "memory_relation":
                response = await stub.QueryRelations(
                    memory_pb2.QueryRelationsRequest(
                        user_id=user,
                        occupant_id="primary",
                        object=marker,
                        limit=100,
                    ),
                    timeout=15,
                )
                return proto_snapshot(response)
            if target.id == "voiceprint":
                response = await stub.ListVoiceprints(
                    memory_pb2.ListVoiceprintsRequest(user_id=user),
                    timeout=15,
                )
                return proto_snapshot(response)
            if target.id == "profile_identity":
                response = await stub.GetContext(
                    memory_pb2.GetContextRequest(
                        user_id=user,
                        session_id=self.session_id(user),
                        scopes=["profile.identity"],
                    ),
                    timeout=15,
                )
                return proto_snapshot(response)
            if target.id == "session_history":
                response = await stub.GetSession(
                    memory_pb2.GetSessionRequest(
                        session_id=self.session_id(user),
                        last_n=1000,
                    ),
                    timeout=15,
                )
                return proto_snapshot(response)
        raise RuntimeError("GDPR memory adapter target is unsupported")

    async def snapshot_fingerprint(
        self,
        target,
        user: str,
        marker: str,
    ) -> str:
        payload = {
            "target": target.id,
            "persistent": self._persistent_snapshot(target, user),
            "consumer": await self._consumer_snapshot(
                target,
                user,
                marker,
            ),
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    async def delete(self, user: str, action: str) -> bool:
        if action != "forget_user":
            raise RuntimeError("GDPR memory adapter delete action is unsupported")
        async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
            response = await memory_pb2_grpc.MemoryStub(channel).ForgetUser(
                memory_pb2.ForgetUserRequest(user_id=user),
                timeout=15,
            )
        return response.ok


async def cleanup_privacy_user(
    adapter: MemoryPrivacyAdapter,
    user: str,
    targets,
) -> None:
    if not await adapter.delete(user, "forget_user"):
        raise RuntimeError("GDPR bootstrap cleanup delete failed")
    residues = [
        target.id
        for target in targets
        if await adapter.count(target, user) != 0
    ]
    if residues:
        raise RuntimeError("GDPR bootstrap cleanup left persistent residues")


async def run_gdpr_bootstrap(recorder: CaseRecorder) -> None:
    manifest = load_manifest(
        ROOT / "test" / "e2e_manifest.yaml",
        repo_root=ROOT,
    )
    validate_runtime_privacy_sync(
        manifest.privacy.targets,
        privacy_registry.PRIVACY_TARGETS,
    )
    runtime_targets = tuple(privacy_registry.PRIVACY_TARGETS)
    due = tuple(
        target
        for target in runtime_targets
        if target.enforced_from == "M-A"
        and target.lifecycle == "deletable"
    )
    if not due:
        raise RuntimeError("GDPR bootstrap found no M-A deletion targets")
    adapter = MemoryPrivacyAdapter()
    target_user = recorder.user_id("target")
    control_user = recorder.user_id("control")
    recorder.register_cleanup(
        target_user,
        lambda: asyncio.run(
            cleanup_privacy_user(adapter, target_user, due),
        ),
    )
    recorder.register_cleanup(
        control_user,
        lambda: asyncio.run(
            cleanup_privacy_user(adapter, control_user, due),
        ),
    )

    def record(case_id: str, ok: bool, detail: str) -> None:
        check(case_id, ok, detail)

    result = await run_deletion_bootstrap(
        targets=runtime_targets,
        adapters={"memory": adapter},
        milestone="M-A",
        target_user=target_user,
        control_user=control_user,
        record=record,
    )
    if tuple(target.id for target in due) != result.due_target_ids:
        raise RuntimeError("GDPR bootstrap due target selection drifted")


async def run(recorder: CaseRecorder) -> None:
    global WS
    WS = recorder.ws_url()
    user = recorder.user_id()
    recorder.register_cleanup(user, lambda: cleanup_namespace(user))
    print("=== M2 记忆图谱真栈验证 ===")
    if namespace_count(user) != 0:
        recorder.fail_case(
            "isolation_precondition",
            "isolation_precondition",
            "namespace was not empty before setup",
        )
        return

    # ① 偏好加权：同一偏好反复说。抽取 capability 只直达 memory，
    # 不作为可观测 session_id，避免 bearer 落入 obs/log。
    print("\n[① 偏好加权：同一偏好说三次]")
    pref_session = recorder.session_id(1)
    pref_capability = recorder.memory_capability(1)
    for i, line in enumerate(["记住，我最喜欢的空调温度是26度",
                              "对了，我还是喜欢26度的空调",
                              "空调我一直都喜欢26度"]):
        r = await ask(line, recorder.session_id(i + 1))
        await append_extract(
            session=pref_session,
            user=user,
            text=line,
            e2e_memory_capability=pref_capability,
        )
        print(f"   → {line}  ⇒ {(r.get('speech') or '')[:36]}")
        await asyncio.sleep(6)
    row = pref_row("climate.temperature", user)
    check("preference_stored", bool(row), "偏好已入库",
          json.dumps(row, ensure_ascii=False)[:100])
    if row:
        check("preference_weighted", float(row.get("weight") or 0) > 0,
              "参与了加权（weight>0）",
              str(row.get("weight")))
        check("preference_evidence", int(row.get("evidence_count") or 0) >= 1,
              "证据计数已物化",
              str(row.get("evidence_count")))
    owner = quoted(user)
    n_temp = sql(
        "SELECT count(*) FROM memory_item "
        f"WHERE user_id={owner} AND predicate LIKE 'climate.temperature%' "
        "AND superseded_by IS NULL",
    )
    check("preference_single_current", n_temp == "1",
          "同一偏好只有一条现行条目（复现是加权不是堆条目）", n_temp)

    # ② 关系边入图
    print("\n[② 关系边：告知家人与其常去地点]")
    relation_text = "记住，我女儿叫小雨，她在阳光小学上学"
    r = await ask(relation_text, recorder.session_id(4))
    await append_extract(
        session=recorder.session_id(2),
        user=user,
        text=relation_text,
        e2e_memory_capability=recorder.memory_capability(2),
    )
    print(f"   ⇒ {(r.get('speech') or '')[:50]}")
    await asyncio.sleep(8)
    edges = sql(
        "SELECT string_agg(subject||'-'||rel||'-'||object, ' | ') "
        f"FROM memory_relation WHERE user_id={owner}",
    )
    if not edges:
        # 关系抽取走真实 LLM，单次可能返回空结构；用同一 signed owner 做一次有界
        # 重试，仍要求最终真实落库，不能把采样空响应当成 GDPR/图谱通过。
        await append_extract(
            session=recorder.session_id(2),
            user=user,
            text=relation_text,
            e2e_memory_capability=recorder.memory_capability(2),
        )
        await asyncio.sleep(8)
        edges = sql(
            "SELECT string_agg(subject||'-'||rel||'-'||object, ' | ') "
            f"FROM memory_relation WHERE user_id={owner}",
        )
    check("relations_stored", bool(edges), "关系边已入图", edges[:120])
    if edges:
        check("family_relation", "family" in edges, "亲属边存在")
        check("place_relation", "place_of" in edges or "阳光小学" in edges,
              "地点边存在")
    else:
        check("family_relation", False, "亲属边存在", "关系边为空")
        check("place_relation", False, "地点边存在", "关系边为空")

    # ③ 人称地点解析
    print("\n[③ 人称目的地：去接孩子放学]")
    r3 = await ask("导航去接孩子放学", recorder.session_id(5))
    sp3 = r3.get("speech") or ""
    print(f"   ⇒ {sp3[:70]}")
    if edges and "place_of" in edges:
        check("person_place_resolution", "阳光小学" in sp3 or "小学" in sp3,
              "解析到了孩子的学校（一跳：孩子→小雨→阳光小学）", sp3[:50])
    else:
        check("person_place_resolution", False,
              "解析到了孩子的学校（一跳）", "关系边未建成")

    # ④ 未知人称诚实追问
    print("\n[④ 未登记的人称：诚实追问不猜]")
    r4 = await ask("导航去接我妈", recorder.session_id(6))
    sp4 = r4.get("speech") or ""
    print(f"   ⇒ {sp4[:70]}")
    check("unknown_person_clarifies", "在哪" in sp4 or "哪里" in sp4 or "告诉我" in sp4,
          "答的是追问而不是瞎导航", sp4[:50])

    # ⑤ GDPR 级联删除（红线）
    print("\n[⑤ GDPR 硬删：关系边必须一起没（红线）]")
    before = sql(
        f"SELECT count(*) FROM memory_relation WHERE user_id={owner}",
    )
    async with httpx.AsyncClient(base_url=AUDIO_API, timeout=20) as api:
        resp = await api.post("/api/memory/forget", json={"user_id": user})
        print(f"   forget → HTTP {resp.status_code}")
        try:
            forget_payload = resp.json()
        except (TypeError, json.JSONDecodeError):
            forget_payload = {}
    await asyncio.sleep(2)
    after_items = sql(f"SELECT count(*) FROM memory_item WHERE user_id={owner}")
    after_rels = sql(f"SELECT count(*) FROM memory_relation WHERE user_id={owner}")
    check("forget_items", after_items == "0",
          "记忆条目已删干净", f"{after_items} 条残留")
    check("forget_relations", after_rels == "0",
          "**关系边已级联删除**（残留=GDPR 假删除）",
          f"删前 {before} → 删后 {after_rels}")

    print("\n[⑥ GDPR bootstrap：动态遍历 M-A 隐私注册表]")
    check(
        "legacy_forget_endpoint_ok",
        resp.status_code == 200 and forget_payload.get("ok") is True,
        "既有 HMI ForgetUser 入口返回真实成功",
        f"HTTP {resp.status_code}",
    )
    await run_gdpr_bootstrap(recorder)


def main() -> int:
    _source_contract()
    global _recorder
    _recorder = CaseRecorder()
    with _recorder:
        asyncio.run(run(_recorder))
    result = _recorder.result
    print(
        f"\n=== 结果：{result.counts['passed']}/{result.counts['selected']} 通过 ===",
    )
    return _recorder.exit_code()


if __name__ == "__main__":
    sys.exit(main())
