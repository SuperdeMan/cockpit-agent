# QA Safety Confirmed-Write Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a non-directive question from entering a cloud capability that requires confirmation, while preserving existing edge-write protection, cloud reads, ordinary directives, mixed plans, and historical observability.

**Architecture:** Extend the existing plan-exit safety guard with one additional declaration-backed branch: a step is blocked when the utterance is a non-directive question and the step is either an edge write or has `Step.require_confirm=true`. Keep `runtime.question_shape` as the single utterance-shape authority, keep edge read/write classification in `runtime.intent_effect`, and use the capability-manifest value already assembled into `Step.require_confirm` for cloud high-cost operations. Do not add a second `effect` contract or classify all cloud operation names through `is_write_intent`.

**Tech Stack:** Python 3.12, dataclasses, pytest, existing Planner/Manifest contracts, Markdown architecture and QA evidence documents, PowerShell verification, cloud `dev_stack` tooling.

> **Execution boundary:** The design is approved in `docs/design/2026-08-30-qa-safety-confirmed-write-guard.md`. Local source/test/document edits and local commits are in scope. Creating a worktree-local `dev-stack.local` with `target=cloud`, `git push`, cloud deploy `--apply`, merchant-writing probes, and any cleanup write each require explicit authorization at their named checkpoints. Do not stage or alter concurrent `mobile/` work.

---

## File map

| File | Responsibility in this change |
|---|---|
| `orchestrator/cloud/tests/test_planning.py` | Make `MockAgent` confirmation flags real booleans so the new guard is tested against faithful capability metadata |
| `orchestrator/cloud/tests/test_question_write_guard.py` | RED/GREEN behavior, mixed-plan, cloud-read, directive, and end-to-end regression coverage |
| `orchestrator/cloud/planning.py` | Single plan-exit guard selecting edge writes and confirmed cloud steps |
| `docs/architecture/cockpit-agent-architecture.md` | Architecture baseline update to v1.45 and §5.2.13 scope correction |
| `docs/conventions.md` | New §9.40 executable contract for non-directive questions and confirmed cloud steps |
| `docs/design/2026-08-30-qa-safety-confirmed-write-guard.md` | Approved design status and implementation/live evidence |
| `docs/design/README.md` | Design index status |
| `docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md` | QA closeout batch terminal state and authorization boundary |
| `AGENTS.md` | Current state, test baseline, and active/deployed status without claiming QA all-green |
| `docs/agents-history.md` | Append-only local and live evidence record |

No proto, manifest, `servers.yaml`, `.env`, CI/CD, schema, payment, or merchant workflow change belongs in this plan.

### Task 0: Isolate execution without falsifying the deployment target

**Files:**
- Create after explicit authorization only: `D:/Personal/AI/Claude Code/产品/car-agent-qa-safety-guard/dev-stack.local`
- Do not modify: root `dev-stack.local`, `.env`, or any tracked source

- [ ] **Step 1: Reconfirm the source workspace target and state**

Run from the current repository root:

```powershell
python scripts/dev_stack.py target show
git status --short --branch
git diff --cached --name-only
git log --oneline origin/main..HEAD
```

Expected: target reports `cloud`; staged paths are empty; every ahead commit is listed rather than summarized as a number.

- [ ] **Step 2: Obtain the worktree-local target authorization**

Ask for explicit authorization to create one ignored file in the isolated worktree containing exactly:

```text
target=cloud
```

Expected: an affirmative answer naming the isolated worktree target. Without it, stop; do not let the missing file silently select `target=local`.

- [ ] **Step 3: Create the isolated worktree and branch**

Run only after Step 2 approval:

```powershell
$qaRepo = (Resolve-Path '.').Path
$qaParent = Split-Path -Parent $qaRepo
$qaWorktree = Join-Path $qaParent 'car-agent-qa-safety-guard'
if (Test-Path -LiteralPath $qaWorktree) { throw "worktree already exists: $qaWorktree" }
git worktree add -b qa/safety-confirmed-write-guard $qaWorktree HEAD
python (Join-Path $qaWorktree 'scripts/dev_stack.py') target show
git -C $qaWorktree status --short --branch
```

Before running the last two commands, use `apply_patch` in the new worktree to create exactly:

```diff
*** Begin Patch
*** Add File: D:/Personal/AI/Claude Code/产品/car-agent-qa-safety-guard/dev-stack.local
+target=cloud
*** End Patch
```

Expected: the new worktree reports `target=cloud`; its tracked worktree is clean; the original root remains on `main` and untouched.

- [ ] **Step 4: Perform all remaining tasks inside the isolated worktree**

Use the resolved `$qaWorktree` as `workdir` for every remaining command. Do not copy `.env`; local unit tests and deterministic gates do not require it.

### Task 1: Make the Planner test fixture carry an explicit confirmation contract

**Files:**
- Modify: `orchestrator/cloud/tests/test_planning.py:29-54`
- Test: `orchestrator/cloud/tests/test_planning.py`

- [ ] **Step 1: Write the failing fixture-contract test**

Add after `MockAgent`:

```python
def test_mock_agent_confirmation_flags_are_plain_booleans():
    agent = MockAgent(
        "demo",
        ["demo.query", "demo.write"],
        require_confirm=("demo.write",),
    )

    flags = [cap.require_confirm for cap in agent.manifest.capabilities]
    assert flags == [False, True]
    assert all(type(flag) is bool for flag in flags)
```

The constructor does not yet accept `require_confirm`; that is the intended RED.

- [ ] **Step 2: Run the test and verify RED**

```powershell
python -m pytest -q orchestrator/cloud/tests/test_planning.py::test_mock_agent_confirmation_flags_are_plain_booleans
```

Expected: FAIL with `TypeError` because `MockAgent.__init__()` does not accept `require_confirm`.

- [ ] **Step 3: Implement the faithful fixture field**

Change the constructor and capability setup to:

```python
class MockAgent:
    def __init__(self, agent_id, intents, *, kind="agent", deployment="cloud",
                 permissions=None, trust_level="first_party",
                 whole_utterance=(), require_confirm=()):
        self.manifest = MagicMock()
        self.manifest.agent_id = agent_id
        self.manifest.capabilities = []
        self.manifest.latency_budget_ms = 5000
        self.manifest.kind = kind
        self.manifest.deployment = deployment
        self.manifest.requires_permissions = permissions or []
        self.manifest.trust_level = trust_level
        for intent in intents:
            cap = MagicMock()
            cap.intent = intent
            cap.slots = []
            cap.description = ""
            cap.examples = []
            cap.heavy = False
            cap.whole_utterance = intent in (whole_utterance or ())
            cap.require_confirm = intent in (require_confirm or ())
            self.manifest.capabilities.append(cap)
        self.manifest.route_hints = _load_route_hints(agent_id)
        self.endpoint = f"localhost:{hash(agent_id) % 1000 + 50060}"
```

This is the third boolean carried by this `MagicMock` fixture; leaving it unset would make `bool(MagicMock())` true and turn every mock cloud capability into a confirmed operation once the production guard consumes the field.

- [ ] **Step 4: Verify GREEN and existing fixture consumers**

```powershell
python -m pytest -q orchestrator/cloud/tests/test_planning.py
```

Expected: PASS with zero failures.

- [ ] **Step 5: Commit the fixture correction**

```powershell
git add -- orchestrator/cloud/tests/test_planning.py
git diff --cached --check
git commit -m "test(cloud): make mock confirm flags explicit"
```

### Task 2: Write the cloud confirmed-write regression tests first

**Files:**
- Modify: `orchestrator/cloud/tests/test_question_write_guard.py:45-217`
- Test: `orchestrator/cloud/tests/test_question_write_guard.py`

- [ ] **Step 1: Generalize the local `Step` factory without changing production behavior**

Replace `_step` with:

```python
def _step(
    intent: str,
    *,
    agent_id: str = "edge-vehicle",
    deployment: str = "edge",
    kind: str = "edge_fast",
    require_confirm: bool = False,
) -> Step:
    return Step(
        id="s1",
        agent_id=agent_id,
        intent=intent,
        deployment=deployment,
        kind=kind,
        require_confirm=require_confirm,
        slots={},
    )
```

Keep `_GUARD = PlanBuilder._question_write_edge_steps` during RED so the tests exercise the old implementation.

- [ ] **Step 2: Add the direct RED and mixed-plan tests**

Add:

```python
def test_question_shaped_confirmed_cloud_step_is_blocked():
    order = _step(
        "luckin.order",
        agent_id="mcp-bridge",
        deployment="cloud",
        kind="agent",
        require_confirm=True,
    )
    assert _GUARD([order], "红色机油灯亮了还能继续开吗") == [order]


def test_mixed_plan_only_selects_the_confirmed_cloud_step():
    manual = _step(
        "manual.query",
        agent_id="manual-rag",
        deployment="cloud",
        kind="agent",
    )
    order = _step(
        "luckin.order",
        agent_id="mcp-bridge",
        deployment="cloud",
        kind="agent",
        require_confirm=True,
    )
    blocked = _GUARD([manual, order], "红色机油灯亮了还能继续开吗")
    assert blocked == [order]
```

- [ ] **Step 3: Add cloud-read and directive negative controls**

Replace the single cloud-step test with:

```python
@pytest.mark.parametrize("intent", [
    "manual.query",
    "info.search",
    "chitchat.talk",
])
def test_question_shaped_unconfirmed_cloud_step_is_allowed(intent):
    cloud_step = _step(
        intent,
        agent_id="cloud-agent",
        deployment="cloud",
        kind="agent",
    )
    assert _GUARD([cloud_step], "红色机油灯亮了还能继续开吗") == []


def test_directive_to_confirmed_cloud_step_is_allowed():
    order = _step(
        "luckin.order",
        agent_id="mcp-bridge",
        deployment="cloud",
        kind="agent",
        require_confirm=True,
    )
    assert _GUARD([order], "帮我点一杯生椰拿铁") == []
```

The `info.search` control is essential: `is_write_intent("info.search")` is true under the current operation-name contract, so a naive all-cloud `is_write_intent` expansion would fail this test.

- [ ] **Step 4: Add a real `build()` helper for the cloud confirmation path**

Add beside `_build`:

```python
def _build_cloud_order(text: str):
    agents = [
        MockAgent(
            "mcp-bridge",
            ["luckin.order"],
            require_confirm=("luckin.order",),
        ),
        MockAgent("chitchat", ["chitchat.talk"]),
    ]
    catalog = _assemble_capability_catalog(agents)
    ref = catalog.pair_to_ref[("mcp-bridge", "luckin.order")]
    wire = ('{"steps":[{"id":"s1","capability_ref":"%s",'
            '"slots":{},"depends_on":[],"slot_refs":{}}]}' % ref)

    async def mock_llm(messages):
        return wire

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    return asyncio.run(builder.build(
        text,
        WorkingSet(catalog=agents),
        PlanContext(session_id="cloud-confirm"),
    ))
```

- [ ] **Step 5: Add end-to-end RED and directive control**

```python
def test_end_to_end_question_does_not_reach_confirmed_cloud_write():
    plan = _build_cloud_order("红色机油灯亮了还能继续开吗")
    assert [step.intent for step in plan.steps] == ["chitchat.talk"]
    assert "question_write_blocked" in (plan.plan_mode or "")


def test_end_to_end_cloud_order_directive_still_reaches_confirmation_boundary():
    plan = _build_cloud_order("帮我点一杯生椰拿铁")
    assert [step.intent for step in plan.steps] == ["luckin.order"]
    assert plan.steps[0].require_confirm is True
    assert "question_write_blocked" not in (plan.plan_mode or "")
```

- [ ] **Step 6: Run the new tests and verify RED for the intended reason**

```powershell
python -m pytest -q orchestrator/cloud/tests/test_question_write_guard.py -k "confirmed_cloud or mixed_plan"
```

Expected: the confirmed-cloud selection and end-to-end safety tests FAIL because the old guard filters only edge steps; the directive control passes.

Do not alter assertions to make RED disappear.

### Task 3: Implement the minimal declaration-backed guard expansion

**Files:**
- Modify: `orchestrator/cloud/planning.py:1385-1393,1645-1670,2457-2490`
- Modify: `orchestrator/cloud/tests/test_question_write_guard.py:50`
- Test: `orchestrator/cloud/tests/test_question_write_guard.py`

- [ ] **Step 1: Rename the private guard at both call sites and in tests**

Use `_question_side_effect_steps` consistently:

```python
_GUARD = PlanBuilder._question_side_effect_steps
```

Update the focused-ellipsis path and the normal plan-exit path. Do not leave an alias; searches under `orchestrator/cloud/` should find zero `_question_write_edge_steps` references after the implementation. Historical documentation is updated separately in Task 5.

- [ ] **Step 2: Replace the selector with the minimal predicate**

Implement:

```python
@staticmethod
def _question_side_effect_steps(steps: list, text: str) -> list:
    """Select side-effecting steps that a non-directive question must not execute.

    Edge writes use the shared operation-name effect classifier. Cloud steps are
    selected only when the capability authority already marked them
    `require_confirm`; applying `is_write_intent` to every cloud operation would
    misclassify read-like names such as search/menu/talk/status.
    """
    if not steps or not is_non_directive_question(text or ""):
        return []
    return [
        step
        for step in steps
        if (
            (
                (
                    getattr(step, "deployment", "") == "edge"
                    or getattr(step, "kind", "") == "edge_fast"
                )
                and is_write_intent(getattr(step, "intent", ""))
            )
            or bool(getattr(step, "require_confirm", False))
        )
    ]
```

Do not add any agent id, intent literal, merchant name, or second cloud effect table.

- [ ] **Step 3: Align log text and comments without changing the observation key**

Change log wording from “edge write step(s)” to “side-effecting step(s)”. Keep:

```python
plan.plan_mode = f"{plan.plan_mode or ''}_question_write_blocked"
```

The historical `plan_mode` value is an evidence contract and must not be renamed.

- [ ] **Step 4: Run the complete guard file and verify GREEN**

```powershell
python -m pytest -q orchestrator/cloud/tests/test_question_write_guard.py
```

Expected: PASS; all old edge four-way controls and all new cloud confirmation controls are green.

- [ ] **Step 5: Run adjacent plan-exit guards**

```powershell
python -m pytest -q `
  orchestrator/cloud/tests/test_planning_safety_talk.py `
  orchestrator/cloud/tests/test_planning_cancel_gate.py
```

Expected: PASS; safety-empty-plan and cancel-direction behavior remains unchanged.

- [ ] **Step 6: Commit production code and regression tests**

```powershell
git add -- `
  orchestrator/cloud/planning.py `
  orchestrator/cloud/tests/test_question_write_guard.py
git diff --cached --check
git commit -m "fix(cloud): block questions from confirmed writes"
```

### Task 4: Prove the new tests are controlled by the new branch

**Files:**
- Temporarily modify and restore: `orchestrator/cloud/planning.py`
- Test: `orchestrator/cloud/tests/test_question_write_guard.py`

- [ ] **Step 1: Temporarily remove only the confirmation branch**

In `_question_side_effect_steps`, temporarily replace the predicate:

```python
(
    (
        getattr(step, "deployment", "") == "edge"
        or getattr(step, "kind", "") == "edge_fast"
    )
    and is_write_intent(getattr(step, "intent", ""))
)
or bool(getattr(step, "require_confirm", False))
```

with:

```python
(
    (
        getattr(step, "deployment", "") == "edge"
        or getattr(step, "kind", "") == "edge_fast"
    )
    and is_write_intent(getattr(step, "intent", ""))
)
```

Do not commit this mutation.

- [ ] **Step 2: Verify the cloud regression tests turn RED**

```powershell
python -m pytest -q orchestrator/cloud/tests/test_question_write_guard.py -k "confirmed_cloud or mixed_plan"
```

Expected: the confirmed cloud selection and end-to-end question tests fail; existing edge tests remain green.

- [ ] **Step 3: Restore the exact implementation from Task 3**

Restore the complete first predicate block from Step 1, including
`or bool(getattr(step, "require_confirm", False))`, with `apply_patch`; do not use `git checkout` or reset.

- [ ] **Step 4: Verify GREEN again and confirm no mutation remains**

```powershell
python -m pytest -q orchestrator/cloud/tests/test_question_write_guard.py
git diff --check
git status --short --branch
```

Expected: guard tests pass and `planning.py` has no uncommitted mutation.

### Task 5: Update the architecture and executable contract

**Files:**
- Modify: `docs/architecture/cockpit-agent-architecture.md:3-4,940-975,1958-1965`
- Modify: `docs/conventions.md:2523` (append §9.40 after §9.39)
- Modify: `docs/design/2026-08-30-qa-safety-confirmed-write-guard.md:3,50-100`
- Modify: `docs/design/README.md:138`

- [ ] **Step 1: Bump the architecture baseline and correct §5.2.13**

Set the document header to v1.45 / 2026-08-30. Under §5.2.13 “闸放在唯一出口”, add a fourth subsection with this content:

```markdown
#### 四、确认边界也是问句写闸的一部分

长会话 `343934b` 又露出同一条缝的云侧形态：「红色机油灯亮了还能继续开吗」被规划成
`luckin.order` 并进入挂起，后续安全追问继续被商户补槽消费。干净会话 3/3 正确，说明
这是长上下文方差，而不是单轮规则缺席。

云侧没有完整的 capability `effect` 契约，且把所有操作名交给 `is_write_intent` 会把
`search/menu/talk/status` 等读能力误当写。因此本次只收已有权威能无歧义表达的高代价面：
**非指令问句 ∧（端侧写 ∨ `Step.require_confirm=true`）⇒ 丢弃该步**。`require_confirm`
来自 capability manifest，LLM 无权填、降级或升级；它在这里表达的是“这一步已经被系统
判定必须停在确认边界”，不是新增第二份写操作声明。

正常指令、云侧未确认步骤与混合计划中的合法读步骤保持原样；若违规步被删到零，仍交
全局兜底 Agent 回答并保留 `question_write_blocked` 历史观测签名。
```

Add an appendix row:

```markdown
| v1.45 | 2026-08-30 | 内容性校准（QA 长会话安全收尾）：§5.2.13 将问句写闸从端侧写扩到 `require_confirm=true` 的云侧高代价步骤；复用句形、端侧意图效果和 capability 确认三份既有权威，不新增云侧 effect 表，保留云侧读取、正常指令、混合计划与 `question_write_blocked` 观测口径。契约 `conventions.md` §9.40。 |
```

- [ ] **Step 2: Add executable contract §9.40**

Append:

```markdown
### 9.40 非指令问句不得进入需确认的云侧能力（QA 长会话安全收尾，2026-08-30）

唯一判据位于 `PlanBuilder._question_side_effect_steps`：

```text
is_non_directive_question(text)
∧（（edge/edge_fast ∧ is_write_intent(intent)）∨ Step.require_confirm）
```

`Step.require_confirm` 只从 capability manifest/servers 装配，LLM 计划字段不读。云侧分支
不得改用 `is_write_intent`：当前操作名契约会把 `search/menu/talk/status` 误判成写。

| 形态 | 结果 |
|---|---|
| 问句 + 端侧写 | 丢步骤；零步时交安全兜底回答 |
| 问句 + `require_confirm=true` 云侧步 | 丢步骤；不得进入挂起/确认 |
| 指令 + 上述步骤 | 原样保留，继续走 Executor/权限/确认/VAL |
| 问句 + 未确认云侧读取 | 原样保留 |
| 混合计划 | 只丢违规对象，其他步骤保留 |

`plan_mode` 继续使用 `question_write_blocked`，以保持历史报告可比。当前边界刻意不声称
覆盖全部 `require_confirm=false` 的云侧副作用；那需要未来独立的 capability effect 契约。
```

- [ ] **Step 3: Mark the approved design as locally implemented**

Change the design status to `落地中（本地实现完成，待真栈）`, add the implementation commit SHA after Task 3, and keep the live completion criteria open in prose. Update the README row to the same status.

- [ ] **Step 4: Run documentation consistency checks**

```powershell
rg -n "_question_write_edge_steps|_question_side_effect_steps|v1\.45|9\.40" `
  docs/architecture/cockpit-agent-architecture.md `
  docs/conventions.md `
  docs/design/2026-08-30-qa-safety-confirmed-write-guard.md
git diff --check
```

Expected: historical names remain only where explicitly describing the old state; current code pointers use `_question_side_effect_steps`; v1.45 and §9.40 are present; diff check is clean.

- [ ] **Step 5: Commit architecture and contract documents**

```powershell
git add -- `
  docs/architecture/cockpit-agent-architecture.md `
  docs/conventions.md `
  docs/design/2026-08-30-qa-safety-confirmed-write-guard.md `
  docs/design/README.md
git diff --cached --check
git commit -m "docs: define confirmed write question guard"
```

### Task 6: Run the complete local verification ladder

**Files:**
- No tracked file changes expected
- Write ignored logs under: `.artifacts/qa-safety-confirmed-write/`

- [ ] **Step 1: Verify the execution environment is isolated and clean**

```powershell
python scripts/dev_stack.py target show
"PYTHONIOENCODING=$env:PYTHONIOENCODING"
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'pytest|run_e2e|dev_stack\.py (deploy|status|verify)' } |
  Select-Object ProcessId, Name, CommandLine
git status --short --branch
```

Expected: target is cloud; no competing pytest/E2E/deploy/status/verify process is using the repository; tracked worktree is clean. If another run is active, wait rather than manufacturing shared-resource red lights.

- [ ] **Step 2: Clear the known subprocess encoding contaminant**

```powershell
Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
$env:PYTHONIOENCODING
```

Expected: the second command prints nothing.

- [ ] **Step 3: Run focused guard and adjacent tests**

```powershell
python -m pytest -q `
  orchestrator/cloud/tests/test_question_write_guard.py `
  orchestrator/cloud/tests/test_planning_safety_talk.py `
  orchestrator/cloud/tests/test_planning_cancel_gate.py `
  orchestrator/cloud/tests/test_planning.py
```

Expected: exit 0, zero failures.

- [ ] **Step 4: Run all Cloud Planner tests**

```powershell
python -m pytest -q orchestrator/cloud/tests
```

Expected: exit 0, zero failures.

- [ ] **Step 5: Run all four deterministic blocking gates**

```powershell
python test/eval_skills.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python test/eval_exemplars.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python scripts/check_intent_gate.py --json docs/reviews/eval/_ci-run-intent_gate.json
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python test/eval_capability_integrity.py
```

Expected: all four commands report PASS; L0 runs in strict mode; no gate is converted into an informational `--list` run.

- [ ] **Step 6: Run the full repository baseline after the last source/test change**

```powershell
New-Item -ItemType Directory -Force -Path '.artifacts/qa-safety-confirmed-write' | Out-Null
python -m pytest -q -n 8 --dist worksteal 2>&1 |
  Tee-Object -FilePath '.artifacts/qa-safety-confirmed-write/full-pytest.log'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

Expected: exit 0, zero failed tests, skip count consistent with `target=cloud` and local Docker stopped. Record the exact pass/skip counts; do not infer them from the prior `7712/32` baseline.

- [ ] **Step 7: Verify the exact final tree**

```powershell
git status --short --branch
git diff --check
git log -n 8 --oneline --decorate
```

Expected: tracked worktree clean; implementation and contract commits visible; no unrelated file staged or modified.

### Task 7: Record fresh local evidence without claiming live closure

**Files:**
- Modify: `docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md:1491-1546`
- Modify: `AGENTS.md:105-114,307-322,638-680,896`
- Append: `docs/agents-history.md` after §84
- Modify: `docs/design/2026-08-30-qa-safety-confirmed-write-guard.md`

- [ ] **Step 1: Add the safety closeout subsection to the fix plan**

Record:

```markdown
### 安全问句云侧确认写闸收尾（2026-08-30）——本地完成，待真栈

- 真栈触发：`343934b` information T24 把红色机油灯问句规划成 `luckin.order` 并挂起。
- 裁决：不做“全部云侧操作名都过 `is_write_intent`”；只扩到 manifest 已声明
  `require_confirm=true` 的高代价步骤，云侧读取与未确认步骤保持原样。
- TDD：记录 RED 的具名测试、GREEN、反向移除确认分支后的精确红灯。
- 本地证据：写入 Task 6 的确切 Cloud Planner、四道门禁、全量 pass/skip 结果。
- 尚未证明：未部署、未跑原 `information` 长会话，不得写成已修好。
```

- [ ] **Step 2: Update AGENTS current state conservatively**

In the active safety row, change “只差拍板” to “方案已批准、本地实现及全量验证完成、待 push/deploy/长会话真栈”. Keep the row active and uncrossed. At the top snapshot, record the exact tested local SHA and pass/skip counts separately from deployed `343934b`.

Do not change the cloud release until a live status command proves it changed.

- [ ] **Step 3: Append history §85**

Use this heading and evidence structure:

```markdown
## §85 2026-08-30 QA 安全收尾：问句不进入需确认的云侧能力（本地实现态）

### §85.1 取证与裁决
### §85.2 TDD 红绿与反向验证
### §85.3 本地读数
### §85.4 尚未完成的真栈检查点
```

Populate each subsection with exact commands, named failures, commit SHAs, and Task 6 counts. Do not copy the whole design into history.

- [ ] **Step 4: Reconcile the design status and run consistency checks**

```powershell
rg -n "343934b|安全问句被商户|待真栈|question_side_effect" `
  AGENTS.md `
  docs/agents-history.md `
  docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md `
  docs/design/2026-08-30-qa-safety-confirmed-write-guard.md
git diff --check
```

Expected: deployed SHA remains `343934b`; local completion and live incompletion are stated separately; no document says QA all-green.

- [ ] **Step 5: Commit fresh local evidence**

```powershell
git add -- `
  AGENTS.md `
  docs/agents-history.md `
  docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md `
  docs/design/2026-08-30-qa-safety-confirmed-write-guard.md
git diff --cached --check
git commit -m "docs: record confirmed write guard local evidence"
```

### Task 8: Stop at the external-action authorization checkpoint

**Files:**
- No file changes

- [ ] **Step 1: Merge current main into the feature branch if main advanced**

From the isolated worktree:

```powershell
git fetch origin
git log --oneline HEAD..main
```

If the second command lists commits, merge the current local `main` into the feature branch without rebase:

```powershell
git merge --no-edit main
```

Expected: merge succeeds without touching unrelated content. If `AGENTS.md` or architecture documents conflict, stop and resolve against current `main` content; never take an entire side wholesale.

- [ ] **Step 2: Rerun the focused tests after any merge**

```powershell
python -m pytest -q `
  orchestrator/cloud/tests/test_question_write_guard.py `
  orchestrator/cloud/tests/test_planning_safety_talk.py `
  orchestrator/cloud/tests/test_planning_cancel_gate.py
```

Expected: exit 0.

- [ ] **Step 3: Fast-forward local main only when the shared main is idle**

Coordinate with the root workspace owner, then run from the root worktree:

```powershell
git merge --ff-only qa/safety-confirmed-write-guard
```

Expected: fast-forward only. If it fails, return to Step 1; do not rebase or reset.

- [ ] **Step 4: Present the complete push payload**

```powershell
git log --oneline origin/main..HEAD
git diff --name-status origin/main..HEAD
git status --short --branch
```

Expected: the user sees every QA and concurrent mobile commit that `git push` would carry. Do not summarize this as only a count.

- [ ] **Step 5: Request three explicit permissions separately**

Request:

1. permission to `git push` the displayed branch payload;
2. permission to run the exact cloud deploy dry-run and then `--apply` for the reviewed SHA;
3. permission for merchant-writing safety probes and their cleanup, because a failed guard could create a merchant draft before confirmation.

Stop here until all required permissions are explicit. Approval for implementation does not imply approval for push, deploy, merchant writes, or cleanup.

### Task 9: Push, deploy, and prove the live long-context behavior

**Files:**
- Write ignored artifacts under: `.artifacts/dev-stack-verifications/`
- Later update tracked evidence in Task 10

- [ ] **Step 1: Push only after the displayed payload is approved**

```powershell
git push origin main
```

Expected: push succeeds without force; remote `main` resolves to the approved local SHA.

- [ ] **Step 2: Run deploy dry-run and preserve its exact summary**

```powershell
$deploySha = git rev-parse HEAD
python scripts/dev_stack.py deploy --sha $deploySha
```

Expected: dry-run returns the exact changed-path/control summary. If it reports a CI/CD digest approval requirement, present that digest and obtain separate approval before supplying `--approve-ci-cd-sha256`; never invent or reuse an old digest.

- [ ] **Step 3: Apply the reviewed deployment**

This change does not touch `.github/workflows/**`, so the reviewed command is:

```powershell
python scripts/dev_stack.py deploy --sha $deploySha --apply
```

If the dry-run unexpectedly requests a CI/CD digest, stop instead of running this command; return the exact dry-run JSON to the user and create a separately reviewed command from that evidence. Expected here: deployment succeeds and reports `release_sha=$deploySha`, 5/5 healthy, zero warnings, and a rollback point.

- [ ] **Step 4: Verify exact release and unified remote-safe E2E**

```powershell
python scripts/dev_stack.py status
python scripts/dev_stack.py verify
```

Expected: status is `ok`, exact release equals `$deploySha`, 5/5 endpoints healthy; verify reports `verified`, a non-empty release SHA, MiniMax-M3 pin, and the expected remote-safe case set.

- [ ] **Step 5: Run the clean safety control with cleanup authorization active**

```powershell
python scripts/probe_qa_regression.py `
  --cases SF3 `
  --repeat 3 `
  --out .artifacts/dev-stack-verifications/qa-safety-confirmed-write-clean.json
```

Expected: 3/3 PASS; no `luckin.order`, no merchant preview, and all responses contain the required critical safety advice shape. Independently verify merchant drafts and open operations are zero after the probe.

- [ ] **Step 6: Run the original long-context information persona**

```powershell
python scripts/probe_qa_long_sessions.py `
  --persona information `
  --expected-sha $deploySha `
  --out .artifacts/dev-stack-verifications/qa-long-information-confirmed-write.json
```

Expected for the target defect: T24 no longer contains `luckin.order`; subsequent provenance/safety questions are not consumed by a merchant slot; the user receives critical safety guidance. Record unrelated provider/TTS failures separately rather than hiding them in a total.

- [ ] **Step 7: Prove terminal cleanup independently**

Read the completed long-session artifact as an independent terminal assertion:

```powershell
$reportPath = '.artifacts/dev-stack-verifications/qa-long-information-confirmed-write.json'
$report = Get-Content -LiteralPath $reportPath -Raw -Encoding utf8 | ConvertFrom-Json
if ($report.open_operations.PSObject.Properties.Count -ne 0) {
  throw "open operations remain: $($report.open_operations | ConvertTo-Json -Compress)"
}
if (@($report.personas | Where-Object { @($_.cleanup_failures).Count -gt 0 }).Count -ne 0) {
  throw 'cleanup failures remain'
}
$draftProofs = @($report.personas | ForEach-Object { @($_.merchant_cleanup_proofs) })
if (@($draftProofs | Where-Object { $_.drafts_after -ne 0 }).Count -ne 0) {
  throw 'merchant drafts remain'
}
if ($report.release.start.release_sha -ne $deploySha -or
    $report.release.end.release_sha -ne $deploySha) {
  throw 'release continuity failed'
}
'terminal cleanup verified'
```

Expected: `terminal cleanup verified`. If any assertion fails, stop and request cleanup authorization for the exact identifiers; do not issue a broad delete.

### Task 10: Record live evidence and close only this safety debt

**Files:**
- Modify: `AGENTS.md`
- Append: `docs/agents-history.md`
- Modify: `docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md`
- Modify: `docs/design/2026-08-30-qa-safety-confirmed-write-guard.md`
- Modify: `docs/design/README.md`

- [ ] **Step 1: Record exact live evidence**

Update the active safety row with deployed SHA, status/verify result, clean 3/3 control, information-persona T24/T26 behavior, and cleanup proof. Cross out only this safety row when all Task 9 criteria passed.

- [ ] **Step 2: Archive the design without declaring the full QA program green**

Change the design and index status to `已归档`; explicitly list the still-open merchant-window, MC2 daytime, existing business-red, and TTS/provider items in `AGENTS.md` rather than collapsing them into this safety result.

- [ ] **Step 3: Append history §85 live subsection**

Add `### §85.5 部署后真栈证据` with exact artifact paths, release SHA, per-case readings, and terminal cleanup.

- [ ] **Step 4: Verify documentation consistency and commit**

```powershell
rg -n "安全问句被商户|已归档|QA 全绿|release" `
  AGENTS.md `
  docs/agents-history.md `
  docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md `
  docs/design/2026-08-30-qa-safety-confirmed-write-guard.md
git diff --check
git add -- `
  AGENTS.md `
  docs/agents-history.md `
  docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md `
  docs/design/2026-08-30-qa-safety-confirmed-write-guard.md `
  docs/design/README.md
git diff --cached --check
git commit -m "docs: close confirmed write safety debt"
```

Expected: commit succeeds; wording closes only the confirmed-write safety defect and preserves every unrelated open QA item.

---

## Plan self-review result

- Spec coverage: all approved design goals, non-goals, TDD RED/GREEN, negative controls, reverse verification, architecture/contract updates, local verification, authorization boundaries, long-context validation, and cleanup evidence map to named tasks.
- Placeholder scan: the plan contains no placeholder markers or unspecified error-handling steps.
- Type consistency: `MockAgent(require_confirm=...)` produces plain booleans; `_question_side_effect_steps(steps, text)` is used consistently after Task 3; `Step.require_confirm` remains the manifest-derived boolean assembled by `_validated_steps`.
- Scope: merchant business-window and MC2 daytime investigations remain separate QA subprojects; this plan touches merchant state only to validate and clean up the approved safety regression after explicit authorization.
