"""M4 P0 协议探针：S2S provider（omni realtime）真实行为矩阵（RFC 2026-07-25-m4-s2s §3.4）。

文档钉不死的点由此钉死——本仓库每次接新协议的固定教训（ASR 双协议、qwen finish_reason
前科、M1a tool_choice 矩阵）。**协议冻结前必跑，厂商 API 迭代后可重跑验证未漂移。**

探的七件（★=RFC §3.4 原列，R=实测中发现的实现级风险）：
  ★T tools 支持度 —— session.update 的 tools 是否被真实接受（被静默丢弃=该模型不可用于
     §5.1 单工具 escalate 分工，安全设计整体不成立）
  ★1 barge-in 竞态 —— response.cancel 后是否仍有音频残包（会话层丢弃策略的依据）
  ★2 事件序 —— function_call 的流式形态、adapter 应在哪个事件产出归一化 tool_call
  ★3 上下文 —— provider 侧多轮是否真保持（§2.3「provider session=可丢弃缓存」的前提：
     provider 持有上下文、本侧只在重连时重注入）
  ★4 时延 —— 中文口语首音频包/首文本增量（灰度门槛硬指标 §6.2）
  R1 双播风险 —— 回注 function_call_output 后 provider 是否**自动续说**（若自动，就会与
     主链 TTS 撞成双播，§5.2「逃逸轮执行结果只为上下文连续、不为播报」必须改设计）
  R2 悬挂韧性 —— function_call 不回注 output 是否坏会话（决定回注失败要不要补偿）

音频源：经跑着的 llm-gateway /api/tts 合成中文再喂回（e2e_voice_loop 同款 round-trip
打法），无需入库二进制音频资产。

前置：`make up`（要 llm-gateway 的 /api/tts）+ 根 .env 带 DashScope key。缺任一 → SKIP。
用法：python test/e2e_s2s_probe.py [--model qwen3.5-omni-flash-realtime]
                                   [--case all|tools|escalate|cancel|inject|context|latency]
                                   [--rounds 3]
"""
from __future__ import annotations
import argparse
import array
import asyncio
import base64
import difflib
import io
import json
import os
import re
import statistics
import sys
import time
import urllib.request
import urllib.error
import wave
from pathlib import Path

from support.e2e import CaseRecorder, is_network_timeout
from support.tts import select_tts_capability

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 控制台 gbk 防崩

_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """最小 .env 加载（同 e2e_real_providers 惯例）：注入 os.environ，不覆盖已有。"""
    configured_root = Path(os.getenv("E2E_STACK_ROOT", ""))
    runtime_root = (
        configured_root
        if configured_root.is_absolute()
        else _ROOT
    )
    path = runtime_root / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

# ── 被探协议的固定参数（与生产实现同源：网关 s2s adapter 直接复用本节常量语义）──
WS_URL = os.getenv("S2S_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
DEFAULT_MODEL = os.getenv("S2S_MODEL", "qwen3.5-omni-flash-realtime")
LLM_HTTP = f"http://{os.getenv('LLM_GATEWAY_HTTP_HOST', 'localhost')}:{os.getenv('AUDIO_HTTP_PORT', '50059')}"

# §5.1 单工具 escalate——S2S 模型只有一个出口，不注入座舱 capability 清单。
ESCALATE_TOOL = {
    "type": "function",
    "name": "escalate",
    "description": (
        "当用户的请求超出闲聊/常识问答范围时调用——车辆控制（空调/车窗/座椅/氛围灯）、导航、"
        "查询实时信息（天气/股价/新闻/赛事）、设置提醒、支付等一切需要执行动作或查询车辆/外部"
        "系统的请求，都必须调用本工具移交，不要自己口头答应。把用户请求原样转述。"
    ),
    "parameters": {
        "type": "object",
        "properties": {"utterance": {"type": "string", "description": "用户请求原话"}},
        "required": ["utterance"],
    },
}
PERSONA = ("你是车载语音助手小舟，回答简短口语化，一两句话。"
           "需要执行动作或查实时信息时必须调用 escalate 工具移交，不要自己口头答应。")

# 探针语料：闲聊句（应自答）/ 车控句（应 escalate）/ 记名+问名（★3 上下文）
UTT_CHAT = "今天心情不错，给我讲个冷笑话吧"
UTT_CONTROL = "把空调调到二十四度"
UTT_NAME = "我叫小明，记住我的名字"
UTT_ASK_NAME = "我刚才说我叫什么名字"
_SPOKEN_NAME_RE = re.compile(r"叫([\u4e00-\u9fff]{2,4})")


def _context_recall_matches(transcript: str, answer: str) -> bool:
    """Compare provider memory against what its ASR actually heard."""

    heard = _SPOKEN_NAME_RE.search(transcript or "")
    recalled = _SPOKEN_NAME_RE.search(answer or "")
    if not heard or not recalled:
        return False
    contextual = any(marker in answer for marker in ("刚才", "之前", "你说"))
    similarity = difflib.SequenceMatcher(
        None,
        heard.group(1),
        recalled.group(1),
    ).ratio()
    return contextual and similarity >= 0.5


def _cancel_residual_is_bounded(status: str | None, deltas: int) -> bool:
    """Allow one already-in-flight provider packet; the session drops it."""

    return status == "cancelled" and 0 <= deltas <= 1


def _is_empty_acoustic_transport_failure(result: dict) -> bool:
    """Retry only when the provider produced no usable acoustic observation.

    A completed turn with no escalate is a real protocol verdict and must fail.
    A transcript or tool call is also evidence, even if the socket later errors.
    """

    if result.get("transcript") or result.get("tool_call"):
        return False
    status = result.get("status")
    return status is None or status == "error" or str(status).startswith("ws_closed:")


def _api_key() -> str:
    return (os.getenv("S2S_API_KEY") or os.getenv("DASHSCOPE_ASR_KEY")
            or os.getenv("LLM_EMBED_API_KEY") or "")


# ── 音频源：/api/tts 合成 → 16k mono s16le ──
def _record_tts_preflight_error(
    recorder: CaseRecorder,
    exc: BaseException,
) -> None:
    """Classify transport absence separately from TTS runtime failures."""

    if isinstance(exc, urllib.error.HTTPError):
        recorder.fail_case(
            "s2s_provider_protocol",
            "provider_http_error",
            f"llm-gateway TTS returned HTTP {exc.code}",
        )
    elif is_network_timeout(exc):
        recorder.fail_case(
            "s2s_provider_protocol",
            "provider_timeout",
            "llm-gateway TTS preflight timed out",
        )
    elif isinstance(
        exc,
        (urllib.error.URLError, ConnectionError),
    ):
        recorder.skip_case(
            "s2s_provider_protocol",
            "provider_unavailable",
            "llm-gateway TTS endpoint is unavailable",
        )
    else:
        recorder.fail_case(
            "s2s_provider_protocol",
            "provider_execution_failed",
            f"llm-gateway TTS preflight failed: {type(exc).__name__}",
        )


_pcm_cache: dict[str, bytes] = {}


def synth_pcm16k(text: str) -> bytes:
    """经 llm-gateway /api/tts 合成中文语音，转 16k mono s16le 裸 PCM（探针输入）。"""
    if text in _pcm_cache:
        return _pcm_cache[text]
    with urllib.request.urlopen(
        f"{LLM_HTTP}/api/tts/stream/info",
        timeout=10,
    ) as response:
        tts_info = json.loads(response.read())
    provider, voice_id = select_tts_capability(tts_info)
    req = urllib.request.Request(
        f"{LLM_HTTP}/api/tts", method="POST",
        data=json.dumps({
            "text": text,
            "format": "wav",
            "provider": provider,
            "voice_id": voice_id,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read())
    if data.get("error"):
        raise RuntimeError(f"TTS 失败: {data['error']}")
    with wave.open(io.BytesIO(base64.b64decode(data["audio"])), "rb") as w:
        ch, fr, n = w.getnchannels(), w.getframerate(), w.getnframes()
        pcm = w.readframes(n)
    a = array.array("h")
    a.frombytes(pcm)
    if ch == 2:
        a = array.array("h", [(a[i] + a[i + 1]) // 2 for i in range(0, len(a) - 1, 2)])
    if fr != 16000:  # 线性重采样（探针精度够用；生产侧 HMI 直接采 16k）
        ratio = 16000 / fr
        out = array.array("h", [0]) * int(len(a) * ratio)
        for i in range(len(out)):
            src = i / ratio
            j = int(src)
            if j + 1 < len(a):
                f = src - j
                out[i] = int(a[j] * (1 - f) + a[j + 1] * f)
            elif j < len(a):
                out[i] = a[j]
        a = out
    _pcm_cache[text] = a.tobytes()
    return _pcm_cache[text]


class Probe:
    """一条 realtime 会话的最小驱动器（只做探测，不含生产侧状态机）。"""

    def __init__(self, ws, model: str):
        self.ws = ws
        self.model = model
        self._eid = 0
        self.created: dict = {}
        self.updated: dict = {}
        self.event_types: list[str] = []

    async def send(self, obj: dict) -> None:
        self._eid += 1
        await self.ws.send_json({"event_id": f"ev{self._eid}", **obj})

    async def recv(self, timeout: float) -> dict:
        import aiohttp
        msg = await self.ws.receive(timeout=timeout)
        if msg.type != aiohttp.WSMsgType.TEXT:
            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                return {"type": "__closed__", "code": self.ws.close_code}
            return {"type": "__skip__"}
        m = json.loads(msg.data)
        t = m.get("type", "")
        if t and t not in self.event_types:
            self.event_types.append(t)
        return m

    async def _await_type(self, want: str, timeout: float) -> dict | None:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            m = await self.recv(timeout - (time.monotonic() - t0))
            if m.get("type") == want:
                return m
            if m.get("type") in ("error", "__closed__"):
                return m
        return None

    async def open(self, *, tools: bool = True, audio: bool = True) -> tuple[dict, dict]:
        """建会话并注入 session.update；返回 (created.session, updated.session)。"""
        m = await self._await_type("session.created", 15)
        if not m or m.get("type") != "session.created":
            raise RuntimeError(f"未拿到 session.created: {m}")
        self.created = m.get("session", {}) or {}
        sess: dict = {
            "modalities": ["text", "audio"] if audio else ["text"],
            "instructions": PERSONA,
            "input_audio_format": "pcm16",
            "sample_rate": 16000,
            "turn_detection": {"type": "server_vad", "threshold": 0.2, "silence_duration_ms": 800},
        }
        if tools:
            sess["tools"] = [ESCALATE_TOOL]
            sess["tool_choice"] = "auto"
        await self.send({"type": "session.update", "session": sess})
        u = await self._await_type("session.updated", 15)
        self.updated = (u or {}).get("session", {}) or {}
        return self.created, self.updated

    async def push_audio(self, pcm: bytes, *, tail_frames: int = 13) -> float:
        """推 PCM + 静音尾（触发 server VAD 收尾）；返回推完的时刻（时延基准）。"""
        step = 3200  # 100ms @16k s16le
        for i in range(0, len(pcm), step):
            await self.send({"type": "input_audio_buffer.append",
                             "audio": base64.b64encode(pcm[i:i + step]).decode("ascii")})
        sil = base64.b64encode(b"\x00" * step).decode("ascii")
        for _ in range(tail_frames):
            await self.send({"type": "input_audio_buffer.append", "audio": sil})
        return time.monotonic()

    async def run_turn(self, pcm: bytes, *, timeout: float = 45.0,
                       cancel_after_deltas: int = 0) -> dict:
        """跑一轮音频对话，返回观测量。cancel_after_deltas>0 → 第 N 个音频 delta 后送 cancel（★1）。"""
        t_push = await self.push_audio(pcm)
        r = {"transcript": "", "answer": "", "tool_call": None, "status": None,
             "audio_deltas": 0, "audio_bytes": 0, "first_audio_ms": None,
             "first_text_ms": None, "post_cancel_deltas": 0, "post_cancel_bytes": 0,
             "transcript_done_full": ""}
        cancelled = False
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            try:
                m = await self.recv(timeout - (time.monotonic() - t0))
            except asyncio.TimeoutError:
                break
            t = m.get("type", "")
            if t == "__skip__":
                continue
            if t == "__closed__":
                r["status"] = f"ws_closed:{m.get('code')}"
                break
            if t == "error":
                r["status"] = "error"
                r["error"] = json.dumps(m.get("error"), ensure_ascii=False)[:300]
                break
            if "input_audio_transcription" in t:
                r["transcript"] = m.get("transcript") or m.get("text") or r["transcript"]
            elif t.endswith("audio_transcript.delta") or t.endswith("text.delta"):
                if r["first_text_ms"] is None:
                    r["first_text_ms"] = (time.monotonic() - t_push) * 1000
                r["answer"] += m.get("delta", "") or ""
            elif t.endswith("audio_transcript.done"):
                # ★1 实测：被 cancel 的轮次这里仍带**完整全文**（模型已生成完的），
                # ≠ 用户实际听到的内容——回灌 assistant 文本时须按截断处理。
                r["transcript_done_full"] = m.get("transcript") or ""
            elif t == "response.text.done":
                r["answer"] = m.get("text") or r["answer"]
            elif t == "response.audio.delta":
                b = len(base64.b64decode(m.get("delta", "") or ""))
                r["audio_deltas"] += 1
                r["audio_bytes"] += b
                if r["first_audio_ms"] is None:
                    r["first_audio_ms"] = (time.monotonic() - t_push) * 1000
                if cancelled:
                    r["post_cancel_deltas"] += 1
                    r["post_cancel_bytes"] += b
                elif cancel_after_deltas and r["audio_deltas"] >= cancel_after_deltas:
                    await self.send({"type": "response.cancel"})
                    cancelled = True
            elif t == "response.function_call_arguments.done":
                # ★2：name/call_id/arguments 在此齐备 → adapter 归一化 tool_call 的落点
                r["tool_call"] = {"name": m.get("name"), "call_id": m.get("call_id"),
                                  "arguments": m.get("arguments")}
            elif t == "response.done":
                resp = m.get("response") or {}
                r["status"] = resp.get("status")
                r["output_types"] = [o.get("type") for o in (resp.get("output") or [])]
                break
        return r

    async def inject_tool_result(self, call_id: str, output: str) -> None:
        """回注工具结果（**不发 response.create**——R1 实测据此判定是否自动续说）。"""
        await self.send({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": call_id, "output": output}})

    async def drain(self, seconds: float) -> list[str]:
        """收 N 秒内所有事件类型（用于判「静默 vs 自动续说」）。"""
        got: list[str] = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            try:
                m = await self.recv(seconds - (time.monotonic() - t0))
            except asyncio.TimeoutError:
                break
            t = m.get("type", "")
            if t and t != "__skip__":
                got.append(t)
        return got


class Report:
    def __init__(self):
        self.rows: list[tuple[str, bool | None, str]] = []

    def add(self, name: str, ok: bool | None, detail: str) -> None:
        self.rows.append((name, ok, detail))
        mark = {True: "✓", False: "✗", None: "·"}[ok]
        print(f"  [{mark}] {name}：{detail}")

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.rows if ok is False)


async def _session(key: str, model: str):
    import aiohttp
    s = aiohttp.ClientSession()
    ws = await s.ws_connect(f"{WS_URL}?model={model}",
                            headers={"Authorization": f"Bearer {key}"}, heartbeat=20.0)
    return s, ws


async def case_tools(key: str, model: str, rep: Report) -> None:
    """★T：tools 是否被真实接受。被静默丢弃 → 该模型不可用于 §5.1 分工契约。"""
    print("\n--- ★T tools 支持度（session.update 回显）---")
    s, ws = await _session(key, model)
    try:
        p = Probe(ws, model)
        created, updated = await p.open()
        print(f"  created: voice={created.get('voice')} in={created.get('input_audio_format')} "
              f"out={created.get('output_audio_format')} asr={created.get('input_audio_transcription')}")
        echoed = updated.get("tools")
        names = [t.get("name") for t in echoed] if isinstance(echoed, list) else []
        rep.add("tools 被接受", bool(names) and "escalate" in names,
                f"session.updated.tools={names or '<被静默丢弃>'}"
                + ("" if names else " ←该模型不可用于单工具 escalate 分工"))
        # server VAD 的两个默认位直接决定 L-Session 设计（自动 create_response / 自动打断）
        td = created.get("turn_detection") or {}
        rep.add("server VAD 自动生成 response", td.get("create_response") is not False,
                f"create_response={td.get('create_response')} interrupt_response={td.get('interrupt_response')}"
                " →轮次由 provider 自驱，L-Session 不必手动 response.create")
    finally:
        await ws.close()
        await s.close()


async def case_escalate(key: str, model: str, rep: Report) -> None:
    """★2：闲聊自答 vs 车控 escalate，及 function_call 事件形态。"""
    print("\n--- ★2 分流与 function_call 事件形态（音频路径）---")
    s, ws = await _session(key, model)
    try:
        p = Probe(ws, model)
        await p.open()
        r1 = await p.run_turn(synth_pcm16k(UTT_CHAT))
        rep.add("闲聊句自答不误触发", r1["tool_call"] is None and bool(r1["answer"]),
                f"转写={r1['transcript']!r} 回答={r1['answer'][:40]!r} tool_call={r1['tool_call']}")
        r2 = await p.run_turn(synth_pcm16k(UTT_CONTROL))
        if _is_empty_acoustic_transport_failure(r2):
            # Real-time provider occasionally closes an otherwise healthy session before
            # ASR emits anything. One fresh-session retry distinguishes transport/acoustic
            # loss from a completed model decision; completed wrong routing is never retried.
            print(
                "  车控轮无转写/工具事件，使用新 session 有界重试一次："
                f"status={r2.get('status')} error={r2.get('error', '')[:120]}"
            )
            await ws.close()
            await s.close()
            s, ws = await _session(key, model)
            p = Probe(ws, model)
            await p.open()
            r2 = await p.run_turn(synth_pcm16k(UTT_CONTROL))
        tc = r2["tool_call"]
        ok = bool(tc) and tc.get("name") == "escalate"
        args_ok = False
        if ok:
            try:
                args_ok = bool(json.loads(tc["arguments"]).get("utterance"))
            except Exception:
                args_ok = False
        rep.add("车控句发 escalate", ok and args_ok,
                f"转写={r2['transcript']!r} tool_call={tc}")
        # 逃逸轮零音频 = §6.2「判定点在生成前」的听感前提（不会播到一半换嗓子）
        rep.add("逃逸轮零音频零文本", r2["audio_deltas"] == 0 and not r2["answer"],
                f"audio_deltas={r2['audio_deltas']} answer={r2['answer'][:30]!r}"
                " →模型不会先口头答应再移交")
        print(f"  事件谱: {p.event_types}")
    finally:
        await ws.close()
        await s.close()


async def case_cancel(key: str, model: str, rep: Report) -> None:
    """★1：播报中 response.cancel 的残包行为。"""
    print("\n--- ★1 barge-in 竞态（播报中 response.cancel）---")
    s, ws = await _session(key, model)
    try:
        p = Probe(ws, model)
        await p.open()
        r = await p.run_turn(synth_pcm16k(UTT_CHAT), cancel_after_deltas=3)
        rep.add("cancel 令 response 终止", r["status"] == "cancelled",
                f"response.done status={r['status']}")
        rep.add("cancel 后音频残包有界", _cancel_residual_is_bounded(
            r["status"],
            r["post_cancel_deltas"],
        ),
                f"cancel 后 {r['post_cancel_deltas']} 个 delta / {r['post_cancel_bytes']} bytes"
                "（至多允许 1 个在飞包；本侧必须丢弃）")
        trunc = r["transcript_done_full"]
        rep.add("被打断轮的文本≠已播内容", bool(trunc) and len(trunc) > len(r["answer"]) * 0.5,
                f"audio_transcript.done 带全文 {len(trunc)} 字、实播音频仅 {r['audio_deltas']} 包"
                " →回灌须标截断，不可当「用户听到的」")
    finally:
        await ws.close()
        await s.close()


async def case_inject(key: str, model: str, rep: Report) -> None:
    """R1/R2：回注是否自动续说（双播风险）、悬挂 call 是否坏会话。"""
    print("\n--- R1 回注不自动续说（双播风险）/ R2 悬挂 call 韧性 ---")
    s, ws = await _session(key, model)
    try:
        p = Probe(ws, model)
        await p.open()
        r = await p.run_turn(synth_pcm16k(UTT_CONTROL))
        tc = r["tool_call"]
        if not tc:
            rep.add("R1 前置（拿到 function_call）", False, "车控句未 escalate，R1/R2 无法测")
            return
        await p.inject_tool_result(tc["call_id"], "已执行：空调已设为24度（主链已播报，勿重复播报）")
        got = await p.drain(6)
        auto = [t for t in got if t in ("response.created", "response.audio.delta")]
        rep.add("回注后不自动续说", not auto,
                f"6s 内事件={got or '<静默>'}"
                + ("  ←!!会与主链播报撞成双播，§5.2 需改设计" if auto else "  →§5.2 前提成立"))
        r2 = await p.run_turn(synth_pcm16k(UTT_NAME))
        rep.add("回注后会话仍健康", r2["status"] == "completed" and bool(r2["answer"]),
                f"下一轮 status={r2['status']} 回答={r2['answer'][:40]!r}")
    finally:
        await ws.close()
        await s.close()

    # R2：完全不回注，直接进下一轮
    s, ws = await _session(key, model)
    try:
        p = Probe(ws, model)
        await p.open()
        r = await p.run_turn(synth_pcm16k(UTT_CONTROL))
        if not r["tool_call"]:
            rep.add("R2 前置（拿到 function_call）", None, "本次车控句未 escalate，跳过")
            return
        r2 = await p.run_turn(synth_pcm16k(UTT_NAME))
        rep.add("悬挂 call 不坏会话", r2["status"] == "completed" and bool(r2["answer"]),
                f"不回注后下一轮 status={r2['status']} 回答={r2['answer'][:40]!r}"
                " →回注失败无需补偿")
    finally:
        await ws.close()
        await s.close()


async def case_context(key: str, model: str, rep: Report) -> None:
    """★3：provider 侧多轮上下文保持（§2.3 前提）。"""
    print("\n--- ★3 provider 侧多轮上下文 ---")
    s, ws = await _session(key, model)
    try:
        p = Probe(ws, model)
        await p.open()
        r1 = await p.run_turn(synth_pcm16k(UTT_NAME))
        r2 = await p.run_turn(synth_pcm16k(UTT_ASK_NAME))
        ans = r2["answer"]
        ok = _context_recall_matches(r1["transcript"], ans)
        rep.add(
            "provider 保持多轮上下文",
            ok,
            f"轮1 转写={r1['transcript'][:40]!r} 轮2 回答={ans[:60]!r} "
            "→§2.3「provider 持有上下文、本侧只在重连重注入」成立",
        )
    finally:
        await ws.close()
        await s.close()


async def case_latency(key: str, model: str, rep: Report, rounds: int) -> None:
    """★4：中文口语首音频包/首文本增量时延（灰度门槛硬指标）。"""
    print(f"\n--- ★4 首响应时延（{rounds} 轮采样）---")
    audio_ms: list[float] = []
    text_ms: list[float] = []
    sr_note = ""
    for i in range(rounds):
        s, ws = await _session(key, model)
        try:
            p = Probe(ws, model)
            await p.open()
            r = await p.run_turn(synth_pcm16k(UTT_CHAT))
            if r["first_audio_ms"]:
                audio_ms.append(r["first_audio_ms"])
            if r["first_text_ms"]:
                text_ms.append(r["first_text_ms"])
            if r["audio_bytes"] and not sr_note:
                # 输出采样率：session 只报 "pcm"，按「字节数 ÷ 2 ÷ 采样率 ≈ 语音时长」反推
                secs24 = r["audio_bytes"] / 2 / 24000
                secs16 = r["audio_bytes"] / 2 / 16000
                sr_note = (f"{r['audio_bytes']} bytes → 24k≈{secs24:.2f}s / 16k≈{secs16:.2f}s "
                           f"（{len(r['answer'])} 字，据语速判 24kHz）")
            print(f"  轮{i+1}: 首音频={r['first_audio_ms'] and round(r['first_audio_ms'])}ms "
                  f"首文本={r['first_text_ms'] and round(r['first_text_ms'])}ms "
                  f"音频={r['audio_deltas']}包/{r['audio_bytes']}B")
        finally:
            await ws.close()
            await s.close()
    if audio_ms:
        rep.add("首音频包时延", None,
                f"样本={[round(x) for x in audio_ms]}ms P50={statistics.median(audio_ms):.0f} "
                f"max={max(audio_ms):.0f}")
    if text_ms:
        rep.add("首文本增量时延", None,
                f"样本={[round(x) for x in text_ms]}ms P50={statistics.median(text_ms):.0f}")
    if sr_note:
        rep.add("输出采样率（反推）", None, sr_note)


CASES = {
    "tools": case_tools,
    "escalate": case_escalate,
    "cancel": case_cancel,
    "inject": case_inject,
    "context": case_context,
}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--case", default="all",
                    help="all | " + " | ".join(list(CASES) + ["latency"]))
    ap.add_argument("--rounds", type=int, default=3, help="★4 时延采样轮数")
    args = ap.parse_args()

    recorder = CaseRecorder()
    with recorder:
        key = _api_key()
        if not key:
            print("SKIP：无 DashScope key")
            recorder.skip_case(
                "s2s_provider_protocol",
                "credential_unavailable",
                "no S2S provider credential is available",
            )
        else:
            try:
                import aiohttp  # noqa: F401
            except ImportError:
                recorder.skip_case(
                    "s2s_provider_protocol",
                    "profile_unavailable",
                    "aiohttp is not installed in the acoustic profile",
                )
            else:
                try:
                    synth_pcm16k(UTT_CHAT)
                except Exception as exc:
                    print(
                        "S2S TTS preflight failed: "
                        f"{type(exc).__name__}",
                    )
                    _record_tts_preflight_error(recorder, exc)
                else:
                    print(
                        f"S2S 协议探针  model={args.model}  ws={WS_URL}",
                    )
                    rep = Report()
                    want = (
                        list(CASES) + ["latency"]
                        if args.case == "all"
                        else [args.case]
                    )
                    for name in want:
                        if name not in CASES and name != "latency":
                            recorder.fail_case(
                                "invalid_probe_selection",
                                "invalid_argument",
                                "unknown S2S probe case",
                            )
                            continue
                        before = len(rep.rows)
                        if name == "latency":
                            await case_latency(
                                key,
                                args.model,
                                rep,
                                args.rounds,
                            )
                        else:
                            await CASES[name](key, args.model, rep)
                        rows = rep.rows[before:]
                        hard = [ok for _, ok, _ in rows if ok is not None]
                        if any(ok is False for ok in hard):
                            recorder.fail_case(
                                f"s2s_{name}",
                                "provider_protocol_error",
                                "one or more provider protocol assertions failed",
                            )
                        else:
                            recorder.pass_case(f"s2s_{name}")
                        for index, (_label, ok, detail) in enumerate(
                            rows,
                            start=1,
                        ):
                            if ok is None and "跳过" in detail:
                                recorder.skip_case(
                                    f"s2s_{name}_optional_{index}",
                                    "data_unavailable",
                                    "optional provider behavior was not observed",
                                )

                    print("\n" + "=" * 72)
                    hard_rows = [
                        (name, ok)
                        for name, ok, _ in rep.rows
                        if ok is not None
                    ]
                    print(
                        f"探针结论："
                        f"{sum(1 for _, ok in hard_rows if ok)}/"
                        f"{len(hard_rows)} 项协议断言通过"
                        f"（另 {len(rep.rows) - len(hard_rows)} 项为量测记录）",
                    )
                    if rep.failed:
                        print(f"✗ {rep.failed} 项不符")
                    else:
                        print("✓ 协议形态与 RFC §3 一致")
    return recorder.exit_code()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
