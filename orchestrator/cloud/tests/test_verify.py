"""Outcome Verifier 契约测试（M2 P1）。

四条铁律（红了就是机制破了，不是测试挑剔）：
1. **中央零领域分支**——verify.py 与 executor._verify_outcome 的源码里不得出现任何
   agent_id/intent 字面量（否则会长成下一个 fast_intent.py，v1.2 评审既定）；
2. **即插即用**——新能力只声明 manifest 的 verification 就生效，编排核心零改动；
3. **副作用永不重放**——retry 只对 require_confirm=false 的步开放；
4. **R9 口径**——UNSAT 报告保持 OK 状态（FAILED 上的话术会被聚合器吞成裸「处理失败」）。
"""
import asyncio
import inspect

import pytest

from orchestrator.cloud import verify as V
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import Plan, PlanContext, Step, StepResult, StepStatus


# ── 求值器一：schema ──────────────────────────────────────────────────────

def test_schema_sat_when_keys_present():
    assert V.eval_schema({"data_keys": ["items"]}, {"items": [1, 2]}) == V.SAT


@pytest.mark.parametrize("data", [
    {},                       # 键缺失
    {"items": None},          # 显式空
    {"items": []},            # 空列表——「空结果假 OK」的典型形态
    {"items": {}},
    {"items": ""},
])
def test_schema_unsat_on_missing_or_empty(data):
    assert V.eval_schema({"data_keys": ["items"]}, data) == V.UNSAT


def test_schema_zero_and_false_are_real_values():
    """0 度、false 是真实答案不是缺失——把它们判成空会制造假警。"""
    assert V.eval_schema({"data_keys": ["temp"]}, {"temp": 0}) == V.SAT
    assert V.eval_schema({"data_keys": ["on"]}, {"on": False}) == V.SAT


def test_schema_unknown_when_declaration_incomplete():
    """声明不全 → 不定罪（UNKNOWN），不能反过来把所有步都判失败。"""
    assert V.eval_schema({}, {"items": []}) == V.UNKNOWN
    assert V.eval_schema({"data_keys": []}, {"items": []}) == V.UNKNOWN


def test_schema_requires_all_declared_keys():
    assert V.eval_schema({"data_keys": ["a", "b"]}, {"a": 1}) == V.UNSAT
    assert V.eval_schema({"data_keys": ["a", "b"]}, {"a": 1, "b": 2}) == V.SAT


# ── 求值器二：state_match ─────────────────────────────────────────────────

def test_state_match_sat():
    assert V.eval_state_match({"keys": {"hvac_on": "true"}},
                              {"hvac_on": True}) == V.SAT


def test_state_match_unsat_when_world_did_not_change():
    """VAL 说成功、状态没落地——这正是本机制存在的理由（scene 首跑真抓到过）。"""
    assert V.eval_state_match({"keys": {"hvac_on": "true"}},
                              {"hvac_on": False}) == V.UNSAT


def test_state_match_cross_type_equality():
    """镜像里是原生类型，声明里（YAML/Struct）一律字符串——跨类型必须能比。"""
    assert V.eval_state_match({"keys": {"hvac_temp": "22"}},
                              {"hvac_temp": 22}) == V.SAT
    assert V.eval_state_match({"keys": {"hvac_on": "false"}},
                              {"hvac_on": False}) == V.SAT


def test_state_match_unknown_when_mirror_blind():
    """观测缺失 ≠ 没做成。镜像空 / 键不在镜像里 → 不定罪。"""
    assert V.eval_state_match({"keys": {"hvac_on": "true"}}, {}) == V.UNKNOWN
    assert V.eval_state_match({"keys": {"hvac_on": "true"}}, None) == V.UNKNOWN
    assert V.eval_state_match({"keys": {"hvac_on": "true"}},
                              {"window": "open"}) == V.UNKNOWN
    assert V.eval_state_match({"keys": {"hvac_on": "true"}},
                              {"hvac_on": None}) == V.UNKNOWN


def test_state_match_unsat_beats_unknown():
    """一个确凿不满足 + 一个读不到 → 报（有硬证据说明没做成就该说）。"""
    verdict = V.eval_state_match(
        {"keys": {"hvac_on": "true", "hvac_temp": "22"}}, {"hvac_on": False})
    assert verdict == V.UNSAT


def test_state_match_unknown_when_declaration_incomplete():
    assert V.eval_state_match({}, {"hvac_on": True}) == V.UNKNOWN


# ── 调度与超时轮询 ───────────────────────────────────────────────────────

class _Mirror:
    def __init__(self, *snapshots):
        self._snaps = list(snapshots) or [{}]

    def snapshot(self):
        return self._snaps.pop(0) if len(self._snaps) > 1 else self._snaps[0]


def test_evaluate_unknown_mode_is_forward_compatible():
    """未来新增 mode 在旧编排上不定罪（前向兼容），不是崩也不是误报。"""
    verdict = asyncio.run(V.evaluate({"mode": "readback", "expect": {}}, {}))
    assert verdict == V.UNKNOWN


def test_evaluate_state_match_polls_until_converged(monkeypatch):
    """车控生效有 ms-s 级延迟（动作到端→VAL→diff 经 NATS 回来），立刻断言必然误报。"""
    monkeypatch.setattr(V, "_POLL_INTERVAL_S", 0.001)
    mirror = _Mirror({}, {"hvac_on": False}, {"hvac_on": True})
    verdict = asyncio.run(V.evaluate(
        {"mode": "state_match", "timeout_ms": 500,
         "expect": {"keys": {"hvac_on": "true"}}}, {}, mirror=mirror))
    assert verdict == V.SAT


def test_evaluate_state_match_times_out_to_unsat(monkeypatch):
    monkeypatch.setattr(V, "_POLL_INTERVAL_S", 0.001)
    verdict = asyncio.run(V.evaluate(
        {"mode": "state_match", "timeout_ms": 30,
         "expect": {"keys": {"hvac_on": "true"}}}, {},
        mirror=_Mirror({"hvac_on": False})))
    assert verdict == V.UNSAT


def test_evaluate_state_match_without_mirror_is_unknown():
    """无 NATS（镜像未接线）→ UNKNOWN，绝不因为看不见就说没做成。"""
    verdict = asyncio.run(V.evaluate(
        {"mode": "state_match", "expect": {"keys": {"hvac_on": "true"}}}, {}))
    assert verdict == V.UNKNOWN


# ── retry 授权：副作用永不重放 ───────────────────────────────────────────

def test_retry_blocked_for_confirm_required_steps():
    """铁律③：require_confirm 的步重放 = 副作用二次执行，而用户只确认过一次。"""
    v = {"on_fail": "retry", "max_attempts": 3}
    assert V.retry_allowed(v, require_confirm=True, attempts=0) is False


def test_retry_allowed_within_max_attempts():
    v = {"on_fail": "retry", "max_attempts": 2}
    assert V.retry_allowed(v, require_confirm=False, attempts=0) is True
    assert V.retry_allowed(v, require_confirm=False, attempts=1) is True
    assert V.retry_allowed(v, require_confirm=False, attempts=2) is False


def test_retry_not_allowed_for_report_mode():
    assert V.retry_allowed({"on_fail": "report"}, False, 0) is False
    assert V.retry_allowed({}, False, 0) is False       # 缺省即 report


def test_max_attempts_defaults_to_one():
    v = {"on_fail": "retry"}
    assert V.retry_allowed(v, False, 0) is True
    assert V.retry_allowed(v, False, 1) is False


# ── 铁律①：中央零领域分支（源码断言）─────────────────────────────────────

_DOMAIN_TOKENS = ("hvac", "info.weather", "nearby", "navigation", "charging",
                  "trip.", "scene.", "media.", "reminder", "research.")


def test_verify_module_has_no_domain_literals():
    """verify.py 里出现任何域字面量 = 机制破了（会长成下一个 fast_intent.py）。

    注释里出现是允许的（解释来由）；**代码行**里不允许。
    """
    src = inspect.getsource(V)
    code = [ln for ln in src.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    code = "\n".join(code).split('"""')[0::2]      # 去掉 docstring 段
    body = "\n".join(code)
    for token in _DOMAIN_TOKENS:
        assert token not in body, f"verify.py 代码里出现领域字面量 {token!r}"


def test_executor_verify_hook_has_no_domain_literals():
    src = inspect.getsource(DagExecutor._verify_outcome)
    src += inspect.getsource(DagExecutor._evaluate)
    src += inspect.getsource(DagExecutor._should_report)
    code = "\n".join(ln for ln in src.splitlines()
                     if ln.strip() and not ln.strip().startswith("#"))
    for token in _DOMAIN_TOKENS:
        assert token not in code, f"executor 对账钩子里出现领域字面量 {token!r}"


# ── executor 尾链行为 ────────────────────────────────────────────────────

class _Dispatcher:
    """按调用序返回预置结果，并记录调用次数（验重试次数）。"""

    def __init__(self, *results):
        self._results = list(results)
        self.calls = 0

    async def dispatch(self, step, ctx):
        self.calls += 1
        return self._results[min(self.calls - 1, len(self._results) - 1)]


class _Resp:
    """最小 ExecuteResponse 替身（executor._to_result 只读这几个字段）。"""

    def __init__(self, status=0, speech="好的", data=None, ui_card=None):
        self.status = status
        self.speech = speech
        self.ui_card = ui_card
        self.actions = []
        self.follow_up = ""
        self.data = data if data is not None else {}
        self.missing_slots = []


def _step(**over) -> Step:
    kw = {"id": "s1", "agent_id": "a", "intent": "x.y",
          "latency_budget_ms": 5000, "verification": {}}
    kw.update(over)
    return Step(**kw)


def _run(executor, step) -> StepResult:
    async def _go():
        results = []
        async for r in executor.run(Plan(steps=[step]), PlanContext()):
            results.append(r)
        return results[0]
    return asyncio.run(_go())


def test_no_verification_declared_is_passthrough():
    """未声明 = 不验 = 与 M2 之前逐字一致（零行为变化）。"""
    d = _Dispatcher(_Resp(data={}))
    res = _run(DagExecutor(dispatcher=d), _step())
    assert res.status == StepStatus.OK and "_verify" not in (res.data or {})
    assert d.calls == 1


def test_unsat_report_keeps_ok_status_and_marks_data():
    """铁律④ R9：报告走 OK + `_verify` 保留键，不用 FAILED。"""
    d = _Dispatcher(_Resp(data={"items": []}, ui_card={"type": "place_list"}))
    step = _step(verification={"mode": "schema", "on_fail": "report",
                               "expect": {"data_keys": ["items"]}})
    res = _run(DagExecutor(dispatcher=d), step)
    assert res.status == StepStatus.OK          # ← FAILED 会被聚合器吞成裸「处理失败」
    assert res.data["_verify"] == {"verdict": "unsat", "mode": "schema", "attempts": 0}
    assert res.speech == "好的"                  # 原话术不被改写


def test_sat_leaves_result_untouched():
    d = _Dispatcher(_Resp(data={"items": [1]}))
    step = _step(verification={"mode": "schema", "expect": {"data_keys": ["items"]}})
    res = _run(DagExecutor(dispatcher=d), step)
    assert "_verify" not in res.data


def test_retry_re_executes_once_then_reports():
    """UNSAT + retry：重跑一次；仍 UNSAT 落 report 路径（不无限重试）。"""
    d = _Dispatcher(_Resp(data={"items": []}, ui_card={"t": 1}),
                    _Resp(data={"items": []}, ui_card={"t": 1}))
    step = _step(verification={"mode": "schema", "on_fail": "retry",
                               "max_attempts": 1,
                               "expect": {"data_keys": ["items"]}})
    res = _run(DagExecutor(dispatcher=d), step)
    assert d.calls == 2
    assert res.data["_verify"]["attempts"] == 1


def test_retry_success_clears_report():
    """重试拿到真东西 → 干净透传，不留任何「没确认到」的噪声。"""
    d = _Dispatcher(_Resp(data={"items": []}, ui_card={"t": 1}),
                    _Resp(data={"items": [1]}, ui_card={"t": 1}))
    step = _step(verification={"mode": "schema", "on_fail": "retry",
                               "max_attempts": 1,
                               "expect": {"data_keys": ["items"]}})
    res = _run(DagExecutor(dispatcher=d), step)
    assert d.calls == 2 and "_verify" not in res.data


def test_retry_never_replays_side_effect_steps():
    """铁律③在 executor 尾链的落地：require_confirm 的步即便声明 retry 也只跑一次。"""
    d = _Dispatcher(_Resp(data={"items": []}, ui_card={"t": 1}))
    step = _step(require_confirm=True, meta={"confirmed": "true"},
                 verification={"mode": "schema", "on_fail": "retry",
                               "max_attempts": 3,
                               "expect": {"data_keys": ["items"]}})
    res = _run(DagExecutor(dispatcher=d), step)
    assert d.calls == 1
    assert res.data["_verify"]["verdict"] == "unsat"


def test_non_ok_result_is_not_verified():
    """只验「声称成功」的步：NEED_SLOT/FAILED 走各自通道，不叠对账噪声。"""
    d = _Dispatcher(_Resp(status=2, speech="要哪个城市？"))
    step = _step(verification={"mode": "schema", "on_fail": "retry",
                               "expect": {"data_keys": ["items"]}})
    res = _run(DagExecutor(dispatcher=d), step)
    assert res.status == StepStatus.NEED_SLOT and d.calls == 1


def test_honest_degradation_is_not_double_reported():
    """Agent 已按 R9 诚实降级（无卡无 data，只有一句解释）→ 不再补对账口径。"""
    d = _Dispatcher(_Resp(speech="周边搜索服务暂时不可用，稍后再试一次？", data={}))
    step = _step(verification={"mode": "schema", "on_fail": "report",
                               "expect": {"data_keys": ["items"]}})
    res = _run(DagExecutor(dispatcher=d), step)
    assert "_verify" not in (res.data or {})


def test_streaming_paths_must_call_verify_outcome():
    """**流式直通不经 `_exec_step`**：engine D0 与 loop T2 都直接把 stream 的 final 当结果，
    不显式调对账就是「声明了却静默不生效」的缺口——本卡真栈首验实测到（weather 走 D0 流式，
    一条 step.verify span 都没有）。这条源码断言钉死修复，防以后重构时又掉。
    """
    from orchestrator.cloud import engine as engine_mod
    from orchestrator.cloud import loop as loop_mod
    for mod, label in ((engine_mod, "engine D0"), (loop_mod, "loop T2")):
        src = inspect.getsource(mod)
        assert "_verify_outcome" in src, f"{label} 流式直通路径未接执行后对账"
        assert "allow_retry=False" in src, f"{label} 流式路径不该重试（话术已流出会重复播报）"


def test_streaming_path_reports_without_retry():
    """流式路径 allow_retry=False：即便声明了 retry 也只报不重跑。"""
    d = _Dispatcher(_Resp(data={"items": []}, ui_card={"t": 1}))
    ex = DagExecutor(dispatcher=d)
    step = _step(verification={"mode": "schema", "on_fail": "retry",
                               "max_attempts": 3,
                               "expect": {"data_keys": ["items"]}})
    sr = StepResult(step_id="s1", status=StepStatus.OK, speech="已播报",
                    ui_card={"t": 1}, data={"items": []})
    out = asyncio.run(ex._verify_outcome(step, sr, PlanContext(), allow_retry=False))
    assert d.calls == 0                       # 一次都不重跑
    assert out.data["_verify"]["verdict"] == "unsat"


def test_verify_errors_fail_open(monkeypatch):
    """对账是增强：求值器炸了照常透传，绝不炸主链。"""
    async def _boom(*a, **k):
        raise RuntimeError("verifier bug")
    monkeypatch.setattr(V, "evaluate", _boom)
    d = _Dispatcher(_Resp(data={"items": []}, ui_card={"t": 1}))
    step = _step(verification={"mode": "schema", "expect": {"data_keys": ["items"]}})
    res = _run(DagExecutor(dispatcher=d), step)
    assert res.status == StepStatus.OK and "_verify" not in res.data


def test_env_kill_switch_disables_verification(monkeypatch):
    """VERIFY_OUTCOME=off 一键回 M2 之前（声明照读、只是不执行对账）。"""
    monkeypatch.setenv("VERIFY_OUTCOME", "off")
    d = _Dispatcher(_Resp(data={"items": []}, ui_card={"t": 1}))
    step = _step(verification={"mode": "schema", "on_fail": "retry",
                               "expect": {"data_keys": ["items"]}})
    res = _run(DagExecutor(dispatcher=d), step)
    assert d.calls == 1 and "_verify" not in res.data


# ── 铁律②：即插即用（新能力只投 manifest 就生效）─────────────────────────

def test_new_capability_verification_works_without_central_change(tmp_path):
    """照 M0b skills 即插测试先例：临时 manifest 声明 verification →
    _validated_steps 装配出来即生效，编排核心一行不改。"""
    from agents._sdk.manifest import load_manifest
    from orchestrator.cloud.planning import PlanBuilder

    (tmp_path / "manifest.yaml").write_text("""
agent_id: brand-new-agent
version: 0.1.0
capabilities:
  - intent: brandnew.query
    description: 全新能力
    verification:
      mode: schema
      on_fail: retry
      max_attempts: 2
      expect: { data_keys: [rows] }
""", encoding="utf-8")
    manifest = load_manifest(str(tmp_path / "manifest.yaml"))

    class _Agent:
        endpoint = "brand-new:50099"

    agent = _Agent()
    agent.manifest = manifest
    steps = PlanBuilder._validated_steps(
        [{"id": "s1", "agent_id": "brand-new-agent", "intent": "brandnew.query"}],
        {"brand-new-agent": agent})
    assert steps[0].verification == {
        "mode": "schema", "timeout_ms": 0, "on_fail": "retry", "max_attempts": 2,
        "expect": {"data_keys": ["rows"]}}

    # 且这份声明在 executor 尾链真的生效（不是只装配不消费）
    d = _Dispatcher(_Resp(data={}, ui_card={"t": 1}), _Resp(data={}, ui_card={"t": 1}),
                    _Resp(data={}, ui_card={"t": 1}))
    res = _run(DagExecutor(dispatcher=d), steps[0])
    assert d.calls == 3                            # 首跑 + 2 次重试
    assert res.data["_verify"]["verdict"] == "unsat"


def test_llm_cannot_inject_verification():
    """「验不验、验什么」不是模型的决定权——与 require_confirm 同一条权威链。"""
    from agents._sdk.manifest import load_manifest
    from orchestrator.cloud.planning import PlanBuilder

    class _Agent:
        endpoint = "x:1"

    from cockpit.agent.v1 import agent_pb2
    agent = _Agent()
    agent.manifest = agent_pb2.AgentManifest(
        agent_id="a", capabilities=[agent_pb2.Capability(intent="x.y")])
    steps = PlanBuilder._validated_steps(
        [{"id": "s1", "agent_id": "a", "intent": "x.y",
          "verification": {"mode": "schema", "expect": {"data_keys": ["hacked"]}}}],
        {"a": agent})
    assert steps[0].verification == {}      # LLM 输出的该字段一律不读


# ── 聚合器诚实口径 ───────────────────────────────────────────────────────

def _compose(results):
    async def _llm(messages, **kw):
        return "合成话术"
    return asyncio.run(Aggregator(_llm).compose("用户问句", results))


def test_aggregator_appends_state_match_note():
    r = StepResult(step_id="s1", status=StepStatus.OK, speech="已为您打开空调",
                   data={"_verify": {"verdict": "unsat", "mode": "state_match"}})
    out = _compose([r])
    assert out["speech"].startswith("已为您打开空调")
    assert "没确认到" in out["speech"]


def test_aggregator_appends_schema_note():
    r = StepResult(step_id="s1", status=StepStatus.OK, speech="为您找到附近的餐厅",
                   data={"_verify": {"verdict": "unsat", "mode": "schema"}})
    assert "没拿到实际内容" in _compose([r])["speech"]


def test_aggregator_silent_when_verified_ok():
    r = StepResult(step_id="s1", status=StepStatus.OK, speech="已为您打开空调",
                   data={"weather": {"temp": 20}})
    assert _compose([r])["speech"] == "已为您打开空调"


def test_aggregator_note_not_repeated_per_step():
    """多步同 mode 只说一次，不逐步轰炸（scene「每激活至多一条汇报」同款克制）。"""
    rs = [StepResult(step_id=f"s{i}", status=StepStatus.OK, speech=f"结果{i}",
                     data={"_verify": {"verdict": "unsat", "mode": "schema"}})
          for i in (1, 2)]
    assert _compose(rs)["speech"].count("没拿到实际内容") == 1


def test_aggregator_note_survives_multi_step_llm_rewrite():
    """多步走 LLM 改写——对账口径在改写**之后**确定性拼接，不会被模型润色掉。"""
    rs = [StepResult(step_id="s1", status=StepStatus.OK, speech="已为您打开空调",
                     data={"_verify": {"verdict": "unsat", "mode": "state_match"}}),
          StepResult(step_id="s2", status=StepStatus.OK, speech="今天晴 20 度")]
    out = _compose(rs)
    assert out["speech"].startswith("合成话术")
    assert "没确认到" in out["speech"]
