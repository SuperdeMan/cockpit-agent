"""真栈闭环：WS 创建（相对秒级）→ NATS agent.proactive 收 reminder_fired（带卡）
→ 列表（fired 未完成仍可见）→ P1a snooze 改期原条目（无尸体）→ 完成 → 清空确认续接
（自清理可重入）。

前置：make up 起全栈。依赖：pip install websockets nats-py
用法：python test/e2e_reminder.py
"""
import asyncio
import json
import subprocess
import sys
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
    websockets = None

URL = ""
NATS_URL = "nats://localhost:4222"
TIMEOUT = 60
PG = ["docker", "exec", "car-agent-postgres-1", "psql", "-U", "cockpit",
      "-d", "cockpit", "-tAc"]
_recorder: CaseRecorder | None = None


def record(case_id: str, name: str, ok: bool, detail: str = ""):
    if _recorder is None:
        raise RuntimeError("CaseRecorder is not initialized")
    if ok:
        _recorder.pass_case(case_id)
    else:
        _recorder.fail_case(case_id, "assertion_failed", detail or name)
    print(f"{'✅' if ok else '❌'} {name}  {detail}")


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
        raise RuntimeError("reminder SQL command timed out") from exc
    if out.returncode != 0:
        raise RuntimeError("reminder SQL command failed")
    return (out.stdout or "").strip()


def quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def parse_count(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("reminder namespace count is invalid") from exc
    if value < 0 or str(value) != raw:
        raise RuntimeError("reminder namespace count is invalid")
    return value


def namespace_count(user: str) -> int:
    return parse_count(sql(
        f"SELECT count(*) FROM reminder_item WHERE user_id={quoted(user)}",
    ))


def cleanup_namespace(user: str) -> None:
    sql(f"DELETE FROM reminder_item WHERE user_id={quoted(user)}")
    remaining = namespace_count(user)
    if remaining:
        raise RuntimeError(f"reminder cleanup left {remaining} rows")


def reminder_id(user: str, title: str) -> str:
    return sql(
        "SELECT id FROM reminder_item "
        f"WHERE user_id={quoted(user)} AND title={quoted(title)}",
    )


def reminder_title() -> str:
    """隔离靠签名 owner 命名空间，不把机器 run id 污染进用户话语。"""
    return "检查验收结果"


def creation_text(title: str) -> str:
    return f"20秒后提醒我{title}"


async def ask(text: str, desc: str, session: str) -> dict:
    if websockets is None:
        raise RuntimeError("websockets is unavailable")
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({"text": text, "session_id": session}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
            if msg.get("type") in ("final", "error"):
                print(f"  [{desc}] {text} → {msg.get('speech', msg.get('message', ''))[:60]}")
                return msg


async def run(recorder: CaseRecorder) -> None:
    global URL
    URL = recorder.ws_url()
    user = recorder.user_id()
    session = recorder.session_id(1)
    title = reminder_title()
    recorder.register_cleanup(user, lambda: cleanup_namespace(user))
    if namespace_count(user) != 0:
        recorder.fail_case(
            "isolation_precondition",
            "isolation_precondition",
            "namespace was not empty before setup",
        )
        return

    # 1) 创建（20秒后）→ 回读确认
    r = await ask(creation_text(title), "创建", session)
    created_id = reminder_id(user, title)
    record("create_readback", "1.创建回读",
           r.get("type") == "final" and title in r.get("speech", "")
           and bool(created_id))

    # 2/3) 订 NATS 等 reminder_fired（20s 相对时间 + 5s 轮询 → 40s 内必到）
    got: list[dict] = []
    try:
        import nats
        nc = await nats.connect(NATS_URL)

        async def on_msg(m):
            try:
                p = json.loads(m.data.decode())
                if (
                    p.get("agent_id") == "reminder"
                    and p.get("user_id") == user
                    and title in p.get("speech", "")
                ):
                    got.append(p)
            except Exception:
                pass

        sub = await nc.subscribe("agent.proactive", cb=on_msg)
        for _ in range(80):
            if got:
                break
            await asyncio.sleep(0.5)
        await sub.unsubscribe()
        await nc.close()
    except Exception as e:
        recorder.fail_case(
            "nats_subscription",
            "environment_unavailable",
            f"NATS subscription failed ({type(e).__name__})",
        )
        return
    recorder.pass_case("nats_subscription")
    ok_fire = bool(got) and got[0].get("type") == "reminder_fired" \
        and title in got[0].get("speech", "")
    card_type = (got[0].get("card") or {}).get("type", "") if got else ""
    record("fired_event", "2.到点触达(NATS)", ok_fire,
           got[0].get("speech", "")[:40] if got else "未收到")
    record("fired_card", "3.触达带卡",
           card_type in ("reminder_card", "card_group"), card_type)

    # 4) 列表：fired 未完成仍可见（诚实呈现，设计 §4）
    r = await ask("我今天有什么安排", "列表", session)
    record("list_contains_item", "4.列表含该条", title in r.get("speech", ""))

    # 4b) P1a snooze：改期原条目，列表仍 1 条（旧实现会新建第二条留 fired 尸体）
    r = await ask(f"10分钟后再提醒我{title}", "snooze", session)
    ok_snooze = "再提醒你" in r.get("speech", "")
    same_id = reminder_id(user, title)
    r = await ask("我今天有什么安排", "snooze后列表", session)
    record("snooze_in_place", "4b.snooze改期无尸体",
           ok_snooze and same_id == created_id and namespace_count(user) == 1,
           r.get("speech", "")[:40])

    # 5) 完成（pending/fired 均可完成）
    r = await ask(f"完成提醒：{title}", "完成", session)
    record("complete_item", "5.完成", "已完成" in r.get("speech", ""))

    # 6) 清空：NEED_CONFIRM → 确认续接（engine meta.confirmed 契约）；也是自清理
    r = await ask("把提醒都清空", "清空请求", session)
    if r.get("need_confirm"):
        r2 = await ask("确定", "确认", session)
        record("clear_confirm", "6.清空确认闭环", "清空" in r2.get("speech", ""))
    else:
        record("clear_confirm", "6.清空确认闭环",
               "没有" in r.get("speech", ""), "已无活动项，直答")


def main() -> int:
    _source_contract()
    global _recorder
    _recorder = CaseRecorder()
    with _recorder:
        asyncio.run(run(_recorder))
    result = _recorder.result
    print(f"\n{result.counts['passed']}/{result.counts['selected']} passed")
    return _recorder.exit_code()


if __name__ == "__main__":
    sys.exit(main())
