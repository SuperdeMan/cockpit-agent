"""跨域提醒 P1c：sports 生产者——sports_scores 卡场次 → REMINDABLE_ACTIVE（卡序对齐）。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.info.src.handlers.sports import SportsMixin, _kickoff_epoch

TZ = timezone(timedelta(hours=8))


class _S(SportsMixin):
    pass


def _res(fixtures):
    return SimpleNamespace(ui_card={"type": "sports_scores",
                                    "title": "FIFA 世界杯 · 明天",
                                    "fixtures": fixtures})


def _ep(y, mo, d, h):
    return int(datetime(y, mo, d, h, 0, tzinfo=TZ).timestamp())


def test_kickoff_epoch():
    assert _kickoff_epoch("2026-07-12T03:00:00+08:00") == _ep(2026, 7, 12, 3)
    assert _kickoff_epoch("2026-07-12T03:00:00") == _ep(2026, 7, 12, 3)   # 裸时间按上海
    assert _kickoff_epoch("") == 0
    assert _kickoff_epoch("垃圾") == 0


@pytest.mark.asyncio
async def test_save_remindable_all_kickoffs_in_card_order():
    """写全部有 kickoff 的场次（含已结束）——序号必须与卡片渲染同序，
    「第二场」不因首场已结束而错位（过去项由消费侧诚实答复）。"""
    s = _S()
    ctx = SimpleNamespace(save_shared_state=AsyncMock())
    fx = [
        {"status": "finished", "home": "X", "away": "Y",
         "kickoff": "2026-07-11T01:00:00+08:00"},
        {"status": "scheduled", "home": "葡萄牙", "away": "西班牙",
         "kickoff": "2026-07-12T03:00:00+08:00"},
        {"status": "scheduled", "home": "无时间", "away": "Z", "kickoff": ""},
        {"status": "scheduled", "home": "巴西", "away": "阿根廷",
         "kickoff": "2026-07-12T19:00:00+08:00"},
    ]
    await s._save_remindable(ctx, _res(fx))
    key, payload = ctx.save_shared_state.call_args.args
    assert key == "remindable_active"
    assert payload["source"] == "info.sports" and payload["label"].startswith("FIFA")
    assert [it["title"] for it in payload["items"]] == \
        ["X vs Y", "葡萄牙 vs 西班牙", "巴西 vs 阿根廷"]
    assert payload["items"][1]["fire_at"] == _ep(2026, 7, 12, 3)


@pytest.mark.asyncio
async def test_save_remindable_skips_gracefully():
    s = _S()
    ctx = SimpleNamespace(save_shared_state=AsyncMock())
    await s._save_remindable(ctx, _res([{"status": "scheduled", "home": "A",
                                         "away": "B", "kickoff": ""}]))
    ctx.save_shared_state.assert_not_called()          # 无 kickoff → 不写不覆盖
    await s._save_remindable(ctx, SimpleNamespace(ui_card={"type": "weather"}))
    ctx.save_shared_state.assert_not_called()          # 非赛事卡不碰
    await s._save_remindable(None, _res([]))           # ctx 缺失不炸
    boom = SimpleNamespace(save_shared_state=AsyncMock(side_effect=RuntimeError("x")))
    await s._save_remindable(boom, _res([{"status": "scheduled", "home": "A",
                                          "away": "B",
                                          "kickoff": "2026-07-12T03:00:00+08:00"}]))
    # best-effort：写入异常被吞，不影响出卡
