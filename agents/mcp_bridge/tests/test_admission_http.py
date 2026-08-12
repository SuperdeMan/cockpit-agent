"""admission 批 3 扩展契约：transport/headers ${VAR} 展开 / 缺 env 拒载 /
compensate 两态 / compensate_tool 存在性校验。"""
import asyncio
import os
import textwrap

import pytest

from agents.mcp_bridge.src.admission import (COMPENSATE_POLICIES, REJECT_COMPENSATE,
                                             REJECT_ENV, ToolSpec, ServerSpec,
                                             WorkflowSpec, admit, admit_workflow,
                                             load_servers,
                                             normalize_hostname)


def _write_yaml(tmp_path, body: str) -> str:
    p = tmp_path / "servers.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


def test_workflow_admission_is_nonempty_scope_complete_and_minimal():
    # Read dependencies may remain public.  Write dependencies are an internal
    # implementation detail of the high-level workflow and must be hidden.
    read = ToolSpec(name="read", intent="internal.read", expose=True,
                    schema_sha="read-pin", required_scopes=["merchant.write"])
    write = ToolSpec(name="create", intent="internal.create", expose=False,
                     schema_sha="write-pin", write=True, require_confirm=True,
                     required_scopes=["merchant.write"])
    admitted = {"read": read, "create": write, "unrelated": read}

    empty = WorkflowSpec(intent="m.order", handler="m", required_tools=[])
    assert "required_tools" in admit_workflow(empty, admitted)

    weak_scope = WorkflowSpec(
        intent="m.order", handler="m", required_tools=["read", "create"],
        required_scopes=[], require_confirm=True)
    assert "required_scopes" in admit_workflow(weak_scope, admitted)

    no_confirm = WorkflowSpec(
        intent="m.order", handler="m", required_tools=["read", "create"],
        required_scopes=["merchant.write"], require_confirm=False)
    assert "require_confirm" in admit_workflow(no_confirm, admitted)

    valid = WorkflowSpec(
        intent="m.order", handler="m", required_tools=["read", "create"],
        required_scopes=["merchant.write"], require_confirm=True)
    assert admit_workflow(valid, admitted) == ""
    assert set(valid.required_tools) == {"read", "create"}


def test_workflow_rejects_unpinned_dependency_and_exposed_write_dependency():
    workflow = WorkflowSpec(
        intent="m.order", handler="m", required_tools=["read", "create"],
        required_scopes=["merchant.read", "merchant.write"])
    read = ToolSpec(
        name="read", intent="m.read", expose=True, schema_sha="read-pin",
        required_scopes=["merchant.read"])
    unpinned_write = ToolSpec(
        name="create", intent="m.create", expose=False, schema_sha="",
        write=True, require_confirm=True, required_scopes=["merchant.write"])

    reason = admit_workflow(
        workflow, {"read": read, "create": unpinned_write})
    assert "schema_sha" in reason and "create" in reason

    exposed_write = ToolSpec(
        name="create", intent="m.create", expose=True, schema_sha="write-pin",
        write=True, require_confirm=True, required_scopes=["merchant.write"])
    reason = admit_workflow(
        workflow, {"read": read, "create": exposed_write})
    assert "expose=false" in reason and "create" in reason


def test_transport_http_fields_parsed(tmp_path, monkeypatch):
    monkeypatch.setenv("T_TOKEN", "sekret-token-1")
    path = _write_yaml(tmp_path, """
        servers:
          - id: merchant
            transport: streamable_http
            url: https://mcp.example.cn
            headers:
              Authorization: "Bearer ${T_TOKEN}"
            version: ""
            pay_url_hosts: [" Pay.Example.CN. "]
            tools:
              - name: t1
                intent: m.menu
                write: false
                expose: false
                forward_owner: true
                required_scopes: [merchant.read]
                idempotency_mode: upstream
                retry_policy: never
                timeout_outcome: uncertain
    """)
    spec = load_servers(path)[0]
    assert spec.transport == "streamable_http"
    assert spec.url == "https://mcp.example.cn"
    assert spec.headers["Authorization"] == "Bearer sekret-token-1"
    assert spec.pay_url_hosts == ["pay.example.cn"]     # 域名归一小写
    assert spec.env_error == ""
    tool = spec.tools[0]
    assert tool.expose is False and tool.forward_owner is True
    assert tool.required_scopes == ["merchant.read"]
    assert tool.idempotency_mode == "upstream" and tool.retry_policy == "never"
    assert tool.timeout_outcome == "uncertain"


def test_payment_amount_contract_fields_are_loaded(tmp_path):
    path = _write_yaml(tmp_path, """
        servers:
          - id: merchant
            command: [python, -m, merchant]
            version: ""
            pay_url_hosts: [pay.example.cn]
            tools:
              - name: create
                intent: merchant.create
                pay_url_locator: data.payUrl
                amount_locator: data.discountPrice
                amount_unit: yuan
    """)

    tool = load_servers(path)[0].tools[0]

    assert tool.pay_url_locator == "data.payUrl"
    assert tool.amount_locator == "data.discountPrice"
    assert tool.amount_unit == "yuan"


def test_declared_read_result_rejects_invalid_predicate_and_amount_unit():
    base = dict(
        name="lookup", intent="merchant.status", schema_sha="",
        success_predicate={"success": [True]},
        result_map={
            "order_id": "data.orderId",
            "status": "data.status",
            "amount_cents": "data.amount",
        },
        status_map={"已取消": "已取消"})
    bad_unit = ToolSpec(**base, amount_unit="dollars")
    admitted, rejected = admit(_spec([bad_unit]), _offered("lookup"))
    assert admitted == []
    assert any("amount_unit" in reason for reason in rejected)

    bad_predicate = ToolSpec(**{
        **base,
        "success_predicate": {"": [True]},
        "amount_unit": "yuan",
    })
    admitted, rejected = admit(_spec([bad_predicate]), _offered("lookup"))
    assert admitted == []
    assert any("success_predicate" in reason for reason in rejected)

    missing_can_match_null = ToolSpec(**{
        **base,
        "success_predicate": {"success": [None]},
        "amount_unit": "yuan",
    })
    admitted, rejected = admit(
        _spec([missing_can_match_null]), _offered("lookup"))
    assert admitted == []
    assert any("success_predicate" in reason for reason in rejected)


def test_declared_amount_unit_must_be_explicit_in_yaml(tmp_path):
    path = _write_yaml(tmp_path, """
        servers:
          - id: merchant
            command: [python, -m, merchant]
            version: ""
            tools:
              - name: lookup
                intent: merchant.status
                success_predicate: {success: [true]}
                result_map:
                  order_id: data.orderId
                  status: data.status
                  amount_cents: data.amount
    """)
    spec = load_servers(path)[0]
    assert spec.tools[0].amount_unit == ""
    admitted, rejected = admit(spec, _offered("lookup"))
    assert admitted == []
    assert any("amount_unit" in reason for reason in rejected)


def test_remote_write_requires_declared_business_success_predicate():
    write = ToolSpec(
        name="create", intent="merchant.internal.create", write=True,
        expose=False, require_confirm=True,
        idempotency_mode="local_at_most_once", retry_policy="never",
        timeout_outcome="uncertain", compensate_policy="terminal")
    remote = ServerSpec(
        id="merchant", command=[], version="", tools=[write],
        transport="streamable_http")
    admitted, rejected = admit(remote, _offered("create"))
    assert admitted == []
    assert any("success_predicate" in reason for reason in rejected)

    write.success_predicate = {"success": [True], "code": [0]}
    admitted, rejected = admit(remote, _offered("create"))
    assert [tool.name for tool, _ in admitted] == ["create"]
    assert rejected == []

    write.expose = True
    admitted, rejected = admit(remote, _offered("create"))
    assert admitted == []
    assert any("expose=false" in reason for reason in rejected)


@pytest.mark.parametrize("transport", ["http", "STREAMABLE_HTTP"])
def test_admission_rejects_unknown_or_noncanonical_transport(transport):
    tool = ToolSpec(name="lookup", intent="merchant.lookup")
    spec = ServerSpec(
        id="merchant", command=[], version="", tools=[tool],
        transport=transport)

    admitted, rejected = admit(spec, _offered("lookup"))

    assert admitted == []
    assert any("transport" in reason and transport in reason
               for reason in rejected)


def test_third_party_stdio_cannot_expose_write_tool():
    write = ToolSpec(
        name="create", intent="merchant.internal.create", write=True,
        expose=True, require_confirm=True,
        idempotency_mode="local_at_most_once", retry_policy="never",
        timeout_outcome="uncertain", compensate_policy="terminal",
        success_predicate={"success": [True]})
    third_party = ServerSpec(
        id="merchant", command=[], version="", tools=[write],
        demo=False, trust="third_party", transport="stdio")

    admitted, rejected = admit(third_party, _offered("create"))

    assert admitted == []
    assert any("expose" in reason for reason in rejected)


def test_third_party_stdio_hidden_write_requires_success_predicate():
    write = ToolSpec(
        name="create", intent="merchant.internal.create", write=True,
        expose=False, require_confirm=True,
        idempotency_mode="local_at_most_once", retry_policy="never",
        timeout_outcome="uncertain", compensate_policy="terminal")
    third_party = ServerSpec(
        id="merchant", command=[], version="", tools=[write],
        demo=False, trust="third_party", transport="stdio")

    admitted, rejected = admit(third_party, _offered("create"))
    assert admitted == []
    assert any("success_predicate" in reason for reason in rejected)

    write.success_predicate = {"success": [True]}
    admitted, rejected = admit(third_party, _offered("create"))
    assert [tool.name for tool, _ in admitted] == ["create"]
    assert rejected == []


def test_demo_stdio_is_the_only_exposed_write_exception():
    write = ToolSpec(
        name="create", intent="demo.create", write=True,
        expose=True, require_confirm=True,
        idempotency_mode="local_at_most_once", retry_policy="never",
        timeout_outcome="uncertain", compensate_policy="terminal")
    demo = ServerSpec(
        id="demo", command=[], version="", tools=[write],
        demo=True, trust="local", transport="stdio")

    admitted, rejected = admit(demo, _offered("create"))

    assert [tool.name for tool, _ in admitted] == ["create"]
    assert rejected == []


@pytest.mark.parametrize("candidate", ["", "   ", float("nan"),
                                         float("inf"), float("-inf")])
def test_success_predicate_rejects_empty_or_nonfinite_scalars(candidate):
    tool = ToolSpec(
        name="lookup", intent="merchant.status",
        success_predicate={"code": [candidate]},
        result_map={"order_id": "data.id", "status": "data.status"},
        status_map={"已取消": "已取消"})
    admitted, rejected = admit(_spec([tool]), _offered("lookup"))
    assert admitted == []
    assert any("success_predicate" in reason for reason in rejected)


@pytest.mark.parametrize("status_map", [
    {},
    {"已取消 手机13800138000": "已取消 手机13800138000"},
    {"已取消": "RAW-SECRET-TOKEN"},
])
def test_declared_status_result_requires_controlled_status_map(status_map):
    tool = ToolSpec(
        name="lookup", intent="merchant.status",
        success_predicate={"code": [0]},
        result_map={"order_id": "data.id", "status": "data.status"},
        status_map=status_map)

    admitted, rejected = admit(_spec([tool]), _offered("lookup"))

    assert admitted == []
    assert any("status_map" in reason for reason in rejected)


def test_workflow_fields_are_parsed_without_exposing_internal_tools(tmp_path):
    path = _write_yaml(tmp_path, """
        servers:
          - id: merchant
            command: [python, -m, x]
            version: ""
            workflows:
              - intent: luckin.order
                handler: luckin
                required_tools: [shop.list, product.search, order.create]
                required_scopes: [merchant.write]
                description: 瑞幸下单
                examples: [点一杯瑞幸]
                slots: [item_query, quantity]
                require_confirm: true
            tools:
              - name: shop.list
                intent: _internal.luckin.shop
                expose: false
    """)
    spec = load_servers(path)[0]
    workflow = spec.workflows[0]
    assert workflow.intent == "luckin.order"
    assert workflow.handler == "luckin"
    assert workflow.required_tools == ["shop.list", "product.search", "order.create"]
    assert workflow.required_scopes == ["merchant.write"]
    assert workflow.require_confirm is True
    assert spec.tools[0].expose is False


def test_missing_env_marks_server_rejected(tmp_path, monkeypatch):
    """缺 token → 具名拒载理由，不静默拿空 token 出站吃 401（§9.9）。"""
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    path = _write_yaml(tmp_path, """
        servers:
          - id: merchant
            transport: streamable_http
            url: https://mcp.example.cn
            headers:
              Authorization: "Bearer ${NOPE_TOKEN}"
            version: ""
            tools: []
    """)
    spec = load_servers(path)[0]
    assert REJECT_ENV in spec.env_error
    assert "NOPE_TOKEN" in spec.env_error


def test_stdio_defaults_unchanged(tmp_path):
    """存量 stdio 声明零改动照旧解析（demo-coffee 的形态）。"""
    path = _write_yaml(tmp_path, """
        servers:
          - id: demo
            command: [python, -m, x]
            version: "0.1.0"
            tools:
              - {name: t, intent: s.menu, write: false}
    """)
    spec = load_servers(path)[0]
    assert spec.transport == "stdio" and spec.url == "" and spec.headers == {}
    assert spec.env_error == ""


def _spec(tools) -> ServerSpec:
    # Legacy write-invariant tests exercise the trusted local demo exception;
    # third-party and remote boundary cases construct ServerSpec explicitly.
    return ServerSpec(
        id="s", command=[], version="", tools=tools,
        demo=True, trust="local", transport="stdio")


def _offered(*names):
    return [{"name": n, "inputSchema": {}} for n in names]


def test_compensate_tool_must_exist_in_allowlist():
    """存在性校验（批 3 补死）：声明了却不在白名单=「声明存在≠能用」的准入器残留。"""
    t = ToolSpec(name="order.create", intent="m.order", write=True,
                 require_confirm=True, compensate_tool="order.cancel",
                 idempotency_mode="local_at_most_once", retry_policy="never",
                 timeout_outcome="uncertain")
    admitted, rejected = admit(_spec([t]), _offered("order.create", "order.cancel"))
    assert not admitted and any(REJECT_COMPENSATE in r for r in rejected)

    cancel = ToolSpec(name="order.cancel", intent="m.cancel", write=True,
                      require_confirm=True, compensate_policy="terminal",
                      idempotency_mode="local_at_most_once", retry_policy="never",
                      timeout_outcome="uncertain")
    admitted2, rejected2 = admit(_spec([t, cancel]),
                                 _offered("order.create", "order.cancel"))
    assert {a[0].name for a in admitted2} == {"order.create", "order.cancel"}
    assert rejected2 == []


def test_compensator_must_be_distinct_write_terminal_tool():
    create = ToolSpec(name="create", intent="m.create", write=True,
                      require_confirm=True, compensate_tool="status",
                      idempotency_mode="local_at_most_once", retry_policy="never",
                      timeout_outcome="uncertain")
    status = ToolSpec(name="status", intent="m.status", write=False)
    admitted, rejected = admit(_spec([create, status]),
                               _offered("create", "status"))
    assert {tool.name for tool, _ in admitted} == {"status"}
    assert any("create" in reason and "terminal" in reason for reason in rejected)

    self_cycle = ToolSpec(name="cancel", intent="m.cancel", write=True,
                          require_confirm=True, compensate_tool="cancel",
                          idempotency_mode="local_at_most_once", retry_policy="never",
                          timeout_outcome="uncertain")
    admitted2, rejected2 = admit(_spec([self_cycle]), _offered("cancel"))
    assert admitted2 == []
    assert any("cancel" in reason and "terminal" in reason for reason in rejected2)

    a = ToolSpec(name="a", intent="m.a", write=True, require_confirm=True,
                 compensate_tool="b", idempotency_mode="local_at_most_once",
                 retry_policy="never", timeout_outcome="uncertain")
    b = ToolSpec(name="b", intent="m.b", write=True, require_confirm=True,
                 compensate_tool="a", idempotency_mode="local_at_most_once",
                 retry_policy="never", timeout_outcome="uncertain")
    admitted3, rejected3 = admit(_spec([a, b]), _offered("a", "b"))
    assert admitted3 == []
    assert sum("terminal" in reason for reason in rejected3) == 2


def test_abandon_unpaid_requires_structured_expiry_and_action_prompt():
    misleading = ToolSpec(
        name="order.create", intent="m.order", write=True, require_confirm=True,
        compensate_policy="abandon_unpaid", idempotency_mode="local_at_most_once",
        retry_policy="never", timeout_outcome="uncertain",
        confirm_prompt="不支付不会自动失效，也绝不会取消。")
    admitted, rejected = admit(_spec([misleading]), _offered("order.create"))
    assert not admitted and any("unpaid_expiry" in r for r in rejected), (
        "否定句或关键词拼接不能成为商户自动失效的唯一证据")

    no_prompt = ToolSpec(
        name="order.create", intent="m.order", write=True, require_confirm=True,
        compensate_policy="abandon_unpaid", idempotency_mode="local_at_most_once",
        retry_policy="never", timeout_outcome="uncertain", unpaid_expiry=True)
    admitted2, rejected2 = admit(_spec([no_prompt]), _offered("order.create"))
    assert not admitted2 and any("confirm_prompt" in r for r in rejected2)

    ok = ToolSpec(
        name="order.create", intent="m.order", write=True, require_confirm=True,
        compensate_policy="abandon_unpaid", idempotency_mode="local_at_most_once",
        retry_policy="never", timeout_outcome="uncertain", unpaid_expiry=True,
        confirm_prompt="准备下单：{args}，确认吗？")
    admitted3, rejected3 = admit(_spec([ok]), _offered("order.create"))
    assert len(admitted3) == 1 and rejected3 == []


@pytest.mark.parametrize("flag", [1, "true", "yes", [], {}])
def test_abandon_unpaid_expiry_flag_must_be_boolean_true(flag):
    tool = ToolSpec(
        name="order.create", intent="m.order", write=True, require_confirm=True,
        compensate_policy="abandon_unpaid", idempotency_mode="local_at_most_once",
        retry_policy="never", timeout_outcome="uncertain", unpaid_expiry=flag,
        confirm_prompt="准备下单：{args}，确认吗？")
    admitted, rejected = admit(_spec([tool]), _offered("order.create"))
    assert admitted == []
    assert any("unpaid_expiry" in reason for reason in rejected)


def test_unknown_compensate_policy_rejected():
    t = ToolSpec(name="x", intent="m.x", write=True, require_confirm=True,
                 compensate_policy="whatever", idempotency_mode="local_at_most_once",
                 retry_policy="never", timeout_outcome="uncertain")
    admitted, rejected = admit(_spec([t]), _offered("x"))
    assert not admitted and any("whatever" in r for r in rejected)
    assert set(COMPENSATE_POLICIES) == {"tool", "abandon_unpaid", "terminal"}


def test_terminal_write_is_valid_but_confirmation_is_mandatory():
    unsafe = ToolSpec(name="cancel", intent="m.cancel", write=True,
                      compensate_policy="terminal", retry_policy="never",
                      idempotency_mode="local_at_most_once",
                      timeout_outcome="uncertain")
    admitted, rejected = admit(_spec([unsafe]), _offered("cancel"))
    assert not admitted and any("require_confirm" in r for r in rejected)

    safe = ToolSpec(name="cancel", intent="m.cancel", write=True,
                    require_confirm=True, compensate_policy="terminal",
                    retry_policy="never", idempotency_mode="local_at_most_once",
                    timeout_outcome="uncertain")
    admitted2, rejected2 = admit(_spec([safe]), _offered("cancel"))
    assert [t.name for t, _ in admitted2] == ["cancel"] and rejected2 == []


def test_upstream_idempotency_arg_must_exist_in_live_schema():
    tool = ToolSpec(name="create", intent="m.create", write=True,
                    require_confirm=True, compensate_policy="terminal",
                    idempotency_mode="upstream",
                    idempotency_key_arg="request_id",
                    timeout_outcome="uncertain")
    offered = [{"name": "create", "inputSchema": {
        "type": "object", "properties": {"item": {"type": "string"}}}}]
    admitted, rejected = admit(_spec([tool]), offered)
    assert not admitted and any("idempotency_key_arg" in r for r in rejected)

    offered[0]["inputSchema"]["properties"]["request_id"] = {"type": "string"}
    admitted2, rejected2 = admit(_spec([tool]), offered)
    assert [t.name for t, _ in admitted2] == ["create"] and rejected2 == []


def test_local_at_most_once_requires_never_retry_policy():
    unsafe = ToolSpec(name="cancel", intent="m.cancel", write=True,
                      require_confirm=True, compensate_policy="terminal",
                      idempotency_mode="local_at_most_once",
                      retry_policy="safe", timeout_outcome="uncertain")
    admitted, rejected = admit(_spec([unsafe]), _offered("cancel"))
    assert not admitted and any("retry_policy" in r for r in rejected)

    unsafe.retry_policy = "never"
    admitted2, rejected2 = admit(_spec([unsafe]), _offered("cancel"))
    assert [t.name for t, _ in admitted2] == ["cancel"] and rejected2 == []


def test_compensator_schema_drift_rejects_dependent_create_in_second_phase():
    create = ToolSpec(name="create", intent="m.create", write=True,
                      require_confirm=True, compensate_tool="cancel",
                      idempotency_mode="local_at_most_once", retry_policy="never",
                      timeout_outcome="uncertain")
    cancel = ToolSpec(name="cancel", intent="m.cancel", write=True,
                      require_confirm=True, compensate_policy="terminal",
                      retry_policy="never", idempotency_mode="local_at_most_once",
                      timeout_outcome="uncertain",
                      schema_sha="expected")
    admitted, rejected = admit(
        _spec([create, cancel]),
        [{"name": "create", "inputSchema": {}},
         {"name": "cancel", "inputSchema": {}}])
    assert admitted == []
    assert any("cancel" in r and "schema_mismatch" in r for r in rejected)
    assert any("create" in r and "compensate" in r for r in rejected)


def test_pay_url_locator_requires_nonempty_server_host_allowlist():
    tool = ToolSpec(name="create", intent="m.create", write=True,
                    require_confirm=True, compensate_policy="abandon_unpaid",
                    idempotency_mode="local_at_most_once", retry_policy="never",
                    timeout_outcome="uncertain", unpaid_expiry=True,
                    confirm_prompt="准备下单：{args}，确认吗？",
                    pay_url_locator="pay.url")
    admitted, rejected = admit(_spec([tool]), _offered("create"))
    assert admitted == []
    assert any("pay_url_hosts" in r for r in rejected)


@pytest.mark.parametrize("host", [
    "", "   ", "https://pay.example.cn", "pay.example.cn:443",
    "pay.example.cn/path", "//pay.example.cn", "bad host", ".",
    "127.0.0.1", "169.254.169.254",
])
def test_pay_url_locator_rejects_every_non_hostname_allowlist_entry(host):
    spec = ServerSpec(id="merchant", command=[], version="", tools=[],
                      pay_url_hosts=[host])
    tool = ToolSpec(name="lookup", intent="m.lookup",
                    pay_url_locator="payment.url")
    spec.tools = [tool]
    admitted, rejected = admit(spec, _offered("lookup"))
    assert admitted == []
    assert any("pay_url_hosts" in r for r in rejected)


def test_pay_url_locator_accepts_only_normalized_hostname_allowlist():
    spec = ServerSpec(id="merchant", command=[], version="", tools=[],
                      pay_url_hosts=["pay.example.cn", "backup.example.cn"])
    tool = ToolSpec(name="lookup", intent="m.lookup",
                    pay_url_locator="payment.url",
                    amount_locator="payment.amount", amount_unit="yuan")
    spec.tools = [tool]
    admitted, rejected = admit(spec, _offered("lookup"))
    assert [t.name for t, _ in admitted] == ["lookup"] and rejected == []


@pytest.mark.parametrize("amount_locator,amount_unit", [
    ("", "yuan"), ("payment.amount", ""),
    ("payment.amount", "dollars"),
])
def test_pay_url_locator_requires_audited_amount_contract(
        amount_locator, amount_unit):
    spec = ServerSpec(
        id="merchant", command=[], version="", tools=[],
        pay_url_hosts=["pay.example.cn"])
    tool = ToolSpec(
        name="lookup", intent="m.lookup", pay_url_locator="payment.url",
        amount_locator=amount_locator, amount_unit=amount_unit)
    spec.tools = [tool]

    admitted, rejected = admit(spec, _offered("lookup"))

    assert admitted == []
    assert any("amount_locator" in reason or "amount_unit" in reason
               for reason in rejected)


@pytest.mark.parametrize("host", ["faß.de", "支付.example"])
def test_unicode_payment_hostname_is_rejected_instead_of_idna_aliasing(host):
    """The bridge only admits audited ASCII hosts; IDNA 2003 aliases are unsafe."""
    assert normalize_hostname(host) == ""


def test_non_demo_server_cannot_admit_forward_owner():
    tool = ToolSpec(name="lookup", intent="m.lookup", forward_owner=True)
    non_demo = ServerSpec(
        id="third-party", command=[], version="", tools=[tool],
        demo=False, trust="third_party", transport="stdio")
    admitted, rejected = admit(non_demo, _offered("lookup"))
    assert admitted == []
    assert any("forward_owner" in r for r in rejected)

    demo = ServerSpec(id="demo", command=[], version="", tools=[tool], demo=True)
    admitted2, rejected2 = admit(demo, _offered("lookup"))
    assert [t.name for t, _ in admitted2] == ["lookup"] and rejected2 == []


@pytest.mark.parametrize("tool", [
    ToolSpec(name="const", intent="m.const",
             const_args={"_owner_user_id": "attacker"}),
    ToolSpec(name="mapped", intent="m.mapped",
             arg_map={"account": "_owner_user_id"}),
    ToolSpec(name="idem", intent="m.idem",
             idempotency_key_arg="_owner_user_id"),
])
def test_owner_internal_argument_cannot_be_declared_by_tool_config(tool):
    admitted, rejected = admit(_spec([tool]), _offered(tool.name))
    assert admitted == []
    assert any("_owner_user_id" in r for r in rejected)


@pytest.mark.parametrize("mode", ["none", "", "unexpected"])
def test_every_write_requires_explicit_supported_idempotency_mode(mode):
    tool = ToolSpec(name="cancel", intent="m.cancel", write=True,
                    require_confirm=True, compensate_policy="terminal",
                    idempotency_mode=mode, retry_policy="never",
                    timeout_outcome="uncertain")
    admitted, rejected = admit(_spec([tool]), _offered("cancel"))
    assert admitted == []
    assert any("idempotency_mode" in r for r in rejected)


@pytest.mark.parametrize("outcome", ["", "definite", "UNCERTAIN"])
def test_local_at_most_once_requires_explicit_uncertain_timeout_outcome(outcome):
    tool = ToolSpec(name="cancel", intent="m.cancel", write=True,
                    require_confirm=True, compensate_policy="terminal",
                    idempotency_mode="local_at_most_once", retry_policy="never",
                    timeout_outcome=outcome)
    admitted, rejected = admit(_spec([tool]), _offered("cancel"))
    assert admitted == []
    assert any("timeout_outcome" in r and "uncertain" in r for r in rejected)


def test_local_at_most_once_accepts_uncertain_timeout_outcome():
    tool = ToolSpec(name="cancel", intent="m.cancel", write=True,
                    require_confirm=True, compensate_policy="terminal",
                    idempotency_mode="local_at_most_once", retry_policy="never",
                    timeout_outcome="uncertain")
    admitted, rejected = admit(_spec([tool]), _offered("cancel"))
    assert [t.name for t, _ in admitted] == ["cancel"] and rejected == []


@pytest.mark.parametrize("outcome", ["", "definite", "UNCERTAIN"])
def test_upstream_write_also_requires_explicit_uncertain_timeout_outcome(outcome):
    tool = ToolSpec(name="create", intent="m.create", write=True,
                    require_confirm=True, compensate_policy="terminal",
                    idempotency_mode="upstream", retry_policy="never",
                    idempotency_key_arg="request_id", timeout_outcome=outcome)
    offered = [{"name": "create", "inputSchema": {
        "type": "object", "properties": {"request_id": {"type": "string"}}}}]
    admitted, rejected = admit(_spec([tool]), offered)
    assert admitted == []
    assert any("timeout_outcome" in r and "uncertain" in r for r in rejected)


def test_read_tools_unaffected_by_compensate_rules():
    t = ToolSpec(name="menu", intent="m.menu", write=False)
    admitted, rejected = admit(_spec([t]), _offered("menu"))
    assert len(admitted) == 1 and rejected == []


def test_const_args_parsed_with_native_types(tmp_path):
    """const_args 保留 yaml 原生类型（beType: 1 是 int）——真实商户的 required
    枚举选择器靠它声明死，LLM 槽位不填场景参数（麦当劳激活时新增）。"""
    path = _write_yaml(tmp_path, """
        servers:
          - id: m
            transport: streamable_http
            url: https://x
            version: ""
            tools:
              - name: t1
                intent: m.stores
                write: false
                const_args: {beType: 1, searchType: 2, tag: "a"}
    """)
    spec = load_servers(path)[0]
    assert spec.tools[0].const_args == {"beType": 1, "searchType": 2, "tag": "a"}
    assert isinstance(spec.tools[0].const_args["beType"], int)
