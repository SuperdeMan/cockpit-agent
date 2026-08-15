"""安全焦点跨轮保持（阶段 1 / 卡 Q9，QA 轮 I-054 + N4）。

现象（迷你集 SF3 真栈原文，三轮同一 session）：
  T1「红色机油灯亮了怎么办？」→「手册里没查到相关内容，建议联系客服」
  T2「现在在高速还能继续开吗？」→ **回答了天气**「气温29℃…适合出行」
  T3「慢一点开可以吗？」        → **执行了 `volume.dec`**，话术「调小了」

⚠ N4 的归因在实施时被实测纠正：端侧 `classify("慢一点开可以吗？")` 返回 **None**
（`_is_non_directive_question` 判 True），**不是端侧劫持**——是整句上云后由
**云侧 planner** 落到了音量。所以它属于「能力缺席→就近挑工具」（Q8）在安全语境里的
形态：系统没有「开慢点」这个能力，planner 就近挑了唯一带「小一点」语义的那个。

本卡要钉的是**状态**这一半：一次安全警告必须成为会话状态，跨轮可见。
机制沿用 G8 `_route_session` 的保留键先例（编排不认识 Agent 私有字段）：
Agent 经 `data["_safety_alert"]` 声明 → `Focus.safety_alert` → 粘性接力 + 限龄 →
渲染进 planner 上下文。

**刻意不做**（写在这里免得下一个人以为是漏了）：不加「安全语境下禁止一切无关车控」
的硬闸。安全对话不该剥夺用户开空调的权利；真正要防的是**把安全提问误解成车控指令**
——那是落域问题，归 Q8/Q13，不是在执行器上加一道会误伤的闸能解决的。
"""
import time

from orchestrator.cloud.context import Focus, _render_focus, extract_focus
from orchestrator.cloud.models import Plan, Step, StepResult, StepStatus


def _ok(step_id: str, data: dict) -> StepResult:
    return StepResult(step_id=step_id, status=StepStatus.OK, data=data)


def _plan(intent="manual.query", agent_id="manual-rag"):
    return Plan(steps=[Step(id="s0", intent=intent, agent_id=agent_id, slots={})])


# ── 抽取 ─────────────────────────────────────────────────────────────────

def test_safety_alert_reserved_key_lands_in_focus():
    focus = extract_focus(_plan(), [
        _ok("s0", {"_safety_alert": {"level": "critical", "signal": "红色机油灯"}})])
    assert focus is not None
    assert focus.safety_alert.get("level") == "critical"
    assert focus.safety_alert.get("signal") == "红色机油灯"
    assert focus.safety_alert.get("ts") > 0, "必须盖时间戳，消费方要按它限龄"


def test_invalid_safety_alert_is_dropped_not_coerced():
    """模型输出是不可信输入：非法元素**直接丢，不做 str() 转换**（CLAUDE.md §6）。"""
    for bad in ({"level": "很严重"}, {"level": ["critical"]}, "critical", 42, None):
        focus = extract_focus(_plan(), [_ok("s0", {"_safety_alert": bad})])
        alert = (focus.safety_alert if focus else {}) or {}
        assert not alert, f"非法 _safety_alert 应被丢弃：{bad!r} → {alert}"


def test_failed_step_does_not_declare_safety_alert():
    res = StepResult(step_id="s0", status=StepStatus.FAILED,
                     data={"_safety_alert": {"level": "critical", "signal": "x"}})
    focus = extract_focus(_plan(), [res])
    assert not ((focus.safety_alert if focus else {}) or {})


# ── 渲染 ─────────────────────────────────────────────────────────────────

def test_safety_alert_renders_into_prompt_block():
    focus = Focus(safety_alert={"level": "critical", "signal": "红色机油灯",
                                "ts": int(time.time())})
    block = _render_focus(focus)
    assert "红色机油灯" in block
    assert "未解除" in block or "安全" in block, f"必须让模型看见这是个未解除的安全态：{block}"


def test_no_safety_alert_renders_nothing():
    """反向对照：没有安全态时这一行不得出现（防止污染每一轮 prompt）。"""
    block = _render_focus(Focus(last_intent="weather.query"))
    assert "安全" not in block


def test_expired_safety_alert_is_not_rendered():
    """限龄：陈旧告警不得永远挂着（同 active_route 的 ts 纪律）。"""
    stale = Focus(safety_alert={"level": "critical", "signal": "红色机油灯",
                                "ts": int(time.time()) - 24 * 3600})
    assert "红色机油灯" not in _render_focus(stale)


def test_focus_with_only_safety_alert_is_not_empty():
    """只有安全态时 focus 也必须被持久化——否则它一轮就没了。"""
    assert not Focus(safety_alert={"level": "amber", "signal": "胎压",
                                   "ts": int(time.time())}).is_empty()
