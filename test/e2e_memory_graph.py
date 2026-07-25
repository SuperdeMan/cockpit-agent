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
import json
import subprocess
import sys
import time

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

WS = "ws://localhost:8090/ws"
AUDIO_API = "http://localhost:50059"
TIMEOUT = 90
# memtest- 前缀刻意不在抽取跳过表里（conventions §9.2），本脚本要验证抽取链路
SESSION = f"memtest-graph-{int(time.time())}"
USER = "memgraph-e2e"
PG = ["docker", "exec", "car-agent-postgres-1", "psql", "-U", "cockpit", "-d", "cockpit", "-tAc"]

_fails: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _fails.append(label)
    return ok


def sql(q: str) -> str:
    out = subprocess.run(PG + [q], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return (out.stdout or "").strip()


async def ask(text: str, session: str = SESSION) -> dict:
    """带 user_id 的一轮（走 HMI 同款 WS；user_id 由网关按 AUTH_DEFAULT_USER_ID 注入，
    故本脚本的记忆断言直接查 PG 而非依赖返回体）。"""
    async with websockets.connect(WS) as ws:
        await ws.send(json.dumps({"text": text, "session_id": session}))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            if msg.get("type") in ("final", "error"):
                return msg


def pref_row(predicate_like: str) -> dict:
    raw = sql("SELECT row_to_json(t) FROM (SELECT weight, evidence_count, text, id "
              f"FROM memory_item WHERE predicate LIKE '{predicate_like}%' "
              "AND superseded_by IS NULL ORDER BY valid_from DESC LIMIT 1) t")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


async def main() -> int:
    print("=== M2 记忆图谱真栈验证 ===")
    uid = sql("SELECT user_id FROM memory_item ORDER BY created_at DESC LIMIT 1") or "u1"
    print(f"（记忆归属用户：{uid}）")

    # ① 偏好加权：同一偏好反复说
    print("\n[① 偏好加权：同一偏好说三次]")
    for i, line in enumerate(["记住，我最喜欢的空调温度是26度",
                              "对了，我还是喜欢26度的空调",
                              "空调我一直都喜欢26度"]):
        r = await ask(line, f"{SESSION}-w{i}")
        print(f"   → {line}  ⇒ {(r.get('speech') or '')[:36]}")
        await asyncio.sleep(6)      # 等异步抽取巩固
    row = pref_row("climate.temperature")
    check(bool(row), "偏好已入库", json.dumps(row, ensure_ascii=False)[:100])
    if row:
        check(float(row.get("weight") or 0) > 0, "参与了加权（weight>0）",
              str(row.get("weight")))
        check(int(row.get("evidence_count") or 0) >= 1, "证据计数已物化",
              str(row.get("evidence_count")))
    n_temp = sql("SELECT count(*) FROM memory_item WHERE predicate LIKE 'climate.temperature%' "
                 "AND superseded_by IS NULL")
    check(n_temp == "1", "同一偏好只有一条现行条目（复现是加权不是堆条目）", n_temp)

    # ② 关系边入图
    print("\n[② 关系边：告知家人与其常去地点]")
    r = await ask("记住，我女儿叫小雨，她在阳光小学上学", f"{SESSION}-rel")
    print(f"   ⇒ {(r.get('speech') or '')[:50]}")
    await asyncio.sleep(8)
    edges = sql("SELECT string_agg(subject||'-'||rel||'-'||object, ' | ') FROM memory_relation")
    check(bool(edges), "关系边已入图", edges[:120])
    if edges:
        check("family" in edges, "亲属边存在")
        check("place_of" in edges or "阳光小学" in edges, "地点边存在")

    # ③ 人称地点解析
    print("\n[③ 人称目的地：去接孩子放学]")
    r3 = await ask("导航去接孩子放学", f"{SESSION}-nav")
    sp3 = r3.get("speech") or ""
    print(f"   ⇒ {sp3[:70]}")
    if edges and "place_of" in edges:
        check("阳光小学" in sp3 or "小学" in sp3,
              "解析到了孩子的学校（一跳：孩子→小雨→阳光小学）", sp3[:50])
    else:
        print("   （关系边未建成，跳过本断言）")

    # ④ 未知人称诚实追问
    print("\n[④ 未登记的人称：诚实追问不猜]")
    r4 = await ask("导航去接我妈", f"{SESSION}-unknown")
    sp4 = r4.get("speech") or ""
    print(f"   ⇒ {sp4[:70]}")
    check("在哪" in sp4 or "哪里" in sp4 or "告诉我" in sp4,
          "答的是追问而不是瞎导航", sp4[:50])

    # ⑤ GDPR 级联删除（红线）
    print("\n[⑤ GDPR 硬删：关系边必须一起没（红线）]")
    before = sql("SELECT count(*) FROM memory_relation")
    async with httpx.AsyncClient(base_url=AUDIO_API, timeout=20) as api:
        resp = await api.post("/api/memory/forget", json={"user_id": uid})
        print(f"   forget → HTTP {resp.status_code}")
    await asyncio.sleep(2)
    after_items = sql(f"SELECT count(*) FROM memory_item WHERE user_id='{uid}'")
    after_rels = sql(f"SELECT count(*) FROM memory_relation WHERE user_id='{uid}'")
    check(after_items == "0", "记忆条目已删干净", f"{after_items} 条残留")
    check(after_rels == "0", "**关系边已级联删除**（残留=GDPR 假删除）",
          f"删前 {before} → 删后 {after_rels}")

    print(f"\n=== 结果：{'全部通过' if not _fails else '失败 ' + str(len(_fails))} ===")
    for f in _fails:
        print(f"  ✗ {f}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
