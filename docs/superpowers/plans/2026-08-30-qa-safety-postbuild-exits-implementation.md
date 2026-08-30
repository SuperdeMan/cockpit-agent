# QA Safety Post-Build Exits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the replan, escalate, and fallback post-build paths so a non-directive question cannot dispatch an edge write or a manifest-confirmed cloud capability after the primary Planner guard has run.

**Architecture:** Extract one declaration-backed step-filter primitive from the existing question side-effect guard and call it immediately before every newly produced plan can dispatch: `PlanBuilder.build()`, `LoopController` replan reception, `PlannerEngine._run_escalated()`, and fallback capability selection. Each receiver uses server-owned original text (`text`, `user_text`, or `ctx.raw_text`), never an LLM goal or Agent reason; full blocking terminates without dispatch, while mixed plans preserve legal steps, completed observations, and aggregation inputs.

**Tech Stack:** Python 3.12, dataclasses, asyncio, pytest, existing Planner/Loop/Engine contracts, PowerShell, Markdown architecture and evidence records.

> **Execution boundary:** Work only in `D:\Personal\AI\Claude Code\产品\car-agent-qa-safety-guard` until local implementation and review are complete. Local source, test, document, ignored artifact, and local commit changes are in scope. Do not alter `.env`, `dev-stack.local`, schemas, CI/CD, secrets, merchant data, or production state. `git push`, main-worktree integration, deploy `--apply`, remote-safe verification, long-session probes, and any live or mutating cleanup each stop at the explicit authorization checkpoints in Task 7.

---

## File map

| File | Responsibility in this change |
|---|---|
| `orchestrator/cloud/planning.py` | One reusable question-side-effect filter; guarded fallback capability scan; blocked adaptive plan downgrade |
| `orchestrator/cloud/tests/test_question_write_guard.py` | Fallback scan, same-guard rejection, later safe talk, and simple-complexity contracts |
| `orchestrator/cloud/loop.py` | Guard `ReplanDecision.steps` with server-owned `user_text` before stream/executor dispatch |
| `orchestrator/cloud/tests/test_loop.py` | Edge/confirmed/read/directive/mixed replan cases, zero dispatch, and observation preservation |
| `orchestrator/cloud/engine.py` | Guard the validated escalate mini-plan with `ctx.raw_text` before either D0 or normal executor dispatch |
| `orchestrator/cloud/tests/test_engine_escalate.py` | D0 and normal-path negatives for edge/confirmed targets; directive/read positives |
| `docs/architecture/cockpit-agent-architecture.md` | Amend v1.45 §5.2.13 so “all exits” includes post-build receivers |
| `docs/conventions.md` | Amend §9.40 with raw-text authority and replan/escalate/fallback behavior |
| `AGENTS.md` | Reopen current state, correct external/environment count 5→6, and later record fresh evidence |
| `docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md` | Reopen the QA safety closeout and record the final post-build closure |
| `docs/design/2026-08-30-qa-safety-confirmed-write-guard.md` | Keep the addendum authoritative and replace historical-only evidence with fresh result boundaries |
| `docs/design/README.md` | Change the design index from local-complete to final-review-reopened, then to the proved local state |
| `docs/agents-history.md` | Append §86 with RED/GREEN, reviews, fresh counts, warnings, and external authorization boundary |

No proto, manifest, `servers.yaml`, `.env`, CI/CD, schema, payment, merchant workflow, or live-data change belongs in this implementation.

### Task 1: Make fallback selection reuse the guard and stop blocked adaptive empty loops

**Files:**
- Modify: `orchestrator/cloud/tests/test_question_write_guard.py`
- Modify: `orchestrator/cloud/planning.py:2430-2502`

- [ ] **Step 1: Add fallback and complexity RED tests**

Extend `test_question_write_guard.py` with these concrete contracts:

```python
def test_talk_only_plan_skips_guarded_first_capability_and_uses_later_safe_talk():
    agent = MockAgent(
        "chitchat",
        ["warning_light.close", "chitchat.talk"],
        kind="edge_fast",
        deployment="edge",
    )
    builder = PlanBuilder(llm_fn=None, registry_fn=None)

    plan = builder._talk_only_plan("红色机油灯亮了怎么办", [agent])

    assert plan is not None
    assert [step.intent for step in plan.steps] == ["chitchat.talk"]


def test_talk_only_plan_returns_none_when_every_capability_matches_guard():
    agent = MockAgent(
        "chitchat",
        ["warning_light.close", "shop.order"],
        kind="edge_fast",
        deployment="edge",
        require_confirm=("shop.order",),
    )
    builder = PlanBuilder(llm_fn=None, registry_fn=None)

    assert builder._talk_only_plan("红色机油灯亮了怎么办", [agent]) is None


def test_all_blocked_adaptive_plan_with_safe_talk_is_downgraded_to_simple():
    builder = PlanBuilder(llm_fn=None, registry_fn=None)
    agents = [MockAgent("chitchat", ["chitchat.talk"])]
    plan = Plan(
        steps=[_step("warning_light.close")],
        raw_text="红色机油灯亮了怎么办",
        complexity="adaptive",
    )

    guarded = builder._apply_question_side_effect_guard(
        plan, plan.raw_text, agents,
    )

    assert [step.intent for step in guarded.steps] == ["chitchat.talk"]
    assert guarded.complexity == "simple"


def test_all_blocked_adaptive_plan_without_safe_talk_is_empty_simple():
    builder = PlanBuilder(llm_fn=None, registry_fn=None)
    plan = Plan(
        steps=[_step("warning_light.close")],
        raw_text="红色机油灯亮了怎么办",
        complexity="adaptive",
    )

    guarded = builder._apply_question_side_effect_guard(
        plan, plan.raw_text, [],
    )

    assert guarded.steps == []
    assert guarded.complexity == "simple"
```

- [ ] **Step 2: Capture the fallback RED artifact**

```powershell
New-Item -ItemType Directory -Force -Path '.artifacts/qa-safety-confirmed-write-postbuild' | Out-Null
python -m pytest -q `
  orchestrator/cloud/tests/test_question_write_guard.py::test_talk_only_plan_skips_guarded_first_capability_and_uses_later_safe_talk `
  orchestrator/cloud/tests/test_question_write_guard.py::test_talk_only_plan_returns_none_when_every_capability_matches_guard `
  orchestrator/cloud/tests/test_question_write_guard.py::test_all_blocked_adaptive_plan_with_safe_talk_is_downgraded_to_simple `
  orchestrator/cloud/tests/test_question_write_guard.py::test_all_blocked_adaptive_plan_without_safe_talk_is_empty_simple 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/01-talk-red.log'
if ($LASTEXITCODE -eq 0) { throw 'RED did not fail' }
```

Expected: the first test exposes `capabilities[0]` selection, the second exposes unsafe reinsertion or premature selection, and both complexity tests expose `adaptive` surviving after every executable step was removed.

- [ ] **Step 3: Extract one pure filter and make the build finalizer consume it**

In `PlanBuilder`, keep `_question_side_effect_steps()` as the only selection formula and add this identity-preserving primitive:

```python
@staticmethod
def _filter_question_side_effect_steps(
        steps: list, text: str) -> tuple[list, list]:
    blocked = PlanBuilder._question_side_effect_steps(steps, text)
    if not blocked:
        return list(steps), []
    blocked_ids = {id(step) for step in blocked}
    return [step for step in steps if id(step) not in blocked_ids], blocked
```

Change `_apply_question_side_effect_guard()` to call this primitive, preserve `question_write_blocked`, and set `plan.complexity = "simple"` only when all original steps were blocked and the result is safe talk or empty. Do not downgrade a mixed plan that retains a legitimate adaptive step.

- [ ] **Step 4: Scan fallback capabilities in manifest order**

Replace the `capabilities[0]` logic in `_talk_only_plan()` with a loop that validates each candidate and asks the same primitive whether it is safe for the original text:

```python
for agent in (agents or []):
    if agent.manifest.agent_id != _FALLBACK_AGENT:
        continue
    for capability in (agent.manifest.capabilities or []):
        steps = self._validated_steps(
            [{"id": "s1", "agent_id": agent.manifest.agent_id,
              "intent": capability.intent, "slots": {"text": text},
              "depends_on": [], "slot_refs": {}}],
            {agent.manifest.agent_id: agent},
        )
        if len(steps) != 1:
            continue
        kept, blocked = self._filter_question_side_effect_steps(steps, text)
        if blocked or len(kept) != 1:
            continue
        return Plan(steps=kept, raw_text=text)
return None
```

The same guard, not a second “confirmed only” condition, rejects both edge writes and confirmed cloud candidates. Continuing the loop is required so a later safe `chitchat.talk` remains reachable.

- [ ] **Step 5: Run GREEN and adjacent guard tests**

```powershell
python -m pytest -q orchestrator/cloud/tests/test_question_write_guard.py 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/01-talk-green.log'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

Expected: exit 0, every test in the file passes, confirmed fallback remains rejected, and the four historical edge-direction controls remain green.

- [ ] **Step 6: Commit Task 1**

```powershell
git add orchestrator/cloud/planning.py orchestrator/cloud/tests/test_question_write_guard.py
git diff --cached --check
git commit -m "fix: guard fallback safety exits"
```

### Task 2: Guard replan reception with the original user text

**Files:**
- Modify: `orchestrator/cloud/tests/test_loop.py`
- Modify: `orchestrator/cloud/loop.py:159-214`

- [ ] **Step 1: Add replan receiver RED coverage**

Add a small `_replan_case()` fixture around the existing `_Planner`, `_Executor`, `_Aggregator`, and `_collect` helpers. It must execute one initial read result so the replan path has a real observation, then return the supplied `ReplanDecision`:

```python
def _replan_case(replan_steps, *, user_text, goal="LLM goal", results=None):
    planner = _Planner([ReplanDecision(done=False, steps=replan_steps)])
    executor = _Executor({
        "s1": StepResult("s1", StepStatus.OK, speech="已取得初始事实"),
        **(results or {}),
    })
    controller = LoopController(
        planner, executor, _Aggregator(), None,
        max_iters=1, budget_ms=5000,
    )
    events = _collect(
        controller,
        goal=goal,
        initial_plan=Plan(
            steps=[Step(id="s1", agent_id="info", intent="manual.query")],
            complexity="adaptive",
        ),
        agents=[], ctx=PlanContext(), user_text=user_text,
    )
    return planner, executor, events
```

Use it for these named tests and exact expectations:

```python
def test_question_replan_edge_write_uses_user_text_not_llm_goal_and_never_dispatches():
    step = Step(id="r1", agent_id="edge-vehicle", intent="warning_light.close",
                deployment="edge", kind="edge_fast")
    planner, executor, _ = _replan_case(
        [step], user_text="红色机油灯亮了怎么办", goal="关闭故障灯",
    )
    assert executor.runs == [["s1"]]
    assert planner.observations[0][-1]["step_id"] == "s1"


def test_question_replan_confirmed_cloud_step_never_dispatches():
    step = Step(id="r1", agent_id="mcp-bridge", intent="luckin.order",
                deployment="cloud", kind="agent", require_confirm=True)
    _, executor, _ = _replan_case(
        [step], user_text="红色机油灯亮了还能继续开吗",
    )
    assert executor.runs == [["s1"]]


def test_question_replan_cloud_read_still_dispatches():
    step = Step(id="r1", agent_id="manual", intent="manual.query",
                deployment="cloud", kind="agent")
    _, executor, _ = _replan_case(
        [step], user_text="红色机油灯亮了怎么办",
        results={"r1": StepResult("r1", StepStatus.OK, speech="请停车检查")},
    )
    assert executor.runs == [["s1"], ["r1"]]


def test_directive_replan_edge_write_still_dispatches():
    step = Step(id="r1", agent_id="edge-vehicle", intent="warning_light.close",
                deployment="edge", kind="edge_fast")
    _, executor, _ = _replan_case(
        [step], user_text="关闭双闪",
        results={"r1": StepResult("r1", StepStatus.OK, speech="已关闭")},
    )
    assert executor.runs == [["s1"], ["r1"]]


def test_mixed_question_replan_keeps_read_and_preserves_observation_chain():
    read = Step(id="r1", agent_id="manual", intent="manual.query")
    write = Step(id="r2", agent_id="edge-vehicle", intent="warning_light.close",
                 deployment="edge", kind="edge_fast")
    planner, executor, _ = _replan_case(
        [read, write], user_text="红色机油灯亮了怎么办",
        results={"r1": StepResult("r1", StepStatus.OK, speech="停车检查")},
    )
    assert executor.runs == [["s1"], ["r1"]]
    assert [obs["step_id"] for obs in planner.observations[0]] == ["s1"]
```

- [ ] **Step 2: Capture the loop RED artifact**

```powershell
python -m pytest -q orchestrator/cloud/tests/test_loop.py -k 'question_replan or directive_replan or mixed_question_replan' 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/02-loop-red.log'
if ($LASTEXITCODE -eq 0) { throw 'RED did not fail' }
```

Expected: edge and confirmed question replans reach `_Executor.runs`; the read/directive controls already pass or fail only because the shared fixture is not yet complete.

- [ ] **Step 3: Filter immediately after `ReplanDecision` reception**

Import `PlanBuilder` from `.planning`. In `LoopController.run()`, after `decision.done`/empty handling and before `decision.to_plan(goal)`, call the static Planner primitive with `user_text`; this keeps existing `_Planner` test doubles unchanged:

```python
kept, blocked = PlanBuilder._filter_question_side_effect_steps(
    decision.steps, user_text,
)
if blocked:
    logger.warning(
        "Question-shaped utterance replanned into side-effecting step(s) %s; "
        "dropping before dispatch",
        [step.intent for step in blocked],
    )
if not kept:
    break
decision.steps = kept
current = decision.to_plan(goal)
```

If direct mutation of `ReplanDecision.steps` conflicts with a test asserting decision reuse, construct a new `ReplanDecision(done=False, steps=kept, skill_effects=list(decision.skill_effects))`. In both forms, use `user_text`; do not use `goal`, `initial_plan.goal`, or `current.raw_text`. Preserve `results`, `observations`, `initial_plan.skills`, and `initial_plan.exemplars` exactly as the existing code does.

- [ ] **Step 4: Run GREEN and the whole loop file**

```powershell
python -m pytest -q orchestrator/cloud/tests/test_loop.py 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/02-loop-green.log'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

Expected: exit 0; all-new blocked cases show zero `r1/r2` dispatch; cloud read, directive, mixed retention, prior observation, streaming, suspension, and skill inheritance tests remain green.

- [ ] **Step 5: Commit Task 2**

```powershell
git add orchestrator/cloud/loop.py orchestrator/cloud/tests/test_loop.py
git diff --cached --check
git commit -m "fix: guard adaptive replan dispatch"
```

### Task 3: Guard both escalate sources before executor dispatch

**Files:**
- Modify: `orchestrator/cloud/tests/test_engine_escalate.py`
- Modify: `orchestrator/cloud/engine.py:1085-1128`

- [ ] **Step 1: Extend the escalate fixture with authoritative capability metadata**

Add `import pytest`. Change `_Cap` and `_agents()` so each target carries authoritative confirmation and routing metadata without changing existing defaults:

```python
class _Cap:
    def __init__(self, intent, heavy=False, require_confirm=False):
        self.intent, self.slots, self.description = intent, [], intent
        self.heavy = heavy
        self.require_confirm = require_confirm
        self.examples = []
```

Keep the original chitchat/info agents first, then append these three manifests to `_agents()`:

```python
    edge = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="edge-vehicle", trust_level="first_party", latency_budget_ms=2000,
        deployment="edge", kind="edge_fast", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("warning_light.close")], route_hints=[],
    ), endpoint="stub:50064")
    merchant = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="mcp-bridge", trust_level="first_party", latency_budget_ms=5000,
        deployment="cloud", kind="agent", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("luckin.order", require_confirm=True)], route_hints=[],
    ), endpoint="stub:50065")
    manual = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="manual", trust_level="first_party", latency_budget_ms=5000,
        deployment="cloud", kind="agent", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("manual.query")], route_hints=[],
    ), endpoint="stub:50066")
    return [chitchat, info, edge, merchant, manual]
```

- [ ] **Step 2: Add D0 and normal executor RED cases**

Parameterize route and unsafe target so both source paths prove both rejection classes:

```python
@pytest.mark.parametrize("route", ["d0", "executor"])
@pytest.mark.parametrize("intent", ["warning_light.close", "luckin.order"])
def test_question_escalate_rejects_side_effect_before_dispatch(route, intent):
    esc = {"_escalate": {"intent": intent, "slots": {}, "reason": "model_redirect"}}
    if route == "d0":
        spy = _EscSpy(script=[("final", _Resp(speech="", data=esc))])
        text = "红色机油灯亮了还能继续开吗"
    else:
        spy = _EscSpy(unary_seq=[_Resp(speech="", data=esc)])
        text = "红色机油灯亮了还能继续开吗"
    engine, _ = _make_engine(spy)

    events = _run(engine, _req(text))

    assert intent not in [call[0] for call in spy.unary_calls]
    assert not any(event.get("need_confirm") for event in events)
```

Add two positive controls:

```python
def test_directive_escalate_to_edge_write_still_dispatches():
    esc = {"_escalate": {"intent": "warning_light.close", "slots": {},
                          "reason": "model_redirect"}}
    spy = _EscSpy(
        script=[("final", _Resp(speech="", data=esc))],
        unary_seq=[_Resp(speech="已关闭双闪。")],
    )
    engine, _ = _make_engine(spy)

    events = _run(engine, _req("关闭双闪"))

    assert [call[0] for call in spy.unary_calls].count("warning_light.close") == 1
    assert events[-1]["speech"] == "已关闭双闪。"


def test_question_escalate_to_cloud_read_still_dispatches():
    esc = {"_escalate": {"intent": "manual.query", "slots": {},
                          "reason": "model_redirect"}}
    spy = _EscSpy(
        unary_seq=[
            _Resp(speech="", data=esc),
            _Resp(speech="请立即安全停车并查阅车辆手册。"),
        ],
    )
    engine, _ = _make_engine(spy)

    events = _run(engine, _req("红色机油灯亮了还能继续开吗"))

    assert [call[0] for call in spy.unary_calls].count("manual.query") == 1
    assert events[-1]["kind"] == "final"
    assert events[-1]["speech"]
```

- [ ] **Step 3: Capture the escalate RED artifact**

```powershell
python -m pytest -q orchestrator/cloud/tests/test_engine_escalate.py -k 'question_escalate or directive_escalate' 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/03-escalate-red.log'
if ($LASTEXITCODE -eq 0) { throw 'RED did not fail' }
```

Expected: all four question/unsafe combinations dispatch before the fix; read and directive controls either pass or expose fixture metadata that must be corrected without weakening assertions.

- [ ] **Step 4: Filter the validated mini-plan with `ctx.raw_text`**

In `_run_escalated()`, retain `_validated_steps()` as the manifest authority, then guard before constructing/executing `mini`:

```python
steps = self.planner._validated_steps([...], agent_map)
kept, blocked = PlanBuilder._filter_question_side_effect_steps(
    steps, ctx.raw_text,
)
if blocked:
    logger.warning(
        "Question-shaped utterance escalated into side-effecting step(s) %s; "
        "dropping before dispatch",
        [step.intent for step in blocked],
    )
if not kept:
    sink["results"] = []
    sink["plan"] = Plan(steps=[], raw_text=ctx.raw_text)
    return
mini = Plan(steps=kept, raw_text=ctx.raw_text)
```

Keep the existing one-hop loop prevention, heavy progress events, NEED_CONFIRM suspension, and sink contract. Do not use `esc["reason"]`, the original plan goal, or Agent-provided speech for the safety decision. Both the D0 call site and the normal executor call site already converge on `_run_escalated()`; do not duplicate the guard at those callers.

- [ ] **Step 5: Run GREEN and all escalate tests**

```powershell
python -m pytest -q orchestrator/cloud/tests/test_engine_escalate.py 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/03-escalate-green.log'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

Expected: exit 0; four unsafe question redirects have no target dispatch or confirmation suspension; the directive edge write and question read dispatch once; all existing D0/normal, one-hop, NEED_CONFIRM, streamed, and heavy-progress contracts remain green.

- [ ] **Step 6: Commit Task 3**

```powershell
git add orchestrator/cloud/engine.py orchestrator/cloud/tests/test_engine_escalate.py
git diff --cached --check
git commit -m "fix: guard escalated plan dispatch"
```

### Task 4: Run three review stages and retain RED provenance

**Files:**
- No tracked file changes required by the review itself
- Write ignored evidence: `.artifacts/qa-safety-confirmed-write-postbuild/`

- [ ] **Step 1: Stage 1 specification review after RED, before broad GREEN**

Review the staged tests against this matrix and write the verdict to
`.artifacts/qa-safety-confirmed-write-postbuild/review-1-spec.md`:

| Exit | Unsafe negative | Safe positive | Text authority | Full-block behavior | Mixed/observability |
|---|---|---|---|---|---|
| fallback | edge write + confirmed cloud | later safe talk | `text` | `None` | capability scan continues |
| replan | edge write + confirmed cloud | directive + cloud read | `user_text` | break, zero dispatch | legal step + prior observation retained |
| escalate | D0 + normal × edge/confirmed | directive + cloud read | `ctx.raw_text` | empty sink, zero dispatch | existing caller result/aggregation preserved |

Expected review verdict: every cell points to a named test; none relies on `goal`, `reason`, or hard-coded safety-domain words in production logic. If a cell lacks a named test, add it before implementation proceeds.

- [ ] **Step 2: Stage 2 implementation-quality review after Tasks 1–3 GREEN**

Inspect these exact diffs:

```powershell
git diff a07cc7b6cf14886f8f172af0f41a6acd63e1b71e -- `
  orchestrator/cloud/planning.py `
  orchestrator/cloud/loop.py `
  orchestrator/cloud/engine.py `
  orchestrator/cloud/tests/test_question_write_guard.py `
  orchestrator/cloud/tests/test_loop.py `
  orchestrator/cloud/tests/test_engine_escalate.py
```

Record PASS/FAIL in `.artifacts/qa-safety-confirmed-write-postbuild/review-2-quality.md` for: one filter formula only; object-identity filtering; no LLM goal/reason authority; full block has zero executor/stream dispatch; mixed plans retain legal steps; fallback scans beyond a rejected first capability; no new agent/intent literal in production code; no broad exception swallowing; no proto/schema/config change.

Expected: all checks PASS before documentation updates. A FAIL returns to the responsible TDD task and requires that task’s focused file to be rerun.

- [ ] **Step 3: Stage 3 final exit audit after documentation and full verification**

Run:

```powershell
rg -n "to_plan\(|_validated_steps\(|executor\.run\(|_stream\(|call_agent\(" orchestrator/cloud
```

For each hit that can create or receive a new plan after `PlanBuilder.build()`, record its guarding reason in
`.artifacts/qa-safety-confirmed-write-postbuild/review-3-final.md`. The required conclusion is either “covered by build”, “covered by replan receiver”, “covered by `_run_escalated`”, or “does not accept planner/Agent-produced steps”. Any fourth unguarded dispatch receiver reopens Task 1 architecture before push.

- [ ] **Step 4: Verify all RED/GREEN artifacts exist and are non-empty**

```powershell
$required = @(
  '01-talk-red.log','01-talk-green.log',
  '02-loop-red.log','02-loop-green.log',
  '03-escalate-red.log','03-escalate-green.log',
  'review-1-spec.md','review-2-quality.md','review-3-final.md'
)
$required | ForEach-Object {
  $path = Join-Path '.artifacts/qa-safety-confirmed-write-postbuild' $_
  if (-not (Test-Path -LiteralPath $path) -or (Get-Item -LiteralPath $path).Length -eq 0) {
    throw "missing review evidence: $path"
  }
}
```

Expected: no exception; each RED log contains a non-zero pytest result and each paired GREEN log contains exit-0 output.

### Task 5: Reconcile architecture, contract, current state, history, and indexes

**Files:**
- Modify: `docs/architecture/cockpit-agent-architecture.md:940-1030,1996`
- Modify: `docs/conventions.md:2527-2570`
- Modify: `AGENTS.md:105-120,312-325,704-720,802-810,938-946`
- Modify: `docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md:1547-1585`
- Modify: `docs/design/2026-08-30-qa-safety-confirmed-write-guard.md:1-12, after §11`
- Modify: `docs/design/README.md` row for `2026-08-30-qa-safety-confirmed-write-guard.md`
- Append: `docs/agents-history.md` as §86

- [ ] **Step 1: Amend v1.45 and §9.40 without inventing a second rule**

In architecture v1.45 §5.2.13 and its version-table row, replace “all `build()` exits” with “every dispatch-bound plan exit”, enumerate build/replan/escalate/fallback receivers, and state the source-text mapping `build:text / loop:user_text / escalate:ctx.raw_text`. Keep the formula unchanged and keep cloud unconfirmed side effects outside the current claim.

In conventions §9.40 add:

```text
新的 plan 产物在首次 dispatch 前必须重新过同一判据：build 读 text，replan receiver
读 user_text，escalate 读 ctx.raw_text；goal/reason 不具备安全权威。replan 全删按 done
终止，escalate 全删返回空 sink，mixed 只删违规对象。build 全删后只允许同守卫放行的
fallback capability；只剩 talk/空计划时 complexity=simple。
```

Do not bump the architecture beyond v1.45 for this corrective addendum unless the repository owner separately chooses a version change.

- [ ] **Step 2: Reopen and then close current-state documents with fresh evidence only**

Update AGENTS, the root-cause fix plan, the design, and the design README so they say:

- `dfad687` and `a07cc7b` counts are historical baselines that did not cover post-build exits;
- the final implementation SHA and fresh exact counts come only from Task 6;
- local completion is distinct from push/deploy/live completion;
- `343934b` remains the cloud release until a later live status proves otherwise.

Correct the AGENTS attribution sentence from `外部/环境 5` to `外部/环境 6`; do not change the other category counts unless the source list itself proves a different correction.

- [ ] **Step 3: Append history §86**

Use this exact heading structure:

```markdown
## §86 2026-08-30 QA 安全闸最终审查增补：post-build 三出口收口

### §86.1 最终审查为何推翻“本地完成”
### §86.2 三组 RED 与最小实现
### §86.3 三阶段审查
### §86.4 fresh 本地读数、warning 分类与 manifest
### §86.5 尚需单独授权的 merge/push/deploy/live
```

Record exact commit SHAs, named RED failures, fresh commands/counts, manifest/hash values, and the statement `production impact not established`. Do not copy the complete design narrative into history.

- [ ] **Step 4: Check cross-document consistency and commit docs**

```powershell
rg -n "dfad687|a07cc7b|343934b|post-build|外部/环境 6|production impact not established" `
  AGENTS.md `
  docs/architecture/cockpit-agent-architecture.md `
  docs/conventions.md `
  docs/agents-history.md `
  docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md `
  docs/design/2026-08-30-qa-safety-confirmed-write-guard.md `
  docs/design/README.md
git diff --check
git add AGENTS.md docs/architecture/cockpit-agent-architecture.md docs/conventions.md `
  docs/agents-history.md docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md `
  docs/design/2026-08-30-qa-safety-confirmed-write-guard.md docs/design/README.md
git diff --cached --check
git commit -m "docs: close post-build safety exits"
```

Expected: old SHAs are labeled historical; no file calls local evidence live proof; the cloud release remains `343934b`; the external/environment count is 6.

### Task 6: Run fresh verification, classify warnings, and publish one canonical local manifest

**Files:**
- No tracked source changes expected after the last verification starts
- Write ignored evidence: `.artifacts/qa-safety-confirmed-write-postbuild/`
- Copy canonical ignored evidence to: `D:\Personal\AI\Claude Code\产品\car-agent\.artifacts\qa-safety-confirmed-write-postbuild\`

- [ ] **Step 1: Verify target, Python, environment, and process isolation**

```powershell
python scripts/dev_stack.py target show
python --version
"PYTHONIOENCODING=$env:PYTHONIOENCODING"
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'pytest|run_e2e|dev_stack\.py (deploy|status|verify)' } |
  Select-Object ProcessId, Name, CommandLine
git status --short --branch
```

Expected: target is `cloud`; Python is 3.12; no competing test/E2E/deploy/status/verify process is using shared resources. Clear `PYTHONIOENCODING` with `Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue` before tests. Do not start local Docker.

- [ ] **Step 2: Run fresh targeted safety tests**

```powershell
python -m pytest -q `
  orchestrator/cloud/tests/test_question_write_guard.py `
  orchestrator/cloud/tests/test_loop.py `
  orchestrator/cloud/tests/test_engine_escalate.py `
  orchestrator/cloud/tests/test_planning_safety_talk.py `
  orchestrator/cloud/tests/test_planning_cancel_gate.py `
  orchestrator/cloud/tests/test_planning.py 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/targeted.log'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

Expected: exit 0 and zero failures. Record the exact pass/skip/warning counts emitted by this fresh run; do not prescribe or reuse a historical total.

- [ ] **Step 3: Run all Cloud Planner tests**

```powershell
python -m pytest -q orchestrator/cloud/tests 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/cloud-tests.log'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

Expected: exit 0 and zero failures. Record the fresh exact counts from this run.

- [ ] **Step 4: Run the four deterministic blocking gates**

```powershell
python test/eval_skills.py 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/gate-skills.log'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python test/eval_exemplars.py 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/gate-exemplars.log'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python scripts/check_intent_gate.py --json docs/reviews/eval/_ci-run-intent_gate.json 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/gate-intent.log'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python test/eval_capability_integrity.py 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/gate-capability.log'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

Expected: all four return 0; the intent gate is strict, not an informational list run. Record each exact count from its own output.

- [ ] **Step 5: Run the full repository after the last tracked change**

```powershell
python -m pytest -q -n 8 --dist worksteal 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write-postbuild/full-pytest.log'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

Expected: exit 0, zero failures. Record the fresh exact pass/skip/warning totals; no historical total is a required result. If a parallel-only failure appears, follow AGENTS “隔离” discipline and retain both the first log and the isolated rerun evidence rather than calling it flaky.

- [ ] **Step 6: Classify every warning category**

Create `.artifacts/qa-safety-confirmed-write-postbuild/warning-classification.md` listing each warning class, count, emitting test/file, whether it existed at `a07cc7b`, and whether the new diff touches that path. The conclusion must use this exact risk language:

```text
production impact not established
```

Do not write “non-production risk” or infer production safety from a test-only fixture explanation.

- [ ] **Step 7: Generate the fresh verification manifest and hashes**

Create `.artifacts/qa-safety-confirmed-write-postbuild/verification-manifest-utf8.json` with UTF-8 JSON containing: `tested_code_contract_sha` from `git rev-parse HEAD` before the evidence-only document commit; clean `git status --porcelain`; exact commands; exact exit codes and counts; warning categories; review artifact names; SHA256 for every listed log; deployed release explicitly still `343934b`; `live_verified=false`; and `risk_statement="production impact not established"`.

Hash the manifest after all fields are final:

```powershell
Get-FileHash -Algorithm SHA256 `
  '.artifacts/qa-safety-confirmed-write-postbuild/verification-manifest-utf8.json'
Get-ChildItem '.artifacts/qa-safety-confirmed-write-postbuild' -File |
  Sort-Object Name |
  Get-FileHash -Algorithm SHA256 |
  Format-Table Path, Hash -AutoSize
```

Expected: every manifest-listed file exists and its computed SHA256 matches the manifest; `live_verified` is false.

- [ ] **Step 8: Copy the canonical evidence into root `.artifacts`**

```powershell
$source = (Resolve-Path '.artifacts/qa-safety-confirmed-write-postbuild').Path
$rootArtifacts = 'D:\Personal\AI\Claude Code\产品\car-agent\.artifacts'
$target = Join-Path $rootArtifacts 'qa-safety-confirmed-write-postbuild'
if (Test-Path -LiteralPath $target) { throw "canonical target already exists: $target" }
New-Item -ItemType Directory -Force -Path $rootArtifacts | Out-Null
Copy-Item -LiteralPath $source -Destination $target -Recurse
Get-FileHash -Algorithm SHA256 `
  (Join-Path $source 'verification-manifest-utf8.json'), `
  (Join-Path $target 'verification-manifest-utf8.json')
```

Expected: source and root-copy manifest hashes are identical. The copy is ignored/local-only and does not change tracked root files. If the target exists, stop and inspect it; do not overwrite or delete evidence.

- [ ] **Step 9: Commit fresh evidence references after rerunning affected doc checks**

Update only the evidence numbers/hashes in the tracked documents from Task 5, then run:

```powershell
git diff --check
git add AGENTS.md docs/agents-history.md `
  docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md `
  docs/design/2026-08-30-qa-safety-confirmed-write-guard.md docs/design/README.md
git diff --cached --check
git commit -m "docs: record fresh safety exit evidence"
```

Expected: tracked tree is clean after commit; every recorded count and hash comes from Task 6 logs; old `dfad687`/`a07cc7b` values remain labeled history only. Record this last commit as `evidence_doc_sha`; it is docs-only and must not be described as independently test-bound.

### Task 7: Final review, integration, and external authorization checkpoints

**Files:**
- No additional tracked change unless final review finds a defect

- [ ] **Step 1: Final local review and exact diff inventory**

```powershell
git status --short --branch
git diff --check
git log --oneline --decorate a07cc7b6cf14886f8f172af0f41a6acd63e1b71e..HEAD
git diff --stat a07cc7b6cf14886f8f172af0f41a6acd63e1b71e..HEAD
git diff --name-only a07cc7b6cf14886f8f172af0f41a6acd63e1b71e..HEAD
```

Expected: only the mapped production/test/docs files changed; the worktree is clean; Task 4 review 3 is PASS. The manifest’s `tested_code_contract_sha` is an ancestor of HEAD, and this command proves every later path is evidence-only documentation:

```powershell
$manifest = Get-Content -Raw -Encoding utf8 `
  '.artifacts/qa-safety-confirmed-write-postbuild/verification-manifest-utf8.json' |
  ConvertFrom-Json
git merge-base --is-ancestor $manifest.tested_code_contract_sha HEAD
if ($LASTEXITCODE -ne 0) { throw 'tested SHA is not an ancestor of HEAD' }
git diff --name-only "$($manifest.tested_code_contract_sha)..HEAD"
```

Expected: the ancestor check returns 0 and the later diff contains only the evidence documents named in Task 6 Step 9.

- [ ] **Step 2: Stop for main-worktree integration authorization**

Before changing `D:\Personal\AI\Claude Code\产品\car-agent`, show the full commit list and changed-file list from Step 1, plus root `git status --short --branch`. Obtain explicit approval for the chosen integration operation. Do not merge into a dirty or concurrently used main worktree.

- [ ] **Step 3: Stop separately for `git push` authorization**

After integration and before push, run in root:

```powershell
git fetch origin
git log --oneline origin/main..HEAD
git diff --name-status origin/main..HEAD
```

Present every commit that the branch-level push would carry. Execute no `git push` until the user explicitly authorizes that exact list.

- [ ] **Step 4: Stop separately for deploy authorization**

After an authorized push, require a clean, committed, main-reachable SHA. Run only the documented deploy dry-run to obtain the controlled-path summary/digest, present it, and request a separate deploy `--apply` authorization for that exact SHA and digest. Do not alter `.env`, CI/CD, infrastructure, schema, or system configuration.

- [ ] **Step 5: Stop separately for live verification authorization**

After an authorized deploy, run `python scripts/dev_stack.py status`, then the documented unified `verify`/remote-safe lane bound to the exact deployed SHA. Merchant writes, payments, real vehicle control, data deletion, cleanup writes, or `remote_mutating=true` remain excluded without their own explicit approval.

- [ ] **Step 6: Run the approved live acceptance only after all prior checkpoints**

The live acceptance set is: clean-session safety question repetitions, the original `information` long-session persona, release/readback equality, zero merchant drafts, zero new pending operations, and cleanup verification. Record exact commands, release SHA, results, and side-effect inventory; do not transfer local full-suite claims to the deployed SHA.

- [ ] **Step 7: Close the design only when live evidence exists**

If and only if the exact deployed SHA passes the approved live set, update AGENTS, fix plan, design, design README, and history with the deployed release and evidence. Otherwise keep the item active and record the precise failed or unrun checkpoint.

---

## Plan self-review checklist

- [ ] Every A–G requirement maps to Tasks 1–7.
- [ ] No unknown test total is asserted as a required future result; every verification step records its fresh exact output.
- [ ] All three receivers use server-owned original text and one shared formula.
- [ ] RED, GREEN, three reviews, warnings, manifest, root artifact copy, and SHA256 checks are named.
- [ ] The phrase `production impact not established` is used; “non-production risk” is absent.
- [ ] Main integration, push, deploy, remote-safe/live, and mutating actions are separate authorization points.
- [ ] `git diff --check` passes and all Markdown code fences are balanced.
