"""文本副产品回灌 + 上下文重注入材料（M4 RFC §7 / §2.3）——memory / obs 侧的桥。

**回灌是强制项，不是可选优化。** S2S 轮次绕过 edge-gateway（§2.1 通道拓扑决策的代价），
不回灌则：记忆断代（下次 planner 看不到这几轮）、obs 无轮次记录（badcase 无从查）、
自进化失明。接手 S2S 最容易漏的整合点就是这里。

另含 §5.3-2「漏移交」检测的纯函数——与回灌同一处消费聚合文本，故同文件。
"""
from __future__ import annotations
import logging
import re
import time
import uuid

logger = logging.getLogger("llm.s2s.reflux")

# ── §5.3-2 漏移交检测（v1 只检测不拦截）──
# 拦截=打断已播出的音频，听感更糟；靠 persona 提示 + 域灰度收窄把率压低，
# 检测数据进 obs 供自进化 nightly（M1b 资产）挖掘该族 badcase、量化漏移交率。
_DONE_MARK = re.compile(r"(已经|已帮你|已为你|帮你|给你|好了|搞定|设置好|调好|打开了|关好)")
_ACTION_WORD = re.compile(r"(空调|车窗|天窗|座椅|氛围灯|温度|风量|导航|路线|音乐|电台|"
                          r"播放|提醒|支付|下单|订|充电|除霜|后备箱)")


def detect_false_promise(answer: str, *, escalated: bool) -> bool:
    """S2S 口头答应了车控/执行却没移交 → True（进 obs 标记，不拦截）。

    判据：完成断言词 × 动作对象词共现，且本轮**无 escalate 记录**。
    误报代价低（只打标记），漏报才是真问题，故取宽判据。
    """
    if escalated or not answer:
        return False
    return bool(_DONE_MARK.search(answer) and _ACTION_WORD.search(answer))


async def build_context_summary(memory_stub, session_id: str, *, last_n: int = 4,
                                user_id: str = "", occupant_id: str = "") -> str:
    """重注入材料：memory 近 N 轮摘要（§2.3）。

    N=4 与 planner 口径一致；**不逐字回放全部历史**（token 成本 + 厂商兼容性，摘要粒度
    足够 chitchat 域连续性）。取不到 → 空串（fail-open，会话照开）。

    M-B：按 OwnerKey 取。语音大模型直接听直接答，摘要就是它唯一的上下文来源——
    混进另一位乘员的对话，它会拿着别人的事实回答这一位。
    """
    if memory_stub is None or not session_id:
        return ""
    try:
        from cockpit.memory.v1 import memory_pb2
        resp = await memory_stub.GetSession(
            memory_pb2.GetSessionRequest(
                session_id=session_id, last_n=last_n, user_id=user_id,
                occupant_id=occupant_id or "primary",
                scope=memory_pb2.HISTORY_SCOPE_OWNER_ONLY), timeout=3.0)
    except Exception as e:
        logger.debug("s2s 取近 N 轮失败（继续开会话）: %s", e)
        return ""
    lines = []
    for t in (resp.turns or []):
        text = (t.text or "").strip()
        if not text:
            continue
        who = "用户" if t.role == "user" else "你"
        lines.append(f"{who}：{text[:120]}")
    return "\n".join(lines[-last_n * 2:])


class Reflux:
    """每 turn 收束后的回灌器（注入 memory stub 取用器 + obs emitter）。"""

    def __init__(self, *, memory_stub_getter=None, obs=None, gate_content=None,
                 session_id: str = "", user_id: str = "", vehicle_id: str = "",
                 occupant_id: str = "", provider_name: str = "", model: str = ""):
        self._stub_getter = memory_stub_getter
        self._obs = obs
        self._gate = gate_content
        self.session_id = session_id
        self.user_id = user_id
        self.vehicle_id = vehicle_id
        self.occupant_id = occupant_id
        self.provider_name = provider_name
        self.model = model
        self.false_promises = 0

    async def __call__(self, turn) -> None:
        """turn 收束回调（S2SSession 的 reflux 注入点）。"""
        await self.reflux_turn(turn)

    async def reflux_turn(self, turn) -> None:
        escalated = bool(getattr(turn, "escalated", False))
        transcript = (turn.transcript or "").strip()
        answer = (turn.answer or "").strip()
        fp = detect_false_promise(answer, escalated=escalated)
        if fp:
            self.false_promises += 1
            logger.info("s2s 疑似漏移交（口头答应未 escalate）turn=%s", turn.turn_id)

        # 1) memory —— escalated 轮由主链自己 AppendTurn，此处不重复（§7-3）
        if not escalated and transcript:
            await self._append_turns(transcript, answer, turn_key=turn.turn_id or "")

        # 2) obs —— 轮次记录 + span。escalated 轮只补 span 关联（不重复出 obs.turn）
        await self._emit_obs(turn, transcript=transcript, answer=answer,
                             escalated=escalated, false_promise=fp)

    async def _append_turns(self, transcript: str, answer: str,
                            *, turn_key: str = "") -> None:
        if self._stub_getter is None:
            return
        try:
            from cockpit.memory.v1 import memory_pb2
            stub = self._stub_getter()
            # S2S 的 exchange 就是它自己的 turn（M-B）；缺 id 时不编一个，
            # 留空走服务端旧行为，重放保护退化但绝不写错归属。
            for role, text, tid in (
                ("user", transcript, f"{turn_key}:user" if turn_key else ""),
                ("assistant", answer, f"{turn_key}:assistant:0" if turn_key else ""),
            ):
                if not text:
                    continue
                await stub.AppendTurn(memory_pb2.AppendTurnRequest(
                    session_id=self.session_id, role=role, text=text,
                    user_id=self.user_id, vehicle_id=self.vehicle_id,
                    occupant_id=self.occupant_id or "primary",
                    turn_id=tid, exchange_id=turn_key), timeout=5.0)
        except Exception as e:
            # 记忆断代是真损失 → warning 级留痕（obs 里能看到缺口），但不拖死会话
            logger.warning("s2s AppendTurn 回灌失败: %s", e)

    async def _emit_obs(self, turn, *, transcript: str, answer: str,
                        escalated: bool, false_promise: bool) -> None:
        if self._obs is None:
            return
        trace_id = uuid.uuid4().hex[:16]
        dur_ms = max(0.0, (time.monotonic() - turn.started_at) * 1000)
        try:
            if not escalated:
                await self._obs.emit_turn(
                    trace_id, self.session_id, user_text=transcript, speech=answer,
                    status="ok" if turn.end_reason in ("complete", "cancelled") else "error",
                    path="s2s", input_source="voice", duration_ms=dur_ms)
            await self._obs.emit_span(
                trace_id, "s2s.turn",
                status="ok" if turn.end_reason != "error" else "error",
                duration_ms=dur_ms,
                attrs={
                    "provider": self.provider_name, "model": self.model,
                    "turn_id": turn.turn_id, "reason": turn.end_reason,
                    "escalated": escalated, "truncated": bool(turn.truncated),
                    "audio_bytes": turn.audio_bytes,
                    "s2s_false_promise": false_promise,
                    "utterance": (self._gate(turn.utterance, 120)
                                  if (self._gate and escalated) else ""),
                    # 被打断轮只存已播出的增量（★1：provider 全文≠用户听到的）
                    "answer": self._gate(answer, 200) if self._gate else "",
                })
        except Exception as e:
            logger.debug("s2s obs 回灌失败: %s", e)
