"""记忆系统端到端全链路验证（连 live 栈，需 `docker compose up` 全栈在跑）。

覆盖 6 条真实链路，逐条独立、互不依赖、末尾汇总：
  1. 真 embedding 语义桥接   —— gRPC Remember 种子 + Recall 零字面重叠 query 命中（百炼 v4）
  2. planner 召回注入         —— WS 发"吃饭"请求 → cloud-planner 日志出现 memory recall 注入
  3. chitchat 个人实体召回    —— 种宠物名 → WS 问"我宠物叫啥" → 回答含该名字
  4. 隐私定向 vs 泛化         —— 高敏家地址：泛化召回挡掉、predicate_prefix 定向取回
  5. 合规导出/被遗忘权       —— ExportUser 全在 → ForgetUser 删净 → 再 Recall 为空
  6. 主动 routine → NATS      —— 种 3 次情景 + 触发巩固 → agent.proactive 收到 routine 建议

全部 user/session 由 manifest runner 签发的当前 run/test namespace 派生。

用法：python test/e2e_memory.py
依赖：grpc(gen/python)、websockets；NATS 链路需 nats-py（缺失则 SKIP，不算失败）。
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

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
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台中文/避免 gbk 崩
except Exception:
    pass
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "gen", "python"))

import grpc  # noqa: E402
from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc  # noqa: E402

try:
    import websockets
except ImportError:
    print("请先 pip install websockets")
    sys.exit(2)

MEM_ADDR = os.getenv("MEM_ADDR", "localhost:50053")
WS_URL = ""
NATS_URL = os.getenv("NATS_URL_LOCAL", "nats://localhost:4222")
PLANNER_CONTAINER = os.getenv("PLANNER_CONTAINER", "car-agent-cloud-planner-1")
_recorder: CaseRecorder | None = None


def record(case_id: str, name: str, ok: bool, detail: str = ""):
    if _recorder is None:
        raise RuntimeError("CaseRecorder is not initialized")
    if ok:
        _recorder.pass_case(case_id)
    else:
        _recorder.fail_case(case_id, "assertion_failed", detail or name)
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


# ── memory gRPC 助手 ───────────────────────────────────────────────────
def _item(**kw) -> memory_pb2.MemoryItem:
    return memory_pb2.MemoryItem(**kw)


async def mem_remember(stub, items):
    return await stub.Remember(memory_pb2.RememberRequest(items=items))


async def mem_recall(stub, user_id, query="", **kw):
    req = memory_pb2.RecallRequest(user_id=user_id, query=query, **kw)
    return await stub.Recall(req)


async def mem_forget(stub, user_id, scopes=None):
    return await stub.ForgetUser(memory_pb2.ForgetUserRequest(
        user_id=user_id, scopes=list(scopes or [])))


async def mem_export(stub, user_id):
    r = await stub.ExportUser(memory_pb2.ExportUserRequest(user_id=user_id))
    return json.loads(r.json) if r.json else {}


def _parse_export(response) -> dict:
    if not response.json:
        raise RuntimeError("memory namespace export was empty")
    try:
        data = json.loads(response.json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("memory namespace export was invalid") from exc
    if not isinstance(data, dict):
        raise RuntimeError("memory namespace export was invalid")
    if (
        not isinstance(data.get("profile"), dict)
        or not isinstance(data.get("memories"), list)
        or not isinstance(data.get("relations"), list)
    ):
        raise RuntimeError("memory namespace export was invalid")
    return data


def namespace_count(user: str, sessions: tuple[str, ...]) -> int:
    with grpc.insecure_channel(MEM_ADDR) as channel:
        stub = memory_pb2_grpc.MemoryStub(channel)
        response = stub.ExportUser(
            memory_pb2.ExportUserRequest(user_id=user),
            timeout=10,
        )
        data = _parse_export(response)
        count = (
            len(data["memories"])
            + len(data["relations"])
            + (1 if data["profile"] else 0)
        )
        for session in sessions:
            response = stub.GetSession(
                memory_pb2.GetSessionRequest(
                    session_id=session,
                    last_n=50,
                ),
                timeout=10,
            )
            count += len(response.turns)
    return count


def cleanup_namespace(user: str, sessions: tuple[str, ...]) -> None:
    with grpc.insecure_channel(MEM_ADDR) as channel:
        stub = memory_pb2_grpc.MemoryStub(channel)
        response = stub.ForgetUser(
            memory_pb2.ForgetUserRequest(user_id=user),
            timeout=15,
        )
    if not response.ok:
        raise RuntimeError("memory ForgetUser cleanup was rejected")
    remaining = namespace_count(user, sessions)
    if remaining:
        raise RuntimeError(
            f"memory namespace cleanup left {remaining} owner records",
        )


# ── WS 助手 ─────────────────────────────────────────────────────────────
async def ws_ask(text: str, session_id: str, timeout: int = 60) -> str:
    """发一条 WS 请求，累积流式话术直到 final/error，返回完整话术。"""
    speech = []
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({"text": text, "session_id": session_id}))
        while True:
            try:
                d = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            except asyncio.TimeoutError:
                break
            t = d.get("type")
            if t == "speech_delta":
                speech.append(d.get("delta", ""))
            elif t == "final":
                speech.append(d.get("speech", ""))
                break
            elif t == "error":
                speech.append("[error]" + str(d.get("message", "")))
                break
    return "".join(speech)


def planner_logs_since(seconds: int) -> str:
    """取 cloud-planner 最近 N 秒日志（含 stderr）。"""
    try:
        out = subprocess.run(
            ["docker", "logs", "--since", f"{seconds}s", PLANNER_CONTAINER],
            capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace")
        return (out.stdout or "") + (out.stderr or "")
    except Exception as e:
        return f"[log fetch failed: {e}]"


# ════════════════════════════════════════════════════════════════════════
# 链路 1：真 embedding 语义桥接（百炼 v4）
# ════════════════════════════════════════════════════════════════════════
async def check_semantic_bridge(stub, user: str) -> bool:
    """种『用户不吃辣』+ 干扰『喜欢摇滚乐』，用与种子**零字面重叠**的 query『饮食偏好』召回。
    lexical 对此 query 必返空；能命中且口味排在音乐之前，证明真向量语义生效。"""
    if not os.getenv("LLM_EMBED_API_KEY"):
        record("semantic_bridge", "1.语义桥接(真embedding)", False,
               "LLM_EMBED_API_KEY is unavailable")
        return True
    await mem_remember(stub, [
        _item(user_id=user, kind="semantic", text="用户不吃辣", predicate="taste.spicy",
              scope="profile.taste", confidence=0.9),
        _item(user_id=user, kind="semantic", text="用户喜欢摇滚乐", predicate="music.genre",
              scope="profile.music", confidence=0.9),
    ])
    rec = await mem_recall(stub, user, query="饮食偏好", top_k=3)
    preds = [m.predicate for m in rec.items]
    if not preds:
        record("semantic_bridge", "1.语义桥接(真embedding)", False,
               "零重叠 query 召回为空 → embedding 未生效（检查 LLM_EMBED_API_KEY/百炼）")
        return False
    ok = "taste.spicy" in preds and preds.index("taste.spicy") == 0
    record("semantic_bridge", "1.语义桥接(真embedding)", ok,
           f"query='饮食偏好' → {list(zip(preds, [round(s,3) for s in rec.scores]))}")
    return ok


# ════════════════════════════════════════════════════════════════════════
# 链路 2：planner 召回注入（跨轮偏好影响规划）
# ════════════════════════════════════════════════════════════════════════
async def check_planner_injection(stub, user: str, session: str) -> bool:
    """给当前 runner user 种口味偏好 → WS 发请求 → planner 日志出现 recall。
    （路由到哪个 Agent 可能浮动，故以 planner 召回日志为稳健证据。）"""
    if not os.getenv("LLM_EMBED_API_KEY"):
        record("planner_injection", "2.planner召回注入", False,
               "LLM_EMBED_API_KEY is unavailable")
        return True
    await mem_remember(stub, [
        _item(user_id=user, kind="semantic", text="用户不吃辣，喜欢清淡", predicate="taste.spicy",
              scope="profile.taste", confidence=0.9)])
    await asyncio.sleep(1)
    speech = await ws_ask("我想找个地方吃饭，给点建议", session)
    await asyncio.sleep(1.5)
    logs = planner_logs_since(40)
    marker = f"memory recall for {user}"
    hit = marker in logs and "taste.spicy" in logs
    # 摘录命中日志行
    line = next((ln.strip() for ln in logs.splitlines() if marker in ln), "")
    record("planner_injection", "2.planner召回注入", hit,
           line or f"回复『{speech[:30]}…』，日志未见召回")
    return hit


# ════════════════════════════════════════════════════════════════════════
# 链路 3：chitchat 个人实体召回（宠物名）
# ════════════════════════════════════════════════════════════════════════
async def check_chitchat_pet(stub, user: str, session: str) -> bool:
    if not os.getenv("LLM_API_KEY"):
        record("chitchat_pet", "3.chitchat宠物召回", False,
               "LLM_API_KEY is unavailable")
        return True
    await mem_remember(stub, [
        _item(user_id=user, kind="semantic", text="用户的宠物叫旺财", predicate="person.pet",
              scope="profile.person", privacy_level="sensitive", provenance="user_stated",
              confidence=0.9)])
    await asyncio.sleep(1)
    speech = await ws_ask("我的宠物叫什么名字", session)
    ok = "旺财" in speech
    record("chitchat_pet", "3.chitchat宠物召回", ok, f"回复『{speech[:40]}…』")
    return ok


# ════════════════════════════════════════════════════════════════════════
# 链路 4：隐私定向 vs 泛化（高敏家地址）
# ════════════════════════════════════════════════════════════════════════
async def check_privacy_targeting(stub, user: str) -> bool:
    await mem_remember(stub, [
        _item(user_id=user, kind="semantic", text="家在上海长宁阳光小区", predicate="place.home",
              scope="profile.places", privacy_level="highly_sensitive",
              provenance="user_stated", confidence=1.0)])
    general = await mem_recall(stub, user, query="阳光小区")
    targeted = await mem_recall(stub, user, query="", predicate_prefix="place.")
    g_leak = any(m.predicate == "place.home" for m in general.items)
    t_hit = any(m.predicate == "place.home" for m in targeted.items)
    ok = (not g_leak) and t_hit
    record("privacy_targeting", "4.隐私定向vs泛化", ok,
           f"泛化命中家={g_leak}(应False) 定向取回家={t_hit}(应True)")
    return ok


# ════════════════════════════════════════════════════════════════════════
# 链路 5：合规导出 / 被遗忘权
# ════════════════════════════════════════════════════════════════════════
async def check_compliance(stub, user: str) -> bool:
    await mem_remember(stub, [
        _item(user_id=user, kind="semantic", text="用户不吃辣", predicate="taste.spicy",
              scope="profile.taste", confidence=0.9),
        _item(user_id=user, kind="episodic", text="在西湖边散步", scope="episodic.general",
              confidence=0.8),
    ])
    exported = await mem_export(stub, user)
    ex_preds = {m["predicate"] for m in exported.get("memories", [])}
    forgot = await mem_forget(stub, user)
    after = await mem_recall(stub, user, query="", scopes=["profile.taste"])
    ok = ("taste.spicy" in ex_preds and forgot.deleted >= 2 and len(after.items) == 0)
    record("compliance", "5.合规导出/被遗忘权", ok,
           f"导出 {len(ex_preds)} 条/删除 {forgot.deleted} 条/删后召回 {len(after.items)} 条")
    return ok


# ════════════════════════════════════════════════════════════════════════
# 链路 6：主动 routine → NATS（高频行为沉淀 + 主动建议投递）
# ════════════════════════════════════════════════════════════════════════
async def check_proactive_routine(
    stub,
    user: str,
    session: str,
    e2e_memory_capability: str,
) -> bool:
    try:
        import nats
    except ImportError:
        record("proactive_routine", "6.主动routine→NATS", False,
               "nats-py is unavailable")
        return False

    got: list[dict] = []
    try:
        nc = await nats.connect(NATS_URL)
    except Exception as e:
        record("proactive_routine", "6.主动routine→NATS", False,
               f"NATS connection failed ({type(e).__name__})")
        return False

    async def on_msg(m):
        try:
            p = json.loads(m.data.decode())
            if p.get("agent_id") == "memory":
                got.append(p)
        except Exception:
            pass

    sub = await nc.subscribe("agent.proactive", cb=on_msg)
    # 种 3 次同一情景（早晨公司星巴克买咖啡）
    for _ in range(3):
        await mem_remember(stub, [_item(
            user_id=user, kind="episodic", text="早晨在公司星巴克买咖啡", scope="episodic.general",
            value_json=json.dumps({"action": "买咖啡", "place": "公司星巴克", "hour": 8},
                                  ensure_ascii=False))])
    # 4 轮 AppendTurn（带 user_id）触发服务端巩固 → derive → 发主动建议。
    for i in range(4):
        await stub.AppendTurn(memory_pb2.AppendTurnRequest(
            session_id=session, role="user", text=f"闲聊{i}", user_id=user,
            e2e_memory_capability=e2e_memory_capability))
    # 等主动建议到达
    for _ in range(20):
        if got:
            break
        await asyncio.sleep(0.5)
    await sub.unsubscribe()
    await nc.close()
    ok = bool(got) and got[0].get("type") == "routine_suggestion"
    record("proactive_routine", "6.主动routine→NATS", ok,
           (f"收到主动建议『{got[0].get('speech', '')[:30]}…』" if got else "未收到主动建议"))
    return ok


# ── 主流程 ──────────────────────────────────────────────────────────────
CASES = {
    "semantic_bridge": check_semantic_bridge,
    "planner_injection": check_planner_injection,
    "chitchat_pet": check_chitchat_pet,
    "privacy_targeting": check_privacy_targeting,
    "compliance": check_compliance,
    "proactive_routine": check_proactive_routine,
}


def selected_cases(argv: list[str] | None = None) -> list[str]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", choices=tuple(CASES))
    args = parser.parse_args(argv)
    return list(dict.fromkeys(args.case or CASES))


async def run_selected(recorder: CaseRecorder, selected: list[str]) -> None:
    global WS_URL
    WS_URL = recorder.ws_url()
    users = {
        case_id: recorder.user_id(case_id.replace("_", "-"))
        for case_id in selected
    }
    if any(case_id in selected for case_id in ("planner_injection", "chitchat_pet")):
        shared = recorder.user_id()
        for case_id in ("planner_injection", "chitchat_pet"):
            if case_id in selected:
                users[case_id] = shared
    if "proactive_routine" in selected:
        users["proactive_routine"] = recorder.user_id()

    sessions_by_user: dict[str, list[str]] = {
        user: []
        for user in users.values()
    }
    if "planner_injection" in selected:
        sessions_by_user[users["planner_injection"]].append(
            recorder.session_id(2),
        )
    if "chitchat_pet" in selected:
        sessions_by_user[users["chitchat_pet"]].append(
            recorder.session_id(3),
        )
    proactive_session = None
    proactive_capability = None
    if "proactive_routine" in selected:
        proactive_session = recorder.session_id(1)
        proactive_capability = recorder.memory_capability(1)
        sessions_by_user[users["proactive_routine"]].append(
            proactive_session,
        )

    unique_users = tuple(dict.fromkeys(users.values()))
    for user in unique_users:
        sessions = tuple(dict.fromkeys(sessions_by_user[user]))
        recorder.register_cleanup(
            user,
            lambda owner=user, owner_sessions=sessions: cleanup_namespace(
                owner,
                owner_sessions,
            ),
        )

    blocked_users: set[str] = set()
    for user in unique_users:
        count = namespace_count(
            user,
            tuple(dict.fromkeys(sessions_by_user[user])),
        )
        if count:
            blocked_users.add(user)

    print("=== 记忆系统端到端全链路验证 ===\n")
    async with grpc.aio.insecure_channel(MEM_ADDR) as ch:
        stub = memory_pb2_grpc.MemoryStub(ch)
        try:
            await mem_recall(stub, recorder.user_id("probe"), query="x")
        except Exception as e:
            for case_id in selected:
                recorder.fail_case(
                    case_id,
                    "environment_unavailable",
                    f"memory endpoint unavailable ({type(e).__name__})",
                )
            return

        for case_id in selected:
            user = users[case_id]
            if user in blocked_users:
                recorder.fail_case(
                    case_id,
                    "isolation_precondition",
                    "namespace was not empty before setup",
                )
                continue
            try:
                if case_id == "planner_injection":
                    await check_planner_injection(
                        stub,
                        user,
                        recorder.session_id(2),
                    )
                elif case_id == "chitchat_pet":
                    await check_chitchat_pet(
                        stub,
                        user,
                        recorder.session_id(3),
                    )
                elif case_id == "proactive_routine":
                    if proactive_session is None or proactive_capability is None:
                        raise RuntimeError(
                            "proactive memory capability is unavailable",
                        )
                    await check_proactive_routine(
                        stub,
                        user,
                        proactive_session,
                        proactive_capability,
                    )
                else:
                    await CASES[case_id](stub, user)
            except Exception as e:
                recorder.fail_case(
                    case_id,
                    "unhandled_exception",
                    f"case raised {type(e).__name__}",
                )
                print(f"  [FAIL] {case_id} — {type(e).__name__}")


def main(argv: list[str] | None = None) -> int:
    _source_contract()
    selected = selected_cases(argv)
    global _recorder
    _recorder = CaseRecorder()
    with _recorder:
        asyncio.run(run_selected(_recorder, selected))
    result = _recorder.result
    print(
        f"\n=== 汇总：{result.counts['passed']}/{result.counts['selected']} 通过 ===",
    )
    return _recorder.exit_code()


if __name__ == "__main__":
    sys.exit(main())
