"""`runtime.context_snapshot`：跨模式重建的「最近完整问答」渲染（评审四轮 R4-05，2026-09-24）。

修前 S2S 摘要每条硬截 120 字、只取 4 条消息：长消息末尾的「不要启动导航」被截掉。判据：按完整问答对取；过长时保留首尾与
每一个带否定 / 限定 / 未完成标记的分句，其余以「…」省略；分句本身从不截断；助手那条带执行过的动作名。
"""
from __future__ import annotations

from runtime.context_snapshot import clip_message, render_exchanges


def test_short_messages_are_unchanged():
    assert clip_message("打开副驾车窗") == "打开副驾车窗"


def test_a_long_message_keeps_its_restriction_clauses_and_ends():
    text = "我们先去深圳湾公园，" + "路上想听点轻音乐，" * 20 + "但是不要走高速，也别启动导航，只要先看路线"
    clipped = clip_message(text)
    assert len(clipped) < len(text)
    for kept in ("我们先去深圳湾公园", "但是不要走高速", "也别启动导航", "只要先看路线"):
        assert kept in clipped, kept
    assert "…" in clipped


def test_an_unfinished_state_is_never_compressed_into_a_finished_one():
    text = "路线已经规划好了，" + "全程大约三十公里，" * 10 + "但还没开始导航，等您说出发"
    assert "但还没开始导航" in clip_message(text)


def test_a_long_unpunctuated_transcript_is_kept_whole():
    """没有标点就没有可省的中间分句（常见于无标点转写）：整条保留，不切句子。"""
    text = "我想去深圳湾公园散步但是不要启动导航" * 10
    assert clip_message(text) == text


def test_render_takes_the_last_whole_exchanges():
    turns = [{"role": r, "text": f"{r}{i}", "exchange_id": f"x{i}"}
             for i in range(6) for r in ("user", "assistant")]
    lines = render_exchanges(turns, exchanges=4)
    assert lines[0] == "用户：user2" and lines[-1] == "你：assistant5" and len(lines) == 8


def test_render_groups_legacy_turns_without_exchange_ids():
    turns = [{"role": "user", "text": "问一"}, {"role": "assistant", "text": "答一"},
             {"role": "user", "text": "问二"}, {"role": "assistant", "text": "答二"}]
    assert render_exchanges(turns, exchanges=1) == ["用户：问二", "你：答二"]


def test_render_carries_executed_actions_on_the_assistant_line():
    turns = [{"role": "user", "text": "关掉", "exchange_id": "x"},
             {"role": "assistant", "text": "关了", "exchange_id": "x", "actions": ["window.close", "seat.heating.off"]}]
    assert render_exchanges(turns)[-1] == "你：关了（已执行：window.close、seat.heating.off）"
