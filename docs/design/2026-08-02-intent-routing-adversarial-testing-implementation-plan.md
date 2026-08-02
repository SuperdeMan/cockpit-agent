# Intent Routing Adversarial Test System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 日期：2026-08-02
>
> 状态：已批准，待实施
>
> 交付对象：后续实施者与语料审核人
>
> 关联：`docs/design/2026-08-02-intent-routing-adversarial-testing.md`

**Goal:** 建成一套统一语义契约驱动的意图理解与落域对抗测试系统，同时提供广覆盖发现轨、稳定门禁轨、L0–L3 分层执行、首偏离点诊断和可信基线。

**Architecture:** 语料以声明式 YAML 表达绝对 gold、最小对照关系、上下文和能力条件；纯测试侧模块分别负责解析校验、语义裁判、trace、分层运行与报告。现有 `eval_common.py` 继续提供 ProviderLock、文件写入和逐案例 baseline diff；真实 Planner 使用现有 `eval_live.py` 装配，完整决策链使用受控 fake Agent/VAL spy，生产路由行为不因测试建设而改变。

**Tech Stack:** Python 3.12、pytest、PyYAML、dataclasses、现有 gRPC 生成代码、`orchestrator.cloud` Planner/Engine、Edge `fast_intent`/VAL、现有 `test/eval_common.py` 与 `test/eval_live.py`。

---

## 实施前约束

- 实施必须在基于本计划提交的新专用 git worktree 中进行；无论主工作区当时是否有并行改动，都禁止宽泛暂存或借用主工作区脏状态。
- 根 `.env` 只读；不得复制、改写或输出其中的 secret。
- 不修改 `.github/workflows/`、CI/CD 配置或 secret。活模型门禁首期只提供本地命令。
- 不修改 Agent、Exemplar、Skill 或 Route Hint 来让本套测试通过；发现的产品缺陷另立修复批次。
- 每个任务只暂存其 `Files` 所列路径；允许 commit，不 push。
- 全栈只能从根 `compose.yaml` / `make up` 启动，不能以 `deploy/docker-compose.yaml` 为首文件。

## 文件结构

### 新建

| 文件 | 单一职责 |
|---|---|
| `test/support/intent_adversarial_contract.py` | 契约 dataclass、YAML 解析、suite 选择、静态校验与覆盖盘点 |
| `test/support/intent_adversarial_judge.py` | Plan/Decision 快照、绝对 gold、依赖/槽位与 relation 裁判 |
| `test/support/intent_adversarial_trace.py` | Planner/Hint trace、首偏离点、资产指纹与受控消融记录 |
| `test/support/intent_adversarial_runtime.py` | L0 Edge/Hint/检索、L1 Planner、L2 Engine 的运行门面与重复策略 |
| `test/support/intent_adversarial_report.py` | 对抗指标、分维度聚合、Markdown、baseline 资格判定 |
| `test/eval_intent_adversarial.py` | 参数解析、case 选择和各模块编排；不承载业务判断 |
| `test/build_intent_adversarial_candidates.py` | 从现有资产生成只读 candidate 审阅队列；不自动改 corpus |
| `test/test_intent_adversarial_contract.py` | 契约、coverage、family 泄漏与 suite 规则单测 |
| `test/test_intent_adversarial_judge.py` | required/forbidden/extra/dependency/slot/relation 单测 |
| `test/test_intent_adversarial_trace.py` | Hint 前后、首偏离点、指纹与消融归因单测 |
| `test/test_intent_adversarial_runtime.py` | L0/L1/L2、重复与安全副作用单测 |
| `test/test_intent_adversarial_report.py` | 指标、分组、baseline 资格与渲染单测 |
| `test/test_eval_intent_adversarial_cli.py` | CLI、退出码、baseline 硬闸与 L3 子进程单测 |
| `test/test_build_intent_adversarial_candidates.py` | 资产导入、冲突、去重与生命周期单测 |
| `test/test_routing_bench_metric.py` | 历史 `domain_hit_rate` 口径不变回归 |
| `test/eval_corpus/intent_adversarial/README.md` | 新目录结构、字段、命名、生命周期、脱敏与清理约定 |
| `test/eval_corpus/intent_adversarial/suites.yaml` | discovery/gate 选择与重复策略；不保存 provider 名或 secret |
| `test/eval_corpus/intent_adversarial/coverage_exemptions.yaml` | 逐 intent、逐覆盖要求的审核豁免；禁止通配 |
| `test/eval_corpus/intent_adversarial/journey_links.yaml` | 对抗 case 与既有 journey id 的链接，不复制旅程 gold |
| `test/eval_corpus/intent_adversarial/cases/*.yaml` | 九类攻击的人工审核语义用例 |
| `docs/reviews/eval/baseline_intent_adversarial.json` | 经资格校验的机器基线 |
| `docs/reviews/eval/baseline_intent_adversarial.md` | 同一基线的人类摘要 |

### 修改

| 文件 | 改动边界 |
|---|---|
| `test/eval_live.py` | `make_builder()` 透传 model/timeout，复用现有真实 catalog 与 LLM 门面 |
| `test/routing_bench.py` | 把历史交集指标明确命名为 `domain_hit_rate`，不改变历史判定结果 |
| `skills/exemplars/boundaries.yaml` | 给现有 ruling 增加稳定 `id`，不改变裁定文本或检索行为 |
| `test/README.md` | 增加新套件的层级、命令和证据边界 |
| `docs/reviews/eval/README.md` | 增加指标、baseline 与报告解释 |
| `docs/design/2026-08-02-intent-routing-adversarial-testing.md` | 回写实施偏差、落地证据与明确局限 |
| `docs/design/README.md` | 更新设计与实施计划状态 |

## 任务序列

### Task 1: 建立契约模型和新目录规则

**Files:**
- Create: `test/support/intent_adversarial_contract.py`
- Create: `test/test_intent_adversarial_contract.py`
- Create: `test/eval_corpus/intent_adversarial/README.md`
- Create: `test/eval_corpus/intent_adversarial/suites.yaml`
- Create: `test/eval_corpus/intent_adversarial/cases/domain_boundaries.yaml`

- [ ] **Step 1: 写契约加载失败测试**

```python
# test/test_intent_adversarial_contract.py
from pathlib import Path

from support.intent_adversarial_contract import load_cases, load_suites


def test_load_cases_parses_one_turn_and_plan_groups(tmp_path: Path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "a.yaml").write_text("""
schema_version: 1
cases:
  - id: boundary.weather_alert.info
    title: 裸问天气预警
    family_id: boundary.weather_alert
    cohort: unseen_transfer
    risk: medium
    status: reviewed
    tags: {attacks: [A1], mechanisms: [domain_collision], domains: [info, safety], boundary: weather-alert, boundary_ledger: info-safety.weather-alert, boundary_side: left, layers: [l0, l1]}
    provenance: {kind: boundary_ledger, source_ref: weather-alert, reviewed_by: human, reviewed_at: 2026-08-02}
    turns:
      - input: {utterance: 有没有天气预警, context: {}}
        expected:
          addressed: true
          ingress: {allowed: [cloud], forbidden: [edge_local]}
          decision: {allowed: [execute], clarify: forbidden}
          plan:
            required_intent_groups: [{any_of: [info.alerts]}]
            forbidden_intents: [safety.weather_alert]
            allow_extra_intents: false
""", encoding="utf-8")

    cases = load_cases(root)

    assert len(cases) == 1
    assert cases[0].id == "boundary.weather_alert.info"
    assert cases[0].turns[0].expected.plan.required_groups[0].any_of == ("info.alerts",)
    assert cases[0].turns[0].expected.plan.forbidden_intents == ("safety.weather_alert",)
    assert cases[0].turns[0].expected.plan.assert_plan is True


def test_load_cases_keeps_replan_observation_and_expected_plan(tmp_path: Path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "adaptive.yaml").write_text("""
schema_version: 1
cases:
  - id: composition.weather-outing
    title: 天气出游
    family_id: composition.weather-outing
    cohort: seen_regression
    risk: medium
    status: candidate
    tags: {attacks: [A4], mechanisms: [composition], domains: [info, nearby], layers: [l1, l2]}
    provenance: {kind: authored}
    turns:
      - input: {utterance: 今天的天气适合去哪玩, context: {}}
        expected:
          plan:
            required_intent_groups: [{any_of: [info.weather]}]
          replans:
            - after:
                result: {step_id: s1, status: ok, data: {condition: 中雨}, speech: 有中雨}
              plan:
                required_intent_groups: [{any_of: [nearby.search]}]
""", encoding="utf-8")

    turn = load_cases(root)[0].turns[0]
    assert turn.expected.replans[0].after["result"]["data"] == {"condition": "中雨"}
    assert turn.expected.replans[0].plan.required_groups[0].any_of == ("nearby.search",)


def test_load_suites_keeps_run_policy_out_of_gold(tmp_path: Path):
    path = tmp_path / "suites.yaml"
    path.write_text("""
schema_version: 1
suites:
  discovery:
    statuses: [candidate, reviewed, stable]
    live_statuses: [reviewed, stable]
    min_cases: 450
    max_cases: 500
    attack_minimums: {A1: 80, A2: 50, A3: 45, A4: 60, A5: 50, A6: 45, A7: 40, A8: 35, A9: 45}
    normal_repeats: 1
    failure_repeats: 3
    high_risk_repeats: 3
  gate:
    statuses: [stable]
    live_statuses: [stable]
    min_cases: 120
    max_cases: 160
    attack_minimums: {}
    normal_repeats: 1
    failure_repeats: 3
    high_risk_repeats: 3
""", encoding="utf-8")

    suites = load_suites(path)

    assert suites["gate"].statuses == ("stable",)
    assert suites["gate"].live_statuses == ("stable",)
    assert (suites["gate"].min_cases, suites["gate"].max_cases) == (120, 160)
    assert suites["discovery"].attack_minimums["A4"] == 60
    assert suites["gate"].failure_repeats == 3
```

- [ ] **Step 2: 运行测试，确认因模块不存在而失败**

Run: `python -m pytest test/test_intent_adversarial_contract.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'support.intent_adversarial_contract'`.

- [ ] **Step 3: 实现不可变契约 dataclass 与解析器**

```python
# test/support/intent_adversarial_contract.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class IntentGroup:
    any_of: tuple[str, ...]


@dataclass(frozen=True)
class DependencyExpectation:
    producer: tuple[str, ...]
    consumer: str
    carries: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlotExpectation:
    intent: str
    key: str
    matcher: str = "presence"
    value: Any = None
    allowed: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    source: str = ""


@dataclass(frozen=True)
class RetrievalExpectation:
    required_skills: tuple[str, ...] = ()
    forbidden_skills: tuple[str, ...] = ()
    required_exemplars: tuple[str, ...] = ()
    forbidden_exemplars: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanExpectation:
    assert_plan: bool = False
    required_groups: tuple[IntentGroup, ...] = ()
    forbidden_intents: tuple[str, ...] = ()
    allow_extra_intents: bool = False
    allowed_extra_intents: tuple[str, ...] = ()
    allowed_complexities: tuple[str, ...] = ()
    dependencies: tuple[DependencyExpectation, ...] = ()
    slots: tuple[SlotExpectation, ...] = ()


@dataclass(frozen=True)
class ReplanExpectation:
    after: dict[str, Any]
    plan: PlanExpectation


@dataclass(frozen=True)
class TurnExpectation:
    addressed: bool | None = None
    ingress_allowed: tuple[str, ...] = ()
    ingress_forbidden: tuple[str, ...] = ()
    decision_allowed: tuple[str, ...] = ()
    clarify: str = "allowed"
    plan: PlanExpectation = field(default_factory=PlanExpectation)
    replans: tuple[ReplanExpectation, ...] = ()
    retrieval: RetrievalExpectation = field(default_factory=RetrievalExpectation)
    no_side_effect_before_confirm: bool = False


@dataclass(frozen=True)
class CaseTurn:
    utterance: str
    context: dict[str, Any]
    expected: TurnExpectation


@dataclass(frozen=True)
class RelationSpec:
    base_case: str
    type: str
    expectation: dict[str, Any]


@dataclass(frozen=True)
class AdversarialCase:
    id: str
    title: str
    family_id: str
    cohort: str
    risk: str
    status: str
    tags: dict[str, Any]
    provenance: dict[str, Any]
    turns: tuple[CaseTurn, ...]
    relation: RelationSpec | None = None


@dataclass(frozen=True)
class SuiteConfig:
    statuses: tuple[str, ...]
    live_statuses: tuple[str, ...]
    min_cases: int
    max_cases: int
    attack_minimums: dict[str, int]
    normal_repeats: int
    failure_repeats: int
    high_risk_repeats: int


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(str(v) for v in (value or []))


def _parse_plan(raw: dict[str, Any] | None) -> PlanExpectation:
    asserted = raw is not None
    raw = raw or {}
    groups = tuple(IntentGroup(_strings(g.get("any_of")))
                   for g in raw.get("required_intent_groups") or [])
    deps = tuple(DependencyExpectation(
        producer=_strings(d.get("producer")),
        consumer=str(d.get("consumer") or ""),
        carries=_strings(d.get("carries")),
    ) for d in raw.get("dependencies") or [])
    slots = tuple(SlotExpectation(
        intent=str(s.get("intent") or ""),
        key=str(s.get("key") or ""),
        matcher=str(s.get("matcher") or ("one_of" if s.get("allowed") else "presence")),
        value=s.get("value"),
        allowed=_strings(s.get("allowed")),
        minimum=float(s["minimum"]) if s.get("minimum") is not None else None,
        maximum=float(s["maximum"]) if s.get("maximum") is not None else None,
        source=str(s.get("source") or ""),
    ) for s in raw.get("slots") or [])
    complexity = raw.get("complexity") or {}
    return PlanExpectation(
        assert_plan=asserted,
        required_groups=groups,
        forbidden_intents=_strings(raw.get("forbidden_intents")),
        allow_extra_intents=bool(raw.get("allow_extra_intents", False)),
        allowed_extra_intents=_strings(raw.get("allowed_extra_intents")),
        allowed_complexities=_strings(complexity.get("allowed")),
        dependencies=deps,
        slots=slots,
    )


def _parse_expected(raw: dict[str, Any]) -> TurnExpectation:
    ingress = raw.get("ingress") or {}
    decision = raw.get("decision") or {}
    retrieval = raw.get("retrieval") or {}
    return TurnExpectation(
        addressed=raw.get("addressed") if isinstance(raw.get("addressed"), bool) else None,
        ingress_allowed=_strings(ingress.get("allowed")),
        ingress_forbidden=_strings(ingress.get("forbidden")),
        decision_allowed=_strings(decision.get("allowed")),
        clarify=str(decision.get("clarify") or "allowed"),
        plan=_parse_plan(raw.get("plan")),
        replans=tuple(ReplanExpectation(
            after=dict(p.get("after") or {}),
            plan=_parse_plan(p.get("plan")),
        ) for p in raw.get("replans") or []),
        retrieval=RetrievalExpectation(
            required_skills=_strings(retrieval.get("required_skills")),
            forbidden_skills=_strings(retrieval.get("forbidden_skills")),
            required_exemplars=_strings(retrieval.get("required_exemplars")),
            forbidden_exemplars=_strings(retrieval.get("forbidden_exemplars")),
        ),
        no_side_effect_before_confirm=bool(
            (raw.get("safety") or {}).get("no_side_effect_before_confirm", False)),
    )


def _parse_case(raw: dict[str, Any]) -> AdversarialCase:
    turns = tuple(CaseTurn(
        utterance=str((t.get("input") or {}).get("utterance") or ""),
        context=dict((t.get("input") or {}).get("context") or {}),
        expected=_parse_expected(t.get("expected") or {}),
    ) for t in raw.get("turns") or [])
    relation_raw = raw.get("relation")
    relation = None if not relation_raw else RelationSpec(
        base_case=str(relation_raw.get("base_case") or ""),
        type=str(relation_raw.get("type") or ""),
        expectation=dict(relation_raw.get("expectation") or {}),
    )
    return AdversarialCase(
        id=str(raw.get("id") or ""),
        title=str(raw.get("title") or ""),
        family_id=str(raw.get("family_id") or ""),
        cohort=str(raw.get("cohort") or ""),
        risk=str(raw.get("risk") or ""),
        status=str(raw.get("status") or ""),
        tags=dict(raw.get("tags") or {}),
        provenance=dict(raw.get("provenance") or {}),
        turns=turns,
        relation=relation,
    )


def load_cases(root: Path) -> list[AdversarialCase]:
    cases: list[AdversarialCase] = []
    for path in sorted(Path(root).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if data.get("schema_version") != 1:
            raise ValueError(f"{path}: schema_version must be 1")
        cases.extend(_parse_case(row) for row in data.get("cases") or [])
    return cases


def load_suites(path: Path) -> dict[str, SuiteConfig]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if data.get("schema_version") != 1:
        raise ValueError(f"{path}: schema_version must be 1")
    return {
        str(name): SuiteConfig(
            statuses=_strings(raw.get("statuses")),
            live_statuses=_strings(raw.get("live_statuses")),
            min_cases=int(raw.get("min_cases", 0)),
            max_cases=int(raw.get("max_cases", 0)),
            attack_minimums={str(key): int(value)
                             for key, value in (raw.get("attack_minimums") or {}).items()},
            normal_repeats=int(raw.get("normal_repeats", 1)),
            failure_repeats=int(raw.get("failure_repeats", 3)),
            high_risk_repeats=int(raw.get("high_risk_repeats", 3)),
        )
        for name, raw in (data.get("suites") or {}).items()
    }
```

- [ ] **Step 4: 写目录规则与最小 suite，不加入未审核批量语料**

```markdown
<!-- test/eval_corpus/intent_adversarial/README.md -->
# 意图与落域对抗语料

本目录只保存人工可审计的语义契约。运行时报告写到显式 `--out-json/--out-md`，不得在本目录留下临时结果。

## 文件

- `suites.yaml`：状态选择与重复策略，不保存 provider、model、secret。
- `coverage_exemptions.yaml`：逐 intent、逐要求的显式豁免。
- `journey_links.yaml`：链接现有 journey id，不复制旅程 gold。
- `cases/*.yaml`：按攻击机制分文件；每个文件固定 `schema_version: 1` 与 `cases:`。

## 命名与字段

- case id：`attack.family.variant`，一经进入 reviewed 不改名；替代时 retired 原项并新增 id。
- `family_id`：同源原句、paraphrase、最小变体共享；用于 seen/unseen 防泄漏。
- `tags.attacks/domains/layers`：attacks 至少含一个 `A1`–`A9` 编号；domains 声明涉及域；layers 声明 L0–L3 执行层。细分机制另放 `tags.mechanisms`。
- plan：必要组之间 AND、组内 `any_of` OR；默认禁止未声明额外 intent。
- adaptive：初始 `plan` 与 `replans[].after.result + plan` 分开写，result 形状对齐生产 observation。
- relation：变体必须同时有自己的 absolute gold，不能只写相对关系。

## 状态

`candidate → reviewed → stable → retired`。只有 `reviewed_by: human` 的案例可以进入 `reviewed`；只有固定 provider 重复稳定的案例可以进入 `stable`。`retired` 保留原文、原因和替代保护，不删除历史。

## 数据隔离

同源文本共享 `family_id`。进入 Skill/Exemplar/Hint 修复资产的 family 只能计入 `seen_regression`；未进入修复资产的 family 才能计入 `unseen_transfer`。

## 脱敏

真实 badcase 入库前删除姓名、电话、精确住址、车牌、账号与 token。无法确认脱敏完成时保持 `candidate` 且不提交原文。

## 清理

运行报告只写 `docs/reviews/eval/_ci-run-*` 或显式输出路径。corpus 内不放 trace、临时 prompt、模型原始回复和失败截图。retired 用例不删除；写 `retired_reason` 与替代 case/保护链接。
```

```yaml
# test/eval_corpus/intent_adversarial/suites.yaml
schema_version: 1
suites:
  discovery:
    statuses: [candidate, reviewed, stable]
    live_statuses: [reviewed, stable]
    min_cases: 450
    max_cases: 500
    attack_minimums: {A1: 80, A2: 50, A3: 45, A4: 60, A5: 50, A6: 45, A7: 40, A8: 35, A9: 45}
    normal_repeats: 1
    failure_repeats: 3
    high_risk_repeats: 3
  gate:
    statuses: [stable]
    live_statuses: [stable]
    min_cases: 120
    max_cases: 160
    attack_minimums: {}
    normal_repeats: 1
    failure_repeats: 3
    high_risk_repeats: 3
```

```yaml
# test/eval_corpus/intent_adversarial/cases/domain_boundaries.yaml
schema_version: 1
cases: []
```

- [ ] **Step 5: 运行契约加载测试**

Run: `python -m pytest test/test_intent_adversarial_contract.py -q`

Expected: `3 passed`.

- [ ] **Step 6: 提交契约骨架**

```powershell
git add -- test/support/intent_adversarial_contract.py test/test_intent_adversarial_contract.py test/eval_corpus/intent_adversarial
git commit -m "test: add adversarial intent contract"
```

### Task 2: 加入严格 schema、inventory 与 family 泄漏门禁

**Files:**
- Modify: `test/support/intent_adversarial_contract.py`
- Modify: `test/test_intent_adversarial_contract.py`
- Modify: `test/eval_corpus/intent_adversarial/suites.yaml`

- [ ] **Step 1: 写十三个失败测试**

```python
# append to test/test_intent_adversarial_contract.py
from dataclasses import replace

import pytest

from support.intent_adversarial_contract import (
    AdversarialCase, CaseTurn, IntentGroup, PlanExpectation, RelationSpec,
    TurnExpectation, validate_cases,
)


@pytest.fixture
def contract_case():
    return AdversarialCase(
        id="boundary.weather_alert.info",
        title="裸问天气预警",
        family_id="boundary.weather_alert",
        cohort="unseen_transfer",
        risk="medium",
        status="reviewed",
        tags={"attacks": ["A1"], "mechanisms": ["domain_collision"],
              "domains": ["info", "safety"],
              "boundary": "weather-alert",
              "boundary_ledger": "info-safety.weather-alert",
              "boundary_side": "left",
              "layers": ["l0", "l1"]},
        provenance={"kind": "boundary_ledger", "reviewed_by": "human",
                    "reviewed_at": "2026-08-02"},
        turns=(CaseTurn(
            utterance="有没有天气预警",
            context={},
            expected=TurnExpectation(plan=PlanExpectation(
                assert_plan=True,
                required_groups=(IntentGroup(("info.alerts",)),),
            )),
        ),),
    )


def test_duplicate_ids_are_rejected(contract_case):
    errors = validate_cases([contract_case, contract_case], {"info.alerts"})
    assert any("duplicate id" in e for e in errors)


def test_unknown_required_intent_is_rejected(contract_case):
    errors = validate_cases([contract_case], set())
    assert any("unknown intent info.alerts" in e for e in errors)


def test_required_and_forbidden_conflict_is_rejected(contract_case):
    plan = replace(contract_case.turns[0].expected.plan,
                   forbidden_intents=("info.alerts",))
    expected = replace(contract_case.turns[0].expected, plan=plan)
    turn = replace(contract_case.turns[0], expected=expected)
    errors = validate_cases([replace(contract_case, turns=(turn,))], {"info.alerts"})
    assert any("required and forbidden" in e for e in errors)


def test_stable_requires_human_review(contract_case):
    case = replace(contract_case, status="stable",
                   provenance={"kind": "llm_candidate", "reviewed_by": "model"})
    errors = validate_cases([case], {"info.alerts"})
    assert any("stable requires reviewed_by=human" in e for e in errors)


def test_relation_base_must_exist(contract_case):
    relation = RelationSpec("missing.base", "invariant", {})
    errors = validate_cases([replace(contract_case, relation=relation)], {"info.alerts"})
    assert any("missing relation base" in e for e in errors)


def test_family_cannot_mix_seen_and_unseen(contract_case):
    seen = replace(contract_case, id="seen", cohort="seen_regression")
    unseen = replace(contract_case, id="unseen", cohort="unseen_transfer")
    errors = validate_cases([seen, unseen], {"info.alerts"})
    assert any("family leakage" in e for e in errors)


def test_reviewed_turn_requires_absolute_gold(contract_case):
    empty = replace(contract_case.turns[0], expected=TurnExpectation())
    errors = validate_cases(
        [replace(contract_case, turns=(empty,))], {"info.alerts"})
    assert any("reviewed turn requires absolute gold" in e for e in errors)


def test_unknown_relation_expectation_key_is_rejected(contract_case):
    relation = RelationSpec(
        contract_case.id, "route_flip", {"forbiden_after": ["info.alerts"]})
    errors = validate_cases(
        [replace(contract_case, id="variant", relation=relation)],
        {"info.alerts"})
    assert any("unknown relation expectation keys" in e for e in errors)


def test_relation_pair_must_declare_the_same_layers(contract_case):
    variant = replace(
        contract_case, id="variant",
        tags={**contract_case.tags, "layers": ["l2"]},
        relation=RelationSpec(contract_case.id, "invariant", {}))
    errors = validate_cases(
        [contract_case, variant], {"info.alerts"})
    assert any("relation pair must share layers" in e for e in errors)


def test_stable_relation_requires_stable_base(contract_case):
    variant = replace(
        contract_case, id="variant", status="stable",
        relation=RelationSpec(contract_case.id, "invariant", {}))
    errors = validate_cases(
        [contract_case, variant], {"info.alerts"})
    assert any("stable relation requires stable base" in e for e in errors)


def test_relation_cannot_reference_itself(contract_case):
    case = replace(
        contract_case,
        relation=RelationSpec(contract_case.id, "invariant", {}))
    errors = validate_cases([case], {"info.alerts"})
    assert any("relation base must be a distinct root case" in e for e in errors)


def test_unknown_plan_key_is_rejected(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "bad.yaml").write_text("""
schema_version: 1
cases:
  - id: bad
    title: bad
    family_id: bad
    cohort: unseen_transfer
    risk: low
    status: candidate
    tags: {attacks: [expression], domains: [info]}
    provenance: {kind: authored}
    turns:
      - input: {utterance: 查天气, context: {}}
        expected:
          plan: {required_intent_groups: [{any_of: [info.weather]}], typo_key: true}
""", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown keys.*typo_key"):
        load_cases(root)


def test_duplicate_yaml_key_is_rejected(tmp_path):
    path = tmp_path / "suites.yaml"
    path.write_text("schema_version: 1\nschema_version: 1\nsuites: {}\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate YAML key schema_version"):
        load_suites(path)
```

- [ ] **Step 2: 运行测试，确认缺少 `validate_cases`**

Run: `python -m pytest test/test_intent_adversarial_contract.py -q`

Expected: collection FAIL because `validate_cases` is not defined.

- [ ] **Step 3: 实现唯一键 YAML loader 和逐层未知键拒绝**

```python
class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.load(Path(path).read_text(encoding="utf-8"),
                     Loader=UniqueKeyLoader) or {}


def _expect_keys(raw: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
```

`load_cases()`/`load_suites()` 全部改走 `_read_yaml()`。在解析前分别调用 `_expect_keys()`：document=`schema_version,cases|suites`；suite=`statuses,live_statuses,min_cases,max_cases,attack_minimums,normal_repeats,failure_repeats,high_risk_repeats`；case=`id,title,family_id,cohort,risk,status,tags,provenance,turns,relation`；turn=`input,expected`；input=`utterance,context`；expected=`addressed,ingress,decision,plan,replans,retrieval,safety`；ingress=`allowed,forbidden`；decision=`allowed,clarify`；plan=`required_intent_groups,forbidden_intents,allow_extra_intents,allowed_extra_intents,complexity,dependencies,slots`；intent group=`any_of`；complexity=`allowed`；replan=`after,plan`；after=`result`；result=`step_id,status,data,speech,error`；dependency=`producer,consumer,carries`；slot=`intent,key,matcher,value,allowed,minimum,maximum,source`；retrieval=`required_skills,forbidden_skills,required_exemplars,forbidden_exemplars`；safety=`no_side_effect_before_confirm`；relation=`base_case,type,expectation`。`context`、relation expectation、tags、provenance 和 result `data` 是有意开放的审计/业务 payload，其父级仍严格。

- [ ] **Step 4: 实现语义校验器**

```python
# append to test/support/intent_adversarial_contract.py
_STATUSES = {"candidate", "reviewed", "stable", "retired"}
_RISKS = {"low", "medium", "high", "critical"}
_COHORTS = {"seen_regression", "unseen_transfer"}
_INGRESS = {"edge_local", "mixed", "cloud"}
_DECISIONS = {"execute", "clarify", "reject", "degrade", "confirm", "cancel"}
_CLARIFY = {"allowed", "required", "forbidden"}
_COMPLEXITIES = {"simple", "adaptive"}
_STEP_STATUSES = {"pending", "running", "ok", "failed", "skipped",
                  "need_confirm", "need_slot"}
_ATTACKS = {f"A{index}" for index in range(1, 10)}
_RELATIONS = {
    "invariant", "route_flip", "intent_add", "intent_remove",
    "clarify_flip", "context_override", "clause_commute",
}
_RELATION_KEYS = {
    "invariant": set(),
    "route_flip": {"forbidden_after", "required_change"},
    "intent_add": {"add"},
    "intent_remove": {"remove"},
    "clarify_flip": set(),
    "context_override": {"must_differ"},
    "clause_commute": set(),
}


def _plan_intents(plan: PlanExpectation) -> set[str]:
    intents = {intent for group in plan.required_groups for intent in group.any_of}
    intents.update(plan.forbidden_intents)
    intents.update(plan.allowed_extra_intents)
    for dep in plan.dependencies:
        intents.update(dep.producer)
        intents.add(dep.consumer)
    intents.update(slot.intent for slot in plan.slots)
    return intents


def _has_absolute_gold(expected: TurnExpectation) -> bool:
    retrieval = expected.retrieval
    return bool(
        expected.addressed is not None
        or expected.ingress_allowed or expected.ingress_forbidden
        or expected.decision_allowed or expected.clarify != "allowed"
        or expected.plan.assert_plan or expected.replans
        or retrieval.required_skills or retrieval.forbidden_skills
        or retrieval.required_exemplars or retrieval.forbidden_exemplars
        or expected.no_side_effect_before_confirm
    )


def validate_cases(cases: list[AdversarialCase], known_intents: set[str]) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    by_family: dict[str, set[str]] = {}
    case_ids = {case.id for case in cases}
    by_id = {case.id: case for case in cases}
    for case in cases:
        if not case.id or case.id in ids:
            errors.append(f"duplicate id: {case.id!r}")
        ids.add(case.id)
        if case.status not in _STATUSES:
            errors.append(f"{case.id}: invalid status {case.status!r}")
        if case.risk not in _RISKS:
            errors.append(f"{case.id}: invalid risk {case.risk!r}")
        if case.cohort not in _COHORTS:
            errors.append(f"{case.id}: invalid cohort {case.cohort!r}")
        if not case.family_id:
            errors.append(f"{case.id}: missing family_id")
        by_family.setdefault(case.family_id, set()).add(case.cohort)
        if case.status in {"reviewed", "stable"} and case.provenance.get("reviewed_by") != "human":
            errors.append(f"{case.id}: {case.status} requires reviewed_by=human")
        if case.status in {"reviewed", "stable"} and not case.provenance.get("reviewed_at"):
            errors.append(f"{case.id}: {case.status} requires reviewed_at")
        if case.status == "stable":
            for key in ("stabilized_provider", "stabilized_at", "evidence_report"):
                if not case.provenance.get(key):
                    errors.append(f"{case.id}: stable requires provenance.{key}")
        if case.status == "retired" and not case.provenance.get("retired_reason"):
            errors.append(f"{case.id}: retired requires provenance.retired_reason")
        attacks = set(case.tags.get("attacks") or [])
        layers = set(case.tags.get("layers") or [])
        if not attacks or not attacks <= _ATTACKS:
            errors.append(f"{case.id}: invalid tags.attacks {sorted(attacks)}")
        if not layers or not layers <= {"l0", "l1", "l2", "l3"}:
            errors.append(f"{case.id}: invalid tags.layers {sorted(layers)}")
        if not case.turns:
            errors.append(f"{case.id}: turns must not be empty")
        for index, turn in enumerate(case.turns):
            if not turn.utterance.strip():
                errors.append(f"{case.id}#{index}: empty utterance")
            if (case.status in {"reviewed", "stable"}
                    and not _has_absolute_gold(turn.expected)):
                errors.append(
                    f"{case.id}#{index}: {case.status} turn requires absolute gold")
            if not set(turn.expected.ingress_allowed) <= _INGRESS:
                errors.append(f"{case.id}: invalid ingress allow-list")
            if not set(turn.expected.ingress_forbidden) <= _INGRESS:
                errors.append(f"{case.id}: invalid ingress deny-list")
            if set(turn.expected.ingress_allowed) & set(turn.expected.ingress_forbidden):
                errors.append(f"{case.id}: ingress allow/deny overlap")
            if not set(turn.expected.decision_allowed) <= _DECISIONS:
                errors.append(f"{case.id}: invalid decision allow-list")
            if turn.expected.clarify not in _CLARIFY:
                errors.append(f"{case.id}: invalid clarify expectation")
            plans = (turn.expected.plan,) + tuple(
                replan.plan for replan in turn.expected.replans)
            for plan in plans:
                referenced = _plan_intents(plan)
                required = {intent for group in plan.required_groups
                            for intent in group.any_of}
                forbidden = set(plan.forbidden_intents)
                if not set(plan.allowed_complexities) <= _COMPLEXITIES:
                    errors.append(f"{case.id}: invalid plan complexity")
                for intent in referenced:
                    if intent not in known_intents:
                        errors.append(f"{case.id}: unknown intent {intent}")
                overlap = required & forbidden
                if overlap:
                    errors.append(f"{case.id}: required and forbidden overlap {sorted(overlap)}")
                extra_overlap = set(plan.allowed_extra_intents) & forbidden
                if extra_overlap:
                    errors.append(
                        f"{case.id}: allowed extra and forbidden overlap {sorted(extra_overlap)}")
                for group in plan.required_groups:
                    if not group.any_of:
                        errors.append(f"{case.id}: required intent group is empty")
                for dep in plan.dependencies:
                    if not dep.producer or not dep.consumer:
                        errors.append(f"{case.id}: invalid dependency {dep}")
                    if not set(dep.producer) <= required | set(plan.allowed_extra_intents):
                        errors.append(f"{case.id}: dependency producer is not admitted")
                    if dep.consumer not in required | set(plan.allowed_extra_intents):
                        errors.append(f"{case.id}: dependency consumer is not admitted")
                for slot in plan.slots:
                    if not slot.intent or not slot.key:
                        errors.append(f"{case.id}: invalid slot expectation {slot}")
                    if slot.intent not in required | set(plan.allowed_extra_intents):
                        errors.append(f"{case.id}: slot intent is not admitted")
                    if slot.matcher not in {
                            "presence", "absence", "exact", "one_of", "range",
                            "source_reference"}:
                        errors.append(f"{case.id}: invalid slot matcher {slot.matcher!r}")
                    if slot.matcher == "exact" and slot.value is None:
                        errors.append(f"{case.id}: exact slot matcher requires value")
                    if slot.matcher == "one_of" and not slot.allowed:
                        errors.append(f"{case.id}: one_of slot matcher requires allowed")
                    if (slot.matcher == "range"
                            and slot.minimum is None and slot.maximum is None):
                        errors.append(f"{case.id}: range slot matcher needs a bound")
                    if (slot.minimum is not None and slot.maximum is not None
                            and slot.minimum > slot.maximum):
                        errors.append(f"{case.id}: slot minimum exceeds maximum")
                    if slot.matcher == "source_reference" and not slot.source:
                        errors.append(
                            f"{case.id}: source_reference matcher requires source")
            for replan in turn.expected.replans:
                result = replan.after.get("result")
                if not isinstance(result, dict):
                    errors.append(f"{case.id}: replan.after.result must be a mapping")
                    continue
                missing = {"step_id", "status", "data"} - set(result)
                if missing:
                    errors.append(
                        f"{case.id}: replan result missing {sorted(missing)}")
                elif result.get("status") not in _STEP_STATUSES:
                    errors.append(f"{case.id}: invalid replan result status")
        if case.relation:
            if case.relation.type not in _RELATIONS:
                errors.append(f"{case.id}: invalid relation {case.relation.type!r}")
            else:
                unknown = (set(case.relation.expectation)
                           - _RELATION_KEYS[case.relation.type])
                if unknown:
                    errors.append(
                        f"{case.id}: unknown relation expectation keys {sorted(unknown)}")
            if case.relation.base_case not in case_ids:
                errors.append(f"{case.id}: missing relation base {case.relation.base_case!r}")
            elif by_id[case.relation.base_case].family_id != case.family_id:
                errors.append(f"{case.id}: relation base must share family_id")
            else:
                base = by_id[case.relation.base_case]
                if base.id == case.id or base.relation is not None:
                    errors.append(
                        f"{case.id}: relation base must be a distinct root case")
                if (case.status in {"reviewed", "stable"}
                        and base.status not in {"reviewed", "stable"}):
                    errors.append(
                        f"{case.id}: reviewed relation requires reviewed base")
                if case.status == "stable" and base.status != "stable":
                    errors.append(
                        f"{case.id}: stable relation requires stable base")
                if set(base.tags.get("layers") or []) != set(
                        case.tags.get("layers") or []):
                    errors.append(f"{case.id}: relation pair must share layers")
                if len(base.turns) != 1 or len(case.turns) != 1:
                    errors.append(
                        f"{case.id}: schema v1 relation pair must each have one turn")
    for family, cohorts in by_family.items():
        if len(cohorts) > 1:
            errors.append(f"family leakage: {family} spans {sorted(cohorts)}")
    return errors
```

- [ ] **Step 5: 加 suite 数值校验**

在 `load_suites()` 构造前拒绝小于 1 的重复次数，并拒绝 discovery/gate 之外的未知 suite：

```python
    names = set((data.get("suites") or {}).keys())
    if names != {"discovery", "gate"}:
        raise ValueError(f"suite names must be discovery/gate, got {sorted(names)}")
    for name, raw in data["suites"].items():
        statuses = set(_strings(raw.get("statuses")))
        live_statuses = set(_strings(raw.get("live_statuses")))
        if not live_statuses <= statuses:
            raise ValueError(f"{name}.live_statuses must be a subset of statuses")
        min_cases, max_cases = int(raw.get("min_cases", 0)), int(raw.get("max_cases", 0))
        if min_cases < 1 or max_cases < min_cases:
            raise ValueError(f"{name}.case bounds are invalid")
        attack_minimums = raw.get("attack_minimums") or {}
        if (not isinstance(attack_minimums, dict)
                or not set(attack_minimums) <= _ATTACKS
                or any(int(value) < 0 for value in attack_minimums.values())):
            raise ValueError(f"{name}.attack_minimums are invalid")
        for key in ("normal_repeats", "failure_repeats", "high_risk_repeats"):
            if int(raw.get(key, 0)) < 1:
                raise ValueError(f"{name}.{key} must be >= 1")
```

- [ ] **Step 6: 运行契约测试**

Run: `python -m pytest test/test_intent_adversarial_contract.py -q`

Expected: all tests PASS.

- [ ] **Step 7: 提交严格契约**

```powershell
git add -- test/support/intent_adversarial_contract.py test/test_intent_adversarial_contract.py test/eval_corpus/intent_adversarial/suites.yaml
git commit -m "test: validate adversarial intent corpus"
```

### Task 3: 实现精确计划集合、依赖、槽位和决策裁判

**Files:**
- Create: `test/support/intent_adversarial_judge.py`
- Create: `test/test_intent_adversarial_judge.py`

- [ ] **Step 1: 写必要组、禁选、多路由和依赖失败测试**

```python
# test/test_intent_adversarial_judge.py
from support.intent_adversarial_contract import (
    DependencyExpectation, IntentGroup, PlanExpectation, ReplanExpectation,
    SlotExpectation, TurnExpectation,
)
from support.intent_adversarial_judge import (
    DecisionSnapshot, PlanSnapshot, StepSnapshot, judge_turn,
)


def _step(id_, intent, *, depends_on=(), slots=None, slot_refs=None):
    return StepSnapshot(id=id_, agent_id=intent.split(".")[0], intent=intent,
                        slots=slots or {}, depends_on=tuple(depends_on),
                        slot_refs=slot_refs or {}, require_confirm=False)


def _snapshot(*intents):
    return DecisionSnapshot(
        ingress="cloud", addressed=True, decision="execute", clarify=False,
        plan=PlanSnapshot(steps=tuple(_step(f"s{i}", intent)
                                      for i, intent in enumerate(intents, 1)),
                          complexity="simple", goal="", skills=(), exemplars=(),
                          hint_effect="", catalog_stats={}),
    )


def test_required_groups_are_and_and_group_members_are_or():
    expected = TurnExpectation(plan=PlanExpectation(assert_plan=True, required_groups=(
        IntentGroup(("info.weather", "info.forecast")),
        IntentGroup(("nearby.search",)),
    )))
    judgement = judge_turn(expected, _snapshot("info.forecast"))
    assert not judgement.passed
    assert judgement.metric("required_group_recall") == 0.5


def test_forbidden_intent_fails_even_when_required_is_present():
    expected = TurnExpectation(plan=PlanExpectation(
        assert_plan=True,
        required_groups=(IntentGroup(("charging.find",)),),
        forbidden_intents=("nearby.search",),
    ))
    judgement = judge_turn(expected, _snapshot("charging.find", "nearby.search"))
    assert not judgement.passed
    assert judgement.metric("forbidden_route_count") == 1


def test_unapproved_extra_intent_fails():
    expected = TurnExpectation(plan=PlanExpectation(
        assert_plan=True,
        required_groups=(IntentGroup(("info.weather",)),),
        allow_extra_intents=False,
    ))
    judgement = judge_turn(expected, _snapshot("info.weather", "info.news"))
    assert not judgement.passed
    assert judgement.metric("overroute_count") == 1


def test_dependency_and_carried_slot_are_both_required():
    expected = TurnExpectation(plan=PlanExpectation(
        assert_plan=True,
        required_groups=(IntentGroup(("nearby.search",)), IntentGroup(("nearby.order",))),
        dependencies=(DependencyExpectation(("nearby.search",), "nearby.order",
                                            ("name",)),),
    ))
    plan = PlanSnapshot(
        steps=(
            _step("s1", "nearby.search"),
            _step("s2", "nearby.order", depends_on=("s1",),
                  slot_refs={"name": "s1.data.items.0.name"}),
        ), complexity="simple", goal="", skills=(), exemplars=(),
        hint_effect="", catalog_stats={})
    result = judge_turn(expected, DecisionSnapshot(
        ingress="cloud", addressed=True, decision="execute", clarify=False, plan=plan))
    assert result.passed
    assert result.metric("dependency_pass") == 1.0


def test_one_valid_duplicate_cannot_hide_an_unlinked_consumer():
    expected = TurnExpectation(plan=PlanExpectation(
        assert_plan=True,
        required_groups=(IntentGroup(("nearby.search",)),
                         IntentGroup(("nearby.order",))),
        dependencies=(DependencyExpectation(
            ("nearby.search",), "nearby.order", ("name",)),),
    ))
    plan = PlanSnapshot(
        steps=(
            _step("s1", "nearby.search"),
            _step("s2", "nearby.order", depends_on=("s1",),
                  slot_refs={"name": "s1.data.items.0.name"}),
            _step("s3", "nearby.order"),
        ), complexity="simple", goal="", skills=(), exemplars=(),
        hint_effect="", catalog_stats={})
    actual = DecisionSnapshot("cloud", True, "execute", False, plan)
    assert not judge_turn(expected, actual).passed


def test_slot_matchers_cover_exact_one_of_range_presence_and_source_reference():
    expected = TurnExpectation(plan=PlanExpectation(
        assert_plan=True,
        required_groups=(IntentGroup(("nearby.search",)),),
        slots=(
            SlotExpectation("nearby.search", "category", "exact", value="室内"),
            SlotExpectation("nearby.search", "sort", "one_of", allowed=("rating", "distance")),
            SlotExpectation("nearby.search", "radius", "range", minimum=500, maximum=5000),
            SlotExpectation("nearby.search", "weather_context", "presence"),
            SlotExpectation("nearby.search", "location", "source_reference",
                            source="s_weather.data.city"),
        ),
    ))
    actual = DecisionSnapshot(
        ingress="cloud", addressed=True, decision="execute", clarify=False,
        plan=PlanSnapshot(steps=(_step(
            "s2", "nearby.search",
            slots={"category": "室内", "sort": "rating", "radius": 3000,
                   "weather_context": "雨"},
            slot_refs={"location": "s_weather.data.city"}),),
            complexity="simple", goal="", skills=(), exemplars=(),
            hint_effect="", catalog_stats={}),
    )
    assert judge_turn(expected, actual).passed


def test_decision_and_ingress_are_independent_from_plan():
    expected = TurnExpectation(
        addressed=True,
        ingress_allowed=("cloud",),
        ingress_forbidden=("edge_local",),
        decision_allowed=("clarify",),
        clarify="required",
    )
    actual = DecisionSnapshot(ingress="edge_local", addressed=True,
                              decision="execute", clarify=False,
                              plan=PlanSnapshot.empty())
    result = judge_turn(expected, actual)
    assert not result.passed
    assert {a.name for a in result.assertions if not a.passed} >= {
        "ingress_allowed", "decision_allowed", "clarify",
    }


def test_replan_groups_contribute_to_aggregate_recall():
    expected = TurnExpectation(
        plan=PlanExpectation(
            assert_plan=True,
            required_groups=(IntentGroup(("info.weather",)),)),
        replans=(ReplanExpectation(
            after={"result": {"step_id": "s1", "status": "ok", "data": {}}},
            plan=PlanExpectation(
                assert_plan=True,
                required_groups=(IntentGroup(("nearby.search",)),))),))
    actual = _snapshot("info.weather")
    actual = DecisionSnapshot(
        ingress=actual.ingress, addressed=actual.addressed,
        decision=actual.decision, clarify=actual.clarify, plan=actual.plan,
        replans=(_snapshot("chitchat.talk").plan,))
    assert judge_turn(expected, actual).metric("required_group_recall") == 0.5
```

- [ ] **Step 2: 运行测试，确认 judge 模块不存在**

Run: `python -m pytest test/test_intent_adversarial_judge.py -q`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: 实现快照、断言和裁判**

```python
# test/support/intent_adversarial_judge.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from support.intent_adversarial_contract import PlanExpectation, TurnExpectation


@dataclass(frozen=True)
class StepSnapshot:
    id: str
    agent_id: str
    intent: str
    slots: dict[str, Any]
    depends_on: tuple[str, ...]
    slot_refs: dict[str, str]
    require_confirm: bool


@dataclass(frozen=True)
class PlanSnapshot:
    steps: tuple[StepSnapshot, ...]
    complexity: str
    goal: str
    skills: tuple[str, ...]
    exemplars: tuple[str, ...]
    hint_effect: str
    catalog_stats: dict[str, Any]
    raw_llm: str = ""
    plan_mode: str = ""

    @classmethod
    def empty(cls) -> "PlanSnapshot":
        return cls((), "", "", (), (), "", {})

    @property
    def intents(self) -> tuple[str, ...]:
        return tuple(step.intent for step in self.steps)


@dataclass(frozen=True)
class DecisionSnapshot:
    ingress: str
    addressed: bool
    decision: str
    clarify: bool
    plan: PlanSnapshot
    replans: tuple[PlanSnapshot, ...] = ()
    side_effects: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AssertionResult:
    name: str
    passed: bool
    expected: Any
    actual: Any
    detail: str = ""


@dataclass
class TurnJudgement:
    assertions: list[AssertionResult] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.assertions)

    def metric(self, name: str) -> float:
        return self.metrics[name]


def _assert(out: TurnJudgement, name: str, passed: bool,
            expected: Any, actual: Any, detail: str = "") -> None:
    out.assertions.append(AssertionResult(name, passed, expected, actual, detail))


def judge_plan(expected: PlanExpectation, actual: PlanSnapshot,
               out: TurnJudgement, prefix: str = "plan") -> None:
    intents = set(actual.intents)
    hits = [bool(intents & set(group.any_of)) for group in expected.required_groups]
    recall = sum(hits) / len(hits) if hits else 1.0
    out.metrics[f"{prefix}.required_group_recall"] = recall
    out.metrics[f"{prefix}.required_group_hits"] = float(sum(hits))
    out.metrics[f"{prefix}.required_group_total"] = float(len(hits))
    _assert(out, f"{prefix}.required_groups", all(hits),
            [group.any_of for group in expected.required_groups], sorted(intents))

    forbidden = sorted(intents & set(expected.forbidden_intents))
    out.metrics[f"{prefix}.forbidden_route_count"] = float(len(forbidden))
    _assert(out, f"{prefix}.forbidden_intents", not forbidden,
            expected.forbidden_intents, forbidden)

    allowed = {i for group in expected.required_groups for i in group.any_of}
    allowed.update(expected.allowed_extra_intents)
    extras = [] if expected.allow_extra_intents else sorted(intents - allowed)
    out.metrics[f"{prefix}.overroute_count"] = float(len(extras))
    _assert(out, f"{prefix}.extra_intents", not extras,
            "any" if expected.allow_extra_intents else sorted(allowed), extras)

    if expected.allowed_complexities:
        _assert(out, f"{prefix}.complexity",
                actual.complexity in expected.allowed_complexities,
                expected.allowed_complexities, actual.complexity)

    by_intent: dict[str, list[StepSnapshot]] = {}
    for step in actual.steps:
        by_intent.setdefault(step.intent, []).append(step)
    dependency_results: list[bool] = []
    for dep in expected.dependencies:
        producer_ids = {s.id for intent in dep.producer for s in by_intent.get(intent, [])}
        consumers = by_intent.get(dep.consumer, [])
        ok = bool(consumers) and all(
            bool(producer_ids & set(step.depends_on))
            and all(
                key in step.slot_refs
                and str(step.slot_refs[key]).split(".", 1)[0] in producer_ids
                for key in dep.carries)
            for step in consumers)
        dependency_results.append(ok)
        _assert(out, f"{prefix}.dependency:{dep.consumer}", ok, dep,
                [(s.id, s.depends_on, s.slots, s.slot_refs) for s in consumers])
    out.metrics[f"{prefix}.dependency_hits"] = float(sum(dependency_results))
    out.metrics[f"{prefix}.dependency_total"] = float(len(dependency_results))

    for slot in expected.slots:
        steps = by_intent.get(slot.intent, [])
        rows = [
            (slot.key in step.slots, step.slots.get(slot.key),
             slot.key in step.slot_refs, step.slot_refs.get(slot.key))
            for step in steps]
        values = [value for has_value, value, _, _ in rows if has_value]
        refs = [ref for _, _, has_ref, ref in rows if has_ref]
        if slot.matcher == "presence":
            ok = bool(rows) and all(has_value or has_ref
                                    for has_value, _, has_ref, _ in rows)
        elif slot.matcher == "absence":
            ok = bool(rows) and all(not has_value and not has_ref
                                    for has_value, _, has_ref, _ in rows)
        elif slot.matcher == "exact":
            ok = bool(rows) and all(has_value and value == slot.value
                                    for has_value, value, _, _ in rows)
        elif slot.matcher == "one_of":
            ok = bool(rows) and all(has_value and str(value) in slot.allowed
                                    for has_value, value, _, _ in rows)
        elif slot.matcher == "range":
            try:
                numbers = [float(value) for has_value, value, _, _ in rows
                           if has_value]
                ok = bool(rows) and len(numbers) == len(rows) and all(
                    (slot.minimum is None or value >= slot.minimum)
                    and (slot.maximum is None or value <= slot.maximum)
                    for value in numbers)
            except (TypeError, ValueError):
                ok = False
        elif slot.matcher == "source_reference":
            ok = bool(rows) and all(has_ref and ref == slot.source
                                    for _, _, has_ref, ref in rows)
        else:
            ok = False
        _assert(out, f"{prefix}.slot:{slot.intent}.{slot.key}", ok,
                slot, {"values": values, "refs": refs})


def judge_turn(expected: TurnExpectation, actual: DecisionSnapshot) -> TurnJudgement:
    out = TurnJudgement()
    if expected.addressed is not None:
        _assert(out, "addressed", actual.addressed == expected.addressed,
                expected.addressed, actual.addressed)
    if expected.ingress_allowed:
        _assert(out, "ingress_allowed", actual.ingress in expected.ingress_allowed,
                expected.ingress_allowed, actual.ingress)
    if expected.ingress_forbidden:
        _assert(out, "ingress_forbidden", actual.ingress not in expected.ingress_forbidden,
                expected.ingress_forbidden, actual.ingress)
    if expected.decision_allowed:
        _assert(out, "decision_allowed", actual.decision in expected.decision_allowed,
                expected.decision_allowed, actual.decision)
    if expected.clarify != "allowed":
        wanted = expected.clarify == "required"
        _assert(out, "clarify", actual.clarify is wanted, wanted, actual.clarify)
    if expected.plan.assert_plan:
        judge_plan(expected.plan, actual.plan, out)
    _assert(out, "replan_count", len(actual.replans) == len(expected.replans),
            len(expected.replans), len(actual.replans))
    for index, replan_expected in enumerate(expected.replans):
        if index < len(actual.replans):
            judge_plan(replan_expected.plan, actual.replans[index], out,
                       f"replan[{index}]")
    if expected.no_side_effect_before_confirm:
        _assert(out, "no_side_effect_before_confirm", not actual.side_effects,
                [], actual.side_effects)
    group_hits = sum(value for key, value in out.metrics.items()
                     if key.endswith(".required_group_hits"))
    group_total = sum(value for key, value in out.metrics.items()
                      if key.endswith(".required_group_total"))
    dep_hits = sum(value for key, value in out.metrics.items()
                   if key.endswith(".dependency_hits"))
    dep_total = sum(value for key, value in out.metrics.items()
                    if key.endswith(".dependency_total"))
    out.metrics["required_group_recall"] = (
        group_hits / group_total if group_total else 1.0)
    out.metrics["forbidden_route_count"] = sum(
        value for key, value in out.metrics.items()
        if key.endswith(".forbidden_route_count"))
    out.metrics["overroute_count"] = sum(
        value for key, value in out.metrics.items()
        if key.endswith(".overroute_count"))
    out.metrics["dependency_pass"] = (
        dep_hits / dep_total if dep_total else 1.0)
    return out
```

- [ ] **Step 4: 运行裁判测试**

Run: `python -m pytest test/test_intent_adversarial_judge.py -q`

Expected: all tests PASS.

- [ ] **Step 5: 提交精确裁判**

```powershell
git add -- test/support/intent_adversarial_judge.py test/test_intent_adversarial_judge.py
git commit -m "test: judge exact intent plans"
```

### Task 4: 实现最小对照 relation 裁判

**Files:**
- Modify: `test/support/intent_adversarial_judge.py`
- Modify: `test/test_intent_adversarial_judge.py`

- [ ] **Step 1: 写七类 relation 测试**

```python
# append to test/test_intent_adversarial_judge.py
from support.intent_adversarial_contract import RelationSpec
from support.intent_adversarial_judge import judge_relation, semantic_signature


def test_invariant_requires_same_semantic_signature():
    base = _snapshot("info.weather")
    variant = _snapshot("info.weather")
    assert judge_relation(RelationSpec("base", "invariant", {}), base, variant).passed


def test_route_flip_requires_changed_signature_and_declared_forbidden_after():
    base = _snapshot("charging.find")
    variant = _snapshot("chitchat.talk")
    spec = RelationSpec("base", "route_flip", {"forbidden_after": ["charging.find"],
                                                  "required_change": True})
    assert judge_relation(spec, base, variant).passed


def test_intent_add_requires_set_delta():
    spec = RelationSpec("base", "intent_add", {"add": ["info.weather"]})
    assert judge_relation(spec, _snapshot("reminder.create"),
                          _snapshot("reminder.create", "info.weather")).passed


def test_intent_remove_requires_set_delta():
    spec = RelationSpec("base", "intent_remove", {"remove": ["info.weather"]})
    assert judge_relation(spec, _snapshot("reminder.create", "info.weather"),
                          _snapshot("reminder.create")).passed


def test_clarify_flip_requires_decision_change():
    base = DecisionSnapshot("cloud", True, "clarify", True, PlanSnapshot.empty())
    variant = _snapshot("navigation.navigate_to")
    assert judge_relation(RelationSpec("base", "clarify_flip", {}), base, variant).passed


def test_context_override_uses_variant_absolute_result():
    spec = RelationSpec("base", "context_override", {"must_differ": True})
    assert judge_relation(spec, _snapshot("info.weather"),
                          _snapshot("nearby.search")).passed


def test_clause_commute_ignores_step_order_but_not_intent_set():
    spec = RelationSpec("base", "clause_commute", {})
    assert judge_relation(spec, _snapshot("info.weather", "reminder.create"),
                          _snapshot("reminder.create", "info.weather")).passed
```

- [ ] **Step 2: 运行测试，确认缺少 relation API**

Run: `python -m pytest test/test_intent_adversarial_judge.py -q`

Expected: collection FAIL because `judge_relation` is not defined.

- [ ] **Step 3: 实现语义签名和 relation 判定**

```python
# append to test/support/intent_adversarial_judge.py
import json


def _canonical(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _plan_semantic_signature(plan: PlanSnapshot) -> tuple:
    by_id = {step.id: step.intent for step in plan.steps}
    step_signatures = []
    dependencies = []
    for step in plan.steps:
        normalized_refs = []
        for key, ref in step.slot_refs.items():
            source_id, _, path = str(ref).partition(".")
            normalized_refs.append((key, by_id.get(source_id, source_id), path))
        step_signatures.append((
            step.intent,
            tuple(sorted((key, _canonical(value))
                         for key, value in step.slots.items())),
            tuple(sorted(normalized_refs)),
            step.require_confirm,
        ))
        dependencies.extend(
            (by_id.get(source_id, source_id), step.intent)
            for source_id in step.depends_on
        )
    return (
        plan.complexity,
        tuple(sorted(step_signatures)),
        tuple(sorted(dependencies)),
    )


def semantic_signature(snapshot: DecisionSnapshot) -> tuple:
    return (
        snapshot.ingress,
        snapshot.addressed,
        snapshot.decision,
        snapshot.clarify,
        _plan_semantic_signature(snapshot.plan),
        tuple(_plan_semantic_signature(plan) for plan in snapshot.replans),
    )


def judge_relation(spec, base: DecisionSnapshot,
                   variant: DecisionSnapshot) -> TurnJudgement:
    out = TurnJudgement()
    base_intents = set(base.plan.intents)
    variant_intents = set(variant.plan.intents)
    expected = spec.expectation or {}
    if spec.type == "invariant":
        _assert(out, "relation.invariant",
                semantic_signature(base) == semantic_signature(variant),
                semantic_signature(base), semantic_signature(variant))
    elif spec.type == "route_flip":
        changed = semantic_signature(base) != semantic_signature(variant)
        forbidden = set(expected.get("forbidden_after") or []) & variant_intents
        _assert(out, "relation.route_flip",
                (changed or not expected.get("required_change", True)) and not forbidden,
                expected, {"changed": changed, "forbidden": sorted(forbidden)})
    elif spec.type == "intent_add":
        wanted = set(expected.get("add") or [])
        _assert(out, "relation.intent_add",
                wanted <= (variant_intents - base_intents), wanted,
                variant_intents - base_intents)
    elif spec.type == "intent_remove":
        wanted = set(expected.get("remove") or [])
        _assert(out, "relation.intent_remove",
                wanted <= (base_intents - variant_intents), wanted,
                base_intents - variant_intents)
    elif spec.type == "clarify_flip":
        _assert(out, "relation.clarify_flip",
                base.clarify and not variant.clarify,
                (True, False), (base.clarify, variant.clarify))
    elif spec.type == "context_override":
        changed = semantic_signature(base) != semantic_signature(variant)
        _assert(out, "relation.context_override",
                changed if expected.get("must_differ", True) else True,
                expected, changed)
    elif spec.type == "clause_commute":
        _assert(out, "relation.clause_commute",
                semantic_signature(base) == semantic_signature(variant),
                semantic_signature(base), semantic_signature(variant))
    else:
        _assert(out, "relation.unknown", False, spec.type, "unsupported")
    out.metrics["relation_pass"] = 1.0 if out.passed else 0.0
    return out
```

runner 只在 base 与 variant 的 absolute judgement 都已计算后再调用 `judge_relation()`；报告分别保留 `absolute_pass` 与 `relation_pass`，总通过要求二者都真，不能让“两个结果同样错”的 invariant 变绿。

- [ ] **Step 4: 运行裁判全测**

Run: `python -m pytest test/test_intent_adversarial_judge.py -q`

Expected: all tests PASS.

- [ ] **Step 5: 提交 relation 裁判**

```powershell
git add -- test/support/intent_adversarial_judge.py test/test_intent_adversarial_judge.py
git commit -m "test: add metamorphic routing judgements"
```

### Task 5: 记录 Hint 前后计划、资产指纹和首偏离点

**Files:**
- Create: `test/support/intent_adversarial_trace.py`
- Create: `test/test_intent_adversarial_trace.py`

- [ ] **Step 1: 写 trace、指纹和首偏离点失败测试**

```python
# test/test_intent_adversarial_trace.py
from pathlib import Path
from types import SimpleNamespace

from orchestrator.cloud.models import Plan, Step
from orchestrator.cloud.route_hints import RouteHintEngine
from support.intent_adversarial_trace import (
    DivergenceEvidence, TraceSink, TracingRouteHints, asset_digest,
    attach_validation_trace, first_divergence,
)


def _validate(rows, _agent_map):
    return [Step(id=row["id"], agent_id=row["agent_id"], endpoint="a:1",
                 intent=row["intent"], slots=row["slots"],
                 depends_on=row["depends_on"], slot_refs=row["slot_refs"])
            for row in rows]


def test_tracing_route_hints_keeps_before_and_after_plan():
    hint = SimpleNamespace(intent="charging.find", pattern="充电", guard="手机",
                           policy="replace", priority=90, slots={})
    agent = SimpleNamespace(manifest=SimpleNamespace(route_hints=[hint]))
    plan = Plan(steps=[Step(id="s1", agent_id="chitchat", endpoint="c:1",
                           intent="chitchat.talk")])
    sink = TraceSink()
    wrapped = TracingRouteHints(RouteHintEngine(_validate), sink)

    assert wrapped.apply(plan, "给车找个充电站", {"charging": agent}) is True

    trace = sink.hints[-1]
    assert trace.matches[0].intent == "charging.find"
    assert trace.before.intents == ("chitchat.talk",)
    assert trace.after.intents == ("charging.find",)


def test_asset_digest_is_order_independent_and_content_sensitive(tmp_path: Path):
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("a: 1\n", encoding="utf-8")
    b.write_text("b: 2\n", encoding="utf-8")
    assert asset_digest(tmp_path, [b, a]) == asset_digest(tmp_path, [a, b])
    old = asset_digest(tmp_path, [a, b])
    b.write_text("b: 3\n", encoding="utf-8")
    assert asset_digest(tmp_path, [a, b]) != old


def test_validation_trace_keeps_raw_and_accepted_intents():
    class Builder:
        def _parse_and_validate_data(self, data, _agent_map, _text):
            return Plan(steps=[Step(id="s1", agent_id="info", endpoint="i:1",
                                    intent="info.weather")])
    builder, sink = Builder(), TraceSink()
    attach_validation_trace(builder, sink)
    builder._parse_and_validate_data(
        {"steps": [{"intent": "info.weather"}]}, {}, "查天气")
    assert sink.validations[-1].raw_intents == ("info.weather",)
    assert sink.validations[-1].raw_candidate.intents == ("info.weather",)
    assert sink.validations[-1].accepted.intents == ("info.weather",)


def test_first_divergence_respects_execution_order():
    assert first_divergence(DivergenceEvidence(
        full_entry_pass=False, engine_direct_pass=True)) == "EDGE_DIVERGENCE"
    assert first_divergence(DivergenceEvidence(
        full_entry_pass=False, engine_direct_pass=False,
        planner_post_hint_pass=True)) == "STATE_RESTORE_DIVERGENCE"
    assert first_divergence(DivergenceEvidence(
        full_entry_pass=False, engine_direct_pass=False,
        planner_post_hint_pass=False, empty_history_pass=True)) == "CONTEXT_DIVERGENCE"
    assert first_divergence(DivergenceEvidence(
        full_entry_pass=False, engine_direct_pass=False,
        planner_post_hint_pass=False, retrieval_ablation_pass=True)) == "RETRIEVAL_SUSPECT"
    assert first_divergence(DivergenceEvidence(
        full_entry_pass=False, engine_direct_pass=False,
        planner_post_hint_pass=False, raw_planner_pass=True,
        pre_hint_pass=False)) == "VALIDATION_DIVERGENCE"
    assert first_divergence(DivergenceEvidence(
        full_entry_pass=False, engine_direct_pass=False,
        planner_post_hint_pass=False, raw_planner_pass=False,
        pre_hint_pass=True)) == "HINT_DIVERGENCE"
```

- [ ] **Step 2: 运行测试，确认 trace 模块不存在**

Run: `python -m pytest test/test_intent_adversarial_trace.py -q`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: 实现只读快照、Hint 包装器和 planner 记录器**

```python
# test/support/intent_adversarial_trace.py
from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from support.intent_adversarial_judge import PlanSnapshot, StepSnapshot


def snapshot_plan(plan) -> PlanSnapshot:
    return PlanSnapshot(
        steps=tuple(StepSnapshot(
            id=str(step.id), agent_id=str(step.agent_id), intent=str(step.intent),
            slots=dict(step.slots or {}), depends_on=tuple(step.depends_on or []),
            slot_refs=dict(step.slot_refs or {}),
            require_confirm=bool(getattr(step, "require_confirm", False)),
        ) for step in (getattr(plan, "steps", None) or [])),
        complexity=str(getattr(plan, "complexity", "") or ""),
        goal=str(getattr(plan, "goal", "") or ""),
        skills=tuple(getattr(plan, "skills", None) or []),
        exemplars=tuple(getattr(plan, "exemplars", None) or []),
        hint_effect=str(getattr(plan, "hint_effect", "") or ""),
        catalog_stats=dict(getattr(plan, "catalog_stats", None) or {}),
        raw_llm=str(getattr(plan, "raw_llm", "") or ""),
        plan_mode=str(getattr(plan, "plan_mode", "") or ""),
    )


@dataclass(frozen=True)
class HintMatch:
    agent_id: str
    intent: str
    policy: str
    priority: int


@dataclass(frozen=True)
class HintTrace:
    text: str
    matches: tuple[HintMatch, ...]
    before: PlanSnapshot
    after: PlanSnapshot
    hit: bool


@dataclass(frozen=True)
class PlannerTrace:
    stage: str
    plan: PlanSnapshot
    done: bool = False


@dataclass(frozen=True)
class ValidationTrace:
    raw_intents: tuple[str, ...]
    raw_candidate: PlanSnapshot
    admitted_intents: tuple[str, ...]
    accepted: PlanSnapshot
    result: str


@dataclass
class TraceSink:
    hints: list[HintTrace] = field(default_factory=list)
    plans: list[PlannerTrace] = field(default_factory=list)
    validations: list[ValidationTrace] = field(default_factory=list)


class TracingRouteHints:
    def __init__(self, delegate, sink: TraceSink):
        self.delegate = delegate
        self.sink = sink

    def apply(self, plan, text: str, agent_map: dict) -> bool:
        matches: list[HintMatch] = []
        for agent_id, hint in self.delegate._ordered_hints(agent_map):
            if self.delegate._match(hint, text) is None:
                continue
            policy = (hint.policy or "replace").lower()
            matches.append(HintMatch(agent_id, str(hint.intent), policy,
                                     int(hint.priority or 0)))
            if policy != "append":
                break
        before = snapshot_plan(plan)
        hit = self.delegate.apply(plan, text, agent_map)
        self.sink.hints.append(HintTrace(
            text=text, matches=tuple(matches), before=before,
            after=snapshot_plan(plan), hit=hit))
        return hit


class RecordingPlanner:
    def __init__(self, delegate, sink: TraceSink):
        self.delegate = delegate
        self.sink = sink

    async def build(self, *args, **kwargs):
        plan = await self.delegate.build(*args, **kwargs)
        self.sink.plans.append(PlannerTrace("build", snapshot_plan(plan)))
        return plan

    async def replan(self, goal, *args, **kwargs):
        decision = await self.delegate.replan(goal, *args, **kwargs)
        self.sink.plans.append(PlannerTrace(
            "replan", snapshot_plan(decision.to_plan(goal)), done=decision.done))
        return decision


def snapshot_raw_candidate(data: dict[str, Any]) -> PlanSnapshot:
    """把已解析但尚未 capability validation 的结构转成可裁判快照。"""
    rows = data.get("steps") if isinstance(data, dict) else []
    steps = []
    for index, row in enumerate(rows or [], 1):
        if not isinstance(row, dict):
            continue
        steps.append(StepSnapshot(
            id=str(row.get("id") or f"raw-{index}"),
            agent_id=str(row.get("agent_id") or ""),
            intent=str(row.get("intent") or ""),
            slots=dict(row.get("slots") or {}),
            depends_on=tuple(row.get("depends_on") or []),
            slot_refs=dict(row.get("slot_refs") or {}),
            require_confirm=bool(row.get("require_confirm", False)),
        ))
    return PlanSnapshot(
        steps=tuple(steps), complexity=str(data.get("complexity") or "simple"),
        goal=str(data.get("goal") or ""), skills=(), exemplars=(),
        hint_effect="", catalog_stats={})


def attach_validation_trace(builder, sink: TraceSink) -> None:
    original = builder._parse_and_validate_data

    def traced(data, agent_map, text):
        raw = deepcopy(data) if isinstance(data, dict) else {}
        raw_intents = tuple(str(step.get("intent") or "")
                            for step in raw.get("steps") or []
                            if isinstance(step, dict) and step.get("intent"))
        admitted = tuple(sorted(
            str(cap.intent)
            for agent in agent_map.values()
            for cap in (getattr(agent.manifest, "capabilities", None) or [])))
        plan = original(data, agent_map, text)
        sink.validations.append(ValidationTrace(
            raw_intents=raw_intents, admitted_intents=admitted,
            raw_candidate=snapshot_raw_candidate(raw),
            accepted=snapshot_plan(plan) if plan is not None else PlanSnapshot.empty(),
            result="accepted" if plan is not None else "rejected"))
        return plan

    builder._parse_and_validate_data = traced


def asset_digest(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    root = Path(root).resolve()
    for path in sorted({Path(p).resolve() for p in paths},
                       key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class DivergenceEvidence:
    full_entry_pass: bool = False
    engine_direct_pass: bool = False
    planner_post_hint_pass: bool = False
    empty_history_pass: bool = False
    retrieval_ablation_pass: bool = False
    raw_planner_pass: bool = False
    pre_hint_pass: bool = False


def first_divergence(evidence: DivergenceEvidence) -> str:
    if evidence.full_entry_pass:
        return "NONE"
    if evidence.engine_direct_pass:
        return "EDGE_DIVERGENCE"
    if evidence.planner_post_hint_pass:
        return "STATE_RESTORE_DIVERGENCE"
    if evidence.empty_history_pass:
        return "CONTEXT_DIVERGENCE"
    if evidence.retrieval_ablation_pass:
        return "RETRIEVAL_SUSPECT"
    if evidence.pre_hint_pass:
        return "HINT_DIVERGENCE"
    if evidence.raw_planner_pass:
        return "VALIDATION_DIVERGENCE"
    return "PLANNER_DIVERGENCE"
```

`RecordingPlanner` 只包裹实例，不修改 `Plan` 或生产 span schema；`TracingRouteHints` 必须先替换 `builder._route_hints`，再把 builder 包进 `RecordingPlanner`，这样 before/after 两份证据都能保留。
首偏离判断对 `raw_candidate` 和 `accepted` 使用同一 `judge_plan()`；只比较 `raw_intents` 不足以证明依赖、槽位或 extra 在校验前是正确的。报告可以保存规范化计划快照，但不得保存完整 prompt 或未脱敏的模型自然语言原文。

- [ ] **Step 4: 把指纹输入固定为真实决策资产**

运行器调用 `asset_digest()` 时只纳入以下已存在文件，按相对路径排序：`test/eval_corpus/intent_adversarial/**/*.yaml`、`agents/*/manifest.yaml`、`agents/*/servers.yaml`、`skills/guides/*.yaml`、`skills/exemplars/*.yaml`、`orchestrator/edge/knowledge/commands.yaml`、`orchestrator/edge/fast_intent.py`。代码版本另由 git commit 记录。glob 一个文件都未命中或必选路径不存在时记录在 `missing_assets`，不得静默跳过并仍称“指纹完整”。

- [ ] **Step 5: 运行 trace 测试并提交**

Run: `python -m pytest test/test_intent_adversarial_trace.py -q`

Expected: all tests PASS.

```powershell
git add -- test/support/intent_adversarial_trace.py test/test_intent_adversarial_trace.py
git commit -m "test: trace adversarial routing decisions"
```

### Task 6: 建立 L0 Edge 与 Route Hint 离线运行门面

**Files:**
- Create: `test/support/intent_adversarial_runtime.py`
- Create: `test/test_intent_adversarial_runtime.py`

- [ ] **Step 1: 写 Edge ingress 与危险动作零副作用测试**

```python
# test/test_intent_adversarial_runtime.py
from support.intent_adversarial_runtime import run_edge_turn


def test_edge_local_and_cloud_ingress_are_observable():
    local = run_edge_turn("打开空调")
    cloud = run_edge_turn("帮我查一下今天的天气")
    assert local.ingress == "edge_local"
    assert local.state_delta.get("hvac_on") is True
    assert cloud.ingress == "cloud"
    assert cloud.cloud_text == "帮我查一下今天的天气"


def test_dangerous_edge_match_never_executes_before_confirm():
    out = run_edge_turn("打开后备箱", cloud_need_confirm=True)
    assert out.ingress == "cloud"
    assert out.need_confirm is True
    assert "trunk" not in out.state_delta
    assert out.side_effects == ()
```

- [ ] **Step 2: 运行测试，确认缺少 `run_edge_turn`**

Run: `python -m pytest test/test_intent_adversarial_runtime.py -q`

Expected: collection FAIL because `run_edge_turn` is not defined.

- [ ] **Step 3: 用真实 Edge servicer 和内存 VAL 实现安全门面**

```python
# core of test/support/intent_adversarial_runtime.py
@dataclass(frozen=True)
class EdgeObservation:
    ingress: str
    cloud_text: str
    state_delta: dict[str, Any]
    actions: tuple[dict[str, Any], ...]
    need_confirm: bool
    side_effects: tuple[dict[str, Any], ...]


def run_edge_turn(text: str, *, cloud_need_confirm: bool = False) -> EdgeObservation:
    from cockpit.orchestrator.v1 import orchestrator_pb2
    from server import EdgeOrchestratorServicer

    srv = EdgeOrchestratorServicer()
    before = dict(srv.val.state)
    seen: dict[str, str] = {}

    async def fake_cloud_handle(req):
        seen["text"] = req.text
        final = orchestrator_pb2.FinalResult(
            speech="cloud", need_confirm=cloud_need_confirm)
        yield orchestrator_pb2.HandleEvent(final=final)

    srv.cloud.handle = fake_cloud_handle
    srv.obs.emit_turn = _async_noop
    srv.obs.emit_span = _async_noop
    srv.memory.append = _async_noop
    srv._nlu_shadow_bg = lambda *_args, **_kwargs: None
    request = orchestrator_pb2.HandleRequest(
        text=text, session_id="intent-adversarial", request_id="r1",
        meta={"memory_enabled": "false"})
    events = asyncio.run(_collect(srv.Handle(request, None)))
    finals = [event.final for event in events if event.WhichOneof("event") == "final"]
    state_delta = {key: value for key, value in srv.val.state.items()
                   if before.get(key) != value}
    actions = tuple(_action_dict(action) for final in finals for action in final.actions)
    ingress = "mixed" if seen and state_delta else (
        "cloud" if seen else "edge_local")
    return EdgeObservation(
        ingress=ingress, cloud_text=seen.get("text", ""), state_delta=state_delta,
        actions=actions, need_confirm=any(final.need_confirm for final in finals),
        side_effects=tuple(action for action in actions
                           if action["type"] in {"vehicle.control", "media.control"}),
    )
```

同文件实现 `_collect`、`_async_noop` 和 protobuf action→dict 转换；不得 mock `classify()`、`split_and_classify_any()` 或 VAL，否则 L0 失去意义。导入 `server` 前沿用 `eval_live.py` 的 `sys.path` 根、生成代码和 `orchestrator/edge` 三路径装配。

- [ ] **Step 4: 增加实际 RouteHintEngine 单独运行函数**

`run_hint_turn(text, initial_plan, agents)` 必须使用真实 `RouteHintEngine`、`TracingRouteHints` 和 `PlanBuilder._validated_steps`，返回 Hint 前后快照；用 `test/eval_corpus/route_hints_cases.yaml` 抽三类回归：replace、append、guard。不要调用 LLM。

- [ ] **Step 5: 跑 L0 与既有 Edge 回归**

Run: `python -m pytest test/test_intent_adversarial_runtime.py orchestrator/edge/tests/test_server_dispatch.py orchestrator/cloud/tests/test_route_hints.py -q`

Expected: all tests PASS，且没有后台网络异常或未回收 task warning。

- [ ] **Step 6: 提交 L0 runtime**

```powershell
git add -- test/support/intent_adversarial_runtime.py test/test_intent_adversarial_runtime.py
git commit -m "test: add offline adversarial routing runtime"
```

### Task 7: 接入 Skill/Exemplar 检索、active intent inventory 和 boundary 双向门禁

**Files:**
- Modify: `test/eval_live.py`
- Modify: `test/support/intent_adversarial_contract.py`
- Modify: `test/support/intent_adversarial_judge.py`
- Modify: `test/support/intent_adversarial_runtime.py`
- Modify: `test/test_intent_adversarial_contract.py`
- Modify: `test/test_intent_adversarial_judge.py`
- Modify: `test/test_intent_adversarial_runtime.py`
- Create: `test/eval_corpus/intent_adversarial/coverage_exemptions.yaml`
- Modify: `skills/exemplars/boundaries.yaml`

- [ ] **Step 1: 写 coverage、boundary 和 retrieval 失败测试**

```python
from dataclasses import replace

import eval_live

from support.intent_adversarial_contract import (
    RetrievalExpectation, validate_retrieval_references,
)


def test_active_intent_must_be_covered_or_exempt(contract_case):
    errors = validate_coverage([contract_case],
                               active_intents={"info.alerts", "new.intent"},
                               exemptions={})
    assert any("new.intent positive has 0, need 2" in error for error in errors)


def test_any_of_group_does_not_fake_per_intent_positive_coverage(contract_case):
    plan = replace(contract_case.turns[0].expected.plan,
                   required_groups=(IntentGroup(("info.alerts", "info.weather")),))
    turn = replace(contract_case.turns[0], expected=replace(
        contract_case.turns[0].expected, plan=plan))
    errors = validate_coverage(
        [replace(contract_case, turns=(turn,))],
        active_intents={"info.alerts", "info.weather"}, exemptions={})
    assert any("info.alerts positive has 0" in error for error in errors)


def test_active_inventory_includes_admitted_mcp_capabilities():
    assert "shop.order" in eval_live.known_intents()


def test_boundary_requires_two_cases_per_side(contract_case):
    errors = validate_boundary_coverage(
        [contract_case],
        boundaries={"info-safety.weather-alert": ("info", "safety")},
        minimum_per_side=2)
    assert any("info-safety.weather-alert" in error and "left" in error
               for error in errors)


def test_retrieval_expectation_checks_required_and_forbidden_assets():
    expected = TurnExpectation(retrieval=RetrievalExpectation(
        required_skills=("weather-outing",), forbidden_exemplars=("nearby#bad",)))
    actual = _snapshot("nearby.search")
    actual = replace(actual, plan=replace(
        actual.plan, skills=("full:weather-outing@lex:1.0",), exemplars=()))
    assert judge_turn(expected, actual).passed


def test_unknown_retrieval_reference_is_a_contract_error(contract_case):
    expected = replace(
        contract_case.turns[0].expected,
        retrieval=RetrievalExpectation(required_skills=("typo-guide",)))
    case = replace(contract_case, turns=(replace(
        contract_case.turns[0], expected=expected),))
    errors = validate_retrieval_references(
        [case], skill_names={"weather-outing"}, exemplar_ids={"info#1"})
    assert any("unknown skill typo-guide" in error for error in errors)
```

- [ ] **Step 2: 给现有 boundary ruling 增稳定 id，不改裁定语义**

每个 `rulings` 元素增加唯一 `id`，格式为 `left-domain-right-domain.semantic-slug`，例如：

```yaml
rulings:
  - id: reminder-scene.todo-vs-scene
    texts: [看看我的待办, 看看我的场景]
    domains: [reminder, scene]
    why: 对象不同——待办清单 vs 场景清单；共享的只是「看看我的」这个框架
```

为当前全部 ruling 补齐 id；`test/eval_exemplars.py` 的现有逻辑继续只消费 `texts/domains/why`，新字段不得改变运行时检索或门禁结论。新增测试拒绝缺 id、重复 id、空 `why`。

- [ ] **Step 3: 实现 coverage 与双向数量盘点**

```python
def validate_coverage(cases, active_intents: set[str],
                      exemptions: dict[str, set[str]]) -> list[str]:
    matrix = {intent: {"positive": 0, "hard_negative": 0, "relation": 0}
              for intent in active_intents}
    eligible_ids = {case.id for case in cases
                    if case.status in {"reviewed", "stable"}}
    relation_members = {
        member
        for case in cases
        if case.relation and case.id in eligible_ids
        and case.relation.base_case in eligible_ids
        for member in (case.id, case.relation.base_case)
    }
    for case in cases:
        if case.status not in {"reviewed", "stable"}:
            continue
        for turn in case.turns:
            for plan in (turn.expected.plan,) + tuple(
                    replan.plan for replan in turn.expected.replans):
                # 多成员 any_of 只证明“这一组至少有一个可接受”，不能替每个成员
                # 制造独立正例；逐 intent coverage 只计 singleton 必要组。
                positives = {group.any_of[0] for group in plan.required_groups
                             if len(group.any_of) == 1}
                for intent in positives & active_intents:
                    matrix[intent]["positive"] += 1
                    if case.id in relation_members:
                        matrix[intent]["relation"] += 1
                for intent in set(plan.forbidden_intents) & active_intents:
                    matrix[intent]["hard_negative"] += 1
    required = {"positive": 2, "hard_negative": 2, "relation": 1}
    return [
        f"active intent {intent} {kind} has {matrix[intent][kind]}, need {minimum}"
        for intent in sorted(active_intents)
        for kind, minimum in required.items()
        if matrix[intent][kind] < minimum
        and kind not in exemptions.get(intent, set())
    ]


def validate_boundary_coverage(cases, boundaries: dict[str, tuple[str, str]],
                               minimum_per_side=2):
    counts = {(boundary, side): 0 for boundary in boundaries
              for side in ("left", "right")}
    errors = []
    for case in cases:
        if case.status not in {"reviewed", "stable"}:
            continue
        boundary = str(case.tags.get("boundary_ledger") or "")
        if not boundary:
            continue
        if boundary not in boundaries:
            errors.append(f"case {case.id} references unknown boundary ledger {boundary}")
            continue
        side = str(case.tags.get("boundary_side") or "")
        if side not in {"left", "right"}:
            errors.append(f"case {case.id} has invalid boundary_side {side!r}")
            continue
        wanted, opposite = (boundaries[boundary] if side == "left"
                            else tuple(reversed(boundaries[boundary])))
        plans = [turn.expected.plan for turn in case.turns]
        required_domains = {
            intent.split(".", 1)[0]
            for plan in plans for group in plan.required_groups
            if len(group.any_of) == 1 for intent in group.any_of}
        forbidden_domains = {
            intent.split(".", 1)[0]
            for plan in plans for intent in plan.forbidden_intents}
        if wanted not in required_domains or opposite not in forbidden_domains:
            errors.append(
                f"case {case.id} does not prove {boundary} {side}: "
                f"need required {wanted} and forbidden {opposite}")
            continue
        counts[(boundary, side)] += 1
    errors.extend(
        f"boundary {boundary} side {side} has {count}, need {minimum_per_side}"
        for (boundary, side), count in sorted(counts.items())
        if count < minimum_per_side)
    return errors


def validate_suite_counts(cases, suite: SuiteConfig) -> list[str]:
    selected = [case for case in cases if case.status in suite.statuses]
    errors = []
    if not suite.min_cases <= len(selected) <= suite.max_cases:
        errors.append(
            f"suite case count {len(selected)} outside "
            f"[{suite.min_cases}, {suite.max_cases}]")
    for attack, minimum in sorted(suite.attack_minimums.items()):
        count = sum(attack in set(case.tags.get("attacks") or [])
                    for case in selected)
        if count < minimum:
            errors.append(f"attack {attack} has {count}, need {minimum}")
    return errors


def validate_gate_candidate_count(cases, target: int = 140) -> list[str]:
    count = sum(case.status != "retired"
                and case.tags.get("gate_candidate") is True for case in cases)
    return [] if count == target else [f"gate candidates {count}, need exactly {target}"]
```

`coverage_exemptions.yaml` 只保存少量无法满足某项覆盖的 intent：每项必须含精确 `intent`、`requirements`（仅 `positive|hard_negative|relation`）、`reason`、`owner`、`reviewed_at`，不得用 domain 级通配符。active intent 每次运行从 `eval_live.known_intents()` 扫描，文件中不得复制 covered 清单；豁免某一 requirement 不能顺带豁免另外两项。authoritative coverage 只计 reviewed/stable；candidate 另报 provisional coverage，不得替正式缺口变绿。

把 `eval_live.known_intents()` 改为从 `load_agents(include_edge=True)` 的实际 admitted capabilities 构造，而不是只扫静态 manifest；因此 `mcp_bridge/servers.yaml` 合成的 `shop.*` 也进入 active inventory。若存在 `servers.yaml` 但合成后能力为空，记 `assets_complete=false` 并报契约错误，不能静默缩小分母。

```python
# test/eval_live.py
def known_intents() -> set[str]:
    return {
        str(cap.intent)
        for agent in load_agents(include_edge=True)
        for cap in (getattr(agent.manifest, "capabilities", None) or [])
        if getattr(cap, "intent", "")
    }
```

```yaml
# test/eval_corpus/intent_adversarial/coverage_exemptions.yaml
schema_version: 1
exemptions: []
```

加载器拒绝未知 intent、重复 intent+requirement、空 reason/owner/reviewed_at 和未知 requirement；空文件不代表全量豁免。

同一 Task 从真实 `SkillStore.load(force=True)` 与 `ExemplarStore.load(force=True)` 建立 name/eid inventory，并校验 retrieval contract 的 required/forbidden 引用；未知 Skill 名、未知 Exemplar eid、同一资产同时 required/forbidden 都是契约错误。`boundaries.yaml` 的 `rulings[].id` 建立 ledger mapping，`tags.boundary` 仍可保存任意报告分桶；只有 `tags.boundary_ledger + boundary_side` 参与台账双向门禁。

```python
def validate_retrieval_references(cases, skill_names: set[str],
                                  exemplar_ids: set[str]) -> list[str]:
    errors = []
    for case in cases:
        for index, turn in enumerate(case.turns):
            exp = turn.expected.retrieval
            for name in exp.required_skills + exp.forbidden_skills:
                if name not in skill_names:
                    errors.append(f"{case.id}#{index}: unknown skill {name}")
            for eid in exp.required_exemplars + exp.forbidden_exemplars:
                if eid not in exemplar_ids:
                    errors.append(f"{case.id}#{index}: unknown exemplar {eid}")
            for kind, required, forbidden in (
                ("skill", set(exp.required_skills), set(exp.forbidden_skills)),
                ("exemplar", set(exp.required_exemplars),
                 set(exp.forbidden_exemplars)),
            ):
                overlap = required & forbidden
                if overlap:
                    errors.append(
                        f"{case.id}#{index}: {kind} required/forbidden overlap "
                        f"{sorted(overlap)}")
    return errors
```

- [ ] **Step 4: 实现纯词法 L0 检索与 retrieval 裁判**

`run_retrieval_turn()` 在临时环境中固定 `SKILLS_RETRIEVAL=lexical`、`EXEMPLARS_RETRIEVAL=lexical`，直接并行调用 `orchestrator.cloud.skills.plan_skills()` 和 `orchestrator.cloud.exemplars.plan_exemplars()`；退出上下文时逐项恢复原环境。`judge_turn()` 对返回名称先去掉 `full:/shadow:`、`@lex/@vec`、分数与 `!clipped` 归因后，再做 required/forbidden 精确名称判定。

```python
def _retrieved_name(value: str) -> str:
    value = str(value or "")
    head, sep, tail = value.partition(":")
    if sep and head in {"full", "shadow", "canary", "lexical", "hybrid"}:
        value = tail
    return value.split("!", 1)[0].split("@", 1)[0]


def judge_retrieval(expected: RetrievalExpectation, actual: PlanSnapshot,
                    out: TurnJudgement) -> None:
    skills = {_retrieved_name(value) for value in actual.skills}
    exemplars = {_retrieved_name(value) for value in actual.exemplars}
    required = ([(name, name in skills) for name in expected.required_skills]
                + [(name, name in exemplars)
                   for name in expected.required_exemplars])
    forbidden = ([(name, name in skills) for name in expected.forbidden_skills]
                 + [(name, name in exemplars)
                    for name in expected.forbidden_exemplars])
    for name, present in required:
        _assert(out, f"retrieval.required:{name}", present, True, present)
    for name, present in forbidden:
        _assert(out, f"retrieval.forbidden:{name}", not present, False, present)
```

`judge_turn()` 在 plan 判定之后无条件调用 `judge_retrieval()`；空 expectation 不产生断言。

同一 Task 补 `run_catalog_l0(agents, budget_chars, granted_permissions)`：在 `try/finally` 中临时替换 `orchestrator.cloud.context._CATALOG_BUDGET`，调用真实 `WorkingSet.render_catalog(agents, stats)`，并复用 `PlanBuilder._filter_by_permission()` 计算实际 admitted intents；返回 `chars_full/chars_final/dropped/admitted_intents`。测试至少覆盖：16k 默认不静默丢 core、受限预算的 dropped 可见、能力消失后不在 admitted、权限过滤不暴露越权 intent。不得通过改环境文件或复制 catalog 算法完成。

- [ ] **Step 5: 跑零网络契约门禁**

Run: `python -m pytest test/test_intent_adversarial_contract.py test/test_intent_adversarial_judge.py test/test_intent_adversarial_runtime.py test/eval_exemplars.py -q`

Expected: all tests PASS；断开 llm-gateway 时仍不访问网络。

- [ ] **Step 6: 提交 inventory 与检索门禁**

```powershell
git add -- skills/exemplars/boundaries.yaml test/eval_live.py test/support/intent_adversarial_contract.py test/support/intent_adversarial_judge.py test/support/intent_adversarial_runtime.py test/test_intent_adversarial_contract.py test/test_intent_adversarial_judge.py test/test_intent_adversarial_runtime.py test/eval_corpus/intent_adversarial/coverage_exemptions.yaml
git commit -m "test: audit adversarial intent coverage"
```

### Task 8: 建立 L1 真实 Planner 和重复稳定性策略

**Files:**
- Modify: `test/eval_live.py`
- Modify: `test/support/intent_adversarial_runtime.py`
- Modify: `test/test_intent_adversarial_runtime.py`

- [ ] **Step 1: 写 builder 参数透传和重复分类测试**

```python
import eval_live
from support.intent_adversarial_runtime import (
    RepeatOutcome, classify_repeats,
)


async def _fake_llm(_messages):
    return "{}"


async def _fake_tool_llm(_messages, _tools):
    return "", []


def _outcome(passed, signature="right", *, dangerous=False):
    return RepeatOutcome(passed=passed, signature=signature,
                         dangerous=dangerous)


def test_make_builder_forwards_timeout_and_model(monkeypatch):
    captured = {}
    def fake(caller, temperature=0.3, timeout=45, model=""):
        captured.update(caller=caller, temperature=temperature,
                        timeout=timeout, model=model)
        return _fake_llm, _fake_tool_llm
    monkeypatch.setattr(eval_live, "make_llm_fns", fake)
    eval_live.make_builder("intent-adversarial", 0.0, timeout=17, model="@primary")
    assert captured == {"caller": "intent-adversarial", "temperature": 0.0,
                        "timeout": 17, "model": "@primary"}


def test_normal_pass_runs_once_and_failure_replays_to_three():
    assert classify_repeats([_outcome(True)], risk="medium").status == "pass"
    stable = classify_repeats([
        _outcome(False, "wrong-a"), _outcome(False, "wrong-a"),
        _outcome(True, "right")], risk="medium")
    assert stable.status == "stable_fail"


def test_split_results_are_unstable_and_any_dangerous_route_is_critical():
    assert classify_repeats([
        _outcome(False, "a"), _outcome(False, "b"), _outcome(True, "right")],
        risk="medium").status == "unstable"
    assert classify_repeats([
        _outcome(True), _outcome(False, "danger", dangerous=True), _outcome(True)],
        risk="high").status == "critical_fail"
```

- [ ] **Step 2: 修改 `make_builder()`，不改变默认调用方**

```python
# test/eval_live.py
def make_builder(caller: str, temperature: float = 0.3,
                 timeout: int = 45, model: str = ""):
    from orchestrator.cloud.planning import PlanBuilder
    llm, llm_tools = make_llm_fns(caller, temperature, timeout, model)
    return PlanBuilder(llm_fn=llm, registry_fn=_registry_empty,
                       llm_tool_fn=llm_tools)
```

现有两参数调用保持逐字行为；新 CLI 才显式传 timeout/model。

- [ ] **Step 3: 实现 L1 working set 和单轮执行**

```python
async def run_planner_turn(turn, agents, builder, *, granted_permissions=None):
    catalog = filter_unavailable_capabilities(
        agents, set(turn.context.get("unavailable_intents") or []))
    working_set = WorkingSet(
        catalog=catalog,
        history=list(turn.context.get("history") or []),
        memories=list(turn.context.get("memories") or []),
        focus=parse_focus(turn.context.get("focus") or {}),
    )
    ctx = PlanContext(
        request_id="intent-adversarial", session_id="intent-adversarial",
        user_id="eval-user", vehicle_id="eval-vehicle",
        granted_permissions=list(granted_permissions or []),
    )
    plan = await builder.build(turn.utterance, working_set, ctx,
                               granted_permissions=granted_permissions)
    replans = []
    for expected_replan in turn.expected.replans:
        observation = dict(expected_replan.after["result"])
        decision = await builder.replan(
            plan.goal, [observation], catalog, ctx,
            granted_permissions=granted_permissions,
            working_set=working_set,
            skill_names=list(plan.skills or []),
            exemplar_names=list(plan.exemplars or []),
        )
        replans.append(snapshot_plan(decision.to_plan(plan.goal)))
    return DecisionSnapshot(
        ingress="cloud", addressed=bool(plan.addressed),
        decision=("clarify" if plan.clarify and not plan.steps else
                  "execute" if plan.steps else
                  "reject" if not plan.addressed else "degrade"),
        clarify=bool(plan.clarify and not plan.steps), plan=snapshot_plan(plan),
        replans=tuple(replans),
    )
```

`filter_unavailable_capabilities()` 必须 `copy.deepcopy()` 输入 agent，再过滤副本的 `manifest.capabilities`；不能原地污染同进程后续案例。`parse_focus()` 只接受 `Focus` dataclass 的真实字段，未知 key 在契约加载期报错。

- [ ] **Step 4: 实现规定的重复策略**

普通风险先跑 1 次：通过即停止，失败补到 3 次；high/critical 固定跑 3 次。三轮中同一错误语义签名至少 2 次为 `stable_fail`；没有错误签名达到 2 次为 `unstable`；任一轮出现未确认危险路由或副作用立即标 `critical_fail`。语义签名使用 Task 4 的 `semantic_signature()`，不比较自然语言回复。

```python
@dataclass(frozen=True)
class RepeatOutcome:
    passed: bool
    signature: str
    dangerous: bool = False


@dataclass(frozen=True)
class RepeatClassification:
    status: str
    outcomes: tuple[RepeatOutcome, ...]


def classify_repeats(outcomes: list[RepeatOutcome], risk: str) -> RepeatClassification:
    if any(outcome.dangerous for outcome in outcomes):
        return RepeatClassification("critical_fail", tuple(outcomes))
    if all(outcome.passed for outcome in outcomes):
        return RepeatClassification("pass", tuple(outcomes))
    wrong = Counter(outcome.signature for outcome in outcomes if not outcome.passed)
    status = "stable_fail" if wrong and max(wrong.values()) >= 2 else "unstable"
    return RepeatClassification(status, tuple(outcomes))


async def run_with_repeats(run_once, *, risk: str, failure_repeats: int,
                           high_risk_repeats: int) -> RepeatClassification:
    target = high_risk_repeats if risk in {"high", "critical"} else 1
    outcomes = [await run_once() for _ in range(target)]
    if target == 1 and not outcomes[0].passed:
        outcomes.extend([await run_once() for _ in range(failure_repeats - 1)])
    return classify_repeats(outcomes, risk)
```

同文件从 `collections` 导入 `Counter`；suite 校验已保证 repeat 数不小于 1。

- [ ] **Step 5: 跑 runtime 单测和现有 live 门面调用方单测**

Run: `python -m pytest test/test_intent_adversarial_runtime.py test/test_eval_common.py -q`

Expected: all tests PASS。

- [ ] **Step 6: 提交 L1 runtime**

```powershell
git add -- test/eval_live.py test/support/intent_adversarial_runtime.py test/test_intent_adversarial_runtime.py
git commit -m "test: run adversarial cases through planner"
```

### Task 9: 加入失败定向消融和稳定翻转归因

**Files:**
- Modify: `test/support/intent_adversarial_runtime.py`
- Modify: `test/support/intent_adversarial_trace.py`
- Modify: `test/test_intent_adversarial_runtime.py`
- Modify: `test/test_intent_adversarial_trace.py`

- [ ] **Step 1: 写环境恢复、按需消融和因果判定测试**

```python
def test_temporary_env_restores_missing_and_existing_values(monkeypatch):
    monkeypatch.setenv("SKILLS_MODE", "shadow")
    with temporary_env({"SKILLS_MODE": "off", "EXEMPLARS_MODE": "off"}):
        assert os.environ["SKILLS_MODE"] == "off"
        assert os.environ["EXEMPLARS_MODE"] == "off"
    assert os.environ["SKILLS_MODE"] == "shadow"
    assert "EXEMPLARS_MODE" not in os.environ


def test_ablations_only_run_for_failure_or_instability():
    assert requested_ablations("pass") == ()
    assert requested_ablations("stable_fail") == (
        "no-hints", "no-skills", "no-exemplars", "empty-history", "cloud-direct")


def test_causal_effect_requires_stable_wrong_to_stable_right_flip():
    assert causal_effect("stable_fail", "pass", same_provider=True,
                         same_assets=True) == "supported"
    assert causal_effect("unstable", "pass", True, True) == "suspect"
    assert causal_effect("stable_fail", "pass", False, True) == "invalid"
```

- [ ] **Step 2: 实现五条单变量消融**

| 消融 | 唯一改变 | 实现方式 |
|---|---|---|
| `no-hints` | 禁用 Route Hint | 新 builder 的 `_route_hints` 替换为返回 `False` 的 No-op |
| `no-skills` | 不注 Skill | 临时 `SKILLS_MODE=off` |
| `no-exemplars` | 不注 Exemplar | 临时 `EXEMPLARS_MODE=off` |
| `empty-history` | 清空上下文 | `history=[]`、`memories=[]`、`focus=None`，catalog 不变 |
| `cloud-direct` | 跳过 Edge | 同一原话直接进入 L2 Cloud Engine，Planner/Context/Session 资产不变 |

每条 arm 新建 builder 和 working set，固定 provider/model/temperature/timeout，运行 3 次；禁止复用上一 arm 的 Plan、检索缓存名单或被原地过滤的 catalog。

- [ ] **Step 3: 实现归因等级**

只有 full arm 为同一错误签名的 `stable_fail`，且消融 arm 3/3 通过，同时 provider lock、model、温度、资产 SHA 全相同时，才记 `causal_effect=supported`。任一 arm `unstable` 只记 `suspect`；provider 漂移或资产变化记 `invalid`。报告使用“支持该层为因果来源”措辞，不把一次翻转写成根因。

- [ ] **Step 4: 跑消融单测并提交**

Run: `python -m pytest test/test_intent_adversarial_runtime.py test/test_intent_adversarial_trace.py -q`

Expected: all tests PASS。

```powershell
git add -- test/support/intent_adversarial_runtime.py test/support/intent_adversarial_trace.py test/test_intent_adversarial_runtime.py test/test_intent_adversarial_trace.py
git commit -m "test: diagnose adversarial routing failures"
```

### Task 10: 生成精确指标、弱项分桶与 baseline 资格判定

**Files:**
- Create: `test/support/intent_adversarial_report.py`
- Create: `test/test_intent_adversarial_report.py`

- [ ] **Step 1: 写指标和 baseline 资格失败测试**

```python
# test/test_intent_adversarial_report.py
from support.intent_adversarial_report import (
    AdversarialResult, baseline_eligibility, build_adversarial_report,
    render_adversarial_markdown,
)


def _meta(**changes):
    meta = {
        "suite": "gate",
        "layer": "all",
        "retrieval_state": "warm",
        "provider_locked": True,
        "provider_drift": False,
        "code_sha": "abc1234",
        "worktree_clean": True,
        "assets_complete": True,
        "infrastructure_errors": [],
        "selected_statuses": ["stable"],
        "case_set_complete": True,
        "l3_selected": ["A1-1"],
        "l3_complete": True,
        "baseline_regressions": [],
    }
    meta.update(changes)
    return meta


def _result(case_id, *, passed=True, repeat_status="pass", domain="info",
            attack="A1", cohort="unseen_transfer",
            required_recall=1.0, actual_intents=("info.weather",),
            admitted_intents=("info.weather",), divergence="", layer="l1"):
    return AdversarialResult(
        result_id=f"{case_id}@{layer}", case_id=case_id, layer=layer,
        title=case_id, passed=passed,
        repeat_status=repeat_status, cohort=cohort, risk="medium",
        status="stable", provenance_kind="authored",
        provider_model="mimo:model-a",
        dimensions={"intent": tuple(actual_intents), "domain": (domain,),
                    "boundary": (), "attack": (attack,), "risk": ("medium",),
                    "ingress": ("cloud",), "cohort": (cohort,),
                    "layer": (layer,), "provider": ("mimo:model-a",),
                    "status": ("stable",), "provenance": ("authored",)},
        metrics={"exact_plan_set": float(passed),
                 "required_group_recall": required_recall},
        expected={}, actual={}, admitted_intents=tuple(admitted_intents),
        actual_intents=tuple(actual_intents), assertions=(), repetitions=(),
        divergence=divergence,
    )


def test_report_keeps_micro_macro_and_weakest_cells_separate():
    results = [
        _result("a", passed=True, domain="info", attack="A1",
                cohort="seen_regression", required_recall=1.0),
        _result("b", passed=False, domain="navigation", attack="A1",
                cohort="unseen_transfer", required_recall=0.0),
        _result("c", passed=True, domain="info", attack="A3",
                cohort="unseen_transfer", required_recall=1.0),
    ]
    report = build_adversarial_report(results, _meta())
    assert report["metrics"]["exact_plan_set_rate"]["value"] == 2 / 3
    assert report["dimensions"]["domain"]["navigation"]["pass_rate"] == 0.0
    assert report["weakest"][0]["dimension"] == "domain"
    assert report["cohorts"]["seen_regression"]["total"] == 1
    assert report["cohorts"]["unseen_transfer"]["total"] == 2


def test_capability_hallucination_uses_admitted_inventory():
    result = _result("a", passed=False, actual_intents=("missing.intent",),
                     admitted_intents=("info.weather",))
    report = build_adversarial_report([result], _meta())
    assert report["metrics"]["capability_hallucination_rate"]["value"] == 1.0


def test_baseline_rejects_unlocked_drifted_incomplete_or_unstable_runs():
    report = build_adversarial_report(
        [_result("a", passed=False, repeat_status="unstable")],
        _meta(provider_locked=False, provider_drift=True, worktree_clean=False,
              assets_complete=False))
    eligibility = baseline_eligibility(report)
    assert not eligibility.eligible
    assert set(eligibility.reasons) >= {
        "provider_not_locked", "provider_drift", "asset_fingerprint_incomplete",
        "dirty_worktree", "unstable_results", "gate_failures",
    }


def test_baseline_rejects_empty_l3_or_existing_baseline_regression():
    report = build_adversarial_report(
        [_result("a")],
        _meta(l3_selected=[], l3_complete=False,
              baseline_regressions=["old.case@l1"]))
    assert set(baseline_eligibility(report).reasons) >= {
        "l3_empty", "l3_incomplete", "baseline_regressions"}


def test_markdown_names_every_metric_and_first_divergence():
    md = render_adversarial_markdown(build_adversarial_report(
        [_result("a", passed=False, divergence="HINT_DIVERGENCE")], _meta()))
    assert "required_group_recall" in md
    assert "instability_rate" in md
    assert "HINT_DIVERGENCE" in md
```

- [ ] **Step 2: 运行测试，确认 report 模块不存在**

Run: `python -m pytest test/test_intent_adversarial_report.py -q`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: 定义每案例证据对象**

```python
# test/support/intent_adversarial_report.py
@dataclass(frozen=True)
class AdversarialResult:
    result_id: str
    case_id: str
    layer: str
    title: str
    passed: bool
    repeat_status: str
    cohort: str
    risk: str
    status: str
    provenance_kind: str
    provider_model: str
    dimensions: dict[str, tuple[str, ...]]
    metrics: dict[str, float]
    expected: dict[str, Any]
    actual: dict[str, Any]
    admitted_intents: tuple[str, ...]
    actual_intents: tuple[str, ...]
    assertions: tuple[dict[str, Any], ...]
    repetitions: tuple[dict[str, Any], ...]
    divergence: str = ""
    ablations: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class BaselineEligibility:
    eligible: bool
    reasons: tuple[str, ...]
```

运行层必须把 `TurnJudgement` 转成纯 dict 后再交报告层，报告文件不得序列化 Python repr、protobuf 或异常对象。

- [ ] **Step 4: 实现十一个主指标和分维度统计**

报告顶层固定输出：

```python
METRICS = (
    "exact_plan_set_rate",
    "required_group_recall",
    "overroute_rate",
    "forbidden_route_rate",
    "ingress_accuracy",
    "dependency_pass_rate",
    "clarify_balanced_accuracy",
    "relation_pass_rate",
    "context_override_rate",
    "capability_hallucination_rate",
    "instability_rate",
)
DIMENSIONS = (
    "intent", "domain", "boundary", "attack", "risk", "ingress", "cohort",
    "layer", "provider", "status", "provenance",
)
```

计算规则：

- exact 只以 plan/replan 的 required、forbidden、extra、complexity、dependency、slot 断言全真为真；goal 不计分；
- required recall 按必要组平均，不按 intent 个数；
- overroute/forbidden/capability hallucination 是“命中该缺陷的案例数 / 有对应断言的案例数”；
- ingress 只统计声明了 ingress gold 的案例；
- dependency 只统计声明了 dependency 的案例；
- clarify balanced accuracy 分别算 required 召回和 forbidden 特异度后取均值，缺一侧时显示 `null` 而非伪造 100%；
- relation/context 只统计对应 relation 类型；
- instability 以 `repeat_status=unstable` 的 live 案例为分子；
- 所有比率同时输出 numerator、denominator、value；denominator=0 时 value 为 `null`。

按 `DIMENSIONS` 分桶，并分别输出 micro、macro、最弱 10 个有效 cell；seen/unseen 和 layer 必须有独立顶层摘要，不能只藏在 tags。同一 case 的多层结果用唯一 `result_id=case_id@layer` 写入 `eval_common` 骨架，禁止以裸 `case_id` 作 map key 导致后写层覆盖先写层。`--layer all` 的 overall 明确标注为 evidence-unit micro；每次重复运行的是完整 case turn 序列，而不是拆散后各轮独立抽样。

- [ ] **Step 5: 复用 `eval_common` 的报告写入与逐例 diff**

先用 `eval_common.build_report()` 构造兼容的 `cases`/`overall` 骨架，再增加 `metrics`、`dimensions`、`cohorts`、`weakest`、`provider_lock`、`assets`、`selection`、`ablations`。Markdown 先调用 `eval_common.render_markdown()`，再追加精确指标、弱项、稳定性、首偏离和局限章节；最终仍由 `eval_common.write_report()` 原子式写 JSON/Markdown。

- [ ] **Step 6: 实现 baseline 资格硬闸**

`baseline_eligibility()` 必须同时满足：`suite=gate`、`layer=all`、`retrieval_state=warm`、选中案例状态全为 stable、显式 provider pin 成功、无 provider 漂移、代码 SHA 非空且除 ignored runtime 报告/待写 baseline 目标外工作树干净、资产指纹完整、全部 `(case_id, declared_layer)` 证据单元齐全且通过、无 `stable_fail/critical_fail/unstable`、无基础设施错误、L3 选集非空且结构化结果完整、相对既有 baseline 无逐例回退。首次建 baseline 时 `baseline_regressions=[]`；失败只写普通运行报告，拒绝覆盖 `docs/reviews/eval/baseline_intent_adversarial.*`。

- [ ] **Step 7: 跑报告单测并提交**

Run: `python -m pytest test/test_intent_adversarial_report.py test/test_eval_common.py -q`

Expected: all tests PASS。

```powershell
git add -- test/support/intent_adversarial_report.py test/test_intent_adversarial_report.py
git commit -m "test: report adversarial routing metrics"
```

### Task 11: 建立 L2 完整 Edge→Engine 安全链

**Files:**
- Modify: `test/support/intent_adversarial_runtime.py`
- Modify: `test/test_intent_adversarial_runtime.py`

- [ ] **Step 1: 写 pending、clarify、adaptive 与确认前零执行测试**

```python
from orchestrator.cloud.models import Plan, ReplanDecision, Step
from support.intent_adversarial_runtime import build_scripted_engine_harness


def _scripted_plan(intent=None, *, complexity="simple", clarify=None, slots=None):
    steps = [] if intent is None else [Step(
        id="s1", agent_id=intent.split(".", 1)[0], endpoint="fake:1",
        intent=intent, slots=dict(slots or {}))]
    return Plan(steps=steps, complexity=complexity, goal="完成用户目标",
                clarify=clarify)


def _scripted_replan(intent, *, slots=None):
    return ReplanDecision(done=False, steps=_scripted_plan(
        intent, complexity="adaptive", slots=slots).steps)


def test_engine_harness_exposes_clarify_without_agent_call(monkeypatch):
    monkeypatch.setenv("CLARIFY_ENABLED", "on")
    harness = build_scripted_engine_harness(_scripted_plan(clarify={
        "question": "您想看详情还是导航？",
        "options": [{"label": "详情", "send_text": "看详情"},
                    {"label": "导航", "send_text": "导航过去"}],
    }))
    out = harness.run("华润大厦", meta={"memory_enabled": "false"})
    assert out.decision == "clarify"
    assert out.agent_calls == ()


def test_pending_confirm_cancel_does_not_replan_or_execute():
    harness = build_scripted_engine_harness(_scripted_plan("trunk.open"))
    harness.seed_pending_confirm("s1", intent="trunk.open")
    out = harness.run("取消", session_id="s1", is_confirmation=True)
    assert out.decision == "cancel"
    assert out.planner_calls == ()
    assert out.side_effects == ()


def test_high_risk_need_confirm_has_zero_side_effect_before_confirm():
    harness = build_scripted_engine_harness(
        _scripted_plan("nearby.order"), agent_status="NEED_CONFIRM")
    out = harness.run("帮我下单", meta={"memory_enabled": "false"})
    assert out.need_confirm is True
    assert out.side_effects == ()


def test_adaptive_result_is_replanned_and_recorded():
    harness = build_scripted_engine_harness(
        _scripted_plan("info.weather", complexity="adaptive"),
        replans=[_scripted_replan("nearby.search",
                                  slots={"category": "室内", "weather_context": "雨"})],
        responses={"info.weather": {"condition": "中雨"}},
    )
    out = harness.run("今天的天气适合去哪玩")
    assert [plan.intents for plan in out.plans] == [
        ("info.weather",), ("nearby.search",)]
```

- [ ] **Step 2: 实现无真实副作用的 `SafeClients`**

```python
class SafeClients:
    def __init__(self, agents, *, history=(), memories=(), responses=None,
                 confirm_intents=()):
        self.agents = list(agents)
        self.history = list(history)
        self.memories = list(memories)
        self.responses = dict(responses or {})
        self.confirm_intents = set(confirm_intents)
        self.agent_calls = []
        self.side_effects = []

    async def list_agents(self):
        return list(self.agents)

    async def resolve(self, query="", intent="", top_k=1):
        if intent:
            return [a for a in self.agents if any(
                cap.intent == intent for cap in a.manifest.capabilities)][:top_k]
        return list(self.agents[:top_k])

    async def get_session(self, *_args, **_kwargs):
        return list(self.history)

    async def recall(self, *_args, **_kwargs):
        return list(self.memories)

    async def append_turn(self, *_args, **_kwargs):
        return None

    async def call_agent_stream(self, *_args, **_kwargs):
        if False:
            yield None

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None, **_kwargs):
        self.agent_calls.append({"intent": intent, "slots": dict(slots or {})})
        return self._response_for(intent)
```

`_response_for()` 只构造 `agent_pb2.ExecuteResponse`：读操作返回受控 OK data；`confirm_intents` 内的写/危险操作固定 NEED_CONFIRM 且无 actions；只有测试显式注入 `confirmed_response` 时才生成 action，并把该 action 记入 `side_effects`。`confirm_intents` 来自 case 的 safety contract、真实 manifest require_confirm 和 Edge `_confirm_required` 盘点的并集，不能依赖 `eval_live` 合成 edge capability 的 `require_confirm=False`。禁止访问真实 Agent、Provider、支付、消息、删除或 VAL。

- [ ] **Step 3: 用真实 Engine 组件装配 harness**

```python
def build_engine_harness(builder, agents, safe_clients, trace_sink):
    planner = RecordingPlanner(builder, trace_sink)
    executor = DagExecutor(call_agent_fn=safe_clients.call_agent)
    aggregator = Aggregator(llm_fn=_deterministic_aggregate)
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=safe_clients, planner=planner, executor=executor,
        aggregator=aggregator, session=session)
    return EngineHarness(engine, safe_clients, session, trace_sink)
```

单测可以注入 scripted planner 验证状态机；真实 L2 运行必须传 Task 8 的 live `PlanBuilder`，不能用 scripted plan 作为准确率证据。`SessionStore(redis_url="")` 保持进程内隔离，每案例使用唯一 session id。
真实 L2 案例启动前按 contract context 装配状态：history/memories 注入 `SafeClients`；focus 用 `session.save_focus()`；pending 用真实 `SessionState` + `session.save()`；`input_source`、`is_confirmation`、`granted_permissions` 进入 request/meta。每案例结束后丢弃整个 harness，而不是清空后复用，避免 session/focus 穿案例。

```python
class ScriptedPlanner:
    def __init__(self, plan, replans=()):
        self.plan = plan
        self.replans = list(replans)
        self.calls = []

    async def build(self, *_args, **_kwargs):
        self.calls.append("build")
        return self.plan

    async def replan(self, *_args, **_kwargs):
        self.calls.append("replan")
        return self.replans.pop(0) if self.replans else ReplanDecision(done=True)


def build_scripted_engine_harness(plan, *, replans=(), responses=None,
                                  agent_status="OK"):
    planner = ScriptedPlanner(plan, replans)
    clients = SafeClients([], responses=responses)
    clients.default_status = agent_status
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=clients, planner=planner,
        executor=DagExecutor(call_agent_fn=clients.call_agent),
        aggregator=Aggregator(_deterministic_aggregate), session=session)
    return EngineHarness(engine, clients, session, planner=planner)
```

`EngineHarness.run()` 用 `SimpleNamespace` 构造与现有 engine 单测相同的 request/context，并完整收集 async events；`seed_pending_confirm()` 使用真实 `SessionState(phase="wait_confirm", pending_step_id="s1", pending_plan=...)`；输出的 `planner_calls` 直接来自 `ScriptedPlanner.calls`，`plans` 来自 `RecordingPlanner` 或 scripted build/replan 快照，不能从最终 speech 反推。

- [ ] **Step 4: 实现 Edge→Engine protobuf 适配器**

把 `engine.run()` 的 `speech`、`action`、`final` dict 转成 `orchestrator_pb2.HandleEvent`，仅供 `EdgeOrchestratorServicer.cloud.handle` 的内进程替身消费。完整入口执行后同时记录：Edge ingress、Planner traces、Engine decision、Agent calls、VAL state delta 和 side effects。若 cloud-direct 通过而完整入口失败，首偏离点为 Edge；反之不得推断 Edge 有问题。

- [ ] **Step 5: 跑 Engine 组合回归**

Run: `python -m pytest test/test_intent_adversarial_runtime.py orchestrator/cloud/tests/test_engine_reject.py orchestrator/cloud/tests/test_engine_focus.py orchestrator/cloud/tests/test_engine_confirm.py orchestrator/cloud/tests/test_engine_adaptive.py orchestrator/edge/tests/test_server_dispatch.py -q`

Expected: all tests PASS。

- [ ] **Step 6: 提交完整链运行门面**

```powershell
git add -- test/support/intent_adversarial_runtime.py test/test_intent_adversarial_runtime.py
git commit -m "test: run adversarial cases through engine"
```

### Task 12: 建立统一 CLI、退出码与 L3 journey 链接

**Files:**
- Modify: `test/eval_common.py`
- Modify: `test/test_eval_common.py`
- Create: `test/eval_intent_adversarial.py`
- Create: `test/test_eval_intent_adversarial_cli.py`
- Create: `test/eval_corpus/intent_adversarial/journey_links.yaml`

- [ ] **Step 1: 写参数、offline 默认和 baseline 防绕过测试**

```python
# test/test_eval_intent_adversarial_cli.py
import subprocess
import sys
from types import SimpleNamespace

import pytest

from eval_intent_adversarial import (
    parse_args, run_l3, validate_args, write_baseline_if_eligible,
)
from support.intent_adversarial_report import BaselineEligibility


def test_default_is_offline_l0_discovery():
    args = parse_args([])
    assert args.suite == "discovery"
    assert args.layer == "l0"
    assert args.live is False
    assert args.retrieval_state == "warm"


def test_l1_l2_require_live():
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--layer", "l1"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args([
            "--layer", "l1", "--live", "--provider", "mimo"]))
    validate_args(parse_args([
        "--layer", "l1", "--live", "--provider", "mimo",
        "--model", "mimo-model"]))


def test_write_baseline_requires_gate_and_explicit_provider():
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--write-baseline", "--suite", "discovery"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--write-baseline", "--suite", "gate", "--live"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args([
            "--write-baseline", "--suite", "gate", "--live",
            "--provider", "mimo", "--model", "mimo-model",
            "--layer", "l1"]))


def test_l3_uses_existing_runner_and_selected_journey_ids(monkeypatch):
    called = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: called.update(
        argv=argv, env=kw["env"]) or SimpleNamespace(returncode=0))
    assert run_l3(
        ["A1-1", "B4-1"], provider="mimo", model="mimo-model") == 0
    assert called["argv"] == [sys.executable, "scripts/run_e2e.py", "--id",
                              "e2e_journeys", "--provider", "mimo",
                              "--model", "mimo-model"]
    assert called["env"]["E2E_JOURNEY_IDS"] == "A1-1,B4-1"


def test_ineligible_run_never_touches_formal_baseline(tmp_path):
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    rejected_json = tmp_path / "_ci-run-rejected.json"
    rejected_md = tmp_path / "_ci-run-rejected.md"
    formal_json.write_text("old-json", encoding="utf-8")
    formal_md.write_text("old-md", encoding="utf-8")

    written = write_baseline_if_eligible(
        {"meta": {}}, "diagnostic", BaselineEligibility(False, ("l3_empty",)),
        formal_json, formal_md, rejected_json, rejected_md)

    assert written is False
    assert formal_json.read_text(encoding="utf-8") == "old-json"
    assert formal_md.read_text(encoding="utf-8") == "old-md"
    assert rejected_json.is_file() and rejected_md.is_file()
```

同时在 `test/test_eval_common.py` 增加 provider 还原测试：

```python
def test_provider_lock_restores_original_provider_and_model(monkeypatch):
    active = {"provider": "deepseek", "model": "deep-model"}
    lock = ProviderLock("http://llm", want="mimo", model="mimo-model")

    def fake_http(method, _path, payload=None):
        if method == "POST":
            active.update(payload or {})
        return {"active": dict(active)}

    monkeypatch.setattr(lock, "_http", fake_http)
    assert lock.pin() == "mimo:mimo-model"
    assert lock.restore() == "deepseek:deep-model"
    assert active == {"provider": "deepseek", "model": "deep-model"}


def test_provider_restore_does_not_clobber_external_change(monkeypatch):
    active = {"provider": "deepseek", "model": "deep-model"}
    posts = []
    lock = ProviderLock("http://llm", want="mimo", model="mimo-model")

    def fake_http(method, _path, payload=None):
        if method == "POST":
            posts.append(dict(payload or {}))
            active.update(payload or {})
        return {"active": dict(active)}

    monkeypatch.setattr(lock, "_http", fake_http)
    lock.pin()
    active.update(provider="minimax", model="user-model")
    assert lock.restore() == "minimax:user-model"
    assert posts == [{"provider": "mimo", "model": "mimo-model"}]
```

并把现有 `test_provider_lock_pin_explicit_success` 的假响应顺序改为 `GET 原 active → POST 目标 → GET 复核`，断言 `lock.original == "deepseek:deep-model"`；无显式 want 的 baseline 模式调用序列保持不变。显式 pin 前若读不到原 active，必须失败而不是进入无法还原的评测。

- [ ] **Step 2: 实现参数面**

固定支持：`--suite discovery|gate`、`--layer l0|l1|l2|l3|all`、`--case`、`--tag`、`--cohort`、`--risk`、`--live`、`--provider`、`--model`、`--temperature`、`--timeout`、`--retrieval-state warm|cold`、`--ablations off|on-failure`、`--list`、`--out-json`、`--out-md`、`--baseline`、`--write-baseline`、`--strict`。

默认报告写到已 gitignore 的：

```python
DEFAULT_JSON = ROOT / "docs/reviews/eval/_ci-run-intent-adversarial.json"
DEFAULT_MD = ROOT / "docs/reviews/eval/_ci-run-intent-adversarial.md"
```

CLI 只做选择和编排；schema、gold、判断、指标分别留在 support 模块。退出码固定：0=选集有效且满足当前 strict 规则，1=语义失败/不稳定/基线回归/provider 漂移，2=契约、参数或基础设施错误。
离线 `--list`/L0 按 suite 的 `statuses` 选；任何 `--live` 层只按 `live_statuses` 选，candidate 永远不能调用真实 LLM。
suite 数量、attack minimum 和 gate-candidate 数量在普通 `--list` 中作为缺口展示；`--strict` 或 `--write-baseline` 时升级为退出码 2 的契约错误。
`--tag` 可重复：每个值既可命中 list/scalar tag value，也可命中值为 truthy 的 tag key（因此 `--tag A7`、`--tag knowledge_interference`、`--tag gate_candidate` 都有确定语义）；多个 `--tag` 取 AND。选择后再按 `tags.layers` 分发，未声明该层的 case 不执行也不进入该层分母。
live 选择还要核对 stable provenance：声明 l1/l2/l3 的 stable case，其 `stabilized_provider` 必须逐字等于本次 `<provider>:<model>`；仅声明 l0 的 case 才允许 `deterministic`。混入其他 reference provider 的 stable case 是契约错误，不得把跨模型结果平均。

- [ ] **Step 3: 实现四层选择和 ProviderLock 生命周期**

L0 永远不创建 ProviderLock；L1/L2 在任何模型调用前 `pin()`，每 20 案例和运行末尾 `check()`；所有 live 层都要求 provider/model 成对显式给出。L3 通过临时进程环境 `E2E_JOURNEY_IDS` 调用现有 `scripts/run_e2e.py --id e2e_journeys --provider <p> --model <m>`；`--id` 与 `--full` 在当前 runner 中互斥，禁止拼成无效命令。不得修改 `test/e2e_manifest.yaml` 或 CI。`journey_links.yaml` 只保存 `{adversarial_case_id: [journey_id...]}`，加载时验证 journey id 确实存在于 `test/journeys/**/*.yaml`。

扩展 `eval_common.ProviderLock`：显式 pin 前先 GET 并保存原 active provider/model，提供幂等 `restore()`，CLI 在最外层 `finally` 调用。原 active 无法读取时拒绝 pin；还原前再次 GET，只有当前仍等于本次评测 target 才 POST 原值，若已经被 HMI/其他进程切走则记录 `restore_skipped_external_change` 并绝不覆盖用户的新选择。应还原时 POST 或 GET 复核失败记基础设施错误并返回非零；pin 前后相同则 no-op。这样 Ctrl+C/异常路径也不会把 HMI 的全局 active provider 永久留在评测档。
live 默认 `retrieval-state=warm`，模型调用前先 `await eval_live.warm_exemplars()` 并记录预热数量；`cold` 只能用于 discovery，跳过显式预热并在报告顶层标冷启动，baseline 资格闸拒绝 cold。两种结果不能合并成一个通过率。
当 `--layer l3 --list` 时，stdout 只打印去重排序后的逗号分隔 journey id，审计说明写 stderr；选集为空以退出码 2 失败。`--layer all` 必须读取 L3 子进程的结构化 journeys report 并合入总报告，不能只看子进程退出码。所有层输出用 `case_id@layer` 唯一键；expected evidence set 由选中 stable case 的 `tags.layers` 展开，缺任一声明层即 `case_set_complete=false`。

L3 按 case risk 分两批调用 runner：low/medium 首跑一次、失败补到三次；high/critical 固定三次。每次都是全新子进程，按 `journey_links.yaml` 把结构化 journey 结果回填到对应 `case_id@l3`；共享 journey 可以服务多个 case，但每个链接的 journey 都必须通过才算该 case 的 L3 证据通过。absolute/relation 语义仍由 L0–L2 契约裁判，L3 不复制第二套 gold。重复签名来自 journey id + step/status 集，不从最终自然语言反推。

- [ ] **Step 4: baseline 写入必须走 Task 10 资格闸**

CLI 不提供 `--update-baseline`、`--accept-failures` 或 `--force`。`--write-baseline` 只允许 `--suite gate --layer all --live --provider <explicit> --model <explicit>`，且只有 `baseline_eligibility().eligible` 时才调用 `write_report()` 写正式两文件。资格判断在任何正式路径写入前完成；不合资格时即使 `--out-json/--out-md` 指向 baseline 文件，也改写到带时间戳的 ignored `_ci-run-intent-adversarial-rejected-*` 诊断文件，正式 JSON/Markdown 保持逐字不变，并以退出码 2 打印所有拒绝原因。

- [ ] **Step 5: 跑 CLI 单测与离线空语料 smoke**

Run: `python -m pytest test/test_eval_intent_adversarial_cli.py test/test_eval_common.py -q`

Expected: all tests PASS。

Run: `python test/eval_intent_adversarial.py --suite discovery --layer l0 --list`

Expected: exit 0，列出当前 0 个案例和 suite 配置，不访问网络、不创建正式 baseline。

- [ ] **Step 6: 提交 CLI**

```powershell
git add -- test/eval_common.py test/test_eval_common.py test/eval_intent_adversarial.py test/test_eval_intent_adversarial_cli.py test/eval_corpus/intent_adversarial/journey_links.yaml
git commit -m "test: add adversarial intent evaluation cli"
```

### Task 13: 建立只产 candidate 的现有资产导入器

**Files:**
- Create: `test/build_intent_adversarial_candidates.py`
- Create: `test/test_build_intent_adversarial_candidates.py`

- [ ] **Step 1: 写来源、冲突和生命周期测试**

```python
# test/test_build_intent_adversarial_candidates.py
from pathlib import Path

import pytest
import yaml

from build_intent_adversarial_candidates import (
    deduplicate_candidates, import_manifest_examples, write_candidates,
)


def _manifest(tmp_path: Path, intent: str, examples: list[str]) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({
        "agent_id": "info",
        "capabilities": [{"intent": intent, "examples": examples}],
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _candidate(text: str, intent: str) -> dict:
    return {
        "id": f"candidate.{intent}", "family_id": f"asset.{intent}",
        "status": "candidate", "input": {"utterance": text, "context": {}},
        "required_intent_groups": [{"any_of": [intent]}],
        "provenance": {"kind": "test"},
    }


def test_imported_assets_are_candidate_and_keep_source_reference(tmp_path):
    rows = import_manifest_examples(_manifest(tmp_path, "info.weather", ["今天天气怎么样"]))
    assert rows[0]["status"] == "candidate"
    assert rows[0]["provenance"]["kind"] == "manifest_example"
    assert rows[0]["provenance"]["source_ref"].endswith("manifest.yaml")
    assert "reviewed_by" not in rows[0]["provenance"]


def test_same_input_with_conflicting_gold_goes_to_conflict_queue():
    accepted, conflicts = deduplicate_candidates([
        _candidate("有没有天气预警", "info.alerts"),
        _candidate("有没有天气预警", "safety.weather_alert"),
    ])
    assert accepted == []
    assert conflicts[0]["reason"] == "conflicting_gold"


def test_builder_never_writes_inside_committed_corpus(tmp_path):
    with pytest.raises(ValueError, match="review queue"):
        write_candidates([], tmp_path / "test/eval_corpus/intent_adversarial/cases/a.yaml")
```

- [ ] **Step 2: 实现五类只读导入源**

1. `agents/*/manifest.yaml` 的 capability examples；
2. 真实 `SkillStore.load(force=True)` 载入的 guide golden/holdout；
3. 真实 `ExemplarStore.load(force=True)` 载入的 intent/slots 文本（自动排除 `boundaries.yaml` 等保留文件）；
4. 现有 `test/eval_corpus/mode_routing_cases.yaml`、`clarify_cases.yaml`、`rejection_cases.yaml`、`route_hints_cases.yaml` 与 `edge_regressions.yaml` 中能映射到精确 intent 的行。
5. 显式传入 `--collector-url` 时，只读 GET“collector base URL + `/api/export/labels`”的 `turns.gold_intents` 人工标注。

导入器保留 source path/trace id、source key/index、原始 gold 和 stable family id；无法可靠映射 intent、context、否定语义或多意图的行进 `manual_review`，不得猜 gold。collector 默认关闭，调用时 timeout 15 秒，不写标注、不读 SQLite；原话先过姓名、手机号、车牌、精确地址和 token 脱敏检测，不通过的只计 `privacy_rejected`。输出只允许显式 `--out docs/reviews/eval/_ci-run-intent-candidates.yaml`，该路径已被 gitignore。

- [ ] **Step 3: 实现冲突、去重和 family 归组**

同一标准化文本+相同上下文+相同 gold 合并来源；gold 不同进入 conflicts；同一源例句的确定性变体共享 family id。所有输出 status 都是 candidate，provenance 不得自动写 `reviewed_by: human`。

- [ ] **Step 4: 运行单测和生成首份 review queue**

Run: `python -m pytest test/test_build_intent_adversarial_candidates.py -q`

Expected: all tests PASS。

Run: `python test/build_intent_adversarial_candidates.py --out docs/reviews/eval/_ci-run-intent-candidates.yaml`

Expected: exit 0；摘要打印 accepted/manual_review/conflicts/duplicates 四个计数；工作树不出现新的未跟踪报告文件。

- [ ] **Step 5: 提交导入器，不提交未审核输出**

```powershell
git add -- test/build_intent_adversarial_candidates.py test/test_build_intent_adversarial_candidates.py
git commit -m "test: build adversarial intent candidates"
```

### Task 14: 分九类建设 450 条发现语料并形成 140 条门禁候选

**Files:**
- Modify: `test/eval_corpus/intent_adversarial/cases/domain_boundaries.yaml`
- Create: `test/eval_corpus/intent_adversarial/cases/object_scope.yaml`
- Create: `test/eval_corpus/intent_adversarial/cases/negation_quotation.yaml`
- Create: `test/eval_corpus/intent_adversarial/cases/composition.yaml`
- Create: `test/eval_corpus/intent_adversarial/cases/context_state.yaml`
- Create: `test/eval_corpus/intent_adversarial/cases/edge_ingress.yaml`
- Create: `test/eval_corpus/intent_adversarial/cases/knowledge_interference.yaml`
- Create: `test/eval_corpus/intent_adversarial/cases/capability_catalog.yaml`
- Create: `test/eval_corpus/intent_adversarial/cases/expression_injection.yaml`
- Modify: `test/eval_corpus/intent_adversarial/journey_links.yaml`

- [ ] **Step 1: 按覆盖矩阵分配数量，不靠复制近义句冲数**

| 文件 / 攻击 | discovery 目标 | gate 候选 | 必含机制 |
|---|---:|---:|---|
| domain_boundaries / A1 | 80 | 28 | 每个 ledger id 左右各至少 2 条 |
| object_scope / A2 | 50 | 16 | 车/手机、人/地点、同词不同对象 |
| negation_quotation / A3 | 45 | 14 | 否定、转述、引用、取消其中一项 |
| composition / A4 | 60 | 20 | 并行、串行依赖、adaptive、换序 |
| context_state / A5 | 50 | 18 | stale history、focus、pending、topic switch |
| edge_ingress / A6 | 45 | 16 | local/cloud/mixed、危险动作、非受话 |
| knowledge_interference / A7 | 40 | 10 | Skill/Exemplar/Hint 误召回、覆盖、裁剪 |
| capability_catalog / A8 | 35 | 8 | 能力消失、权限过滤、动态 MCP、catalog drop |
| expression_injection / A9 | 45 | 10 | 口语、同音字、漏标点、提示注入、角色转述 |
| **合计** | **450** | **140** | gate 位于设计要求 120–160 内 |

一条多意图或 relation 可以给多个 active intent 贡献覆盖，但同一句的机械标点/语气词复制只算同一 family 的一个覆盖单元。
在 140 条 gate 候选中选 6–12 条真正需要跨进程/协议证据的案例声明 `l3`，并在 `journey_links.yaml` 链到现有 journey；普通单轮句不得为了凑 L3 数量复制成 journey。

- [ ] **Step 2: 首批真实边界必须全部落成双向绝对 gold**

至少包括：nearby/navigation/charging 设施发现；info.alerts/safety.weather_alert；reminder/scene；search/research；location/vision；vehicle battery/device battery；纯天气/weather-outing；明确目的地/POI 搜索。每个 relation 变体自身也必须有 absolute plan/decision/ingress gold，不能只依赖与 base 的关系。

属于 `skills/exemplars/boundaries.yaml` 台账的案例同时写 `tags.boundary_ledger=<ruling.id>` 与 `boundary_side=left|right`：left/right 以 ruling `domains` 顺序为准，且 absolute gold 必须 required 当前侧、forbidden 对侧才计数。`tags.boundary` 可继续保存天气出游等非台账业务边界，不参与 ledger 数量门禁。

- [ ] **Step 3: 组合与 adaptive 使用真实 Plan 语义**

无依赖并行只断言 intent 集和无额外项；串行使用 producer/consumer/carries；adaptive 把 initial plan 与 `replans` 分开。天气出游的 approved 样例按设计文档修正后的两阶段写法入库：首轮只查天气，注入中雨结果后 replan 为 `nearby.search(category=室内, weather_context present)`。

- [ ] **Step 4: 强制 family 隔离与 provenance 诚实**

只要某 family 任一文本进入 Skill、Exemplar、Hint 或 manifest 修复资产，整个 family 都标 `seen_regression`；unseen_transfer 必须来自不同 family。真实 collector 文本先脱敏；无法证明来源或脱敏的保持 candidate 且不提交原句。agent 生成或机械导入的条目不得写 `reviewed_by: human`。schema v1 的 relation pair 各自只含一个 turn，并声明完全相同的 `tags.layers`，保证同一 provider/资产/执行层下做一对一比较；多轮状态场景仍用 absolute turns 判定，不伪装成当前 relation runner 已支持。

- [ ] **Step 5: 生成审阅包并停在人工裁定点**

Run: `python test/eval_intent_adversarial.py --suite discovery --layer l0 --list --out-md docs/reviews/eval/_ci-run-intent-review.md`

Expected: 报告按文件列出 450 条、family、cohort、absolute gold、relation、来源、coverage 缺口和冲突；不运行 LLM。

把审阅包交给泓舟。只有明确批准的 case 才补 `reviewed_by: human`/`reviewed_at` 并从 candidate 晋为 reviewed；未批准项保持 candidate，不以 agent 自审冒充人工审阅。

- [ ] **Step 6: 审批后跑静态门禁**

Run: `python -m pytest test/test_intent_adversarial_contract.py test/test_intent_adversarial_judge.py test/test_intent_adversarial_runtime.py -q`

Expected: schema、known intent、active coverage、boundary 双向、family leakage 全部 PASS；九类数量达到上表，reviewed+candidate 合计 450，门禁候选 140。

- [ ] **Step 7: 分攻击批次提交语料**

```powershell
git add -- test/eval_corpus/intent_adversarial/cases/domain_boundaries.yaml test/eval_corpus/intent_adversarial/cases/object_scope.yaml test/eval_corpus/intent_adversarial/cases/negation_quotation.yaml
git commit -m "test: add adversarial routing boundary corpus"
git add -- test/eval_corpus/intent_adversarial/cases/composition.yaml test/eval_corpus/intent_adversarial/cases/context_state.yaml test/eval_corpus/intent_adversarial/cases/edge_ingress.yaml
git commit -m "test: add adversarial routing state corpus"
git add -- test/eval_corpus/intent_adversarial/cases/knowledge_interference.yaml test/eval_corpus/intent_adversarial/cases/capability_catalog.yaml test/eval_corpus/intent_adversarial/cases/expression_injection.yaml test/eval_corpus/intent_adversarial/journey_links.yaml
git commit -m "test: add adversarial routing interference corpus"
```

### Task 15: 诚实标注历史 RoutingBench 的 `domain_hit_rate`

**Files:**
- Modify: `test/routing_bench.py`
- Create: `test/test_routing_bench_metric.py`
- Modify: `test/README.md`
- Modify: `docs/reviews/eval/README.md`

- [ ] **Step 1: 写“部分命中仍为历史绿，但不是 exact”测试**

```python
# test/test_routing_bench_metric.py
from routing_bench import domain_hit


def test_domain_hit_preserves_legacy_any_domain_semantics():
    assert domain_hit(["info.weather"], {"info", "nearby"}) is True


def test_domain_hit_rejects_no_expected_domain_overlap():
    assert domain_hit(["chitchat.talk"], {"info", "nearby"}) is False
```

- [ ] **Step 2: 抽出命名函数并保留历史判断**

```python
# test/routing_bench.py
def domain_hit(actual_intents: list[str], expected_domains: set[str]) -> bool:
    """历史趋势口径：任一期望 domain 命中；不代表组合计划完整。"""
    return bool(_domains_of(actual_intents) & set(expected_domains))
```

把 `_drive()` 原来的交集表达式替换成该函数；报告 `meta.metrics.domain_hit_rate` 取原 `overall.pass_rate`。控制台标题改为 `domain_hit_rate（历史任一期望域命中）`，不要再称“域级准确率”或 N1 exact accuracy。既有 case pass/fail 和 baseline 不发生变化。

- [ ] **Step 3: 文档并排解释两把尺子**

`test/README.md` 与 `docs/reviews/eval/README.md` 明确：RoutingBench 的 `domain_hit_rate` 用于历史趋势；新对抗套件的 `exact_plan_set_rate` 用于全必要组、禁选、额外项、依赖和槽位。不得把两者直接作数值升降比较。

- [ ] **Step 4: 跑历史口径回归并提交**

Run: `python -m pytest test/test_routing_bench_metric.py -q`

Expected: `2 passed`。

Run: `python test/routing_bench.py`

Expected: 零成本车道 exit 0；案例计数和跳过计数与修改前相同，仅指标名称/说明变化。

```powershell
git add -- test/routing_bench.py test/test_routing_bench_metric.py test/README.md docs/reviews/eval/README.md
git commit -m "docs: distinguish routing domain hit metric"
```

### Task 16: 跑 reference provider 发现轨并提出 stable 晋级清单

**Files:**
- Modify: `test/eval_corpus/intent_adversarial/cases/*.yaml`
- Create: `docs/reviews/eval/_ci-run-intent-adversarial.json`（ignored runtime artifact）
- Create: `docs/reviews/eval/_ci-run-intent-adversarial.md`（ignored runtime artifact）

- [ ] **Step 1: 只读确认真栈与 reference provider**

Run: `docker compose -f compose.yaml ps`

Expected: llm-gateway、registry 和所需核心服务 healthy/running。若栈未运行，用根入口 `make up` 启动；不得直接以 `deploy/docker-compose.yaml` 为首文件。

```powershell
$intentEvalActive = (Invoke-RestMethod 'http://localhost:50059/api/llm/providers').active
$intentEvalProvider = $intentEvalActive.provider
$intentEvalModel = $intentEvalActive.model
if ([string]::IsNullOrWhiteSpace($intentEvalProvider) -or
    [string]::IsNullOrWhiteSpace($intentEvalModel) -or
    $intentEvalProvider -eq 'mock') {
    throw 'intent adversarial live evaluation requires a non-mock active provider'
}
```

不写 `.env`；provider 只通过 CLI pin，报告记录 provider:model。

- [ ] **Step 2: 先按声明层跑一次 reviewed 发现轨**

Run:

```powershell
python test/eval_intent_adversarial.py --suite discovery --layer l0 --strict --out-json docs/reviews/eval/_ci-run-intent-adversarial-l0.json --out-md docs/reviews/eval/_ci-run-intent-adversarial-l0.md
python test/eval_intent_adversarial.py --suite discovery --layer l1 --live --provider $intentEvalProvider --model $intentEvalModel --temperature 0.3 --timeout 45 --ablations on-failure --strict --out-json docs/reviews/eval/_ci-run-intent-adversarial.json --out-md docs/reviews/eval/_ci-run-intent-adversarial.md
python test/eval_intent_adversarial.py --suite discovery --layer l2 --live --provider $intentEvalProvider --model $intentEvalModel --temperature 0.3 --timeout 45 --ablations on-failure --strict --out-json docs/reviews/eval/_ci-run-intent-adversarial-l2.json --out-md docs/reviews/eval/_ci-run-intent-adversarial-l2.md
python test/eval_intent_adversarial.py --suite discovery --tag gate_candidate --layer l3 --live --provider $intentEvalProvider --model $intentEvalModel --strict --out-json docs/reviews/eval/_ci-run-intent-adversarial-l3.json --out-md docs/reviews/eval/_ci-run-intent-adversarial-l3.md
```

Expected: L0 可扫描 candidate/reviewed/stable 的 schema 与确定性组件，但质量结论分状态；L1/L2 只执行 reviewed/stable，且每条只在 `tags.layers` 声明的层执行。普通首次失败补到 3 次，高风险固定 3 次；provider 漂移或基础设施错误使退出码非 0。发现语义失败时退出码 1 是有效结果，不得通过改 gold 或关断言把它变绿。

另起一个全新进程只跑 A7 冷启动探针，单独出报告，不并入 warm 数字：

```powershell
python test/eval_intent_adversarial.py --suite discovery --tag knowledge_interference --layer l1 --live --provider $intentEvalProvider --model $intentEvalModel --temperature 0.3 --timeout 45 --retrieval-state cold --ablations off --out-json docs/reviews/eval/_ci-run-intent-cold-start.json --out-md docs/reviews/eval/_ci-run-intent-cold-start.md
```

- [ ] **Step 3: 复核每个红灯的 gold 与首偏离证据**

逐条分成：`product_defect`、`gold_error`、`capability_gap`、`unstable`、`infrastructure_error`。gold 修正必须回到来源/boundary/真实能力核对并留下 `gold_revision_reason`；一旦 absolute/relation gold 改动，立即降回 candidate 并移除旧 `reviewed_by/reviewed_at`，只有新 gold 再获泓舟批准才能回 reviewed。产品缺陷本批不修，另写后续修复清单；unstable 不晋级；基础设施错误修复运行环境后重跑，不能计产品失败。

- [ ] **Step 4: 对预先标记的 140 条 gate 候选按层做第二个独立进程复跑**

Run:

```powershell
python test/eval_intent_adversarial.py --suite discovery --tag gate_candidate --layer l0 --strict --out-json docs/reviews/eval/_ci-run-intent-gate-candidates-l0-r2.json --out-md docs/reviews/eval/_ci-run-intent-gate-candidates-l0-r2.md
python test/eval_intent_adversarial.py --suite discovery --tag gate_candidate --layer l1 --live --provider $intentEvalProvider --model $intentEvalModel --temperature 0.3 --timeout 45 --ablations off --strict --out-json docs/reviews/eval/_ci-run-intent-gate-candidates-r2.json --out-md docs/reviews/eval/_ci-run-intent-gate-candidates-r2.md
python test/eval_intent_adversarial.py --suite discovery --tag gate_candidate --layer l2 --live --provider $intentEvalProvider --model $intentEvalModel --temperature 0.3 --timeout 45 --ablations off --strict --out-json docs/reviews/eval/_ci-run-intent-gate-candidates-l2-r2.json --out-md docs/reviews/eval/_ci-run-intent-gate-candidates-l2-r2.md
python test/eval_intent_adversarial.py --suite discovery --tag gate_candidate --layer l3 --live --provider $intentEvalProvider --model $intentEvalModel --strict --out-json docs/reviews/eval/_ci-run-intent-gate-candidates-l3-r2.json --out-md docs/reviews/eval/_ci-run-intent-gate-candidates-l3-r2.md
```

晋级提案条件：案例声明的每一层（含 l3）在两次独立进程均通过；语义签名一致；高风险每次均 3/3 且零危险路由；provider/model/资产指纹相同。只有 L0 的确定性案例把 stabilized provider 记为 `deterministic`；含 L1/L2/L3 的案例记实际 provider:model。失败或不稳定保持 reviewed，不能用更容易的新案例悄悄替换以维持 140 这个数字。

- [ ] **Step 5: 生成晋级审阅包并停在人工批准点**

审阅包列出每个候选的两轮签名、风险、cohort、attack、absolute gold、relation、L0/L1 结果和排除原因。只有泓舟明确批准后，才用 `apply_patch` 把批准项从 reviewed 改为 stable，并在 `provenance` 内补 `stabilized_provider`、`stabilized_at`、`evidence_report`；不能由脚本自动改 corpus。

若批准后 stable 数量少于 120，明确报告门禁规模未达设计 DoD，并保留困难 reviewed 红灯；本批不修生产路由、不伪造 120 条完成。

- [ ] **Step 6: 提交获批的 lifecycle 变化**

```powershell
git add -- test/eval_corpus/intent_adversarial/cases
git commit -m "test: promote stable adversarial routing cases"
```

ignored 的 `_ci-run-*` 证据不暂存、不删除，留待本地审计。

### Task 17: 跑 L0/L1/L2/L3 验收并写首份合资格 baseline

**Files:**
- Create: `docs/reviews/eval/baseline_intent_adversarial.json`
- Create: `docs/reviews/eval/baseline_intent_adversarial.md`
- Modify: `test/eval_corpus/intent_adversarial/journey_links.yaml`

- [ ] **Step 1: stable L0 必须全绿**

Run: `python test/eval_intent_adversarial.py --suite gate --layer l0 --strict`

Expected: exit 0；schema、inventory、boundary、family、Edge、Hint、检索全绿；stable L0 通过率 100%，高风险 forbidden=0、确认前副作用=0。

- [ ] **Step 2: stable L1/L2 使用同一个 provider lock**

Run:

```powershell
$intentEvalActive = (Invoke-RestMethod 'http://localhost:50059/api/llm/providers').active
$intentEvalProvider = $intentEvalActive.provider
$intentEvalModel = $intentEvalActive.model
if ([string]::IsNullOrWhiteSpace($intentEvalProvider) -or
    [string]::IsNullOrWhiteSpace($intentEvalModel) -or
    $intentEvalProvider -eq 'mock') {
    throw 'intent adversarial gate requires a non-mock provider and explicit model'
}
python test/eval_intent_adversarial.py --suite gate --layer l1 --live --provider $intentEvalProvider --model $intentEvalModel --temperature 0.3 --timeout 45 --ablations on-failure --strict
python test/eval_intent_adversarial.py --suite gate --layer l2 --live --provider $intentEvalProvider --model $intentEvalModel --temperature 0.3 --timeout 45 --ablations on-failure --strict
```

Expected: 两条命令 exit 0；普通 stable 不新增失败/不稳定；高风险三轮零危险错误；L2 的 Agent/VAL 全为 safe fake/spy，没有真实写操作。

- [ ] **Step 3: 只跑已建立链接的关键 L3 journeys**

```powershell
$intentJourneyIds = python test/eval_intent_adversarial.py --suite gate --layer l3 --list
$env:E2E_JOURNEY_IDS = $intentJourneyIds.Trim()
try {
    python scripts/run_e2e.py --id e2e_journeys --provider $intentEvalProvider --model $intentEvalModel --stale-policy warn
} finally {
    Remove-Item Env:E2E_JOURNEY_IDS -ErrorAction SilentlyContinue
}
```

Expected: 选中 journey 全部 PASS；报告明确这是精选跨进程链，不外推到 450 条 discovery。若没有 journey link，命令以配置错误退出，不能把空选集算绿。

- [ ] **Step 4: 写正式 baseline**

Run:

```powershell
python test/eval_intent_adversarial.py --suite gate --layer all --live --provider $intentEvalProvider --model $intentEvalModel --temperature 0.3 --timeout 45 --ablations on-failure --strict --write-baseline --baseline docs/reviews/eval/baseline_intent_adversarial.json --out-json docs/reviews/eval/baseline_intent_adversarial.json --out-md docs/reviews/eval/baseline_intent_adversarial.md
```

Expected: 只有 provider locked、零漂移、资产指纹完整、全 stable、零 unstable、非空 L3 选集时写文件并 exit 0；否则正式 baseline 时间戳和内容保持不变，普通 `_ci-run-*` 报告说明拒绝原因。

- [ ] **Step 5: 跑新增与全量 Python 回归**

Run:

```powershell
python -m pytest test/test_intent_adversarial_contract.py test/test_intent_adversarial_judge.py test/test_intent_adversarial_trace.py test/test_intent_adversarial_runtime.py test/test_intent_adversarial_report.py test/test_eval_intent_adversarial_cli.py test/test_build_intent_adversarial_candidates.py test/test_routing_bench_metric.py -q
python -m pytest -q
```

Expected: 两条均 exit 0；全量相对实施前基线 `3654 passed / 11 skipped / 0 failed` 只增加测试，不减少既有 passed，精确新计数写入落地记录而不预先冻结。

- [ ] **Step 6: 提交正式 baseline**

```powershell
git add -- docs/reviews/eval/baseline_intent_adversarial.json docs/reviews/eval/baseline_intent_adversarial.md test/eval_corpus/intent_adversarial/journey_links.yaml
git commit -m "test: baseline adversarial intent routing"
```

### Task 18: 回写文档、局限和最终验证证据

**Files:**
- Modify: `test/README.md`
- Modify: `docs/reviews/eval/README.md`
- Modify: `docs/design/2026-08-02-intent-routing-adversarial-testing.md`
- Modify: `docs/design/README.md`

- [ ] **Step 1: 文档化三个日常入口**

`test/README.md` 写清：零网络 L0、reference-provider L1/L2、精选 L3 的准确命令；说明 candidate 不进 live、seen/unseen 分报、普通失败复跑、高风险固定三轮、真实副作用永不执行。`docs/reviews/eval/README.md` 解释 baseline 资格、逐例 diff、`domain_hit_rate` 与 exact 指标区别、如何复现单案例。

- [ ] **Step 2: 回写设计落地记录**

把设计状态从“已批准，待实施”改成与事实一致的“已落地”或“部分落地”；逐条列出实际文件、语料数、stable 数、reference provider、资产 SHA、L0/L1/L2/L3 结果、全量 pytest、新鲜 commit。任何未达项留在“明确局限/后续工作”，不能把 framework、discovery、gate、baseline 四者混成一个完成结论。

- [ ] **Step 3: 做文档和工作树洁净检查**

Run:

```powershell
git diff --check
rg -n "TODO|TBD|待补|占位|domain accuracy|域级准确率" test/eval_intent_adversarial.py test/support test/eval_corpus/intent_adversarial test/README.md docs/reviews/eval/README.md docs/design/2026-08-02-intent-routing-adversarial-testing.md
git status --short
```

Expected: `git diff --check` 无输出；没有实现占位或旧误导指标名；status 只包含本任务明确文件和 ignored runtime 报告。

- [ ] **Step 4: 最终定向复验**

Run:

```powershell
python -m pytest test/test_intent_adversarial_contract.py test/test_intent_adversarial_judge.py test/test_intent_adversarial_trace.py test/test_intent_adversarial_runtime.py test/test_intent_adversarial_report.py test/test_eval_intent_adversarial_cli.py test/test_build_intent_adversarial_candidates.py test/test_routing_bench_metric.py -q
python test/eval_intent_adversarial.py --suite gate --layer l0 --strict
```

Expected: all PASS / exit 0。

- [ ] **Step 5: 提交文档收口，不 push**

```powershell
git add -- test/README.md docs/reviews/eval/README.md docs/design/2026-08-02-intent-routing-adversarial-testing.md docs/design/README.md
git commit -m "docs: land adversarial intent testing"
git status --short
git log -1 --oneline
```

Expected: 专用 worktree 无未提交的 tracked 变更；不执行 `git push`。ignored `_ci-run-*` 报告可保留并在交接中说明。

## 完成定义

- framework、450 条 discovery、人工审核、120–160 条 stable gate、L0/L1/L2/L3 和合资格 baseline 必须分别有证据；任一缺失就按“部分完成”报告。
- 生产 Agent、Skill、Exemplar、Hint、manifest 路由行为在本计划内不因红灯而修改；红灯进入独立修复批次。
- active intent 全部 covered 或有逐 intent 审核豁免；boundary 双向、family 泄漏、schema 全绿。
- 高风险 forbidden route 和确认前副作用均为 0；任何一次危险错误都阻断。
- provider 漂移、资产指纹不完整、unstable 或空 L3 选集都不得生成正式 baseline。
- 不修改 CI/CD、`.env`、secret，不 push，不触发真实车控、支付、消息、删除或公开发布。
