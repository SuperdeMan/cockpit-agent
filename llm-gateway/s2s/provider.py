"""对下 provider 抽象 + 厂商实现（M4 RFC §3.3，映射表见 RFC §3.5 实测基线）。

**换厂商只加一个本文件的子类**——对上事件协议（protocol.py）与 L-Session 状态机
（session.py）一字不改。这就是「先锁协议不锁厂商」在代码上的形态。

实现的厂商：
  · qwen3.5-omni-flash-realtime（DashScope，选定）—— P0 探针实测 tools 完整支持
  · mock —— 无 key/单测用，产可预期的假事件（不联网）

⚠️ `qwen3-omni-flash-realtime`（无 .5）**不可用**：session.update 的 tools 被静默丢弃，
车控句会口头自答而不移交（P0 探针 ★T）。工厂会拒绝它，别绕过。
"""
from __future__ import annotations
import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import AsyncIterator

from .protocol import escalate_tool

logger = logging.getLogger("llm.s2s.provider")

# 实测：输出采样率协议里不声明（`output_audio_format` 只报 "pcm"），按字节数反推为 24kHz。
QWEN_OUT_SAMPLE_RATE = 24000
IN_SAMPLE_RATE = 16000  # 上行与 /api/asr/stream 同格式（16k mono s16le）

# 归一化事件 kind
EV_TRANSCRIPT = "transcript"
EV_ANSWER_DELTA = "answer_delta"
EV_AUDIO_DELTA = "audio_delta"
EV_TOOL_CALL = "tool_call"
EV_TURN_STARTED = "turn_started"
EV_TURN_DONE = "turn_done"
EV_ERROR = "error"


@dataclass
class S2SEvent:
    """归一化事件——L-Session 只认识这些，不认识任何厂商事件名。"""
    kind: str
    text: str = ""
    final: bool = False
    pcm: bytes = b""
    name: str = ""
    args: str = ""
    call_id: str = ""
    reason: str = ""
    response_id: str = ""
    detail: str = ""


class BaseS2SProvider:
    """S2S provider 抽象（RFC §3.3）。子类实现厂商协议映射。"""

    out_sample_rate = QWEN_OUT_SAMPLE_RATE

    async def open(self, *, voice: str = "", system: str = "",
                   context_summary: str = "", tools: bool = True) -> None:
        raise NotImplementedError

    async def send_audio(self, pcm: bytes) -> None:
        raise NotImplementedError

    async def commit_audio(self) -> None:
        """本轮音频段推完 → 请 provider 收尾定稿（本侧 VAD 判到端点时调）。

        **怎么收尾是厂商差异，由子类决定**：server VAD 型要补静音尾（够长才触发
        silence_duration_ms 判定），支持显式提交的可发 commit。抽象在这里，HMI 不必
        知道任何 provider 的 VAD 参数。
        """
        raise NotImplementedError

    async def cancel_response(self) -> None:
        """barge-in 落点：立即停止当前生成。**必须幂等**——本侧权威，多取消一次无害。"""
        raise NotImplementedError

    async def inject_tool_result(self, call_id: str, output: str) -> None:
        """回注工具结果（逃逸轮的主链回答）。**不得触发续说**——否则与主链播报双播。"""
        raise NotImplementedError

    async def inject_text(self, role: str, text: str) -> None:
        """补注入上下文（重连重注入用）。"""
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    def events(self) -> AsyncIterator[S2SEvent]:
        raise NotImplementedError


class QwenOmniRealtimeProvider(BaseS2SProvider):
    """DashScope omni realtime（OpenAI-realtime 风格壳，与 qwen3-asr / qwen3-tts 同门）。

    事件映射按 P0 探针实测（RFC §3.5）：
      response.created                                   → turn_started
      conversation.item.input_audio_transcription.delta  → transcript(final=False)
      …transcription.completed                           → transcript(final=True)
      response.audio_transcript.delta                    → answer_delta
      response.audio.delta                               → audio_delta（base64 → bytes）
      response.function_call_arguments.done              → tool_call（name/args/call_id 在此齐备）
      response.done                                      → turn_done(reason=status)

    server VAD 的 create_response=True 是**默认开**——轮次由 provider 自驱，
    L-Session 不必手动 response.create（实测 §3.5）。
    """

    out_sample_rate = QWEN_OUT_SAMPLE_RATE

    def __init__(self, api_key: str, ws_url: str, model: str, *,
                 vad_silence_ms: int = 800, vad_threshold: float = 0.2):
        self.api_key = api_key
        self.ws_url = ws_url
        self.model = model
        self.vad_silence_ms = max(300, min(2000, int(vad_silence_ms or 800)))
        self.vad_threshold = vad_threshold
        self._session = None
        self._ws = None
        self._eid = 0
        self._closed = False

    # ── 内部：事件 id 与发送 ──
    async def _send(self, obj: dict) -> None:
        if self._ws is None or self._ws.closed:
            raise RuntimeError("s2s provider ws not open")
        self._eid += 1
        await self._ws.send_json({"event_id": f"ev{self._eid}", **obj})

    async def open(self, *, voice: str = "", system: str = "",
                   context_summary: str = "", tools: bool = True) -> None:
        import aiohttp
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(
            f"{self.ws_url}?model={self.model}",
            headers={"Authorization": f"Bearer {self.api_key}"}, heartbeat=20.0,
            max_msg_size=16 * 1024 * 1024)
        # 等 session.created（容错读几条；协议首事件）
        for _ in range(5):
            msg = await self._ws.receive(timeout=15)
            if msg.type == aiohttp.WSMsgType.TEXT:
                if json.loads(msg.data).get("type") == "session.created":
                    break
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR,
                              aiohttp.WSMsgType.CLOSING):
                raise RuntimeError(f"s2s ws closed before session.created "
                                   f"(code={self._ws.close_code})")
        instructions = system or ""
        if context_summary:
            # 重注入材料并进 instructions（不逐字回放历史——token 成本 + 厂商兼容性，
            # 摘要粒度足够 chitchat 域连续性，RFC §2.3）
            instructions = f"{instructions}\n\n【最近对话摘要】\n{context_summary}".strip()
        sess: dict = {
            "modalities": ["text", "audio"],
            "instructions": instructions,
            "input_audio_format": "pcm16",
            "sample_rate": IN_SAMPLE_RATE,
            "turn_detection": {"type": "server_vad", "threshold": self.vad_threshold,
                               "silence_duration_ms": self.vad_silence_ms},
        }
        if voice:
            sess["voice"] = voice
        if tools:
            sess["tools"] = [escalate_tool()]
            sess["tool_choice"] = "auto"
        await self._send({"type": "session.update", "session": sess})
        # 等 session.updated 并**校验 tools 真被接受**——静默丢弃 tools 的模型不可用于
        # §5.1 分工契约（P0 探针 ★T：qwen3-omni-flash-realtime 就是这种）。
        for _ in range(5):
            msg = await self._ws.receive(timeout=15)
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m.get("type") == "session.updated":
                echoed = (m.get("session") or {}).get("tools")
                names = [t.get("name") for t in echoed] if isinstance(echoed, list) else []
                if tools and "escalate" not in names:
                    raise RuntimeError(
                        f"model {self.model} 静默丢弃 tools（echo={names or '<无>'}）"
                        "——无 escalate 出口，不可用于 S2S 分工契约")
                return
            if m.get("type") == "error":
                raise RuntimeError(f"s2s session.update error: "
                                   f"{json.dumps(m.get('error'), ensure_ascii=False)[:200]}")
        raise RuntimeError("s2s 未收到 session.updated")

    async def send_audio(self, pcm: bytes) -> None:
        await self._send({"type": "input_audio_buffer.append",
                          "audio": base64.b64encode(pcm).decode("ascii")})

    async def commit_audio(self) -> None:
        """补静音尾触发 server VAD 收尾定稿——与 `DashScopeRealtimeASRProvider` 同款打法
        （ASR 面早就踩过这个坑：静音**须长于** silence_duration_ms 才生效，故按它放大帧数）。

        逐帧间隔发送，避免一次性灌爆被服务端丢帧；调用方以 task 形式起，别阻塞上行读循环。
        """
        sil = base64.b64encode(b"\x00" * 3200).decode("ascii")  # 100ms @16k s16le
        tail_frames = max(13, self.vad_silence_ms // 100 + 4)
        for _ in range(tail_frames):
            await self._send({"type": "input_audio_buffer.append", "audio": sil})
            await asyncio.sleep(0.05)

    async def cancel_response(self) -> None:
        # 幂等：无在飞 response 时 provider 回 error，忽略即可（本侧权威，多取消一次无害）
        try:
            await self._send({"type": "response.cancel"})
        except Exception as e:
            logger.debug("s2s cancel_response 忽略: %s", e)

    async def inject_tool_result(self, call_id: str, output: str) -> None:
        # **刻意不发 response.create**：R1 实测——只 create item 则 provider 完全静默，
        # 不会与主链 TTS 撞成双播（RFC §5.2 前提）。
        await self._send({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": call_id, "output": output}})

    async def inject_text(self, role: str, text: str) -> None:
        await self._send({"type": "conversation.item.create", "item": {
            "type": "message", "role": role,
            "content": [{"type": "input_text" if role == "user" else "text", "text": text}]}})

    async def close(self) -> None:
        self._closed = True
        try:
            if self._ws is not None and not self._ws.closed:
                await self._ws.close()
        finally:
            if self._session is not None and not self._session.closed:
                await self._session.close()

    async def events(self) -> AsyncIterator[S2SEvent]:
        """provider 事件 → 归一化事件流。WS 断开即结束（L-Session 据此进 RECONNECTING）。"""
        import aiohttp
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE,
                                    aiohttp.WSMsgType.CLOSING):
                        break
                    continue
                try:
                    m = json.loads(msg.data)
                except Exception:
                    continue
                for ev in self._map(m):
                    yield ev
        except (aiohttp.ClientError, asyncio.IncompleteReadError, ConnectionError) as e:
            yield S2SEvent(kind=EV_ERROR, reason="transport", detail=str(e)[:200])
            return
        if not self._closed:
            yield S2SEvent(kind=EV_ERROR, reason="closed",
                           detail=f"code={self._ws.close_code}")

    def _map(self, m: dict) -> list[S2SEvent]:
        """单条 provider 事件 → 0..n 归一化事件（映射表见 RFC §3.5）。"""
        t = m.get("type", "")
        if t == "response.created":
            return [S2SEvent(kind=EV_TURN_STARTED,
                             response_id=(m.get("response") or {}).get("id", ""))]
        if "input_audio_transcription" in t:
            if t.endswith(".completed"):
                return [S2SEvent(kind=EV_TRANSCRIPT,
                                 text=m.get("transcript") or m.get("text") or "", final=True)]
            if t.endswith(".delta") or t.endswith(".text"):
                # 增量事件带 delta；部分模型走 .text（含 stash 草稿后缀）
                txt = m.get("delta") or ((m.get("text") or "") + (m.get("stash") or ""))
                return [S2SEvent(kind=EV_TRANSCRIPT, text=txt, final=False)] if txt else []
            return []
        if t.endswith("audio_transcript.delta") or t == "response.text.delta":
            d = m.get("delta") or ""
            return [S2SEvent(kind=EV_ANSWER_DELTA, text=d)] if d else []
        if t == "response.audio.delta":
            b64 = m.get("delta") or ""
            if not b64:
                return []
            try:
                return [S2SEvent(kind=EV_AUDIO_DELTA, pcm=base64.b64decode(b64))]
            except Exception:
                return []
        if t == "response.function_call_arguments.done":
            # ★2 实测：name/call_id/arguments 在此齐备，不必等 response.done
            return [S2SEvent(kind=EV_TOOL_CALL, name=m.get("name") or "",
                             args=m.get("arguments") or "", call_id=m.get("call_id") or "")]
        if t == "response.done":
            resp = m.get("response") or {}
            return [S2SEvent(kind=EV_TURN_DONE, reason=resp.get("status") or "completed",
                             response_id=resp.get("id", ""))]
        if t == "error":
            err = m.get("error") or {}
            return [S2SEvent(kind=EV_ERROR, reason="provider",
                             detail=json.dumps(err, ensure_ascii=False)[:200])]
        return []


class MockS2SProvider(BaseS2SProvider):
    """假 provider（无 key / 单测）：闲聊句自答固定话术，含「打开/调/导航/查」等动作词的
    转写产 escalate。**不联网**。真实性铁律③：只在显式 mock 挡位下用，不做运行期静默回退。"""

    out_sample_rate = 24000
    _ACTION_HINTS = ("打开", "关闭", "调到", "调高", "调低", "导航", "查", "播放", "提醒", "支付")

    def __init__(self, *, transcript: str = "你好", answer: str = "嗯，我在呢。"):
        self.transcript = transcript
        self.answer = answer
        self._q: asyncio.Queue[S2SEvent | None] = asyncio.Queue()
        self.sent_audio = 0
        self.commits = 0
        self.cancels = 0
        self.injected: list[tuple[str, str]] = []
        self.opened = False

    async def open(self, *, voice="", system="", context_summary="", tools=True) -> None:
        self.opened = True
        self.open_args = {"voice": voice, "system": system,
                          "context_summary": context_summary, "tools": tools}

    async def send_audio(self, pcm: bytes) -> None:
        self.sent_audio += len(pcm)

    async def commit_audio(self) -> None:
        self.commits += 1

    async def cancel_response(self) -> None:
        self.cancels += 1

    async def inject_tool_result(self, call_id: str, output: str) -> None:
        self.injected.append((call_id, output))

    async def inject_text(self, role: str, text: str) -> None:
        self.injected.append((role, text))

    async def close(self) -> None:
        await self._q.put(None)

    async def feed_utterance(self, transcript: str) -> None:
        """驱动一轮（测试用）：按转写内容决定自答还是 escalate。"""
        await self._q.put(S2SEvent(kind=EV_TURN_STARTED, response_id="mock-resp"))
        await self._q.put(S2SEvent(kind=EV_TRANSCRIPT, text=transcript, final=True))
        if any(h in transcript for h in self._ACTION_HINTS):
            await self._q.put(S2SEvent(kind=EV_TOOL_CALL, name="escalate",
                                       args=json.dumps({"utterance": transcript},
                                                       ensure_ascii=False),
                                       call_id="mock-call"))
            await self._q.put(S2SEvent(kind=EV_TURN_DONE, reason="completed"))
            return
        await self._q.put(S2SEvent(kind=EV_ANSWER_DELTA, text=self.answer))
        await self._q.put(S2SEvent(kind=EV_AUDIO_DELTA, pcm=b"\x00\x01" * 160))
        await self._q.put(S2SEvent(kind=EV_TURN_DONE, reason="completed"))

    async def push(self, ev: S2SEvent) -> None:
        await self._q.put(ev)

    async def events(self) -> AsyncIterator[S2SEvent]:
        while True:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev


# tools 被静默丢弃、不可用于 §5.1 分工契约的型号（P0 探针 ★T 实测）
_TOOLS_UNSUPPORTED = frozenset({
    "qwen3-omni-flash-realtime", "qwen3-omni-realtime", "qwen3-omni-flash",
    "qwen-omni-turbo-realtime", "qwen-omni-turbo-realtime-latest",
})


def s2s_available() -> bool:
    """能力探测（HMI 设置页据此渲染 S2S 挡位可用性）。"""
    prov = (os.getenv("S2S_PROVIDER", "dashscope") or "").strip().lower()
    if prov in ("", "off", "none"):
        return False
    if prov == "mock":
        return True
    return bool(os.getenv("S2S_API_KEY") or os.getenv("DASHSCOPE_ASR_KEY")
                or os.getenv("LLM_EMBED_API_KEY"))


def build_s2s_provider(provider: str = "", model: str = "", *,
                       vad_silence_ms: int = 0) -> BaseS2SProvider | None:
    """按请求/env 选 S2S provider。无可用 key 或 off → None（HMI 回落三段式）。

    拒绝 tools 不支持的型号——**fail-fast 而非静默降级**：让它跑起来只会在真车上
    出「口头答应没办事」（§5.3 体验事故），不如启动就报。
    """
    provider = (provider or os.getenv("S2S_PROVIDER", "dashscope")).strip().lower()
    if provider in ("", "off", "none"):
        return None
    if provider == "mock":
        return MockS2SProvider()
    if provider in ("dashscope", "qwen", "qwen-omni"):
        key = (os.getenv("S2S_API_KEY") or os.getenv("DASHSCOPE_ASR_KEY")
               or os.getenv("LLM_EMBED_API_KEY", ""))
        if not key:
            return None
        mdl = (
            model
            or os.getenv("S2S_MODEL", "")
            or "qwen3.5-omni-flash-realtime"
        )
        if mdl.strip().lower() in _TOOLS_UNSUPPORTED:
            raise ValueError(
                f"S2S 型号 {mdl} 静默丢弃 tools（P0 探针 ★T 实测）——无 escalate 出口，"
                "车控请求会被口头答应而不执行。请用 qwen3.5-omni-flash-realtime。")
        ws = (
            os.getenv("S2S_WS_URL", "")
            or "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
        )
        return QwenOmniRealtimeProvider(
            key, ws, mdl,
            vad_silence_ms=vad_silence_ms or int(
                os.getenv("S2S_VAD_SILENCE_MS", "") or "800",
            ),
            vad_threshold=float(
                os.getenv("S2S_VAD_THRESHOLD", "") or "0.2",
            ))
    return None
