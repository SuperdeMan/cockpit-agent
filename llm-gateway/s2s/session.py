"""L-Session：S2S 会话状态机（M4 RFC §4.2 / §4.3 / §4.5）。

三层状态机里的中间层。**铁律（RFC §2.2）：下层事件永远不直接驱动上层状态迁移，只作为
上层的输入。** 本层对 provider 事件做对账/丢弃/翻译，产出对上事件；用户可感知状态由
L-HMI（voiceLoop）决定，本层不假设 HMI 在什么态。

纯逻辑 + 全注入（provider 工厂 / 下行发送 / 上下文取用 / 回灌），无 aiohttp.web 依赖 →
node 式可单测（同 voiceLoop / ledger 的做法）。

```
CONNECTING → READY ⇄ IN_TURN（turn_started..turn_done）
     ↑          │
     └── RECONNECTING（指数退避 ≤3）── 超限 → DEGRADED（下行 session.state，HMI 回落三段式）
```
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field

from . import protocol as P
from .provider import (
    BaseS2SProvider, EV_ANSWER_DELTA, EV_AUDIO_DELTA, EV_ERROR, EV_TOOL_CALL,
    EV_TRANSCRIPT, EV_TURN_DONE, EV_TURN_STARTED, S2SEvent,
)

logger = logging.getLogger("llm.s2s.session")


class SessionState:
    CONNECTING = "connecting"
    READY = "ready"
    IN_TURN = "in_turn"
    RECONNECTING = "reconnecting"
    DEGRADED = "degraded"
    CLOSED = "closed"


# turn 生命周期（内部态；对上只出 turn.end 的 reason）
T_ACTIVE = "active"
T_CANCELLING = "cancelling"    # barge-in 后：其后到达的该 response 增量/音频全部丢弃
T_ESCALATED = "escalated"      # 已移交主链，等 escalated_result 回灌 provider 上下文
T_ABANDONED = "abandoned"      # §4.3③ 工具调用中被打断：结果回来不 inject、不播报
T_DONE = "done"


@dataclass
class Turn:
    turn_id: str
    response_id: str = ""
    transcript: str = ""
    answer: str = ""          # **已播出的增量累积**——不是 provider 的全文（★1 实测：
    #                            被打断轮 provider 仍给完整全文，那≠用户听到的）
    status: str = T_ACTIVE
    escalate_call_id: str = ""
    utterance: str = ""       # 移交给主链的请求 = 这一轮的最终转写（评审四轮 R4-06）
    interpretation: str = ""  # 模型工具参数里的 utterance：它对请求的**解读**，只留痕，不作请求
    pending_call_id: str = ""  # 工具调用已到、转写还没定稿：这次移交在等转写
    audio_bytes: int = 0
    audio_meta_sent: bool = False
    truncated: bool = False
    started_at: float = field(default_factory=time.monotonic)
    end_reason: str = ""

    @property
    def escalated(self) -> bool:
        return self.status in (T_ESCALATED, T_ABANDONED)


class S2SSession:
    """一条 HMI ↔ provider 的 S2S 会话。

    注入：
      provider_factory()   → BaseS2SProvider（重连要能重新造一个）
      emit_json(dict)      → 发文本下行帧
      emit_audio(bytes)    → 发二进制 PCM 下行帧
      context_provider()   → str  重注入材料（近 N 轮摘要；网关向 memory 取，HMI 不组装）
      reflux(turn)         → None 每轮收束后的强制回灌（§7 防黑洞）
    """

    def __init__(self, *, provider_factory, emit_json, emit_audio,
                 context_provider=None, reflux=None, session_id: str = "",
                 user_id: str = "", voice: str = "", now=time.monotonic,
                 max_reconnects: int = 3, max_turns: int = 0,
                 reconnect_backoff=(0.5, 1.5, 3.0), ring_max_bytes: int = 64000,
                 turn_timeout_s: float = 0, escalate_transcript_wait_s: float = 0):
        self._provider_factory = provider_factory
        self._emit_json = emit_json
        self._emit_audio = emit_audio
        self._context_provider = context_provider
        self._reflux = reflux
        self.session_id = session_id
        self.user_id = user_id
        self.voice = voice
        self.now = now
        self.max_reconnects = max_reconnects
        # 长会话累积治理（★3 未钉死上限 → 给旋钮主动重建，复用重连路径，零新机制）
        self.max_turns = max_turns or int(
            os.getenv("S2S_SESSION_MAX_TURNS", "") or "20",
        )
        self.backoff = reconnect_backoff
        self.ring_max = ring_max_bytes  # 重连期音频前滚缓冲（≈2s @16k s16le）
        # turn 悬挂看门狗：turn 开了却迟迟不 done → 诚实收束（同 voiceLoop thinkingMaxMs 与
        # M2 Ledger「可查可停可诚实报告中断」的思想）。**真栈验证时踩到的真实缺口**：客户端
        # 慢读 → 下行 send 背压 → 事件泵阻塞 → provider 侧数据丢 → 该 turn 永不 done，
        # HMI 干等到 100s 兜底才解。不追究具体成因，任何原因导致的悬挂都在此收口。
        self.turn_timeout_s = turn_timeout_s or float(
            os.getenv("S2S_TURN_TIMEOUT_S", "") or "45",
        )
        # 评审四轮 R4-06：移交要等这一轮的转写定稿（工具调用可能早于 `transcription.completed`）。
        # 等不到就不移交——模型的重述不能顶替原话去主链当请求。
        self.escalate_transcript_wait_s = escalate_transcript_wait_s or float(
            os.getenv("S2S_ESCALATE_TRANSCRIPT_WAIT_S", "") or "2.0",
        )

        self.state = SessionState.CONNECTING
        self.provider: BaseS2SProvider | None = None
        self.turn: Turn | None = None
        self.turns_done = 0
        self._by_id: dict[str, Turn] = {}   # turn_id → Turn（escalated_result 回查）
        self._pump: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None
        self._transcript_wait: asyncio.Task | None = None  # 在等转写定稿的那次移交（R4-06）
        self._commit: asyncio.Task | None = None  # 在途的音频段收尾（静音尾）
        self._ring = bytearray()            # 重连期 HMI 音频缓冲
        self._reconnecting = False
        self._closed = False

    # ── 生命周期 ──
    async def start(self) -> None:
        """建 provider 会话并起事件泵。失败 → 抛给调用方（下行 unsupported/degraded）。"""
        self.provider = self._provider_factory()
        summary = await self._summary()
        await self.provider.open(voice=self.voice, system=P.persona(),
                                context_summary=summary, tools=True)
        self.state = SessionState.READY
        self._pump = asyncio.create_task(self._run_pump())
        await self._emit_json({"type": P.DOWN_SESSION_STATE, "state": P.STATE_READY})

    async def close(self) -> None:
        self._closed = True
        self.state = SessionState.CLOSED
        self._clear_watchdog()
        self._cancel_transcript_wait()
        self._cancel_commit()
        if self._pump is not None:
            self._pump.cancel()
        if self.provider is not None:
            try:
                await self.provider.close()
            except Exception:
                pass

    async def _summary(self) -> str:
        if self._context_provider is None:
            return ""
        try:
            return await self._context_provider() or ""
        except Exception as e:  # 上下文取不到不该拖死会话（fail-open）
            logger.warning("s2s 上下文重注入取用失败（继续开会话）: %s", e)
            return ""

    # ── 上行入口（HMI → 本层）──
    async def push_audio(self, pcm: bytes) -> None:
        """HMI 音频帧。重连期进前滚缓冲（≤ring_max），恢复后续灌。"""
        # 新音频到达 = 用户又说话了 → 撤掉在途的静音尾，别让它污染这一段
        self._cancel_commit()
        if self.state in (SessionState.RECONNECTING, SessionState.CONNECTING):
            self._ring.extend(pcm)
            if len(self._ring) > self.ring_max:
                del self._ring[:len(self._ring) - self.ring_max]
            return
        if self.state == SessionState.DEGRADED or self.provider is None:
            return
        try:
            await self.provider.send_audio(pcm)
        except Exception as e:
            logger.debug("s2s send_audio 失败 → 触发重连: %s", e)
            await self._schedule_reconnect()

    async def audio_done(self) -> None:
        """本轮音频段推完（L-HMI 的 VAD 判到端点）→ 请 provider 收尾定稿。

        **起后台 task 而不是 await**：静音尾要逐帧间隔发（~0.65s），在上行读循环里
        await 会把 barge_in 等帧一起堵住。
        """
        if self.provider is None or self.state == SessionState.DEGRADED:
            return
        self._cancel_commit()
        prov = self.provider

        async def commit():
            try:
                await prov.commit_audio()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("s2s commit_audio 失败: %s", e)

        self._commit = asyncio.create_task(commit())

    def _cancel_commit(self) -> None:
        if self._commit is not None:
            self._commit.cancel()
            self._commit = None

    async def barge_in(self) -> None:
        """①听感打断（RFC §4.3）：本侧权威。立即 cancel + 后续该 response 增量全部丢弃。

        ③工具调用中打断：当前 turn 已 escalated（主链在飞）→ 标 abandoned——结果回来
        不 inject、不播报，**但副作用步照常走完确认链**（打断≠回滚，那由主链自己收束）。
        """
        t = self.turn
        if t is None:
            return
        if t.status == T_ESCALATED:
            t.status = T_ABANDONED
            return
        if t.status != T_ACTIVE:
            return
        t.status = T_CANCELLING
        t.truncated = True
        self._cancel_commit()  # 打断了就别再补静音尾催它说完
        if self.provider is not None:
            await self.provider.cancel_response()  # 幂等
        # keep_status：保留 CANCELLING 而非置 DONE——turn.end 已下行，但 provider 侧
        # 残包可能还在飞，这个态就是「正在丢残包」的权威标记（调试时能与正常结束区分）。
        await self._end_turn(t, P.END_CANCELLED, keep_status=True)

    async def cancel_turn(self) -> None:
        """②任务打断的 S2S 侧对应（THINKING 期取消）。语义同 barge_in 的取消面。"""
        await self.barge_in()

    async def escalated_result(self, turn_id: str, text: str) -> None:
        """逃逸轮主链回答回传 → 注入 provider 上下文（**不触发续说**，见 §5.2/R1）。"""
        t = self._by_id.get(turn_id)
        if t is None or not t.escalate_call_id:
            return
        if t.status == T_ABANDONED:
            logger.debug("s2s 丢弃 abandoned turn 的 escalated_result（不 inject 不播报）")
            return
        if self.provider is None or self.state == SessionState.DEGRADED:
            return
        try:
            await self.provider.inject_tool_result(t.escalate_call_id, text or "（已处理）")
        except Exception as e:
            # R2 实测：悬挂 function_call 不坏会话 → 失败无需补偿
            logger.debug("s2s inject_tool_result 失败（悬挂无害）: %s", e)

    # ── provider 事件泵 ──
    async def _run_pump(self) -> None:
        while not self._closed:
            prov = self.provider
            if prov is None:
                return
            try:
                async for ev in prov.events():
                    await self._on_event(ev)
                    if self._closed:
                        return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("s2s 事件泵异常: %s", e)
            if self._closed:
                return
            # 事件流结束（WS 断）→ 重连；成功则继续泵新 provider 的事件
            if not await self._reconnect():
                return

    async def _on_event(self, ev: S2SEvent) -> None:
        k = ev.kind
        if k == EV_TURN_STARTED:
            await self._on_turn_started(ev)
        elif k == EV_TRANSCRIPT:
            await self._on_transcript(ev)
        elif k == EV_ANSWER_DELTA:
            await self._on_answer_delta(ev)
        elif k == EV_AUDIO_DELTA:
            await self._on_audio_delta(ev)
        elif k == EV_TOOL_CALL:
            await self._on_tool_call(ev)
        elif k == EV_TURN_DONE:
            await self._on_turn_done(ev)
        elif k == EV_ERROR:
            await self._on_error(ev)

    def _ensure_turn(self) -> Turn:
        """转写可能早于 response.created 到达（实测两种序都出现过）→ 谁先来谁开 turn。

        判据取「仅 ACTIVE 才复用」：CANCELLING 的 turn 虽仍在丢残包，但它已下行过
        turn.end，下一轮事件必须开新 turn——否则打断后的新一轮会复用死 turn。
        """
        if self.turn is None or self.turn.status != T_ACTIVE:
            t = Turn(turn_id=P.new_turn_id())
            self.turn = t
            self._by_id[t.turn_id] = t
            self.state = SessionState.IN_TURN
            self._arm_watchdog(t)
        return self.turn

    def _arm_watchdog(self, t: Turn) -> None:
        """给 turn 挂悬挂看门狗（每 turn 一个，收束时清）。"""
        self._clear_watchdog()
        if self.turn_timeout_s <= 0:
            return

        async def bark():
            try:
                await asyncio.sleep(self.turn_timeout_s)
            except asyncio.CancelledError:
                return
            if t.end_reason or self._closed:
                return
            logger.warning("s2s turn %s 悬挂超 %.0fs 未收束 → 诚实收束",
                           t.turn_id, self.turn_timeout_s)
            t.truncated = True
            await self._end_turn(t, P.END_ERROR, detail="provider_silent")

        self._watchdog = asyncio.create_task(bark())

    def _clear_watchdog(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    async def _on_turn_started(self, ev: S2SEvent) -> None:
        t = self._ensure_turn()
        t.response_id = ev.response_id or t.response_id

    async def _on_transcript(self, ev: S2SEvent) -> None:
        t = self._ensure_turn()
        if ev.final:
            t.transcript = ev.text or t.transcript
        await self._emit_json({"type": P.DOWN_TRANSCRIPT, "turn_id": t.turn_id,
                               "text": ev.text, "final": bool(ev.final)})
        if ev.final and t.pending_call_id and t.transcript.strip():
            await self._escalate(t, t.pending_call_id)   # 等到了定稿：这时才移交（R4-06）

    async def _on_answer_delta(self, ev: S2SEvent) -> None:
        t = self.turn
        if t is None or t.status != T_ACTIVE or t.pending_call_id:
            return  # 打断后的残余文本 / 已决定移交（等转写中）：丢弃（否则字幕会继续往下走）
        t.answer += ev.text
        await self._emit_json({"type": P.DOWN_ANSWER_DELTA, "turn_id": t.turn_id,
                               "text": ev.text})

    async def _on_audio_delta(self, ev: S2SEvent) -> None:
        t = self.turn
        if t is None or t.status != T_ACTIVE or t.pending_call_id:
            return  # ★1 残包丢弃（实测 provider cancel 很干净，但 cancel 在途时可能已在飞）
        if not t.audio_meta_sent:
            t.audio_meta_sent = True
            sr = getattr(self.provider, "out_sample_rate", 24000)
            await self._emit_json({"type": P.DOWN_AUDIO_META, "turn_id": t.turn_id,
                                   "sample_rate": sr, "format": "pcm16le"})
        t.audio_bytes += len(ev.pcm)
        await self._emit_audio(ev.pcm)

    async def _on_tool_call(self, ev: S2SEvent) -> None:
        """§5.2 逃逸：唯一的工具就是 escalate。本层只翻译+下行，**不代理执行**。

        评审四轮 R4-06：**移交出去的请求是这一轮的最终转写**，工具参数里的 `utterance` 只是模型的解读。
        修前参数优先：转写「不要开车窗，只解释怎么开」+ 参数「打开车窗」⇒ 主链收到「打开车窗」，engine 还把它盖成
        `safety_origin_text`——执行闸都在，看到的却是被改写过的请求。不做两段文本的相似度（多一个「不」字面几乎一样、
        意图相反）；判据是**来源**：请求只能来自转写。转写还没定稿 ⇒ 等（有界）；等不到 ⇒ 不移交（`_transcript_timeout`）。
        """
        t = self._ensure_turn()
        if ev.name != P.ESCALATE_TOOL_NAME:
            # 单工具契约下不该出现；出现即协议漂移，诚实记录不臆测
            logger.warning("s2s 收到未知工具调用 name=%s（单工具契约外）", ev.name)
            return
        try:
            t.interpretation = str(
                (json.loads(ev.args or "{}") or {}).get("utterance", "") or "").strip()
        except Exception:
            t.interpretation = ""   # 槽位坏了不影响移交：请求本来就取转写
        if t.transcript.strip():
            await self._escalate(t, ev.call_id)
            return
        t.pending_call_id = ev.call_id
        self._arm_transcript_wait(t)

    async def _escalate(self, t: Turn, call_id: str) -> None:
        """转写已定稿：以它为请求移交主链（HMI / Android 按既有 send(utterance) 走，旧客户端原样受益）。"""
        self._cancel_transcript_wait()
        t.pending_call_id = ""
        t.status = T_ESCALATED
        t.escalate_call_id = call_id
        t.utterance = t.transcript.strip()
        await self._emit_json({"type": P.DOWN_ESCALATED, "turn_id": t.turn_id,
                               "utterance": t.utterance, "transcript": t.utterance,
                               "interpretation": t.interpretation})
        await self._end_turn(t, P.END_ESCALATED, keep_status=True)

    def _arm_transcript_wait(self, t: Turn) -> None:
        self._cancel_transcript_wait()

        async def expire():
            try:
                await asyncio.sleep(self.escalate_transcript_wait_s)
            except asyncio.CancelledError:
                return
            await self._transcript_timeout(t)

        self._transcript_wait = asyncio.create_task(expire())

    def _cancel_transcript_wait(self) -> None:
        if self._transcript_wait is not None:
            self._transcript_wait.cancel()
            self._transcript_wait = None

    async def _transcript_timeout(self, t: Turn) -> None:
        """等不到转写定稿：**不移交**。模型的重述不能冒充原话去主链当请求（它可能正好丢了「不要 / 只查」）。
        本轮以 error 收束——两端早已把 error 渲染成「刚才那句没处理成功，你可以再说一遍」；悬挂的 function call 无害（R2 实测）。"""
        self._transcript_wait = None
        if not t.pending_call_id or t.end_reason or self._closed:
            return
        logger.warning("s2s turn %s 工具调用后 %.1fs 仍无转写定稿 → 不移交（模型重述不作请求）",
                       t.turn_id, self.escalate_transcript_wait_s)
        t.pending_call_id = ""
        await self._end_turn(t, P.END_ERROR, detail="transcript_unavailable")

    async def _on_turn_done(self, ev: S2SEvent) -> None:
        t = self.turn
        if t is None:
            return
        if t.status in (T_ESCALATED, T_ABANDONED, T_DONE):
            return  # escalate/打断路径已收束过，provider 的 done 不重复出
        if t.pending_call_id:
            return  # 工具调用之后 provider 照常发 done：这一轮归「等转写」收束（移交或诚实报错）
        reason = P.END_CANCELLED if ev.reason == "cancelled" else P.END_COMPLETE
        await self._end_turn(t, reason)

    async def _on_error(self, ev: S2SEvent) -> None:
        if ev.reason in ("transport", "closed"):
            return  # 事件流会随之结束，由 _run_pump 统一走重连（避免双触发）
        logger.warning("s2s provider 报错: %s", ev.detail)
        t = self.turn
        if t is not None and t.status == T_ACTIVE:
            await self._end_turn(t, P.END_ERROR)

    # ── turn 收束 + 回灌 ──
    async def _end_turn(self, t: Turn, reason: str, *, keep_status: bool = False,
                        detail: str = "") -> None:
        if reason != P.END_ESCALATED and t.pending_call_id:
            # 等转写期间被打断 / 断线 / 看门狗收束：这次移交作废（R4-06）
            t.pending_call_id = ""
            self._cancel_transcript_wait()
        if t.end_reason:
            return  # 幂等：同一 turn 只收束一次
        t.end_reason = reason
        self._clear_watchdog()
        if not keep_status:
            t.status = T_DONE
        self.turns_done += 1
        msg = {"type": P.DOWN_TURN_END, "turn_id": t.turn_id, "reason": reason}
        if detail:
            msg["detail"] = detail
        await self._emit_json(msg)
        if self._reflux is not None:
            try:
                await self._reflux(t)
            except Exception as e:
                # 回灌是强制项但不该拖死会话——失败要留痕（obs 里能看到缺口）
                logger.warning("s2s 回灌失败 turn=%s: %s", t.turn_id, e)
        if self.state == SessionState.IN_TURN:
            self.state = SessionState.READY
        # 长会话累积 → 主动重建（走同一重连路径）
        if self.max_turns and self.turns_done >= self.max_turns and reason != P.END_ESCALATED:
            logger.info("s2s 轮数达上限 %d → 主动重建 session（摘要重注入）", self.max_turns)
            await self._rebuild()

    # ── 重连 / 重建（RFC §4.5）──
    async def _schedule_reconnect(self) -> None:
        if self.state not in (SessionState.RECONNECTING, SessionState.DEGRADED):
            await self._reconnect()

    async def _rebuild(self) -> None:
        """主动重建：与重连同一路径（新 session + 摘要重注入）。"""
        if self.provider is not None:
            try:
                await self.provider.close()
            except Exception:
                pass
        # 事件流随 close 结束 → _run_pump 会自行走 _reconnect；这里只标态
        self.state = SessionState.RECONNECTING

    async def _reconnect(self) -> bool:
        """指数退避重连 ≤max_reconnects；超限 → DEGRADED（HMI 回落三段式）。"""
        if self._closed or self._reconnecting:
            return False
        self._reconnecting = True
        try:
            # IN_TURN 中断连：当前 turn 诚实收束，HMI 据此出「刚才说到一半断了」话术
            t = self.turn
            if t is not None and t.status == T_ACTIVE:
                t.truncated = True
                await self._end_turn(t, P.END_CANCELLED, detail="disconnected")
            self.state = SessionState.RECONNECTING
            await self._emit_json({"type": P.DOWN_SESSION_STATE,
                                   "state": P.STATE_RECONNECTING})
            for i in range(self.max_reconnects):
                await asyncio.sleep(self.backoff[min(i, len(self.backoff) - 1)])
                if self._closed:
                    return False
                try:
                    # 先关旧 provider 再造新的——否则每次重连泄漏一个 aiohttp
                    # ClientSession（长跑会话累积；韧性验证时由 "Unclosed client
                    # session" 警告暴露）。已断的连接 close 一次无害（幂等）。
                    if self.provider is not None:
                        try:
                            await self.provider.close()
                        except Exception:
                            pass
                    self.provider = self._provider_factory()
                    await self.provider.open(voice=self.voice, system=P.persona(),
                                             context_summary=await self._summary(),
                                             tools=True)
                    self.state = SessionState.READY
                    self.turns_done = 0
                    await self._emit_json({"type": P.DOWN_SESSION_STATE,
                                           "state": P.STATE_READY})
                    if self._ring:  # 前滚缓冲续灌（≤2s 补偿，复用 R4.3b pcmRing 思想）
                        pcm = bytes(self._ring)
                        self._ring.clear()
                        try:
                            await self.provider.send_audio(pcm)
                        except Exception:
                            pass
                    logger.info("s2s 重连成功（第 %d 次尝试）", i + 1)
                    return True
                except Exception as e:
                    logger.warning("s2s 重连第 %d 次失败: %s", i + 1, e)
            self.state = SessionState.DEGRADED
            self._ring.clear()
            await self._emit_json({"type": P.DOWN_SESSION_STATE, "state": P.STATE_DEGRADED})
            logger.warning("s2s 重连超限 → DEGRADED（HMI 回落三段式）")
            return False
        finally:
            self._reconnecting = False
