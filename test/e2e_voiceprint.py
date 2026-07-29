"""M4 P4 声纹多用户真栈验证 —— **母提案 M4 最后一条 DoD「多用户记忆隔离旅程」**。

场景（RFC 2026-07-25-m4-p4 §7 P4a-5）：
  ① 首个注册者绑定 primary —— 主驾注册后，他原有的记忆一条不少（本设计最大的回归点）
  ② 第二乘员拿到 occ-N，两人互不相认
  ③ 识别：A 的另一句话认成 A，B 的认成 B
  ④ 诚实降级：太短 / 陌生音频 → 一律回 primary（不是 guest，不是报错）
  ⑤ **记忆隔离（DoD 主线）**：B 说的偏好只进 B 名下，A 召回不到，B 召回得到
  ⑥ **声纹不提权（红线）**：occupant 变了，权限与确认闸一字不变
  ⑦ 删除乘员 → 模板与其记忆同删，主驾的记忆毫发无损

「说话人」使用 runner 在本次 run 独占目录预生成并校验的两种合成音色；
真人声学层仍留真麦验收。

前置：全栈已起（含 postgres + 真 LLM，⑤ 要抽取）+ 声纹面 enabled（模型已拉）。
     milestone 缺任一 → FAIL，不允许 skip。依赖：pip install websockets httpx
用法：python test/e2e_voiceprint.py
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support.e2e import (
    CaseRecorder,
    assert_persistent_source_contract,
)
from scripts.prepare_voiceprint_fixtures import (
    ENROLL_TEXTS,
    MANIFEST_NAME,
    PROBE_TEXT,
    _load_manifest,
    _validate_manifest_schema,
    read_verified_pcm,
    verify_fixtures,
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

WS = ""
AUDIO_API = ""
MEM_ADDR = os.getenv("MEM_ADDR", "localhost:50053")
TIMEOUT = 90
PG = ["docker", "exec", "car-agent-postgres-1", "psql", "-U", "cockpit", "-d", "cockpit", "-tAc"]

_recorder: CaseRecorder | None = None
A_MARKER = "青釉A7"
B_MARKER = "琥珀B9"


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
        raise RuntimeError("voiceprint SQL command timed out") from exc
    if out.returncode != 0:
        raise RuntimeError("voiceprint SQL command failed")
    return (out.stdout or "").strip()


def quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def parse_count(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("voiceprint namespace count is invalid") from exc
    if value < 0 or str(value) != raw:
        raise RuntimeError("voiceprint namespace count is invalid")
    return value


def namespace_count(user: str) -> int:
    owner = quoted(user)
    raw = sql(
        "SELECT "
        f"(SELECT count(*) FROM memory_item WHERE user_id={owner}) + "
        f"(SELECT count(*) FROM memory_relation WHERE user_id={owner}) + "
        f"(SELECT count(*) FROM voiceprint WHERE user_id={owner})",
    )
    return parse_count(raw)


def cleanup_namespace(
    user: str,
    sessions: tuple[str, ...] = (),
) -> None:
    with grpc.insecure_channel(MEM_ADDR) as channel:
        stub = memory_pb2_grpc.MemoryStub(channel)
        deleted = stub.ForgetUser(
            memory_pb2.ForgetUserRequest(user_id=user),
            timeout=15,
        )
        if not deleted.ok:
            raise RuntimeError("voiceprint cleanup ForgetUser returned ok=false")
        remaining = namespace_count(user)
        if remaining:
            raise RuntimeError(f"voiceprint cleanup left {remaining} rows")
        exported = stub.ExportUser(
            memory_pb2.ExportUserRequest(user_id=user),
            timeout=15,
        )
        try:
            payload = json.loads(exported.json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("voiceprint cleanup export is invalid") from exc
        if payload.get("profile"):
            raise RuntimeError("voiceprint cleanup left profile residue")
        for session in sessions:
            response = stub.GetSession(
                memory_pb2.GetSessionRequest(
                    session_id=session,
                    last_n=50,
                ),
                timeout=15,
            )
            if response.turns:
                raise RuntimeError("voiceprint cleanup left session residue")


async def remember_baseline(user: str) -> None:
    async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
        await memory_pb2_grpc.MemoryStub(channel).Remember(
            memory_pb2.RememberRequest(items=[memory_pb2.MemoryItem(
                user_id=user,
                occupant_id="primary",
                kind="semantic",
                text="当前验收命名空间的基线偏好",
                predicate="e2e.baseline",
                scope="profile.test",
                confidence=1.0,
            )]),
        )


async def touch_memory_session(
    number: int,
    *,
    recorder: CaseRecorder,
    user: str,
    occupant: str,
    text: str,
) -> None:
    session = recorder.session_id(number)
    async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
        await memory_pb2_grpc.MemoryStub(channel).AppendTurn(
            memory_pb2.AppendTurnRequest(
                session_id=session,
                role="user",
                text=text,
                user_id=user,
                occupant_id=occupant,
                e2e_memory_capability=recorder.memory_capability(number),
            ),
        )


def load_voiceprint_fixture() -> dict[str, dict]:
    fixture_dir_raw = os.environ.get("E2E_VOICEPRINT_FIXTURE_DIR", "")
    manifest_raw = os.environ.get("E2E_VOICEPRINT_FIXTURE_MANIFEST", "")
    manifest_sha256 = os.environ.get(
        "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256",
        "",
    )
    if not fixture_dir_raw or not manifest_raw or not manifest_sha256:
        raise RuntimeError("runner voiceprint fixture paths are missing")
    fixture_dir = Path(fixture_dir_raw).resolve(strict=True)
    manifest_path = Path(manifest_raw).resolve(strict=True)
    if (
        manifest_path != fixture_dir / MANIFEST_NAME
        or verify_fixtures(fixture_dir) != manifest_path
    ):
        raise RuntimeError("runner voiceprint fixture manifest does not match")
    manifest = _load_manifest(
        fixture_dir,
        expected_sha256=manifest_sha256,
    )
    files = _validate_manifest_schema(manifest)
    speakers: dict[str, dict] = {}
    for voice in manifest["voices"]:
        slot = voice["slot"]
        entries = [
            item
            for item in files
            if item["speaker"] == slot
        ]
        enroll_entries = sorted(
            (item for item in entries if item["purpose"] == "enroll"),
            key=lambda item: item["text_key"],
        )
        probe_entry = next(
            item for item in entries if item["purpose"] == "probe"
        )
        speakers[slot] = {
            "voice_id": voice["voice_id"],
            "enroll": tuple(
                read_verified_pcm(
                    fixture_dir / item["path"],
                    declared_bytes=item["bytes"],
                    declared_sha256=item["sha256"],
                )
                for item in enroll_entries
            ),
            "probe": read_verified_pcm(
                fixture_dir / probe_entry["path"],
                declared_bytes=probe_entry["bytes"],
                declared_sha256=probe_entry["sha256"],
            ),
        }
    if (
        set(speakers) != {"A", "B"}
        or any(len(item["enroll"]) != len(ENROLL_TEXTS)
               for item in speakers.values())
        or manifest["texts"]["probe"] != PROBE_TEXT
    ):
        raise RuntimeError("runner voiceprint fixture matrix is incomplete")
    return speakers


async def enroll(client, uid: str, name: str, pcms: tuple[bytes, ...]) -> dict:
    files = [
        ("sample", (f"s{i}.pcm", pcm, "application/octet-stream"))
        for i, pcm in enumerate(pcms)
    ]
    r = await client.post(f"{AUDIO_API}/api/voiceprint/enroll",
                          params={"user_id": uid, "display_name": name, "format": "pcm16le"},
                          files=files, timeout=60)
    return r.json()


async def identify(client, uid, pcm: bytes) -> dict:
    r = await client.post(f"{AUDIO_API}/api/voiceprint/identify", params={"user_id": uid},
                          content=pcm, timeout=60)
    return r.json()


async def ask(text: str, occupant: str, session: str, extra: dict | None = None) -> dict:
    """带 occupant_id 的一轮（HMI 同款 WS + 同款 meta 键）。"""
    async with websockets.connect(WS) as ws:
        await ws.send(json.dumps({"text": text, "session_id": session,
                                  "meta": {"occupant_id": occupant, **(extra or {})}}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
            if msg.get("type") in ("final", "error"):
                return msg


def mem_rows(uid: str, occ: str, like: str) -> int:
    n = sql(
        "SELECT count(*) FROM memory_item "
        f"WHERE user_id={quoted(uid)} AND occupant_id={quoted(occ)} "
        f"AND text LIKE {quoted('%' + like + '%')} AND superseded_by IS NULL",
    )
    return int(n or 0)


def occupant_counts(uid: str, occ: str) -> tuple[int, int, int]:
    owner = quoted(uid)
    occupant = quoted(occ)
    raw = sql(
        "SELECT "
        f"(SELECT count(*) FROM memory_item WHERE user_id={owner} "
        f"AND occupant_id={occupant}), "
        f"(SELECT count(*) FROM memory_relation WHERE user_id={owner} "
        f"AND occupant_id={occupant}), "
        f"(SELECT count(*) FROM voiceprint WHERE user_id={owner} "
        f"AND occupant_id={occupant})",
    )
    parts = raw.split("|")
    if len(parts) != 3:
        raise RuntimeError("voiceprint occupant count shape is invalid")
    return tuple(parse_count(part.strip()) for part in parts)  # type: ignore[return-value]


def marker_storage_count(uid: str, occ: str, marker: str) -> int:
    owner = quoted(uid)
    occupant = quoted(occ)
    needle = quoted(marker)
    return parse_count(sql(
        "SELECT "
        f"(SELECT count(*) FROM memory_item WHERE user_id={owner} "
        f"AND occupant_id={occupant} AND text LIKE '%'||{needle}||'%' "
        "AND superseded_by IS NULL) + "
        f"(SELECT count(*) FROM memory_relation WHERE user_id={owner} "
        f"AND occupant_id={occupant} AND object={needle} "
        "AND superseded_by IS NULL)",
    ))


def seed_relation(uid: str, occ: str, marker: str) -> None:
    relation_id = f"{uid}-relation-{occ}"
    sql(
        "INSERT INTO memory_relation "
        "(id,tenant_id,user_id,occupant_id,subject,rel,object,object_ref,"
        "confidence,provenance,privacy_level,consent,source_turn_ids,"
        "valid_from,superseded_by,created_at) VALUES "
        f"({quoted(relation_id)},'default',{quoted(uid)},{quoted(occ)},"
        f"{quoted('验收乘员-' + occ)},'prefers_brand',{quoted(marker)},'',"
        "1.0,'user_stated','sensitive','','',0,NULL,0) "
        "ON CONFLICT (id) DO NOTHING",
    )


async def remember_marker(uid: str, occ: str, marker: str) -> None:
    async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
        response = await memory_pb2_grpc.MemoryStub(channel).Remember(
            memory_pb2.RememberRequest(items=[memory_pb2.MemoryItem(
                user_id=uid,
                occupant_id=occ,
                kind="semantic",
                text=f"我的专属验收代号是{marker}",
                predicate="e2e.voiceprint.marker",
                scope="profile.test",
                provenance="user_stated",
                confidence=1.0,
                review_status="user_confirmed",
            )]),
            timeout=15,
        )
    if not response.ok or len(response.ids) != 1:
        raise RuntimeError("voiceprint marker memory was not stored")


async def recall_marker_text(uid: str, occ: str) -> str:
    async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
        response = await memory_pb2_grpc.MemoryStub(channel).Recall(
            memory_pb2.RecallRequest(
                user_id=uid,
                occupant_id=occ,
                query="我的专属验收代号",
                kinds=["semantic"],
                predicate_prefix="e2e.voiceprint.",
                top_k=5,
                min_confidence=0.5,
            ),
            timeout=15,
        )
    return "\n".join(item.text for item in response.items)


async def seed_profile(uid: str) -> None:
    async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
        response = await memory_pb2_grpc.MemoryStub(channel).UpsertProfile(
            memory_pb2.UpsertProfileRequest(
                user_id=uid,
                key="identity",
                value_json=json.dumps(
                    {"task9": "voiceprint-profile-marker"},
                    ensure_ascii=False,
                ),
            ),
            timeout=15,
        )
    if not response.ok:
        raise RuntimeError("voiceprint profile marker was not stored")


async def export_user(uid: str) -> dict:
    async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
        response = await memory_pb2_grpc.MemoryStub(channel).ExportUser(
            memory_pb2.ExportUserRequest(user_id=uid),
            timeout=15,
        )
    try:
        value = json.loads(response.json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("voiceprint user export is invalid") from exc
    if type(value) is not dict:
        raise RuntimeError("voiceprint user export is invalid")
    return value


async def session_turn_count(session: str) -> int:
    async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
        response = await memory_pb2_grpc.MemoryStub(channel).GetSession(
            memory_pb2.GetSessionRequest(session_id=session, last_n=50),
            timeout=15,
        )
    return len(response.turns)


async def forget_user(uid: str, occupant: str = "") -> bool:
    async with grpc.aio.insecure_channel(MEM_ADDR) as channel:
        response = await memory_pb2_grpc.MemoryStub(channel).ForgetUser(
            memory_pb2.ForgetUserRequest(
                user_id=uid,
                occupant_id=occupant,
            ),
            timeout=15,
        )
    return response.ok


async def run(recorder: CaseRecorder) -> None:
    global WS, AUDIO_API
    WS = recorder.ws_url()
    AUDIO_API = os.environ.get("E2E_AUDIO_API_ORIGIN", "")
    if not AUDIO_API:
        raise RuntimeError("runner audio API origin is missing")
    uid = recorder.user_id()
    sessions = tuple(recorder.session_id(number) for number in range(1, 5))
    recorder.register_cleanup(
        uid,
        lambda: cleanup_namespace(uid, sessions),
    )
    print("=== M4 P4 声纹多用户真栈验证 ===")
    fixture = load_voiceprint_fixture()
    recorder.add_artifact(
        f"voiceprint-fixtures/{MANIFEST_NAME}",
        metadata={"kind": "synthetic_voiceprint_fixture_manifest"},
    )
    check(
        "fixture_verified",
        set(fixture) == {"A", "B"},
        "runner 生成的 A/B 合成声纹工件已离线复核",
    )
    if namespace_count(uid) != 0:
        recorder.fail_case(
            "isolation_precondition",
            "isolation_precondition",
            "namespace was not empty before setup",
        )
        return

    await remember_baseline(uid)
    async with httpx.AsyncClient() as client:
        try:
            info = (await client.get(f"{AUDIO_API}/api/voiceprint/info",
                                     params={"user_id": uid}, timeout=10)).json()
        except Exception as e:
            recorder.fail_case(
                "voiceprint_available",
                "environment_unavailable",
                f"llm-gateway unavailable ({type(e).__name__})",
            )
            return
        if not info.get("enabled"):
            recorder.fail_case(
                "voiceprint_available",
                "environment_unavailable",
                "voiceprint provider is disabled",
            )
            return
        recorder.pass_case("voiceprint_available")
        print(f"（provider={info['provider']} model={info.get('model')} dim={info.get('dim')}）")

        check(
            "voice_pair_available",
            fixture["A"]["voice_id"] != fixture["B"]["voice_id"],
            "runner 工件使用两个不同音色",
        )
        print(
            f"（乘员 A={fixture['A']['voice_id']}  "
            f"乘员 B={fixture['B']['voice_id']}  user_id={uid}）",
        )

        # ① 首个注册者绑 primary，且存量记忆一条不少
        print("\n[① 首个注册者绑定 primary（本设计最大的回归点）]")
        # 口径：注册**会**多写一条 identity.name（助手靠它回答「你知道我是谁」），
        # 故这里数的是「除名字之外的既有记忆」——它守的是「不搬家、不失联」，不是总数不变。
        def _other_mem() -> int:
            return int(sql(f"SELECT count(*) FROM memory_item WHERE user_id={quoted(uid)} "
                           "AND occupant_id='primary' "
                           "AND (predicate IS NULL OR predicate<>'identity.name')") or 0)
        before = _other_mem()
        ra = await enroll(client, uid, "泓舟", fixture["A"]["enroll"])
        check("primary_enroll", ra.get("ok") is True, "主驾注册成功",
              json.dumps(ra, ensure_ascii=False)[:110])
        check("primary_assignment", ra.get("occupant_id") == "primary",
              "首个注册者拿到 primary",
              str(ra.get("occupant_id")))
        check("primary_memory_preserved", _other_mem() == before,
              "主驾原有记忆一条不少（注册不搬家、不失联）",
              f"{before} → {_other_mem()}")
        name_rows = sql(f"SELECT text FROM memory_item WHERE user_id={quoted(uid)} "
                        "AND occupant_id='primary' AND predicate='identity.name' "
                        "AND superseded_by IS NULL")
        check("primary_name_memory", "泓舟" in name_rows,
              "名字已写进记忆（「你知道我是谁」的数据来源）", name_rows[:40])
        await touch_memory_session(
            1,
            recorder=recorder,
            user=uid,
            occupant="primary",
            text="你好",
        )

        # ② 第二乘员
        print("\n[② 第二乘员]")
        rb = await enroll(client, uid, "小雨", fixture["B"]["enroll"])
        check("secondary_enroll",
              rb.get("ok") is True and rb.get("occupant_id", "").startswith("occ-"),
              "第二乘员拿到 occ-N", str(rb.get("occupant_id")))
        OCC_B = rb.get("occupant_id") or ""
        if not OCC_B:
            return

        # ③ 识别：注册句之外的另一句话
        print("\n[③ 识别（用未参与注册的另一句话）]")
        ia = await identify(client, uid, fixture["A"]["probe"])
        ib = await identify(client, uid, fixture["B"]["probe"])
        print(f"   A → {json.dumps(ia, ensure_ascii=False)}")
        print(f"   B → {json.dumps(ib, ensure_ascii=False)}")
        check(
            "identify_primary_accept",
            ia.get("decision") == "accept"
            and ia.get("occupant_id") == "primary",
            "A 必须 accept 且精确识别为 primary",
            json.dumps(ia, ensure_ascii=False),
        )
        check(
            "identify_secondary_accept",
            ib.get("decision") == "accept"
            and ib.get("occupant_id") == OCC_B,
            "B 必须 accept 且精确识别为第二乘员",
            json.dumps(ib, ensure_ascii=False),
        )

        # ④ 诚实降级
        print("\n[④ 诚实降级：太短 / 静音]")
        short = await identify(client, uid, b"\x00\x00" * 8000)          # 0.5s
        check("too_short_fallback",
              short.get("occupant_id") == "primary" and short.get("decision") == "too_short",
              "过短音频 → too_short 且回 primary", json.dumps(short, ensure_ascii=False))
        silence = await identify(client, uid, b"\x00\x00" * 16000 * 3)   # 3s 静音
        check("silence_fallback", silence.get("occupant_id") == "primary",
              "静音 → 回 primary（不是 guest、不是报错）",
              json.dumps(silence, ensure_ascii=False))

    # ⑤ 每位乘员各写一个 marker；SQL 存储、Memory Recall、用户可见 WS 三层双向验证。
    print("\n[⑤ A/B 记忆双向隔离：存储 + Recall + 用户可见回答]")
    await remember_marker(uid, "primary", A_MARKER)
    await remember_marker(uid, OCC_B, B_MARKER)
    seed_relation(uid, "primary", A_MARKER)
    seed_relation(uid, OCC_B, B_MARKER)

    check(
        "storage_isolation_a",
        marker_storage_count(uid, "primary", A_MARKER) >= 2
        and marker_storage_count(uid, "primary", B_MARKER) == 0,
        "A 存储只含 A marker，不含 B marker",
    )
    check(
        "storage_isolation_b",
        marker_storage_count(uid, OCC_B, B_MARKER) >= 2
        and marker_storage_count(uid, OCC_B, A_MARKER) == 0,
        "B 存储只含 B marker，不含 A marker",
    )

    recall_a = await recall_marker_text(uid, "primary")
    recall_b = await recall_marker_text(uid, OCC_B)
    check(
        "recall_isolation_a",
        A_MARKER in recall_a and B_MARKER not in recall_a,
        "A 的 Memory Recall 只返回 A marker",
        recall_a[:80],
    )
    check(
        "recall_isolation_b",
        B_MARKER in recall_b and A_MARKER not in recall_b,
        "B 的 Memory Recall 只返回 B marker",
        recall_b[:80],
    )

    visible_a = await ask(
        "我的专属验收代号是什么？请原样说出代号。",
        "primary",
        recorder.session_id(1),
        extra={"occupant_name": "泓舟"},
    )
    visible_b = await ask(
        "我的专属验收代号是什么？请原样说出代号。",
        OCC_B,
        recorder.session_id(2),
        extra={"occupant_name": "小雨"},
    )
    speech_a = visible_a.get("speech") or ""
    speech_b = visible_b.get("speech") or ""
    check(
        "visible_recall_isolation_a",
        A_MARKER in speech_a and B_MARKER not in speech_a,
        "A 的用户可见回答只说 A marker",
        speech_a[:80],
    )
    check(
        "visible_recall_isolation_b",
        B_MARKER in speech_b and A_MARKER not in speech_b,
        "B 的用户可见回答只说 B marker",
        speech_b[:80],
    )

    # 四个 runner 预签 memory session 都建立可删除原始轮次。
    for number, occupant in enumerate(
        ("primary", OCC_B, "primary", OCC_B),
        start=1,
    ):
        await touch_memory_session(
            number,
            recorder=recorder,
            user=uid,
            occupant=occupant,
            text=f"Task9 session {number}",
        )
    await seed_profile(uid)

    # ⑥ A/B 危险动作必须经过完全相同的确认闸。
    print("\n[⑥ 声纹不提权：A/B 危险动作同确认闸]")
    danger_a = await ask("打开后备箱", "primary", recorder.session_id(3))
    danger_b = await ask("打开后备箱", OCC_B, recorder.session_id(4))
    confirm_a = danger_a.get("need_confirm") is True
    confirm_b = danger_b.get("need_confirm") is True
    check(
        "danger_confirm_primary",
        confirm_a,
        "A 的危险动作返回 need_confirm=true",
        (danger_a.get("speech") or "")[:60],
    )
    check(
        "danger_confirm_secondary",
        confirm_b,
        "B 的危险动作返回 need_confirm=true",
        (danger_b.get("speech") or "")[:60],
    )
    check(
        "danger_confirm_same_gate",
        confirm_a is confirm_b is True,
        "A/B 均命中同一确认闸，声纹不构成授权",
    )

    # ⑦ Forget B：B 的三类持久数据全零，A 三类计数逐项不变。
    print("\n[⑦ Forget B：memory/relation/voiceprint 全零且 A 保持]")
    before_a = occupant_counts(uid, "primary")
    before_b = occupant_counts(uid, OCC_B)
    check(
        "forget_secondary_precondition",
        all(value > 0 for value in before_a)
        and all(value > 0 for value in before_b),
        "删除前 A/B 的 memory/relation/voiceprint 均已真实落地",
        f"A={before_a} B={before_b}",
    )
    async with httpx.AsyncClient() as client:
        deleted = (
            await client.delete(
                f"{AUDIO_API}/api/voiceprint/{OCC_B}",
                params={"user_id": uid, "purge_memory": 1},
                timeout=30,
            )
        ).json()
    after_a = occupant_counts(uid, "primary")
    after_b = occupant_counts(uid, OCC_B)
    check(
        "forget_secondary_all_zero",
        deleted.get("ok") is True and after_b == (0, 0, 0),
        "Forget B 后 B 的 memory/relation/voiceprint 均为零",
        f"response={json.dumps(deleted, ensure_ascii=False)} B={after_b}",
    )
    check(
        "forget_secondary_primary_survives",
        after_a == before_a,
        "Forget B 后 A 的三类持久数据逐项不变",
        f"{before_a} → {after_a}",
    )

    # ⑧ Forget user：连 profile 与四个 session 原文一起删除，二次读取均为空。
    print("\n[⑧ Forget user：profile/session 也必须为零]")
    exported_before = await export_user(uid)
    session_before = [
        await session_turn_count(recorder.session_id(number))
        for number in range(1, 5)
    ]
    check(
        "forget_user_profile_seeded",
        bool(exported_before.get("profile", {}).get("identity")),
        "删除前 profile.identity 已真实落地",
    )
    check(
        "forget_user_sessions_seeded",
        all(count > 0 for count in session_before),
        "删除前四个 session 均有原始轮次",
        str(session_before),
    )
    deleted_all = await forget_user(uid)
    exported_after = await export_user(uid)
    session_after = [
        await session_turn_count(recorder.session_id(number))
        for number in range(1, 5)
    ]
    check(
        "forget_user_all_storage_zero",
        deleted_all and namespace_count(uid) == 0,
        "Forget user 后 memory/relation/voiceprint 全部为零",
    )
    check(
        "forget_user_profile_zero",
        not exported_after.get("profile"),
        "Forget user 后 profile 消费面为空",
        json.dumps(exported_after.get("profile"), ensure_ascii=False),
    )
    check(
        "forget_user_sessions_zero",
        session_after == [0, 0, 0, 0],
        "Forget user 后四个 session 消费面均为零",
        str(session_after),
    )


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
