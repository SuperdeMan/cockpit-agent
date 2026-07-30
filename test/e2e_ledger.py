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
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from support.e2e import CaseRecorder, assert_persistent_source_contract


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
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

URL = ""
TIMEOUT = 120
PG = ["docker", "exec", "car-agent-postgres-1", "psql", "-U", "cockpit",
      "-d", "cockpit", "-tAc"]

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


def sql(query: str) -> str:
    try:
        out = subprocess.run(
            PG + [query],
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("task ledger SQL command timed out") from exc
    if out.returncode != 0:
        raise RuntimeError("task ledger SQL command failed")
    return (out.stdout or "").strip()


def quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def parse_count(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("task ledger namespace count is invalid") from exc
    if value < 0 or str(value) != raw:
        raise RuntimeError("task ledger namespace count is invalid")
    return value


async def ask(text: str, desc: str, session: str) -> dict:
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


def ledger_rows(user: str, session: str) -> list[dict]:
    owner = quoted(user)
    sid = quoted(session)
    raw = sql("SELECT json_agg(row_to_json(t)) FROM (SELECT task_id,status,progress,"
              "goal,budget FROM task_ledger "
              f"WHERE user_id={owner} AND session_id={sid}) t")
    if not raw or raw == "":
        return []
    try:
        return json.loads(raw) or []
    except json.JSONDecodeError:
        return []


def ledger_count(user: str) -> int:
    raw = sql(
        f"SELECT count(*) FROM task_ledger WHERE user_id={quoted(user)}",
    )
    return parse_count(raw)


def cleanup_namespace(user: str) -> None:
    owner = quoted(user)
    sql(f"DELETE FROM task_ledger WHERE user_id={owner}")
    remaining = ledger_count(user)
    if remaining:
        raise RuntimeError(f"task ledger cleanup left {remaining} rows")


_PROGRESS_RE = re.compile(r"检索中\s*(\d+)/(\d+)\s*个子问题")


def progress_snapshot_consistent(speech: str, row: dict) -> bool:
    """允许后台在状态直答后继续推进，但不允许话术超前或改写总量。"""
    progress = str(row.get("progress") or "")
    if progress and progress in speech:
        return True
    spoken = _PROGRESS_RE.search(speech)
    current = _PROGRESS_RE.search(progress)
    if spoken and current:
        spoken_done, spoken_total = map(int, spoken.groups())
        current_done, current_total = map(int, current.groups())
        return spoken_total == current_total and spoken_done <= current_done
    return bool(
        spoken
        and str(row.get("status") or "") in {
            "done",
            "failed",
            "cancelled",
            "orphaned",
        }
    )


def inject_research_crash() -> None:
    stack_root = Path(
        os.getenv("E2E_STACK_ROOT") or Path(__file__).resolve().parents[1],
    ).resolve()
    completed = subprocess.run(
        ["docker", "compose", "restart", "deep-research-agent"],
        cwd=stack_root,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError("deep-research crash injection failed")


async def run(recorder: CaseRecorder) -> None:
    global URL
    URL = recorder.ws_url()
    user = recorder.user_id()
    first_session = recorder.session_id(1)
    second_session = recorder.session_id(2)
    recorder.register_cleanup(user, lambda: cleanup_namespace(user))
    print("=== M2 Task Ledger 真栈验证 ===")
    before = ledger_count(user)
    if before:
        recorder.fail_case(
            "isolation_precondition",
            "isolation_precondition",
            "namespace was not empty before setup",
        )
        return

    # ① 受理：开单 + 承诺可停可问
    r1 = await ask("慢慢查一下钠离子电池的产业化进展，查完告诉我",
                   "① 异步深调研受理（应开单 + 承诺可停可问）", first_session)
    rows = ledger_rows(user, first_session)
    after = ledger_count(user)
    check("task_created", after == before + 1, "账本新增一条任务", f"{before} → {after}")
    task = rows[0] if rows else {}
    check("task_running", task.get("status") in ("accepted", "running"),
          "任务状态为在跑", str(task.get("status")))
    budget = task.get("budget") or {}
    check("task_budget",
          bool(budget.get("deadline_ts")) and bool(budget.get("llm_calls_max")),
          "预算已写入（deadline + 调用上限）", json.dumps(budget, ensure_ascii=False))
    check("task_follow_up",
          "别查了" in (r1.get("follow_up") or "") or "怎么样了" in (r1.get("follow_up") or ""),
          "受理话术承诺了可停可问")
    task_id = task.get("task_id") or ""
    if not task_id:
        return

    # ② 幂等：立即重说，避免真实 provider 快速降级完成后把「新开一单」误判为幂等失败。
    n_before = ledger_count(user)
    r3 = await ask("慢慢查一下钠离子电池的产业化进展，查完告诉我",
                   "② 重复受理（应命中幂等、不新开任务）", first_session)
    check("task_idempotent", ledger_count(user) == n_before,
          "账本未新增任务（幂等命中）")
    check("task_idempotent_speech", "已经在查" in (r3.get("speech") or ""),
          "话术是「已经在查了」",
          (r3.get("speech") or "")[:50])

    # ③ 状态查询：确定性直答。后台并发推进时允许账本比回复里的快照更新。
    await asyncio.sleep(0.5)
    r2 = await ask("那个调研查得怎么样了", "② 状态查询（应确定性直答真实进度）",
                   first_session)
    sp2 = r2.get("speech") or ""
    check("status_truthful", "还在查" in sp2 or "查完" in sp2 or "中断" in sp2,
          "答出了真实任务态", sp2[:60])
    rows = ledger_rows(user, first_session)
    current = rows[0] if rows else {}
    prog = current.get("progress") or ""
    check("progress_written", bool(prog), "心跳已写入人话进度", prog)
    check("progress_exact", progress_snapshot_consistent(sp2, current),
          "话术进度是账本的同一或更早快照（没让 LLM 编）", prog)

    # ④ 取消：拉模式
    t0 = time.time()
    r4 = await ask("别查了", "④ 取消（拉模式：置 cancelled，后台下次心跳收尾）",
                   first_session)
    check("cancel_speech", "正在停" in (r4.get("speech") or ""), "取消话术",
          (r4.get("speech") or "")[:40])
    status = sql(
        "SELECT status FROM task_ledger "
        f"WHERE task_id={quoted(task_id)} AND user_id={quoted(user)}",
    )
    check("cancel_status", status == "cancelled", "账本已置 cancelled", status)
    # 后台任务应在一次心跳内自行收尾（不再推进 progress）
    p1 = sql(
        "SELECT progress FROM task_ledger "
        f"WHERE task_id={quoted(task_id)} AND user_id={quoted(user)}",
    )
    await asyncio.sleep(12)
    p2 = sql(
        "SELECT progress FROM task_ledger "
        f"WHERE task_id={quoted(task_id)} AND user_id={quoted(user)}",
    )
    st2 = sql(
        "SELECT status FROM task_ledger "
        f"WHERE task_id={quoted(task_id)} AND user_id={quoted(user)}",
    )
    check("background_stopped", st2 == "cancelled" and p1 == p2,
          f"{int(time.time() - t0)}s 内后台已停手（进度不再推进、状态未被覆盖）",
          f"{p1!r} → {p2!r}")

    # ⑤ 中断诚实报告：**真重启容器**（后台 asyncio.task 随进程消失，不再心跳）
    #   → 惰性判定 orphaned → 查询答「查到一半中断了」。
    # 刻意不用「把 heartbeat_at 改老」模拟：进程还活着时它下一次心跳会把任务复活
    # （这正是防误判的「迟到心跳复活」机制，首跑实测撞到过）——那不是崩溃场景。
    await ask("慢慢查一下固态电池的封装工艺，查完告诉我",
              "⑤-a 再开一条任务", second_session)
    rows = ledger_rows(user, second_session)
    tid = rows[0].get("task_id", "") if rows else ""
    check("crash_task_created", bool(tid), "崩溃注入目标任务已按session精确取得")
    if not tid:
        return
    print("\n[⑤-b 重启 deep-research 容器（模拟崩溃/发版：后台任务随进程消失）]")
    inject_research_crash()
    await asyncio.sleep(20)         # 等 registry 重注册（AGENT_REREGISTER_INTERVAL=10）
    # 免等 ORPHAN_TTL(90s)：只把**心跳时刻**推到过去。此刻已无进程会再心跳，判定成立。
    # 刻意不动 created_at——「最近一条」按它排序，改了会让查询答到更早的那条任务上去
    # （首跑实测踩到：答的是上一条已取消的调研）。
    sql(f"UPDATE task_ledger SET heartbeat_at=now()-interval '600 seconds' "
        f"WHERE task_id={quoted(tid)} AND user_id={quoted(user)}")
    r5 = await ask("刚才那个调研怎么样了",
                   "⑤-c 中断后查询（应诚实说中断，不假装在跑）", second_session)
    sp5 = r5.get("speech") or ""
    check("orphan_speech", "中断" in sp5, "答出了「查到一半中断了」", sp5[:60])
    check("orphan_status", sql(
        "SELECT status FROM task_ledger "
        f"WHERE task_id={quoted(tid)} AND user_id={quoted(user)}",
    ) == "orphaned",
          "账本已惰性改判 orphaned")


def main() -> int:
    _source_contract()
    global _recorder
    _recorder = CaseRecorder()
    with _recorder:
        asyncio.run(run(_recorder))
    result = _recorder.result
    print(f"\n=== 结果：{result.counts['passed']}/{result.counts['selected']} 通过 ===")
    return _recorder.exit_code()


if __name__ == "__main__":
    sys.exit(main())
