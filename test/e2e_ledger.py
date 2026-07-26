"""M2 P0 真栈验证：Task Ledger 四场景（子 RFC §6 DoD）。

场景：
  ① 受理 → 开单落 PG（可查询、可取消的承诺兑现）
  ② 状态查询 → 从账本读事实**确定性直答**（不进 LLM）
  ③ 重复受理 → 幂等去重（连说两遍不双跑）
  ④ 取消 → 拉模式置 cancelled，后台任务下次心跳自行收尾
  ⑤ 中断诚实报告 → 手工把心跳打到过去（模拟崩溃/重启）→ 查询答「查到一半中断了」

前置：全栈已起（含 postgres）。依赖：pip install websockets
用法：python test/e2e_ledger.py
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
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

URL = "ws://localhost:8090/ws"
TIMEOUT = 120
SESSION = f"e2e-ledger-{int(time.time())}"
PG = ["docker", "exec", "car-agent-postgres-1", "psql", "-U", "cockpit",
      "-d", "cockpit", "-tAc"]

_fails: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _fails.append(label)
    return ok


def sql(query: str) -> str:
    out = subprocess.run(PG + [query], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return (out.stdout or "").strip()


async def ask(text: str, desc: str, session: str = SESSION) -> dict:
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({"text": text, "session_id": session}))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            if msg.get("type") == "final":
                print(f"\n[{desc}]\n  输入: {text}\n  回复: {msg.get('speech', '')}")
                if msg.get("follow_up"):
                    print(f"  追问: {msg['follow_up']}")
                return msg
            if msg.get("type") == "error":
                print(f"\n[{desc}] 错误: {msg.get('message')}")
                return msg


def ledger_rows(kind: str = "research") -> list[dict]:
    raw = sql("SELECT json_agg(row_to_json(t)) FROM (SELECT task_id,status,progress,"
              f"goal,budget FROM task_ledger WHERE kind='{kind}' "
              "ORDER BY created_at DESC LIMIT 5) t")
    if not raw or raw == "":
        return []
    try:
        return json.loads(raw) or []
    except json.JSONDecodeError:
        return []


def ledger_count(kind: str = "research") -> int:
    """全表计数。开单断言不能用 ledger_rows()——它带 LIMIT 5，历史任务积到 5 条后
    「before+1」恒败、「不变」恒真（假绿），计数必须走真 count。"""
    raw = sql(f"SELECT count(*) FROM task_ledger WHERE kind='{kind}'")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def cleanup_stale_e2e_tasks() -> None:
    """把上次运行遗留的 e2e 专属任务置终态。残留 running 任务会让本轮①的新受理
    直接命中幂等（话术变「已经在查了」），①②③连锁失真。只动 e2e 固定语料的 goal，
    不碰真实用户任务（探针清空误删真实数据是有过前科的坑）。

    LIKE 按关键词对收窄而非整句：goal 来自 planner 的 query 槽改写，同一句话真栈
    实测出过「钠离子电池的产业化进展 / 钠离子电池产业化进展 / 慢慢查一下…查完告诉我」
    三种形态（改写不稳定=幂等键漂移的同族现象，验收已立卡）——整句 LIKE 清不干净，
    残留照样污染下一轮。"""
    sql("UPDATE task_ledger SET status='cancelled' WHERE kind='research' "
        "AND status IN ('accepted','running') "
        "AND ((goal LIKE '%钠离子电池%' AND goal LIKE '%产业化%') "
        "  OR (goal LIKE '%固态电池%' AND goal LIKE '%封装%'))")


async def main() -> int:
    print("=== M2 Task Ledger 真栈验证 ===")
    cleanup_stale_e2e_tasks()
    before = ledger_count()

    # ① 受理：开单 + 承诺可停可问
    r1 = await ask("慢慢查一下钠离子电池的产业化进展，查完告诉我",
                   "① 异步深调研受理（应开单 + 承诺可停可问）")
    rows = ledger_rows()
    after = ledger_count()   # 与 before 同口径（真 count）；rows 只取 top-5 供字段断言
    check(after == before + 1, "账本新增一条任务", f"{before} → {after}")
    task = rows[0] if rows else {}
    check(task.get("status") in ("accepted", "running"),
          "任务状态为在跑", str(task.get("status")))
    budget = task.get("budget") or {}
    check(bool(budget.get("deadline_ts")) and bool(budget.get("llm_calls_max")),
          "预算已写入（deadline + 调用上限）", json.dumps(budget, ensure_ascii=False))
    check("别查了" in (r1.get("follow_up") or "") or "怎么样了" in (r1.get("follow_up") or ""),
          "受理话术承诺了可停可问")

    # ② 状态查询：确定性直答
    await asyncio.sleep(8)         # 让后台跑出几次心跳
    r2 = await ask("那个调研查得怎么样了", "② 状态查询（应确定性直答真实进度）")
    sp2 = r2.get("speech") or ""
    check("还在查" in sp2 or "查完" in sp2 or "中断" in sp2,
          "答出了真实任务态", sp2[:60])
    rows = ledger_rows()
    prog = (rows[0].get("progress") or "") if rows else ""
    check(bool(prog), "心跳已写入人话进度", prog)
    if prog and "还在查" in sp2:
        check(prog in sp2, "话术里的进度与账本逐字一致（没让 LLM 编）", prog)

    # ③ 幂等：连说两遍不双跑（计数走真 count——LIMIT 5 封顶后「不变」恒真=假绿）
    n_before = ledger_count()
    r3 = await ask("慢慢查一下钠离子电池的产业化进展，查完告诉我",
                   "③ 重复受理（应命中幂等、不新开任务）")
    check(ledger_count() == n_before, "账本未新增任务（幂等命中）")
    check("已经在查" in (r3.get("speech") or ""), "话术是「已经在查了」",
          (r3.get("speech") or "")[:50])

    # ④ 取消：拉模式
    t0 = time.time()
    r4 = await ask("别查了", "④ 取消（拉模式：置 cancelled，后台下次心跳收尾）")
    check("正在停" in (r4.get("speech") or ""), "取消话术",
          (r4.get("speech") or "")[:40])
    task_id = task.get("task_id", "")
    status = sql(f"SELECT status FROM task_ledger WHERE task_id='{task_id}'")
    check(status == "cancelled", "账本已置 cancelled", status)
    # 后台任务应在一次心跳内自行收尾（不再推进 progress）
    p1 = sql(f"SELECT progress FROM task_ledger WHERE task_id='{task_id}'")
    await asyncio.sleep(12)
    p2 = sql(f"SELECT progress FROM task_ledger WHERE task_id='{task_id}'")
    st2 = sql(f"SELECT status FROM task_ledger WHERE task_id='{task_id}'")
    check(st2 == "cancelled" and p1 == p2,
          f"{int(time.time() - t0)}s 内后台已停手（进度不再推进、状态未被覆盖）",
          f"{p1!r} → {p2!r}")

    # ⑤ 中断诚实报告：**真重启容器**（后台 asyncio.task 随进程消失，不再心跳）
    #   → 惰性判定 orphaned → 查询答「查到一半中断了」。
    # 刻意不用「把 heartbeat_at 改老」模拟：进程还活着时它下一次心跳会把任务复活
    # （这正是防误判的「迟到心跳复活」机制，首跑实测撞到过）——那不是崩溃场景。
    await ask("慢慢查一下固态电池的封装工艺，查完告诉我", "⑤-a 再开一条任务")
    rows = ledger_rows()
    tid = rows[0]["task_id"] if rows else ""
    print("\n[⑤-b 重启 deep-research 容器（模拟崩溃/发版：后台任务随进程消失）]")
    subprocess.run(["docker", "compose", "restart", "deep-research-agent"],
                   capture_output=True, text=True)
    await asyncio.sleep(20)         # 等 registry 重注册（AGENT_REREGISTER_INTERVAL=10）
    # 免等 ORPHAN_TTL(90s)：只把**心跳时刻**推到过去。此刻已无进程会再心跳，判定成立。
    # 刻意不动 created_at——「最近一条」按它排序，改了会让查询答到更早的那条任务上去
    # （首跑实测踩到：答的是上一条已取消的调研）。
    sql(f"UPDATE task_ledger SET heartbeat_at=now()-interval '600 seconds' "
        f"WHERE task_id='{tid}'")
    r5 = await ask("刚才那个调研怎么样了", "⑤-c 中断后查询（应诚实说中断，不假装在跑）")
    sp5 = r5.get("speech") or ""
    check("中断" in sp5, "答出了「查到一半中断了」", sp5[:60])
    check(sql(f"SELECT status FROM task_ledger WHERE task_id='{tid}'") == "orphaned",
          "账本已惰性改判 orphaned")

    # 收尾：停掉可能仍在跑的后台任务，避免污染后续验证
    sql("UPDATE task_ledger SET status='cancelled' "
        "WHERE status IN ('accepted','running')")

    print(f"\n=== 结果：{'全部通过' if not _fails else '失败 ' + str(len(_fails))} ===")
    for f in _fails:
        print(f"  ✗ {f}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
