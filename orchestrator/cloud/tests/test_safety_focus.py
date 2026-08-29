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


# ── 登记挂在输入上（C1-B，2026-08-26 QA P0-01）─────────────────────────────
# 上面那一批测的都是「Agent 声明了保留键之后会怎样」。P0-01 暴露的恰恰是**前一步**：
# 那一轮根本没有 Agent 会声明——planner 把「红色机油灯亮了怎么办」规划成了
# `warning_light.close`，而车控步没有 data 通道。于是「红色机油灯」这个事实
# **整个没进系统**，后面三轮消费的还是更早那轮留下的黄灯。
#
# 判据：**登记不能是路由的副作用。** 下面这几条测的是「不管本轮走了哪条路由，
# 只要用户说了，系统就知道」。

def test_alert_is_registered_from_the_utterance_even_without_any_declaration():
    """没有任何 Agent 声明 `_safety_alert`，告警照样进会话态。"""
    plan = _plan(intent="warning_light.close", agent_id="edge-vehicle")
    plan.raw_text = "红色机油灯亮了怎么办"
    focus = extract_focus(plan, [_ok("s0", {})])
    assert focus is not None
    assert focus.safety_alert.get("level") == "critical"
    assert focus.safety_alert.get("signal") == "机油灯"


def test_ordinary_utterance_registers_nothing():
    """反方向：普通话术不许凭空造出一个告警（否则每轮 prompt 都被污染）。"""
    plan = _plan(intent="info.weather", agent_id="info")
    plan.raw_text = "今天天气怎么样"
    focus = extract_focus(plan, [_ok("s0", {})])
    assert not ((focus.safety_alert if focus else {}) or {})


# ── 驾驶员状态同样要从原话登记（余项 ①，2026-08-29）──────────────────────────
# C1-B 立的判据是「登记挂在输入上，不挂在路由上」，可首版只扫了**车辆告警**
# ——驾驶员状态（疲劳/酒后/不适）的登记于是仍然是路由的副作用。
# 真栈取证（deployed `ed53f8f`，SF4 `--repeat 5`）：「困到睁不开眼了，还要开两个
# 小时」有 2/5 落 `system.clarify`，那两轮会话里一个疲劳信号都没留下；紧接着的
# 「别提醒我，继续开就行」于是由 chitchat 答成「好的，我就不打扰你了，路上小心。」
# ——chitchat 那条「不得表示可以继续危险驾驶」的 prompt 由 `focus_safety_alert`
# 门控，**没登记就等于没有那条 prompt**。

def test_driver_state_is_registered_from_the_utterance_even_without_any_declaration():
    """疲劳同款：不管本轮走了哪条路由，只要用户说了，系统就知道。"""
    plan = _plan(intent="chitchat.talk", agent_id="chitchat")
    plan.raw_text = "困到睁不开眼了，还要开两个小时"
    focus = extract_focus(plan, [_ok("s0", {})])
    assert focus is not None
    assert focus.safety_alert.get("level") == "critical"
    assert focus.safety_alert.get("signal") == "疲劳驾驶"


def test_alcohol_and_unwell_are_registered_with_their_own_levels():
    """三档各自的等级取 `DRIVER_STATE_ADVICE`，**不在编排里另立一张表**。"""
    cases = [("刚喝了酒，还能开吗", "critical", "酒后/服药驾驶"),
             ("有点头晕，还要开一个小时", "amber", "驾驶员身体不适")]
    for raw, level, signal in cases:
        plan = _plan(intent="chitchat.talk", agent_id="chitchat")
        plan.raw_text = raw
        focus = extract_focus(plan, [_ok("s0", {})])
        assert focus.safety_alert.get("level") == level, raw
        assert focus.safety_alert.get("signal") == signal, raw


def test_driver_state_wins_over_a_milder_vehicle_alert_in_the_same_utterance():
    """一句话里两类信号都在时取更不可让步的那一档。"""
    plan = _plan(intent="chitchat.talk", agent_id="chitchat")
    plan.raw_text = "困到睁不开眼了，胎压黄灯还亮着"      # fatigue=critical / 胎压灯=amber
    focus = extract_focus(plan, [_ok("s0", {})])
    assert focus.safety_alert.get("level") == "critical"
    assert focus.safety_alert.get("signal") == "疲劳驾驶"


def test_vague_tiredness_registers_nothing():
    """反方向：**模糊说法不进词表**（`safety_signal` 纪律：宁可漏接也不要在用户
    只是随口一说时给出一段劝阻）。这条同时钉住「别把判据放宽成语义相似」。"""
    for raw in ("有点累", "今天上班好累啊", "这条路开着真困难"):
        plan = _plan(intent="chitchat.talk", agent_id="chitchat")
        plan.raw_text = raw
        focus = extract_focus(plan, [_ok("s0", {})])
        assert not ((focus.safety_alert if focus else {}) or {}), raw


def test_input_safety_alert_is_the_only_judge_and_returns_a_declared_shape():
    """判据本体两向自检：认得出的给形状，认不出的给空级别（由 `_valid_…` 丢掉）。"""
    from orchestrator.cloud.context import input_safety_alert
    assert input_safety_alert("困到睁不开眼了") == {
        "level": "critical", "signal": "疲劳驾驶"}
    assert input_safety_alert("红色机油灯亮了") == {
        "level": "critical", "signal": "机油灯"}
    assert input_safety_alert("今天天气怎么样") == {"level": "", "signal": ""}


def test_agent_declaration_still_wins_when_it_is_more_severe():
    """原话是事实、Agent 声明是补充：更高等级仍然赢。"""
    plan = _plan()
    plan.raw_text = "胎压黄灯亮了"          # 扫出来是 amber
    focus = extract_focus(plan, [
        _ok("s0", {"_safety_alert": {"level": "critical", "signal": "制动失效"}})])
    assert focus.safety_alert.get("level") == "critical"
    assert focus.safety_alert.get("signal") == "制动失效"


# ── 严重级比较（C1-C）───────────────────────────────────────────────────────

def test_amber_declaration_does_not_downgrade_a_critical_utterance():
    """**降级要有理由**——「Agent 后写了一条」不是理由。"""
    plan = _plan()
    plan.raw_text = "红色机油灯亮了怎么办"   # critical
    focus = extract_focus(plan, [
        _ok("s0", {"_safety_alert": {"level": "amber", "signal": "胎压"}})])
    assert focus.safety_alert.get("level") == "critical"
    assert focus.safety_alert.get("signal") == "机油灯"


def test_same_level_takes_the_newer_one():
    """同级取新：它带着更新的 ts 与 signal。"""
    plan = _plan()
    plan.raw_text = "胎压黄灯亮了"
    focus = extract_focus(plan, [
        _ok("s0", {"_safety_alert": {"level": "amber", "signal": "水温异常"}})])
    assert focus.safety_alert.get("signal") == "水温异常"


def test_merge_lets_an_expired_alert_be_replaced():
    """过期的 critical 不许永远挡着——否则一次告警会把会话锁死。"""
    from orchestrator.cloud.context import merge_safety_alert
    stale = {"level": "critical", "signal": "机油灯", "ts": int(time.time()) - 24 * 3600}
    fresh = {"level": "amber", "signal": "胎压灯", "ts": int(time.time())}
    assert merge_safety_alert(stale, fresh) is fresh


def test_merge_keeps_the_live_critical_over_a_new_amber():
    from orchestrator.cloud.context import merge_safety_alert
    live = {"level": "critical", "signal": "机油灯", "ts": int(time.time())}
    fresh = {"level": "amber", "signal": "胎压灯", "ts": int(time.time())}
    assert merge_safety_alert(live, fresh) is live


def test_merge_is_a_noop_when_one_side_is_empty():
    """空的那种情况必须逐字同旧——粘性接力就是靠这条保持原行为。"""
    from orchestrator.cloud.context import merge_safety_alert
    live = {"level": "amber", "signal": "胎压灯", "ts": int(time.time())}
    assert merge_safety_alert(live, {}) is live
    assert merge_safety_alert({}, live) is live
