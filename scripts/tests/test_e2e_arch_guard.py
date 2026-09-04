from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts import e2e_contract as contract


REPO_ROOT = Path(__file__).resolve().parents[2]
ArchitectureGuardError = getattr(
    contract,
    "ArchitectureGuardError",
    type("MissingArchitectureGuardError", (RuntimeError,), {}),
)


def _api(name: str):
    assert hasattr(contract, name), f"missing architecture guard API: {name}"
    return getattr(contract, name)


def load_architecture_vocabulary(*args, **kwargs):
    return _api("load_architecture_vocabulary")(*args, **kwargs)


def guard_architecture(*args, **kwargs):
    return _api("guard_architecture")(*args, **kwargs)


def assert_architecture_guard(*args, **kwargs):
    return _api("assert_architecture_guard")(*args, **kwargs)


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _repo(tmp_path: Path) -> Path:
    _write(
        tmp_path,
        "agents/sample/manifest.yaml",
        """
agent_id: sample-agent
capabilities:
  - intent: sample.query
route_hints:
  - intent: sample.query
""",
    )
    _write(
        tmp_path,
        "orchestrator/edge/knowledge/commands.yaml",
        """
objects:
  sample_panel:
    operates: [open]
""",
    )
    _write(
        tmp_path,
        "skills/guides/sample.yaml",
        """
name: sample
type: guide
golden:
  - text: sample
    expect_intents: [sample.query]
""",
    )
    _write(
        tmp_path,
        "runtime/proactive.py",
        """
async def publish_proactive(nc, payload):
    await nc.publish(payload)
""",
    )
    _write(
        tmp_path,
        "orchestrator/cloud/verify.py",
        '"""sample.query in a module docstring is allowed."""\n',
    )
    _write(
        tmp_path,
        "orchestrator/cloud/executor.py",
        """
class DagExecutor:
    async def _verify_outcome(self, step, result):
        return result

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)

    def unrelated_business_compatibility(self):
        return "sample.query"
""",
    )
    _write(
        tmp_path,
        "proactive/governor.py",
        '"""producer types in docstrings are allowed."""\n',
    )
    _write(
        tmp_path,
        "llm-gateway/s2s/session.py",
        "# sample.query in a comment is allowed\n",
    )
    return tmp_path


def _add_brandnew_manifest(root: Path) -> None:
    _write(
        root,
        "agents/brandnew/manifest.yaml",
        """
agent_id: brand-new-agent
capabilities:
  - intent: brandnew.query
route_hints: []
""",
    )


def test_vocabulary_is_dynamic_across_manifests_commands_skills_and_producers(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

NOTICE = "brandnew_notice"

def make_payload():
    local = {"type": NOTICE}
    return local

async def emit(nc):
    payload = make_payload()
    await publish_proactive(nc, payload)
""",
    )

    vocabulary = load_architecture_vocabulary(root)

    assert {
        "brand-new-agent",
        "brandnew.query",
        "brandnew",
        "sample_panel",
    } <= vocabulary.domain_terms
    assert "brandnew_notice" in vocabulary.proactive_types


def test_skill_plan_repair_source_path_is_not_an_intent_namespace(tmp_path: Path):
    """`data.items...` 是 StepResult 路径，不能污染业务词表。"""
    root = _repo(tmp_path)
    _write(
        root,
        "skills/guides/sample.yaml",
        """
name: sample
type: guide
plan_repairs:
  - kind: dependency_slot_ref
    trigger_any: [first]
    producer_intent: repairproducer.query
    consumer_intent: repairconsumer.run
    slot: item
    source_path: data.items.0.name
golden:
  - text: sample
    expect_intents: [sample.query]
""",
    )
    _write(
        root,
        "orchestrator/cloud/verify.py",
        "def passthrough(data):\n    return data\n",
    )

    vocabulary = load_architecture_vocabulary(root)

    assert "sample" in vocabulary.identifier_terms
    assert {"repairproducer", "repairconsumer"} <= vocabulary.identifier_terms
    assert "data" not in vocabulary.identifier_terms
    assert guard_architecture(root) == ()


def test_skill_slot_ref_paths_are_not_intent_namespaces(tmp_path: Path):
    """Trusted StepResult paths must not become executable business terms."""
    root = _repo(tmp_path)
    _write(
        root,
        "skills/guides/sample.yaml",
        """
name: sample
type: guide
knowledge: |
  Read the trusted data.items.0 result path before running the consumer.
few_shots:
  - user: choose the first result
    plan: {"steps":[{"id":"s1","intent":"jsonconsumer.run","slot_refs":{"item":"s1.data.items.0.name"}}]}
golden:
  - text: sample
    expect_intents: [sample.query]
""",
    )
    _write(
        root,
        "skills/exemplars/sample.yaml",
        """
domain: sample
exemplars:
  - text: order from the chosen store
    plan:
      - id: store
        agent: nearby
        intent: nearby.search
      - id: order
        agent: merchant
        intent: structuredconsumer.run
        depends_on: [store]
        slot_refs:
          store_name: store.data.items.0.name
""",
    )
    _write(
        root,
        "orchestrator/cloud/verify.py",
        "def passthrough(store, data, s1):\n    return store, data, s1\n",
    )

    vocabulary = load_architecture_vocabulary(root)

    assert {"jsonconsumer", "structuredconsumer"} <= vocabulary.identifier_terms
    assert {"store", "data", "s1"}.isdisjoint(vocabulary.identifier_terms)
    assert guard_architecture(root) == ()


def test_manifest_null_route_hints_is_an_empty_retired_hint_list(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "agents/sample/manifest.yaml",
        """
agent_id: sample-agent
capabilities:
  - intent: sample.query
route_hints:
""",
    )

    vocabulary = load_architecture_vocabulary(root)

    assert "sample.query" in vocabulary.domain_terms


def test_new_manifest_and_executable_central_branch_fail_without_guard_edit(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(
        root,
        "orchestrator/cloud/verify.py",
        """
def evaluate(intent):
    if (
        intent
        == "brandnew.query"
    ):
        return "special"
    return "generic"
""",
    )

    violations = guard_architecture(root)

    assert any(
        item.path == "orchestrator/cloud/verify.py"
        and item.term == "brandnew.query"
        and item.line == 5
        for item in violations
    )
    with pytest.raises(
        ArchitectureGuardError,
        match=r"orchestrator/cloud/verify\.py:5:\d+.*brandnew\.query",
    ):
        assert_architecture_guard(root)


def test_comments_docstrings_and_unrelated_executor_functions_do_not_fail(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(
        root,
        "orchestrator/cloud/verify.py",
        '''
"""brandnew.query is documentation, not executable policy."""

# brandnew.query remains legal in an explanatory comment.
def evaluate(value):
    """The example brandnew.query is also legal here."""
    return value
''',
    )

    assert guard_architecture(root) == ()


@pytest.mark.parametrize(
    ("source", "node_kind", "term"),
    [
        ('VALUE = "brandnew.query"\n', "Constant", "brandnew.query"),
        (
            'def f(value):\n    return f"brandnew.{value}"\n',
            "JoinedStr",
            "brandnew",
        ),
        ('def f():\n    return {"brandnew.query"}\n', "Set", "brandnew.query"),
        (
            'def f(value):\n'
            '    match value:\n'
            '        case "brandnew.query":\n'
            '            return True\n',
            "MatchValue",
            "brandnew.query",
        ),
        (
            'def f(value):\n'
            '    return value == "brandnew.query"\n',
            "Compare",
            "brandnew.query",
        ),
        ('def f(brandnew):\n    return brandnew\n', "arg", "brandnew"),
        ('def f(brandnew_intent):\n    return None\n', "arg", "brandnew"),
        ('def f(intent_brandnew):\n    return None\n', "arg", "brandnew"),
        ('def f(brandnewIntent):\n    return None\n', "arg", "brandnew"),
        ('def f(sample_panel):\n    return None\n', "arg", "sample_panel"),
    ],
)
def test_ast_guard_detects_all_executable_semantic_forms(
    tmp_path: Path,
    source: str,
    node_kind: str,
    term: str,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(root, "orchestrator/cloud/verify.py", source)

    violations = guard_architecture(root)

    assert any(
        item.node_kind == node_kind and item.term == term
        for item in violations
    ), violations


def test_identifier_spellings_in_comments_and_docstrings_are_ignored(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(
        root,
        "orchestrator/cloud/verify.py",
        '''
"""brandnew_intent intent_brandnew brandnewIntent sample_panel"""

# def f(brandnew_intent, sample_panel): ...
def generic(value):
    """intent_brandnew and brandnewIntent remain documentation."""
    return value
''',
    )

    assert guard_architecture(root) == ()


@pytest.mark.parametrize(
    ("prefix", "expression"),
    [
        ("", 'f"{\'brandnew.query\'}"'),
        ('BRANDNEW = "brandnew.query"\n', 'f"{BRANDNEW}"'),
    ],
)
def test_joined_string_formatted_values_cannot_hide_static_business_terms(
    tmp_path: Path,
    prefix: str,
    expression: str,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(
        root,
        "orchestrator/cloud/verify.py",
        prefix + f"def evaluate():\n    return {expression}\n",
    )

    violations = guard_architecture(root)

    assert any(item.term == "brandnew.query" for item in violations), violations


@pytest.mark.parametrize(
    ("argument", "term"),
    [
        ("seat_command", "seat"),
        ("battery_intent", "battery"),
        ("media_type", "media"),
        ("commandSeat", "seat"),
    ],
)
def test_single_token_command_arguments_require_a_structural_role(
    tmp_path: Path,
    argument: str,
    term: str,
):
    root = _repo(tmp_path)
    _write(
        root,
        "orchestrator/edge/knowledge/commands.yaml",
        """
objects:
  seat:
    operates: [heat]
  battery:
    operates: [query]
  media:
    operates: [play]
  window:
    operates: [open]
""",
    )
    _write(
        root,
        "orchestrator/cloud/verify.py",
        f"def evaluate({argument}):\n    return None\n",
    )

    violations = guard_architecture(root)

    assert any(
        item.node_kind == "arg" and item.term == term
        for item in violations
    ), violations


def test_single_token_command_without_a_role_is_not_an_argument_violation(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "orchestrator/edge/knowledge/commands.yaml",
        """
objects:
  window:
    operates: [open]
""",
    )
    _write(
        root,
        "orchestrator/cloud/verify.py",
        """
def evaluate(window, merge_window_ms):
    return window, merge_window_ms
""",
    )

    assert guard_architecture(root) == ()


def test_only_executor_verify_decision_functions_are_guarded(tmp_path: Path):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    path = root / "orchestrator/cloud/executor.py"
    source = path.read_text(encoding="utf-8")
    path.write_text(
        source.replace(
            "return result",
            'return "brandnew.query" if step else result',
        ),
        encoding="utf-8",
    )

    violations = guard_architecture(root)

    assert any(
        item.path == "orchestrator/cloud/executor.py"
        and item.function == "_verify_outcome"
        for item in violations
    )
    assert not any(item.function == "unrelated_business_compatibility" for item in violations)


@pytest.mark.parametrize(
    "source",
    [
        """
BRANDNEW = "brandnew.query"

class DagExecutor:
    async def _verify_outcome(self, value):
        return value == BRANDNEW

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
        """
class DagExecutor:
    BRANDNEW = "brandnew.query"

    async def _verify_outcome(self, value):
        return value == self.BRANDNEW

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
        """
def business_helper(value):
    return value == "brandnew.query"

def unrelated_helper():
    return "sample.query"

class DagExecutor:
    async def _verify_outcome(self, value):
        return business_helper(value)

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
        """
class DagExecutor:
    def _business_helper(self, value):
        return value == "brandnew.query"

    def unrelated_helper(self):
        return "sample.query"

    async def _verify_outcome(self, value):
        return self._business_helper(value)

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
        """
class DagExecutor:
    async def _verify_outcome(self, value):
        def nested_helper(intent):
            return intent == "brandnew.query"
        return nested_helper(value)

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
    ],
)
def test_executor_guard_follows_reachable_helpers_and_constants(
    tmp_path: Path,
    source: str,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(root, "orchestrator/cloud/executor.py", source)

    violations = guard_architecture(root)

    assert any(
        item.path == "orchestrator/cloud/executor.py"
        and item.term == "brandnew.query"
        for item in violations
    ), violations
    assert not any(item.term == "sample.query" for item in violations), violations


def test_executor_guard_does_not_scan_unreachable_helpers_and_constants(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(
        root,
        "orchestrator/cloud/executor.py",
        """
UNUSED = "brandnew.query"

def unused_helper():
    return UNUSED

class DagExecutor:
    UNUSED = "brandnew.query"

    def unused_method(self):
        return self.UNUSED

    async def _verify_outcome(self, value):
        return value

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
    )

    assert guard_architecture(root) == ()


def test_executor_guard_follows_local_callable_alias_chain_only_when_called(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(
        root,
        "orchestrator/cloud/executor.py",
        """
def business_helper(value):
    return value == "brandnew.query"

def unrelated_helper(value):
    return value == "sample.query"

class DagExecutor:
    async def _verify_outcome(self, value):
        unused = unrelated_helper
        helper = business_helper
        delegated = helper
        return delegated(value)

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
    )

    violations = guard_architecture(root)

    assert any(item.term == "brandnew.query" for item in violations), violations
    assert not any(item.term == "sample.query" for item in violations), violations


@pytest.mark.parametrize("business_first", [True, False])
def test_executor_guard_follows_every_reaching_callable_alias_branch(
    tmp_path: Path,
    business_first: bool,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    first = "business_helper" if business_first else "generic_helper"
    second = "generic_helper" if business_first else "business_helper"
    _write(
        root,
        "orchestrator/cloud/executor.py",
        f"""
def business_helper(value):
    return value == "brandnew.query"

def generic_helper(value):
    return bool(value)

def unrelated_helper(value):
    return value == "sample.query"

class DagExecutor:
    async def _verify_outcome(self, value, flag):
        unused = unrelated_helper
        if flag:
            helper = {first}
        else:
            helper = {second}
        return helper(value)

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
    )

    violations = guard_architecture(root)

    assert any(item.term == "brandnew.query" for item in violations), violations
    assert not any(item.term == "sample.query" for item in violations), violations


def test_executor_guard_does_not_follow_unreached_static_alias_helpers(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(
        root,
        "orchestrator/cloud/executor.py",
        """
def business_helper(value):
    return value == "brandnew.query"

def first_generic(value):
    return bool(value)

def second_generic(value):
    return value is not None

class DagExecutor:
    async def _verify_outcome(self, value, flag):
        unused = business_helper
        if flag:
            helper = first_generic
        else:
            helper = second_generic
        return helper(value)

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
    )

    assert guard_architecture(root) == ()


@pytest.mark.parametrize(
    "control_flow",
    [
        """
        helper = generic_helper
        match flag:
            case True:
                helper = business_helper
""",
        """
        helper = generic_helper
        try:
            helper = business_helper
        except RuntimeError:
            helper = generic_helper
""",
        """
        helper = generic_helper
        try:
            helper = generic_helper
        except RuntimeError:
            helper = business_helper
""",
        """
        helper = generic_helper
        try:
            helper = generic_helper
        except RuntimeError:
            helper = business_helper
        else:
            helper = generic_helper
""",
        """
        helper = generic_helper
        try:
            helper = generic_helper
        finally:
            helper = business_helper
""",
    ],
)
def test_executor_guard_follows_match_and_try_callable_aliases(
    tmp_path: Path,
    control_flow: str,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(
        root,
        "orchestrator/cloud/executor.py",
        """
def business_helper(value):
    return value == "brandnew.query"

def generic_helper(value):
    return bool(value)

class DagExecutor:
    async def _verify_outcome(self, value, flag):
"""
        + control_flow
        + """
        return helper(value)

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
    )

    violations = guard_architecture(root)

    assert any(item.term == "brandnew.query" for item in violations), violations


def test_executor_guard_finally_overwrite_removes_unreached_alias(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(
        root,
        "orchestrator/cloud/executor.py",
        """
def business_helper(value):
    return value == "brandnew.query"

def generic_helper(value):
    return bool(value)

class DagExecutor:
    async def _verify_outcome(self, value):
        try:
            helper = business_helper
        except RuntimeError:
            helper = business_helper
        finally:
            helper = generic_helper
        return helper(value)

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
    )

    assert guard_architecture(root) == ()


def test_executor_guard_match_and_try_ignore_unreached_business_helper(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(
        root,
        "orchestrator/cloud/executor.py",
        """
def business_helper(value):
    return value == "brandnew.query"

def generic_helper(value):
    return bool(value)

class DagExecutor:
    async def _verify_outcome(self, value, flag):
        unused = business_helper
        match flag:
            case True:
                helper = generic_helper
            case _:
                helper = generic_helper
        try:
            helper = generic_helper
        except RuntimeError:
            helper = generic_helper
        return helper(value)

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
    )

    assert guard_architecture(root) == ()


def test_executor_guard_exhaustive_match_overwrites_prior_alias(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _add_brandnew_manifest(root)
    _write(
        root,
        "orchestrator/cloud/executor.py",
        """
def business_helper(value):
    return value == "brandnew.query"

def generic_helper(value):
    return bool(value)

class DagExecutor:
    async def _verify_outcome(self, value, flag):
        helper = business_helper
        match flag:
            case True:
                helper = generic_helper
            case _:
                helper = generic_helper
        return helper(value)

    def _evaluate(self, value):
        return value

    def _should_report(self, value):
        return bool(value)
""",
    )

    assert guard_architecture(root) == ()


def test_proactive_type_flows_through_local_dict_constant_and_helper(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

NOTICE = "helper_notice"

def envelope():
    payload = {"type": NOTICE, "speech": "hello"}
    return payload

async def emit(nc):
    local = envelope()
    await publish_proactive(nc, local)
""",
    )
    _write(
        root,
        "proactive/governor.py",
        """
def decide(payload):
    kind = payload.get("type")
    if kind == "helper_notice":
        return "special"
    return "generic"
""",
    )

    violations = guard_architecture(root)

    assert any(
        item.path == "proactive/governor.py"
        and item.term == "helper_notice"
        for item in violations
    )


def test_proactive_callback_name_is_discovered_from_constructor_data_flow(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import P_ADVISORY, publish_proactive

class Producer:
    def __init__(self, publish):
        self.deliver = publish

    async def emit(self):
        payload = {"type": "callback_notice", "priority": P_ADVISORY}
        await self.deliver(self.nc, payload)

producer = Producer(publish_proactive)
""",
    )

    vocabulary = load_architecture_vocabulary(root)

    assert "callback_notice" in vocabulary.proactive_types


@pytest.mark.parametrize("dynamic_first", [True, False])
def test_proactive_branch_assignments_are_all_resolved_or_fail_closed(
    tmp_path: Path,
    dynamic_first: bool,
):
    root = _repo(tmp_path)
    branches = (
        """
    if flag:
        payload = {"type": runtime_kind}
    else:
        payload = {"type": "safe_notice"}
"""
        if dynamic_first
        else
        """
    if flag:
        payload = {"type": "safe_notice"}
    else:
        payload = {"type": runtime_kind}
"""
    )
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

async def emit(nc, runtime_kind, flag):
"""
        + branches
        + """
    await publish_proactive(nc, payload)
""",
    )

    with pytest.raises(
        ArchitectureGuardError,
        match=r"producer\.py:\d+:\d+.*proactive type.*static",
    ):
        load_architecture_vocabulary(root)


def test_proactive_branch_assignments_collect_every_static_definition(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

async def emit(nc, flag):
    if flag:
        payload = {"type": "first_notice"}
    else:
        payload = {"type": "second_notice"}
    await publish_proactive(nc, payload)
""",
    )

    assert load_architecture_vocabulary(root).proactive_types == frozenset({
        "first_notice",
        "second_notice",
    })


@pytest.mark.parametrize("dynamic_first", [True, False])
def test_module_proactive_branch_assignments_are_all_fail_closed(
    tmp_path: Path,
    dynamic_first: bool,
):
    root = _repo(tmp_path)
    branches = (
        """
PAYLOAD = {"type": RUNTIME_KIND}
if FLAG:
    PAYLOAD = {"type": "safe_notice"}
"""
        if dynamic_first
        else
        """
PAYLOAD = {"type": "safe_notice"}
if FLAG:
    PAYLOAD = {"type": RUNTIME_KIND}
"""
    )
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

FLAG = True
RUNTIME_KIND = input()
"""
        + branches
        + """
async def emit(nc):
    await publish_proactive(nc, PAYLOAD)
""",
    )

    with pytest.raises(
        ArchitectureGuardError,
        match=r"producer\.py:\d+:\d+.*proactive type.*static",
    ):
        load_architecture_vocabulary(root)


@pytest.mark.parametrize(
    "mutation",
    [
        'payload["type"] = runtime_kind',
        'payload.update({"type": runtime_kind})',
        'payload |= {"type": runtime_kind}',
    ],
)
def test_proactive_payload_mutation_is_fail_closed(
    tmp_path: Path,
    mutation: str,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

async def emit(nc, runtime_kind):
    payload = {"type": "safe_notice"}
    """
        + mutation
        + """
    await publish_proactive(nc, payload)
""",
    )

    with pytest.raises(
        ArchitectureGuardError,
        match=r"producer\.py:\d+:\d+.*proactive type.*static",
    ):
        load_architecture_vocabulary(root)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            """
from runtime import proactive as p

async def emit(nc):
    payload = {"type": "module_alias_notice"}
    await p.publish_proactive(nc, payload)
""",
            "module_alias_notice",
        ),
        (
            """
from runtime.proactive import publish_proactive

async def deliver(nc, payload):
    await publish_proactive(nc, payload)

async def relay(nc, payload):
    await deliver(nc, payload)

async def emit(nc):
    payload = {"type": "wrapper_notice"}
    await relay(nc, payload)
""",
            "wrapper_notice",
        ),
        (
            """
from runtime.proactive import publish_proactive

class Producer:
    def __init__(self, sink):
        self.deliver = sink

    async def emit(self):
        payload = {"type": "constructor_notice"}
        await self.deliver(self.nc, payload)

producer = Producer(publish_proactive)
""",
            "constructor_notice",
        ),
        (
            """
from runtime.proactive import publish_proactive

async def deliver(callback, nc, payload):
    await callback(nc, payload)

async def emit(nc):
    payload = {"type": "callback_parameter_notice"}
    await deliver(publish_proactive, nc, payload)
""",
            "callback_parameter_notice",
        ),
    ],
)
def test_proactive_reverse_call_graph_reaches_real_sink(
    tmp_path: Path,
    source: str,
    expected: str,
):
    root = _repo(tmp_path)
    _write(root, "producer.py", source)

    vocabulary = load_architecture_vocabulary(root)

    assert expected in vocabulary.proactive_types


@pytest.mark.parametrize(
    "source",
    [
        """
from runtime import proactive as p
async def emit(nc, runtime_kind):
    payload = {"type": runtime_kind}
    await p.publish_proactive(nc, payload)
""",
        """
from runtime.proactive import publish_proactive
async def deliver(nc, payload):
    await publish_proactive(nc, payload)
async def emit(nc, runtime_kind):
    payload = {"type": runtime_kind}
    await deliver(nc, payload)
""",
        """
from runtime.proactive import publish_proactive
class Producer:
    def __init__(self, sink):
        self.deliver = sink
    async def emit(self, runtime_kind):
        payload = {"type": runtime_kind}
        await self.deliver(self.nc, payload)
producer = Producer(publish_proactive)
""",
        """
from runtime.proactive import publish_proactive
async def deliver(callback, nc, payload):
    await callback(nc, payload)
async def emit(nc, runtime_kind):
    payload = {"type": runtime_kind}
    await deliver(publish_proactive, nc, payload)
""",
    ],
)
def test_reachable_dynamic_proactive_type_is_fail_closed_for_every_sink_shape(
    tmp_path: Path,
    source: str,
):
    root = _repo(tmp_path)
    _write(root, "producer.py", source)

    with pytest.raises(
        ArchitectureGuardError,
        match=r"producer\.py:\d+:\d+.*proactive type.*static",
    ):
        load_architecture_vocabulary(root)


def test_cross_module_static_wrapper_is_followed(tmp_path: Path):
    root = _repo(tmp_path)
    _write(
        root,
        "sink_adapter.py",
        """
from runtime.proactive import publish_proactive
async def deliver(nc, payload):
    await publish_proactive(nc, payload)
""",
    )
    _write(
        root,
        "producer.py",
        """
from sink_adapter import deliver
async def emit(nc):
    payload = {"type": "cross_module_notice"}
    await deliver(nc, payload)
""",
    )

    assert (
        "cross_module_notice"
        in load_architecture_vocabulary(root).proactive_types
    )


def test_unrelated_callback_is_not_a_proactive_sink(tmp_path: Path):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import P_ADVISORY
async def run(callback, runtime_kind):
    await callback({"type": runtime_kind, "priority": P_ADVISORY})
""",
    )

    assert load_architecture_vocabulary(root).proactive_types == frozenset()


def test_callback_wrapper_pairs_callable_and_payload_per_callsite(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

async def unrelated(nc, payload):
    return None

async def deliver(callback, nc, payload):
    await callback(nc, payload)

async def emit(nc, runtime_kind):
    await deliver(
        publish_proactive,
        nc,
        {"type": "reachable_callback_notice"},
    )
    await deliver(unrelated, nc, {"type": runtime_kind})
""",
    )

    assert load_architecture_vocabulary(root).proactive_types == frozenset({
        "reachable_callback_notice",
    })


def test_constructor_callback_pairs_instance_binding_and_payload_callsite(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

async def unrelated(nc, payload):
    return None

class Producer:
    def __init__(self, sink):
        self.deliver = sink

    async def emit(self, nc, payload):
        await self.deliver(nc, payload)

live = Producer(publish_proactive)
dead = Producer(unrelated)

async def run(nc, runtime_kind):
    await live.emit(nc, {"type": "reachable_instance_notice"})
    await dead.emit(nc, {"type": runtime_kind})
""",
    )

    assert load_architecture_vocabulary(root).proactive_types == frozenset({
        "reachable_instance_notice",
    })


def test_function_local_constructor_pairs_instance_and_payload_callsite(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

async def unrelated(nc, payload):
    return None

class Producer:
    def __init__(self, sink):
        self.deliver = sink

    async def emit(self, nc, payload):
        await self.deliver(nc, payload)

async def run(nc, runtime_kind):
    live = Producer(publish_proactive)
    dead = Producer(unrelated)
    await live.emit(nc, {"type": "local_live_notice"})
    await dead.emit(nc, {"type": runtime_kind})
""",
    )

    assert load_architecture_vocabulary(root).proactive_types == frozenset({
        "local_live_notice",
    })


def test_constructor_factory_preserves_sink_and_dynamic_payload_fail_closed(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

class Producer:
    def __init__(self, sink):
        self.deliver = sink

    async def emit(self, nc, payload):
        await self.deliver(nc, payload)

def factory(sink):
    return Producer(sink)

live = factory(publish_proactive)

async def run(nc, runtime_kind):
    await live.emit(nc, {"type": runtime_kind})
""",
    )

    with pytest.raises(
        ArchitectureGuardError,
        match=r"producer\.py:\d+:\d+.*proactive type.*static",
    ):
        load_architecture_vocabulary(root)


def test_constructor_default_sink_preserves_dynamic_payload_fail_closed(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

class Producer:
    def __init__(self, sink=publish_proactive):
        self.deliver = sink

    async def emit(self, nc, payload):
        await self.deliver(nc, payload)

live = Producer()

async def run(nc, runtime_kind):
    await live.emit(nc, {"type": runtime_kind})
""",
    )

    with pytest.raises(
        ArchitectureGuardError,
        match=r"producer\.py:\d+:\d+.*proactive type.*static",
    ):
        load_architecture_vocabulary(root)


def test_function_local_import_does_not_shadow_module_sink_alias(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive as send

async def emit(nc, runtime_kind):
    await send(nc, {"type": runtime_kind})

async def unrelated():
    from unrelated import noop as send
    return send
""",
    )

    with pytest.raises(
        ArchitectureGuardError,
        match=r"producer\.py:\d+:\d+.*proactive type.*static",
    ):
        load_architecture_vocabulary(root)


def test_function_local_import_alias_is_resolved_at_each_callsite(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
async def emit(nc, runtime_kind):
    from runtime.proactive import publish_proactive as send
    await send(nc, {"type": runtime_kind})
    from unrelated import noop as send
    await send(nc, {"type": "not_proactive"})
""",
    )

    with pytest.raises(
        ArchitectureGuardError,
        match=r"producer\.py:\d+:\d+.*proactive type.*static",
    ):
        load_architecture_vocabulary(root)


@pytest.mark.parametrize("sink_first", [True, False])
def test_function_branch_import_aliases_include_every_reaching_sink(
    tmp_path: Path,
    sink_first: bool,
):
    root = _repo(tmp_path)
    first = (
        "from runtime.proactive import publish_proactive as send"
        if sink_first
        else "from unrelated import noop as send"
    )
    second = (
        "from unrelated import noop as send"
        if sink_first
        else "from runtime.proactive import publish_proactive as send"
    )
    _write(
        root,
        "producer.py",
        f"""
async def emit(nc, runtime_kind, flag):
    if flag:
        {first}
    else:
        {second}
    await send(nc, {{"type": runtime_kind}})
""",
    )

    with pytest.raises(
        ArchitectureGuardError,
        match=r"producer\.py:\d+:\d+.*proactive type.*static",
    ):
        load_architecture_vocabulary(root)


def test_function_branch_import_aliases_ignore_unrelated_sinks(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
async def emit(nc, runtime_kind, flag):
    if flag:
        from unrelated import first as send
    else:
        from unrelated import second as send
    await send(nc, {"type": runtime_kind})
""",
    )

    assert load_architecture_vocabulary(root).proactive_types == frozenset()


@pytest.mark.parametrize(
    "body",
    [
        """
    if flag:
        from runtime.proactive import publish_proactive as send
        await send(nc, {"type": runtime_kind})
""",
        """
    try:
        from runtime.proactive import publish_proactive as send
        await send(nc, {"type": runtime_kind})
    except RuntimeError:
        pass
""",
        """
    match flag:
        case True:
            from runtime.proactive import publish_proactive as send
            await send(nc, {"type": runtime_kind})
        case _:
            pass
""",
        """
    for _ in range(1):
        from runtime.proactive import publish_proactive as send
        await send(nc, {"type": runtime_kind})
""",
        """
    with context():
        from runtime.proactive import publish_proactive as send
        await send(nc, {"type": runtime_kind})
""",
        """
    if flag:
        match flag:
            case True:
                from runtime.proactive import publish_proactive as send
                await send(nc, {"type": runtime_kind})
""",
    ],
)
def test_function_import_alias_resolves_inside_control_flow_branch(
    tmp_path: Path,
    body: str,
):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
async def emit(nc, runtime_kind, flag):
"""
        + body,
    )

    with pytest.raises(
        ArchitectureGuardError,
        match=r"producer\.py:\d+:\d+.*proactive type.*static",
    ):
        load_architecture_vocabulary(root)


@pytest.mark.parametrize(
    "source",
    [
        """
async def emit(nc, runtime_kind, flag):
    if flag:
        from runtime.proactive import publish_proactive as send
    else:
        from unrelated import noop as send
        await send(nc, {"type": runtime_kind})
""",
        """
async def emit(nc, runtime_kind, flag):
    match flag:
        case True:
            from runtime.proactive import publish_proactive as send
        case _:
            from unrelated import noop as send
            await send(nc, {"type": runtime_kind})
""",
        """
async def emit(nc, runtime_kind):
    try:
        raise RuntimeError
    except ValueError:
        from runtime.proactive import publish_proactive as send
    except RuntimeError:
        from unrelated import noop as send
        await send(nc, {"type": runtime_kind})
""",
    ],
)
def test_function_import_alias_does_not_cross_unreachable_sibling(
    tmp_path: Path,
    source: str,
):
    root = _repo(tmp_path)
    _write(root, "producer.py", source)

    assert load_architecture_vocabulary(root).proactive_types == frozenset()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            """
from runtime.proactive import publish_proactive

async def emit(nc):
    await publish_proactive(
        nc=nc,
        payload={"type": "direct_keyword_notice"},
    )
""",
            "direct_keyword_notice",
        ),
        (
            """
from runtime.proactive import publish_proactive

async def deliver(nc, payload):
    await publish_proactive(nc, payload=payload)

async def emit(nc):
    await deliver(
        nc=nc,
        payload={"type": "helper_keyword_notice"},
    )
""",
            "helper_keyword_notice",
        ),
    ],
)
def test_keyword_payload_is_collected_for_true_sink_and_helper_wrapper(
    tmp_path: Path,
    source: str,
    expected: str,
):
    root = _repo(tmp_path)
    _write(root, "producer.py", source)

    assert expected in load_architecture_vocabulary(root).proactive_types


@pytest.mark.parametrize(
    "source",
    [
        """
from runtime.proactive import publish_proactive

async def emit(nc, runtime_kind):
    await publish_proactive(
        nc=nc,
        payload={"type": runtime_kind},
    )
""",
        """
from runtime.proactive import publish_proactive

async def deliver(nc, payload):
    await publish_proactive(nc, payload=payload)

async def emit(nc, runtime_kind):
    await deliver(nc=nc, payload={"type": runtime_kind})
""",
    ],
)
def test_keyword_payload_is_fail_closed_when_reachable_type_is_dynamic(
    tmp_path: Path,
    source: str,
):
    root = _repo(tmp_path)
    _write(root, "producer.py", source)

    with pytest.raises(
        ArchitectureGuardError,
        match=r"proactive type.*static.*runtime_kind.*(?:dynamic|no static callers)",
    ):
        load_architecture_vocabulary(root)


def test_dynamic_producer_type_is_fail_closed_with_location(tmp_path: Path):
    root = _repo(tmp_path)
    _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

async def emit(nc, runtime_kind):
    payload = {"type": runtime_kind}
    await publish_proactive(nc, payload)
""",
    )

    with pytest.raises(
        ArchitectureGuardError,
        match=r"producer\.py:5:\d+.*proactive type.*static",
    ):
        load_architecture_vocabulary(root)


def test_proactive_cache_invalidates_same_size_mtime_preserved_source_change(
    tmp_path: Path,
):
    root = _repo(tmp_path)
    producer = _write(
        root,
        "producer.py",
        """
from runtime.proactive import publish_proactive

async def emit(nc):
    await publish_proactive(nc, {"type": "notice_alpha"})
""",
    )
    initial_stat = producer.stat()

    assert "notice_alpha" in load_architecture_vocabulary(root).proactive_types

    producer.write_text(
        """
from runtime.proactive import publish_proactive

async def emit(nc):
    await publish_proactive(nc, {"type": "notice_bravo"})
""",
        encoding="utf-8",
    )
    assert producer.stat().st_size == initial_stat.st_size
    os.utime(
        producer,
        ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns),
    )

    proactive_types = load_architecture_vocabulary(root).proactive_types

    assert "notice_bravo" in proactive_types
    assert "notice_alpha" not in proactive_types


def test_missing_fixed_target_is_fail_closed_and_diagnostic(tmp_path: Path):
    root = _repo(tmp_path)
    (root / "llm-gateway/s2s/session.py").unlink()

    with pytest.raises(
        ArchitectureGuardError,
        match=r"llm-gateway/s2s/session\.py.*does not resolve",
    ):
        guard_architecture(root)


def test_architecture_guard_ignores_dependency_node_modules(tmp_path: Path):
    """依赖树不是仓库源码；worktree 的 node_modules junction 也不得被解析为源码逃逸。"""
    root = _repo(tmp_path)
    _write(
        root,
        "node_modules/vendor/python/generated.py",
        'DYNAMIC_VENDOR_INTENT = "sample.query"\n',
    )

    assert guard_architecture(root) == ()


def test_architecture_guard_ignores_generated_artifact_venvs(tmp_path: Path):
    """验证产物中的隔离环境不是仓库源码，不能污染架构调用图。"""
    root = _repo(tmp_path)
    _write(
        root,
        ".artifacts/venvs/manual-rag/Lib/site-packages/vendor/generated.py",
        "callable = callable\ncallable()\n",
    )

    assert guard_architecture(root) == ()


def test_repository_architecture_guard_passes():
    assert_architecture_guard(REPO_ROOT)
