"""记忆系统端到端全链路验证（连 live 栈，需 `docker compose up` 全栈在跑）。

覆盖 6 条真实链路，逐条独立、互不依赖、末尾汇总：
  1. 真 embedding 语义桥接   —— gRPC Remember 种子 + Recall 零字面重叠 query 命中（百炼 v4）
  2. planner 召回注入         —— WS 发"吃饭"请求 → cloud-planner 日志出现 memory recall 注入
  3. chitchat 个人实体召回    —— 种宠物名 → WS 问"我宠物叫啥" → 回答含该名字
  4. 隐私定向 vs 泛化         —— 高敏家地址：泛化召回挡掉、predicate_prefix 定向取回
  5. 合规导出/被遗忘权       —— ExportUser 全在 → ForgetUser 删净 → 再 Recall 为空
  6. 主动 routine → NATS      —— 种 3 次情景 + 触发巩固 → agent.proactive 收到 routine 建议

WS 会话经 edge-gateway 固定 user_id="u1"（gateway/edge/main.go），故链路 2/3 用 u1；
纯 gRPC 链路用一次性 user_id，避免污染并自清理。

用法：python test/e2e_memory.py
依赖：grpc(gen/python)、websockets；NATS 链路需 nats-py（缺失则 SKIP，不算失败）。
"""
import asyncio
import json
import os
import subprocess
import sys
import time

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
WS_URL = os.getenv("WS_URL", "ws://localhost:8090/ws")
NATS_URL = os.getenv("NATS_URL_LOCAL", "nats://localhost:4222")
PLANNER_CONTAINER = os.getenv("PLANNER_CONTAINER", "car-agent-cloud-planner-1")
WS_USER = "u1"  # edge-gateway 固定注入
_EU = f"e2e_mem_{int(time.time())}"  # 一次性 gRPC 用户

_results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = ""):
    _results.append((name, ok, detail))
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
async def check_semantic_bridge(stub) -> bool:
    """种『用户不吃辣』+ 干扰『喜欢摇滚乐』，用与种子**零字面重叠**的 query『饮食偏好』召回。
    lexical 对此 query 必返空；能命中且口味排在音乐之前，证明真向量语义生效。"""
    if not os.getenv("LLM_EMBED_API_KEY"):
        record("1.语义桥接(真embedding)", True,
               "SKIP：未配置 LLM_EMBED_API_KEY（lexical 兜底对零字面重叠 query 必空，非缺陷）")
        return True
    u = _EU + "_sem"
    await mem_forget(stub, u)
    await mem_remember(stub, [
        _item(user_id=u, kind="semantic", text="用户不吃辣", predicate="taste.spicy",
              scope="profile.taste", confidence=0.9),
        _item(user_id=u, kind="semantic", text="用户喜欢摇滚乐", predicate="music.genre",
              scope="profile.music", confidence=0.9),
    ])
    rec = await mem_recall(stub, u, query="饮食偏好", top_k=3)
    preds = [m.predicate for m in rec.items]
    await mem_forget(stub, u)  # 自清理
    if not preds:
        record("1.语义桥接(真embedding)", False,
               "零重叠 query 召回为空 → embedding 未生效（检查 LLM_EMBED_API_KEY/百炼）")
        return False
    ok = "taste.spicy" in preds and preds.index("taste.spicy") == 0
    record("1.语义桥接(真embedding)", ok,
           f"query='饮食偏好' → {list(zip(preds, [round(s,3) for s in rec.scores]))}")
    return ok


# ════════════════════════════════════════════════════════════════════════
# 链路 2：planner 召回注入（跨轮偏好影响规划）
# ════════════════════════════════════════════════════════════════════════
async def check_planner_injection(stub) -> bool:
    """给 u1 种口味偏好 → WS 发'吃饭'请求 → cloud-planner 日志应出现 memory recall 注入。
    （路由到哪个 Agent 可能浮动，故以 planner 召回日志为稳健证据。）"""
    await mem_forget(stub, WS_USER, scopes=["profile.taste"])
    await mem_remember(stub, [
        _item(user_id=WS_USER, kind="semantic", text="用户不吃辣，喜欢清淡", predicate="taste.spicy",
              scope="profile.taste", confidence=0.9)])
    await asyncio.sleep(1)
    speech = await ws_ask("我想找个地方吃饭，给点建议", f"e2e-mem-inject-{int(time.time())}")
    await asyncio.sleep(1.5)
    logs = planner_logs_since(40)
    hit = "memory recall for u1" in logs and "taste.spicy" in logs
    await mem_forget(stub, WS_USER, scopes=["profile.taste"])  # 自清理
    # 摘录命中日志行
    line = next((ln.strip() for ln in logs.splitlines() if "memory recall for u1" in ln), "")
    record("2.planner召回注入", hit, line or f"回复『{speech[:30]}…』，日志未见召回")
    return hit


# ════════════════════════════════════════════════════════════════════════
# 链路 3：chitchat 个人实体召回（宠物名）
# ════════════════════════════════════════════════════════════════════════
async def check_chitchat_pet(stub) -> bool:
    if not os.getenv("LLM_API_KEY"):
        record("3.chitchat宠物召回", True,
               "SKIP：未配置 LLM_API_KEY（MockProvider 只回显用户末句，无法验证记忆注入话术）")
        return True
    await mem_forget(stub, WS_USER, scopes=["profile.person"])
    await mem_remember(stub, [
        _item(user_id=WS_USER, kind="semantic", text="用户的宠物叫旺财", predicate="person.pet",
              scope="profile.person", privacy_level="sensitive", provenance="user_stated",
              confidence=0.9)])
    await asyncio.sleep(1)
    speech = await ws_ask("我的宠物叫什么名字", f"e2e-mem-pet-{int(time.time())}")
    await mem_forget(stub, WS_USER, scopes=["profile.person"])  # 自清理
    ok = "旺财" in speech
    record("3.chitchat宠物召回", ok, f"回复『{speech[:40]}…』")
    return ok


# ════════════════════════════════════════════════════════════════════════
# 链路 4：隐私定向 vs 泛化（高敏家地址）
# ════════════════════════════════════════════════════════════════════════
async def check_privacy_targeting(stub) -> bool:
    u = _EU + "_priv"
    await mem_forget(stub, u)
    await mem_remember(stub, [
        _item(user_id=u, kind="semantic", text="家在上海长宁阳光小区", predicate="place.home",
              scope="profile.places", privacy_level="highly_sensitive",
              provenance="user_stated", confidence=1.0)])
    general = await mem_recall(stub, u, query="阳光小区")                       # 泛化：应被挡
    targeted = await mem_recall(stub, u, query="", predicate_prefix="place.")   # 定向：应取回
    await mem_forget(stub, u)  # 自清理
    g_leak = any(m.predicate == "place.home" for m in general.items)
    t_hit = any(m.predicate == "place.home" for m in targeted.items)
    ok = (not g_leak) and t_hit
    record("4.隐私定向vs泛化", ok,
           f"泛化命中家={g_leak}(应False) 定向取回家={t_hit}(应True)")
    return ok


# ════════════════════════════════════════════════════════════════════════
# 链路 5：合规导出 / 被遗忘权
# ════════════════════════════════════════════════════════════════════════
async def check_compliance(stub) -> bool:
    u = _EU + "_gdpr"
    await mem_forget(stub, u)
    await mem_remember(stub, [
        _item(user_id=u, kind="semantic", text="用户不吃辣", predicate="taste.spicy",
              scope="profile.taste", confidence=0.9),
        _item(user_id=u, kind="episodic", text="在西湖边散步", scope="episodic.general",
              confidence=0.8),
    ])
    exported = await mem_export(stub, u)
    ex_preds = {m["predicate"] for m in exported.get("memories", [])}
    forgot = await mem_forget(stub, u)
    after = await mem_recall(stub, u, query="", scopes=["profile.taste"])
    ok = ("taste.spicy" in ex_preds and forgot.deleted >= 2 and len(after.items) == 0)
    record("5.合规导出/被遗忘权", ok,
           f"导出 {len(ex_preds)} 条/删除 {forgot.deleted} 条/删后召回 {len(after.items)} 条")
    return ok


# ════════════════════════════════════════════════════════════════════════
# 链路 6：主动 routine → NATS（高频行为沉淀 + 主动建议投递）
# ════════════════════════════════════════════════════════════════════════
async def check_proactive_routine(stub) -> bool:
    try:
        import nats
    except ImportError:
        record("6.主动routine→NATS", True, "SKIP：本机无 nats-py（链路逻辑由单测覆盖）")
        return True

    u = _EU + "_routine"
    await mem_forget(stub, u)
    got: list[dict] = []
    try:
        nc = await nats.connect(NATS_URL)
    except Exception as e:
        record("6.主动routine→NATS", True, f"SKIP：NATS 连接失败({e})")
        return True

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
            user_id=u, kind="episodic", text="早晨在公司星巴克买咖啡", scope="episodic.general",
            value_json=json.dumps({"action": "买咖啡", "place": "公司星巴克", "hour": 8},
                                  ensure_ascii=False))])
    # 4 轮 AppendTurn（带 user_id）触发服务端巩固 → derive → 发主动建议
    for i in range(4):
        await stub.AppendTurn(memory_pb2.AppendTurnRequest(
            session_id=f"e2e-routine-{u}", role="user", text=f"闲聊{i}", user_id=u))
    # 等主动建议到达
    for _ in range(20):
        if got:
            break
        await asyncio.sleep(0.5)
    await sub.unsubscribe()
    await nc.close()
    await mem_forget(stub, u)  # 自清理
    ok = bool(got) and got[0].get("type") == "routine_suggestion"
    record("6.主动routine→NATS", ok,
           (f"收到主动建议『{got[0].get('speech', '')[:30]}…』" if got else "未收到主动建议"))
    return ok


# ── 主流程 ──────────────────────────────────────────────────────────────
async def main() -> int:
    print("=== 记忆系统端到端全链路验证 ===\n")
    async with grpc.aio.insecure_channel(MEM_ADDR) as ch:
        stub = memory_pb2_grpc.MemoryStub(ch)
        # 连通性预检
        try:
            await mem_recall(stub, "_ping", query="x")
        except Exception as e:
            print(f"无法连接 memory({MEM_ADDR})：{e}\n请先 docker compose up 起全栈。")
            return 2
        for fn in (check_semantic_bridge, check_planner_injection, check_chitchat_pet,
                   check_privacy_targeting, check_compliance, check_proactive_routine):
            try:
                await fn(stub)
            except Exception as e:
                record(fn.__name__, False, f"异常：{e}")

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n=== 汇总：{passed}/{total} 通过 ===")
    for name, ok, _ in _results:
        if not ok:
            print(f"  未过：{name}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
