"""MiniMax WS 流式 TTS：分段合并 + RPM 限速 + 1002 限流重连续传（语音批 2026-09-06）。

真机与探针读数（docs/design/2026-09-05-mobile-voice-broadcast-stutter-plan.md §6.3）：
  · 逐分句 `task_continue`：详细版回答一分钟内撞 `rate limit exceeded(RPM)`，网关回 error，
    客户端把已缓冲的几十秒放完就停——「播报到 1980 年设立深圳经济特区就停了」。
  · 请求粒度**不**影响听感：同文同音色，14 个请求与 4 个请求的内部静音都是 13–14 处 / ~4.8s，
    那是 MiniMax 自己在标点处的停顿，另查。
修法：首段仍在第一个软断点发（保首音），其后只按句末断（少请求）；发送经 RPM 滑窗限速；
撞 1002/1039 等窗口滚过再重连，续传未收到 is_final 的段，而不是把整条流报错。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import providers as P  # noqa: E402
from providers import MiniMaxWsStreamingTTSProvider, _RpmBucket, _minimax_segments  # noqa: E402


async def _aiter(items):
    for x in items:
        yield x


def _msg(type_, data):
    return types.SimpleNamespace(type=type_, data=data)


def _text(obj):
    import aiohttp
    return _msg(aiohttp.WSMsgType.TEXT, json.dumps(obj))


class _FakeWS:
    """脚本化 aiohttp WS：receive() 顺序回放；send_json 记录发出的帧。
    `gate_on_started`：脚本里 task_started 之后的消息要等泵至少发出 `min_sent` 帧才回放，
    免得音频/失败事件抢在 task_continue 之前到（真实网络里合成一定晚于文本送达）。"""

    def __init__(self, scripted, *, min_sent=0):
        self._scripted = list(scripted)
        self.sent = []
        self.closed = False
        self._min_sent = min_sent

    async def send_json(self, obj):
        self.sent.append(obj)

    async def receive(self, timeout=None):
        for _ in range(50):
            await asyncio.sleep(0)
            if self._continues() >= self._min_sent or not self._scripted or self._scripted[0][1]:
                break
        if self._scripted:
            gated, m = self._scripted.pop(0)
            if gated:
                for _ in range(200):
                    if self._continues() >= self._min_sent:
                        break
                    await asyncio.sleep(0)
            return m
        import aiohttp
        return _msg(aiohttp.WSMsgType.CLOSED, None)

    def _continues(self):
        return sum(1 for f in self.sent if isinstance(f, dict) and f.get("event") == "task_continue")

    @property
    def continues(self):
        return [f["text"] for f in self.sent if isinstance(f, dict) and f.get("event") == "task_continue"]

    @property
    def events(self):
        return [f.get("event") for f in self.sent if isinstance(f, dict)]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        self.closed = True
        return False


class _FakeSessionSeq:
    """每次 ws_connect 依次交出下一条假 WS（重连用例要看到第二条连接）"""

    def __init__(self, ws_list):
        self._ws_list = list(ws_list)
        self.connects = 0

    def ws_connect(self, *a, **k):
        self.connects += 1
        return self._ws_list.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeClock:
    """可控单调钟 + 不真等的 sleep（记录每次等了多久）"""

    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    async def sleep(self, s):
        self.slept.append(s)
        self.t += max(0.0, s)
        await asyncio.sleep(0)


def _wire(monkeypatch, ws_list):
    import aiohttp
    sess = _FakeSessionSeq(ws_list)
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: sess)
    return sess


def _ok_script(audio_pairs, *, fail_after=None):
    """connected → started → 若干 (audio_hex, is_final) → task_finished；
    fail_after=n 表示第 n 段音频之后来一条 1002 task_failed，不再 finished。所有音频/失败事件都门控在泵发出文本之后。"""
    s = [(False, _text({"event": "connected_success"})), (False, _text({"event": "task_started"}))]
    for i, (hexs, final) in enumerate(audio_pairs):
        s.append((True, _text({"event": "task_continued", "data": {"audio": hexs}, "is_final": final})))
        if fail_after is not None and i + 1 == fail_after:
            s.append((True, _text({"event": "task_failed",
                                   "base_resp": {"status_code": 1002, "status_msg": "rate limit exceeded(RPM)"}})))
            return s
    s.append((True, _text({"event": "task_finished"})))
    return s


# ── 分段：首段软断点，其后句末 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_minimax_segments_first_soft_break_then_sentence_ends():
    deltas = ["深圳，", "今天", "多云，", "气温二十四度。", "出门带伞，", "注意湿滑。"]
    out = [s async for s in _minimax_segments(_aiter(deltas))]
    assert out == ["深圳，", "今天多云，气温二十四度。", "出门带伞，注意湿滑。"]


@pytest.mark.asyncio
async def test_minimax_segments_text_preserved_exactly():
    """分段只改送出的时机，不改朗读文本：拼回去一字不差"""
    text = "秦朝时，深圳属南海郡番禺县，到了东晋咸和六年，也就是公元三三一年，这里设立了东官郡，郡治就在今天的南头一带。宋元时期，盐业和渔业已经相当发达。"
    out = [s async for s in _minimax_segments(_aiter(list(text)))]
    assert "".join(out) == text
    assert out[0] == "秦朝时，"  # 首段贴第一个逗号
    assert len(out) == 3          # 之后两句各一段，不再按逗号碎切


@pytest.mark.asyncio
async def test_minimax_segments_long_run_hard_cut_at_soft_break():
    text = "一二三四五六七八九十，" * 20  # 200 字无句末
    out = [s async for s in _minimax_segments(_aiter([text]), max_chars=60)]
    assert "".join(out) == text
    assert out[0] == "一二三四五六七八九十，"
    assert all(len(s) <= 60 for s in out)
    assert all(s.endswith("，") for s in out[:-1])


@pytest.mark.asyncio
async def test_minimax_segments_no_soft_break_first_when_disabled():
    deltas = ["深圳，", "今天多云。"]
    out = [s async for s in _minimax_segments(_aiter(deltas), first_soft_break=False)]
    assert out == ["深圳，今天多云。"]


# ── RPM 滑窗 ────────────────────────────────────────────────────────────────

def test_rpm_bucket_sliding_window():
    clk = _FakeClock()
    b = _RpmBucket(2, clk.now)
    assert b.try_take() and b.try_take()
    assert not b.try_take()
    assert 59.0 < b.wait_time() <= 60.0
    clk.t += 30
    assert not b.try_take()
    clk.t += 31
    assert b.try_take()          # 两次都滚出了 60s 窗口
    assert b.try_take()
    assert not b.try_take()      # 又满了（2 个在窗内）
    assert b.wait_time() > 0


def test_rpm_bucket_zero_means_unlimited():
    clk = _FakeClock()
    b = _RpmBucket(0, clk.now)
    assert all(b.try_take() for _ in range(100))
    assert b.wait_time() == 0


# ── 全循环：限速 + 重连续传 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_minimax_ws_paces_sends_by_rpm(monkeypatch):
    clk = _FakeClock()
    # 三句 → 首段「第一句，」+ 两个句末段，共 3 次 task_continue；rpm=2 ⇒ 第 3 次要等窗口
    ws = _FakeWS(_ok_script([("aa", True), ("bb", True), ("cc", True)]), min_sent=1)
    _wire(monkeypatch, [ws])
    prov = MiniMaxWsStreamingTTSProvider("k", rpm=2, clock=clk.now, sleep=clk.sleep)
    out = [x async for x in prov.stream(_aiter(["第一句，", "后半。", "第二句。", "第三句。"]))]
    assert ws.continues == ["第一句，", "后半。", "第二句。第三句。"] or ws.continues == ["第一句，", "后半。", "第二句。", "第三句。"]
    # 第 3 次发送前等过窗口（不真等，假钟推进）
    assert clk.slept and max(clk.slept) >= 59.0
    audio = b"".join(x for x in out if isinstance(x, (bytes, bytearray)))
    assert audio == bytes.fromhex("aabbcc")
    assert ws.events[-1] == "task_finish"


@pytest.mark.asyncio
async def test_minimax_ws_rpm_failure_reconnects_and_resends_unfinished(monkeypatch):
    clk = _FakeClock()
    # 连接 1：第 1 段完整（is_final）、第 2 段只到一半就 1002；连接 2：把第 2、3 段补完
    ws1 = _FakeWS(_ok_script([("aa", True), ("bb", False)], fail_after=2), min_sent=3)
    ws2 = _FakeWS(_ok_script([("cc", True), ("dd", True)]), min_sent=2)
    sess = _wire(monkeypatch, [ws1, ws2])
    prov = MiniMaxWsStreamingTTSProvider("k", rpm=100, clock=clk.now, sleep=clk.sleep)
    out = [x async for x in prov.stream(_aiter(["第一句。", "第二句。", "第三句。"]))]
    assert sess.connects == 2
    assert ws1.continues == ["第一句。", "第二句。", "第三句。"]
    # 重连后只续传没收到 is_final 的两段，且先 task_start
    assert ws2.events[0] == "task_start"
    assert ws2.continues == ["第二句。", "第三句。"]
    assert ws2.events[-1] == "task_finish"
    audio = b"".join(x for x in out if isinstance(x, (bytes, bytearray)))
    assert audio == bytes.fromhex("aabbccdd")  # 第 2 段前半 bb 已播，续传会带一点复读，比整段丢好
    metas = [x for x in out if isinstance(x, dict)]
    assert metas and metas[0]["type"] == "meta"
    assert not [x for x in out if isinstance(x, dict) and x.get("type") == "error"]
    assert clk.slept  # 重连前按窗口退避过


@pytest.mark.asyncio
async def test_minimax_ws_non_rpm_failure_still_raises(monkeypatch):
    clk = _FakeClock()
    script = [(False, _text({"event": "connected_success"})), (False, _text({"event": "task_started"})),
              (True, _text({"event": "task_failed", "base_resp": {"status_code": 2013, "status_msg": "invalid voice"}}))]
    ws = _FakeWS(script, min_sent=1)
    _wire(monkeypatch, [ws])
    prov = MiniMaxWsStreamingTTSProvider("k", rpm=100, clock=clk.now, sleep=clk.sleep)
    with pytest.raises(RuntimeError, match="2013"):
        _ = [x async for x in prov.stream(_aiter(["你好。"]))]


@pytest.mark.asyncio
async def test_minimax_ws_task_start_default_has_no_continuous_sound(monkeypatch):
    """默认关 = task_start 与改动前字节级一致（同文实测开了只少 15% 停顿、首片慢 250ms）"""
    monkeypatch.delenv("MINIMAX_TTS_CONTINUOUS_SOUND", raising=False)
    clk = _FakeClock()
    ws = _FakeWS(_ok_script([("aa", True)]), min_sent=1)
    _wire(monkeypatch, [ws])
    prov = MiniMaxWsStreamingTTSProvider("k", rpm=100, clock=clk.now, sleep=clk.sleep)
    _ = [x async for x in prov.stream(_aiter(["你好。"]))]
    start = [f for f in ws.sent if isinstance(f, dict) and f.get("event") == "task_start"][0]
    assert "continuous_sound" not in start
    assert start["audio_setting"] == {"format": "pcm", "sample_rate": 24000, "channel": 1}


@pytest.mark.asyncio
async def test_minimax_ws_continuous_sound_opt_in(monkeypatch):
    monkeypatch.setenv("MINIMAX_TTS_CONTINUOUS_SOUND", "1")
    clk = _FakeClock()
    ws = _FakeWS(_ok_script([("aa", True)]), min_sent=1)
    _wire(monkeypatch, [ws])
    prov = MiniMaxWsStreamingTTSProvider("k", rpm=100, clock=clk.now, sleep=clk.sleep)
    _ = [x async for x in prov.stream(_aiter(["你好。"]))]
    start = [f for f in ws.sent if isinstance(f, dict) and f.get("event") == "task_start"][0]
    assert start["continuous_sound"] is True


def test_minimax_ws_rpm_default_from_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_TTS_RPM", "7")
    assert MiniMaxWsStreamingTTSProvider("k").rpm == 7
    monkeypatch.delenv("MINIMAX_TTS_RPM", raising=False)
    assert MiniMaxWsStreamingTTSProvider("k").rpm == 18
