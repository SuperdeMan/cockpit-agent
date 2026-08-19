"""G7 询问式提醒建议的准入闸（QA 卡 Q11 残余，2026-08-19）。

卡上的口径是「**只在明确未来事件 + 时间可用时 offer**」。这份验的是那句话被落成的
三条确定性判据，以及**它挡住的那个真实形态**：I-014「普通天气查询触发提醒建议、
还锚到次日 00:00」。

两侧都验（§4.3「反向验证要两头做」）：
- 正向——三条判据各自会拒；
- 反向——真实的「周六下午三点钢琴比赛」照样放行（`test_server_rpc.py` 那两条
  端到端断言就是这一半，本文件只补纯函数侧的对照）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_MEM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_MEM_DIR))

from offer_admission import OFFER_MIN_LEAD_S, admit_event_offer  # noqa: E402

# ⚠ **不许 `from tests.test_server_rpc import _servicer`**：`tests` 是裸包名，全量跑批时
# 它已经被别的服务的 tests 包占了 `sys.modules` ⇒ `ModuleNotFoundError`，而单跑
# memory/tests 时恰好解析对 ⇒ **单跑绿、全量红**。本仓记过同族老账（`server` 裸模块名
# 与 orchestrator/edge 冲突、`providers` 包名劫持），这是第三次。
# 按既有惯例：**按文件路径独名加载**，不依赖任何裸包名。
_spec = importlib.util.spec_from_file_location(
    "memory_server_for_offer_test", str(_MEM_DIR / "server.py"))
_mem_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mem_server)


def _servicer():
    """与 `test_server_rpc._servicer` 同款（Redis / 向量存储都走内存兜底）。
    刻意复制这四行而不是跨模块导入——见上面那段注释。"""
    svc = _mem_server.MemoryServicer()
    svc.store.url = ""
    svc.store._vstore._dsn = ""
    return svc

_TZ8 = timezone(timedelta(hours=8))
_NOW = int(datetime(2026, 8, 20, 10, 0, tzinfo=_TZ8).timestamp())


def _ev(iso: str) -> dict:
    dt = datetime.fromisoformat(iso).replace(tzinfo=_TZ8)
    return {"event_time": int(dt.timestamp()), "event_time_iso": iso}


def test_spoken_clock_is_admitted():
    ok, why = admit_event_offer(_ev("2026-08-22T15:00:00"), "钢琴比赛", _NOW)
    assert ok, why


def test_date_only_midnight_is_rejected():
    """I-014 的天气形态：抽取按约定把「只有日期」填成 00:00:00。

    一张「8月21日00:00提醒你」的建议卡本身就是坏的——半夜零点提醒不是服务。
    """
    ok, why = admit_event_offer(_ev("2026-08-21T00:00:00"), "深圳天气", _NOW)
    assert not ok and "时刻" in why


def test_bare_date_without_time_segment_is_rejected():
    ev = {"event_time": _NOW + 5 * 86400, "event_time_iso": "2026-08-25"}
    ok, _why = admit_event_offer(ev, "提车", _NOW)
    assert not ok


def test_event_too_close_is_rejected():
    """「要不要到时候提醒你」在事件已经近在眼前时是噪声，不是服务。"""
    soon = datetime.fromtimestamp(_NOW + OFFER_MIN_LEAD_S - 60, _TZ8)
    ev = {"event_time": int(soon.timestamp()),
          "event_time_iso": soon.strftime("%Y-%m-%dT%H:%M:%S")}
    ok, why = admit_event_offer(ev, "开会", _NOW)
    assert not ok and "提前" in why


def test_empty_title_is_rejected():
    """剥掉时间词后什么都不剩 ⇒ 这条记忆里没有可提醒的事。

    原实现在这里 `or text` 回落成原文，等于把时间词又贴回标题里。
    """
    ok, why = admit_event_offer(_ev("2026-08-22T15:00:00"), "  ", _NOW)
    assert not ok and "没有可提醒" in why


def test_missing_event_time_is_rejected():
    ok, _why = admit_event_offer({"event_time_iso": "2026-08-22T15:00:00"}, "开会", _NOW)
    assert not ok


def test_weather_query_never_becomes_an_offer_end_to_end():
    """端到端：一条「明天深圳天气」形态的 episodic **不许**产出 offer 卡。

    ⚠ 这条走真实的 `_derive_and_emit` 路径，不是只调判据函数——
    「判据判对了」和「判据真的挂在那条路上」是两件事（本仓第三次应验）。
    """
    import asyncio

    svc = _servicer()
    published = []

    class _FakeNC:
        async def publish(self, subject, data):
            published.append(json.loads(data))

    svc._nc = _FakeNC()
    svc._nats_tried = True
    tomorrow = (datetime.now(_TZ8) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)

    async def go():
        ids = await svc.store.remember([{
            "user_id": "u1", "kind": "episodic", "text": "用户明天关注深圳天气",
            "scope": "episodic.general",
            "value_json": json.dumps({
                "event_time": int(tomorrow.timestamp()),
                "event_time_iso": tomorrow.strftime("%Y-%m-%dT%H:%M:%S")})}])
        await svc._derive_and_emit("u1", "primary", new_ids=ids)

    asyncio.run(go())
    offers = [p for p in published if p.get("type") == "event_reminder_offer"]
    assert not offers, f"天气查询被当成未来事件 offer 了：{offers}"
