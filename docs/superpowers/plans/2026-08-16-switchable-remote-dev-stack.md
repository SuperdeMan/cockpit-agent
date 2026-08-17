# 本地/云端可切换真栈 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **激活状态（2026-08-17）：** 切换工具已经具备，但 final 数据迁移未完成；`dev-stack.local`
> 仍缺省为 local，禁止提前激活 cloud。当前现场与完成门禁见
> [`../../reviews/2026-08-17-cloud-data-migration-handoff.md`](../../reviews/2026-08-17-cloud-data-migration-handoff.md)。

**Goal:** 提供人和 agent 共用的 `dev_stack` 入口，让本地单测与前端热更新连接可切换的 local/cloud 后端，云端发布复用既有受控发布器，云端真栈测试只运行显式授权的安全用例，同时保持当前缺省 local 直至最终数据迁移完成。

**Architecture:** `dev-stack.local` 只保存一个非密钥 target，统一解析器把它转换为 local 或 Tailnet HTTPS/WSS 端点；CLI 的状态、发布、前端和验证动作都消费同一个解析结果。现有 E2E manifest 增加远程安全/高影响策略，runner 在 cloud 模式拒绝本地 Compose/profile/fixture 能力并通过 SSH 持有服务器 `release.lock`，从而与发布、备份和数据迁移互斥。

**Tech Stack:** Python 3.11、argparse、pytest、Vite/Node、Docker Compose（仅 local target）、SSH、Tailscale Serve HTTPS/WSS、现有 cloud release/E2E runner

---

## 前置接口

本计划依赖数据迁云计划提供并安装的 `/opt/car-agent/shared/bin/transaction-lock.sh`；依赖的是“`release.lock` 非阻塞互斥”接口，不依赖任何迁移包或业务数据。若先单独实现本计划，只完成本地代码和测试，不安装 `remote-e2e-lock.sh`，也不执行 cloud E2E。

## 文件结构与职责

- Create: `scripts/dev_stack_lib.py` — target 文件、端点、状态、前端环境和 cloud release 委托的纯逻辑。
- Create: `scripts/dev_stack.py` — `target/status/deploy/verify/hmi/dashboard` CLI。
- Create: `scripts/e2e_target.py` — E2E local/cloud 端点注入与远程选择门禁。
- Create: `scripts/cloud_remote_lock.py` — SSH 长连接生命周期内持有云端事务锁。
- Create: `deploy/cloud/remote-e2e-lock.sh` — 在 SSH stdin 关闭前持有 `release.lock`。
- Create: `test/e2e_remote_safe.py` — HMI、Edge、Audio、Dashboard、Collector 与隔离会话/trace 的云端安全探针。
- Create: `scripts/tests/test_dev_stack.py` — target、端点、status/deploy/前端命令单测。
- Create: `scripts/tests/test_e2e_target.py` — manifest 远程策略、选择门禁、端点与锁测试。
- Modify: `scripts/e2e_contract.py` — `remote_safe`/`remote_mutating` 严格 schema。
- Modify: `test/e2e_manifest.yaml` — 为每个 case 显式声明远程策略，新增 remote-safe case。
- Modify: `scripts/run_e2e.py` — `--target`、`--allow-mutating`、云端选择、端点、锁和证据。
- Modify: `scripts/tests/test_e2e_manifest.py` — 新 schema 的完整契约。
- Modify: `scripts/tests/test_run_e2e.py` — local 回归和 cloud fail-closed 行为。
- Modify: `scripts/cloud_release_lib.py` — 新远程锁脚本进入受控基础设施摘要/bootstrap。
- Modify: `scripts/tests/test_cloud_release.py` — 更新共享脚本清单。
- Modify: `scripts/tests/test_cloud_deploy_assets.py` — 远程 E2E 锁脚本静态安全测试。
- Modify: `AGENTS.md` — agent 真栈操作先读 target，cloud 禁止误启本地 Compose。
- Modify: `CLAUDE.md` — 唯一运行环境与可切换真栈边界。
- Modify: `docs/dev-guide.md` — 日常开发、前端热更新、发布、验证与切回 local。
- Modify: `test/README.md` — remote-safe、高影响和证据要求。
- Modify: `deploy/cloud/README.md` — 锁与发布/迁移互斥关系。

## 固定用户入口

```text
python scripts/dev_stack.py target show
python scripts/dev_stack.py target set local
python scripts/dev_stack.py target set cloud
python scripts/dev_stack.py status
python scripts/dev_stack.py deploy --sha HEAD
python scripts/dev_stack.py deploy --sha HEAD --apply
python scripts/dev_stack.py verify
python scripts/dev_stack.py hmi
python scripts/dev_stack.py dashboard
```

`dev-stack.local` 缺失时必须报告 `target=local, source=default`；实现和第一阶段数据迁移期间不得创建 `target=cloud` 文件。

### Task 1: 规则先行，先锁定目录、target 与远程红线

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `docs/dev-guide.md`
- Modify: `test/README.md`
- Modify: `deploy/cloud/README.md`

- [ ] **Step 1: 在 AGENTS.md 与 CLAUDE.md 先写实施期约束**

在任何脚本目录或 manifest 改动前加入以下规则：

```text
真栈动作前先读取 dev-stack.local；文件缺失时 target=local，损坏时 fail closed。
target=cloud 时禁止启动本地 Compose；本地只承载编辑、单测、静态检查和 Vite。
target=local 时继续只用根 compose.yaml / make up，根 .env 仍是唯一运行时来源。
dev-stack.local 只允许 target=local|cloud，不得保存 token、密码、私钥或 URL。
cloud deploy 仍只接受干净、已提交、main 可达的 SHA，不自动 commit/merge/push。
未显式 remote_safe 的 E2E 不得在 cloud 缺省运行；高影响开关不替代人工红线授权。
本阶段不得自动写 target=cloud，也不得停止另一个 agent 正在使用的本地 Docker。
```

- [ ] **Step 2: 在三个运行文档先建立即将合入入口的权威位置**

`docs/dev-guide.md` 固定用户入口为 `scripts/dev_stack.py`；`test/README.md` 固定远程策略字段为 `remote_safe/remote_mutating`；`deploy/cloud/README.md` 固定互斥锁为 `/opt/car-agent/shared/locks/release.lock`。文字标注“以下命令在对应实现提交合入后可用”，不宣称尚未实现的命令已经验证。

- [ ] **Step 3: 检查规范没有引入第二运行环境**

Run: `rg -n "dev-stack.local|target=cloud|remote_safe|release.lock|根 .env" AGENTS.md CLAUDE.md docs/dev-guide.md test/README.md deploy/cloud/README.md`

Expected: 五份文件均能定位对应边界；没有 `deploy/.env`、第二套云端 Compose 或自动 push 入口。

- [ ] **Step 4: 先提交规则，再开始实现**

```bash
git add AGENTS.md CLAUDE.md docs/dev-guide.md test/README.md deploy/cloud/README.md
git commit -m "docs: define switchable true-stack rules"
```

### Task 2: 实现严格 target 文件与统一端点模型

**Files:**
- Create: `scripts/dev_stack_lib.py`
- Create: `scripts/tests/test_dev_stack.py`

- [ ] **Step 1: 写缺省 local、原子 set 和损坏文件 fail-closed 测试**

```python
def test_missing_target_defaults_to_local(tmp_path):
    resolved = dev.resolve_target(tmp_path)
    assert resolved.name == "local"
    assert resolved.source == "default"


@pytest.mark.parametrize("payload", [
    "target=remote\n", "target=cloud\ntarget=local\n", "cloud\n",
    "target=cloud\nextra=x\n", "target=\n",
])
def test_invalid_target_file_fails_closed(tmp_path, payload):
    (tmp_path / "dev-stack.local").write_text(payload, encoding="utf-8")
    with pytest.raises(DevStackError, match="target file"):
        dev.resolve_target(tmp_path)


def test_set_target_writes_one_canonical_line(tmp_path):
    dev.set_target(tmp_path, "cloud")
    assert (tmp_path / "dev-stack.local").read_bytes() == b"target=cloud\n"
```

- [ ] **Step 2: 运行测试确认模块缺失**

Run: `python -m pytest scripts/tests/test_dev_stack.py -q`

Expected: FAIL with `ModuleNotFoundError: scripts.dev_stack_lib`。

- [ ] **Step 3: 实现 target 与端点数据结构**

```python
class DevStackError(RuntimeError):
    """A safe, redacted development-stack error."""


@dataclass(frozen=True)
class TargetSelection:
    name: Literal["local", "cloud"]
    source: Literal["default", "file", "argument"]


@dataclass(frozen=True)
class StackEndpoints:
    hmi: str
    edge_http: str
    edge_ws: str
    audio: str
    dashboard: str
    collector_http: str
    collector_ws: str


LOCAL_ENDPOINTS = StackEndpoints(
    hmi="http://localhost:5173",
    edge_http="http://localhost:8090",
    edge_ws="ws://localhost:8090/ws",
    audio="http://localhost:50059",
    dashboard="http://localhost:5174",
    collector_http="http://localhost:8092",
    collector_ws="ws://localhost:8092/stream",
)
```

`resolve_target()` 只接受缺失文件或恰好一行 `target=local|cloud`；UTF-8 解码错误、BOM、重复键、空行之外的第二行和符号链接均拒绝。`set_target()` 先写同目录 `.partial`、flush+fsync，再 `os.replace`，不读取或修改 `.env`。

- [ ] **Step 4: 实现 cloud FQDN 和选定 env 的严格读取**

```python
TAILNET_FQDN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.ts\.net$")


def cloud_endpoints(fqdn: str) -> StackEndpoints:
    if TAILNET_FQDN_RE.fullmatch(fqdn) is None:
        raise DevStackError("TAILNET_FQDN is missing or invalid")
    return StackEndpoints(
        hmi=f"https://{fqdn}",
        edge_http=f"https://{fqdn}:8443",
        edge_ws=f"wss://{fqdn}:8443/ws",
        audio=f"https://{fqdn}:8444",
        dashboard=f"https://{fqdn}:8445",
        collector_http=f"https://{fqdn}:8446",
        collector_ws=f"wss://{fqdn}:8446/stream",
    )
```

`read_root_env()` 只返回调用者请求的键；同一键重复、无 `=`、NUL、未闭合引号或不可读时 fail closed。状态输出只能展示 FQDN/端口，不能展示 `VITE_WS_TOKEN`、私钥、DSN、密码或整份 `.env`。

- [ ] **Step 5: 运行测试并提交**

Run: `python -m pytest scripts/tests/test_dev_stack.py -q`

Expected: PASS。

```bash
git add scripts/dev_stack_lib.py scripts/tests/test_dev_stack.py
git commit -m "feat: define switchable development stack targets"
```

### Task 3: 实现只读 local/cloud status

**Files:**
- Modify: `scripts/dev_stack_lib.py`
- Modify: `scripts/tests/test_dev_stack.py`

- [ ] **Step 1: 写 status 不自动启动/部署的测试**

```python
def test_local_status_never_starts_compose(fake_runner, tmp_path):
    result = dev.inspect_local_status(tmp_path, LOCAL_ENDPOINTS, fake_runner)
    assert result.target == "local"
    assert not any(
        set(call.argv).intersection({"up", "start", "restart", "build"})
        for call in fake_runner.calls
    )


def test_cloud_status_reads_release_and_five_endpoints_without_deploying(
    fake_cloud_runner, cloud_request,
):
    result = dev.inspect_cloud_status(cloud_request, cloud_endpoints("demo.ts.net"), fake_cloud_runner)
    assert result.release_sha == "1" * 40
    assert result.healthy_endpoints == 5
    assert not any(" deploy " in " ".join(call.argv) for call in fake_cloud_runner.calls)
```

- [ ] **Step 2: 运行测试确认 status 尚未实现**

Run: `python -m pytest scripts/tests/test_dev_stack.py -q`

Expected: FAIL for missing status functions。

- [ ] **Step 3: 实现 local 只读检查**

Local status 固定执行 `docker info`、根 `docker compose -f compose.yaml ps --format json`，验证关键服务 `postgres/redis/edge-gateway/llm-gateway/observability-collector/hmi/dashboard`，并分别请求五个 local 入口。Docker daemon 不可用时返回清晰红色状态，但不调用 `up`。

```python
@dataclass(frozen=True)
class StackStatus:
    target: str
    release_sha: str | None
    container_total: int | None
    container_running: int | None
    healthy_endpoints: int
    endpoint_results: tuple[EndpointStatus, ...]
    warnings: tuple[str, ...]
```

- [ ] **Step 4: 实现 cloud 只读检查**

Cloud status 复用 `cloud_release_lib.discover_remote_state()` 读取 `current_release/runtime_project_name/disk/memory/lock`，再 GET：HMI `/`、Edge `/healthz`、Audio `/api/llm/providers`、Dashboard `/`、Collector `/healthz`。超时、TLS、DNS 和 HTTP 状态分别记录；不自动部署、不调用 Docker、不修改 Tailscale。

- [ ] **Step 5: 验证脱敏与无副作用**

```python
def test_status_json_never_contains_secrets(status_payload):
    encoded = json.dumps(status_payload, ensure_ascii=False)
    for value in ("super-secret-token", "postgresql://", "PRIVATE KEY"):
        assert value not in encoded
```

Run: `python -m pytest scripts/tests/test_dev_stack.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add scripts/dev_stack_lib.py scripts/tests/test_dev_stack.py
git commit -m "feat: inspect local and cloud development stacks"
```

### Task 4: 实现统一 CLI 与受控 cloud deploy 委托

**Files:**
- Create: `scripts/dev_stack.py`
- Modify: `scripts/dev_stack_lib.py`
- Modify: `scripts/tests/test_dev_stack.py`

- [ ] **Step 1: 写 target/deploy/verify 的 CLI 测试**

```python
def test_deploy_requires_cloud_target_and_defaults_to_dry_run(tmp_path, fake_runner):
    dev.set_target(tmp_path, "local")
    assert cli.main(["deploy", "--sha", "HEAD"], repo=tmp_path, runner=fake_runner) == 2
    dev.set_target(tmp_path, "cloud")
    assert cli.main(["deploy", "--sha", "HEAD"], repo=tmp_path, runner=fake_runner) == 0
    assert fake_runner.cloud_release_argv == [
        sys.executable, str(tmp_path / "scripts/cloud_release.py"),
        "deploy", "--sha", "HEAD",
    ]
```

- [ ] **Step 2: 运行测试确认 CLI 缺失**

Run: `python -m pytest scripts/tests/test_dev_stack.py -q`

Expected: FAIL with missing `scripts.dev_stack`。

- [ ] **Step 3: 实现解析器与统一错误码**

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Switchable car-agent development stack")
    connection = parser.add_argument_group("cloud connection")
    identity = os.getenv("CAR_AGENT_SSH_IDENTITY")
    connection.add_argument("--host", default=os.getenv("CAR_AGENT_DEPLOY_HOST"))
    connection.add_argument("--user", default=os.getenv("CAR_AGENT_DEPLOY_USER", "ubuntu"))
    connection.add_argument(
        "--identity", type=Path,
        default=Path(identity) if identity else None,
    )
    connection.add_argument(
        "--kex-algorithms",
        default=os.getenv("CAR_AGENT_SSH_KEX_ALGORITHMS"),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    target = commands.add_parser("target").add_subparsers(dest="target_action", required=True)
    target.add_parser("show")
    setter = target.add_parser("set")
    setter.add_argument("value", choices=("local", "cloud"))
    commands.add_parser("status")
    deploy = commands.add_parser("deploy")
    deploy.add_argument("--sha", default="HEAD")
    deploy.add_argument("--apply", action="store_true")
    commands.add_parser("verify")
    commands.add_parser("hmi")
    commands.add_parser("dashboard")
    return parser
```

退出码固定：0 成功，1 运行/验证失败，2 参数、配置或安全门禁失败，3 受控发布计划拒绝。所有 JSON 输出含 `target` 和 `source`。

- [ ] **Step 4: 委托现有 cloud release，不复制发布逻辑**

```python
def cloud_release_argv(repo: Path, action: str, sha: str | None, apply: bool) -> list[str]:
    argv = [sys.executable, str(repo / "scripts/cloud_release.py")]
    if action == "deploy":
        argv.extend(("deploy", "--sha", sha or "HEAD"))
    elif action == "verify":
        argv.append("verify")
    else:
        raise DevStackError("unsupported cloud release action")
    if apply:
        argv.append("--apply")
    return argv
```

`deploy` 仅 cloud 可用；不带 `--apply` 时仍调用现有发布器 dry-run。不得自动 commit/merge/push，不读取本地 Docker daemon，不改变 `.env`。

- [ ] **Step 5: 运行测试和帮助检查**

Run: `python -m pytest scripts/tests/test_dev_stack.py scripts/tests/test_cloud_release.py -q`

Expected: PASS。

Run: `python scripts/dev_stack.py --help`

Expected: exit 0，列出六类动作。

- [ ] **Step 6: 提交**

```bash
git add scripts/dev_stack.py scripts/dev_stack_lib.py scripts/tests/test_dev_stack.py
git commit -m "feat: add unified development stack commands"
```

### Task 5: 让本地 HMI/Dashboard 热更新连接选定后端

**Files:**
- Modify: `scripts/dev_stack_lib.py`
- Modify: `scripts/dev_stack.py`
- Modify: `scripts/tests/test_dev_stack.py`

- [ ] **Step 1: 写前端命令不调用 Docker 的测试**

```python
def test_cloud_hmi_uses_local_vite_and_remote_endpoints(tmp_path, fake_runner):
    command = dev.frontend_command(
        repo=tmp_path,
        app="hmi",
        target=TargetSelection("cloud", "file"),
        endpoints=cloud_endpoints("demo.ts.net"),
        selected_env={"VITE_WS_TOKEN": "secret"},
    )
    assert command.argv[:3] == ("npm", "run", "dev")
    assert command.cwd == tmp_path / "hmi"
    assert command.env["VITE_EDGE_GATEWAY_URL"] == "https://demo.ts.net:8443"
    assert command.env["VITE_AUDIO_API_URL"] == "https://demo.ts.net:8444"
    assert "docker" not in command.argv
```

- [ ] **Step 2: 运行测试确认 frontend command 缺失**

Run: `python -m pytest scripts/tests/test_dev_stack.py -q`

Expected: FAIL for missing `frontend_command`。

- [ ] **Step 3: 实现 HMI 与 Dashboard 环境映射**

```python
def frontend_environment(
    app: str,
    endpoints: StackEndpoints,
    selected_env: Mapping[str, str],
) -> dict[str, str]:
    if app == "hmi":
        return {
            "VITE_EDGE_GATEWAY_URL": endpoints.edge_http,
            "VITE_AUDIO_API_URL": endpoints.audio,
            "VITE_WS_TOKEN": selected_env.get("VITE_WS_TOKEN", ""),
        }
    if app == "dashboard":
        return {
            "VITE_COLLECTOR_URL": endpoints.collector_http,
            "VITE_EDGE_GATEWAY_URL": endpoints.edge_http,
        }
    raise DevStackError("unknown frontend application")
```

local target 同样显式注入 LOCAL_ENDPOINTS，避免依赖散落默认值；cloud HMI 缺少 `VITE_WS_TOKEN` 时 fail closed。子进程继承宿主常规环境，但输出命令时把 token 值替换为 `[REDACTED]`。

- [ ] **Step 4: 实现进程启动**

`hmi` 在 `hmi/`、`dashboard` 在 `dashboard/` 运行 `npm run dev -- --host 127.0.0.1`；只启动 Node/Vite，绝不调用 Compose。保留 localhost 安全上下文，KWS/VAD 继续在浏览器本地推理。

- [ ] **Step 5: 运行 Node 与 Python 测试**

Run: `python -m pytest scripts/tests/test_dev_stack.py -q`

Expected: PASS。

Run: `npm test --prefix hmi`

Expected: PASS。

Run: `npm test --prefix dashboard`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add scripts/dev_stack.py scripts/dev_stack_lib.py scripts/tests/test_dev_stack.py
git commit -m "feat: connect local frontends to selected stack"
```

### Task 6: 扩展 E2E manifest 的远程执行策略

**Files:**
- Modify: `scripts/e2e_contract.py`
- Modify: `test/e2e_manifest.yaml`
- Modify: `scripts/tests/test_e2e_manifest.py`

- [ ] **Step 1: 写远程策略严格 schema 测试**

```python
def test_case_requires_explicit_remote_policy(tmp_path):
    case = _case()
    with pytest.raises(ManifestError, match="remote_safe"):
        load_fixture_manifest(tmp_path, case)


@pytest.mark.parametrize("safe,mutating", [(True, True), ("yes", False), (False, "no")])
def test_remote_policy_rejects_ambiguous_values(tmp_path, safe, mutating):
    case = _case()
    case.update(remote_safe=safe, remote_mutating=mutating)
    with pytest.raises(ManifestError, match="remote"):
        load_fixture_manifest(tmp_path, case)
```

- [ ] **Step 2: 运行测试确认旧 schema 不认识字段**

Run: `python -m pytest scripts/tests/test_e2e_manifest.py -q`

Expected: FAIL for missing required remote policy。

- [ ] **Step 3: 扩展不可歧义的数据模型**

```python
_CASE_KEYS = frozenset({
    "id", "path", "command", "group", "lanes", "timeout_s", "profile",
    "skip_reasons", "signed_identity", "persistent_data", "memory_sessions",
    "nightly", "fixture_pre_step", "remote_safe", "remote_mutating",
})
_REQUIRED_CASE_KEYS = _CASE_KEYS - {"nightly", "fixture_pre_step"}


@dataclass(frozen=True)
class E2ECase:
    # 现有字段保持顺序和语义
    remote_safe: bool
    remote_mutating: bool
```

解析时要求两个字段都是真实 `bool` 且不能同时为 true。语义固定：`true/false`=cloud 缺省允许；`false/true`=只允许精确 ID + `--allow-mutating`；`false/false`=cloud 永久拒绝，直至 case 完成远程适配和独立 review。

- [ ] **Step 4: 为全部现有 case 显式声明**

首批只有以下 case 标为 `remote_safe: true`：

```yaml
  - id: e2e_protocol_smoke
    remote_safe: true
    remote_mutating: false

  - id: e2e_tts_stream
    remote_safe: true
    remote_mutating: false
```

其余现有 case 全部先写 `remote_safe: false`、`remote_mutating: false`；不得凭名称猜安全。新建 `e2e_remote_safe` 在 Task 9 加入。当前没有生产 case 标为 mutating，门禁能力用测试 fixture 验证；以后只有完成远程端点化、前后状态方案和独立 review 的 case 才能改为 `remote_mutating: true`。

- [ ] **Step 5: 更新 EXPECTED 测试结构并跑 manifest 契约**

`scripts/tests/test_e2e_manifest.py::EXPECTED` 的每个 tuple 增加最后两个布尔位；`_case()` fixture 也显式带两个字段。

Run: `python -m pytest scripts/tests/test_e2e_manifest.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add scripts/e2e_contract.py test/e2e_manifest.yaml scripts/tests/test_e2e_manifest.py
git commit -m "feat: declare remote execution policy for every e2e case"
```

### Task 7: 实现 E2E target 解析、端点注入和 cloud 门禁

**Files:**
- Create: `scripts/e2e_target.py`
- Create: `scripts/tests/test_e2e_target.py`
- Modify: `scripts/run_e2e.py`
- Modify: `scripts/tests/test_run_e2e.py`

- [ ] **Step 1: 写 local 保持原行为与 cloud 选择门禁测试**

```python
def test_local_target_keeps_existing_default_selection(manifest, args):
    selected, full = select_for_target(manifest, args, target="local")
    assert selected == existing_default_cases(manifest)
    assert full is True


def test_cloud_default_selects_only_remote_safe_cases(manifest, args):
    selected, full = select_for_target(manifest, args, target="cloud")
    assert selected
    assert all(case.remote_safe for case in selected)
    assert full is False


def test_cloud_mutating_requires_exact_id_switch_and_policy(manifest):
    args = parse("--target cloud --allow-mutating")
    with pytest.raises(RunnerArgumentError, match="exact --id"):
        select_for_target(manifest, args, target="cloud")
    args = parse("--target cloud --id e2e_fixture_mutating")
    with pytest.raises(RunnerArgumentError, match="allow-mutating"):
        select_for_target(manifest, args, target="cloud")
```

- [ ] **Step 2: 运行测试确认 target 层缺失**

Run: `python -m pytest scripts/tests/test_e2e_target.py scripts/tests/test_run_e2e.py -q`

Expected: FAIL for missing target parser/flags。

- [ ] **Step 3: 实现目标解析与端点环境**

```python
@dataclass(frozen=True)
class E2ETarget:
    name: Literal["local", "cloud"]
    endpoints: StackEndpoints
    release_sha: str | None


def endpoint_environment(target: E2ETarget) -> dict[str, str]:
    return {
        "WS_URL": target.endpoints.edge_ws,
        "EDGE_HTTP_URL": target.endpoints.edge_http,
        "AUDIO_API_URL": target.endpoints.audio,
        "VITE_AUDIO_API_URL": target.endpoints.audio,
        "E2E_AUDIO_API_ORIGIN": target.endpoints.audio,
        "COLLECTOR_URL": target.endpoints.collector_http,
        "COLLECTOR_WS_URL": target.endpoints.collector_ws,
        "HMI_URL": target.endpoints.hmi,
        "DASHBOARD_URL": target.endpoints.dashboard,
        "E2E_TARGET": target.name,
        "E2E_TARGET_RELEASE_SHA": target.release_sha or "",
    }
```

修改 `_child_environment()` 使用已解析变量，不再在生产路径内自行补 `ws://127.0.0.1:8090/ws`。显式 env 仍可用于隔离单测，但 runner 的 local/cloud 端点只从 `E2ETarget` 生成。

- [ ] **Step 4: 增加 runner 参数并实现 cloud fail-closed 条件**

```python
parser.add_argument("--target", choices=("local", "cloud"))
parser.add_argument("--allow-mutating", action="store_true")
parser.add_argument("--host", default=os.getenv("CAR_AGENT_DEPLOY_HOST"))
parser.add_argument("--user", default=os.getenv("CAR_AGENT_DEPLOY_USER", "ubuntu"))
identity = os.getenv("CAR_AGENT_SSH_IDENTITY")
parser.add_argument(
    "--identity", type=Path,
    default=Path(identity) if identity else None,
)
parser.add_argument("--kex-algorithms", default=os.getenv("CAR_AGENT_SSH_KEX_ALGORITHMS"))
```

`--target` 缺省时读取 `dev-stack.local`；文件缺失即 local。四个连接参数与 `cloud_release.py` 相同，cloud 实跑/取锁必须完整，local 不要求。cloud 模式拒绝：`--canonical`、`--parallel-isolation`、`--full`、lease-child、非 root profile、`signed_identity=true`、fixture pre-step、任何未标 remote-safe 的隐式 case。`--allow-mutating` 仅在全部选择都来自精确 `--id` 且每个 case `remote_mutating=true` 时有效；它不覆盖支付、商户写、真实车控、数据删除或系统配置的人工授权。

- [ ] **Step 5: 把 target/release/case 策略写入脱敏结果**

`_base_summary()` 增加：

```python
{
    "target": args.target if args is not None else None,
    "target_release_sha": None,
    "remote_lock": None,
    "allow_mutating": bool(args.allow_mutating) if args is not None else False,
}
```

每个 selection 条目增加 `remote_safe` 与 `remote_mutating`。结果不得包含 FQDN 查询 token、私钥或 root `.env`。

- [ ] **Step 6: 运行 runner 回归**

Run: `python -m pytest scripts/tests/test_e2e_target.py scripts/tests/test_run_e2e.py scripts/tests/test_e2e_manifest.py -q`

Expected: PASS，现有 local 测试断言不变。

Run: `python scripts/run_e2e.py --target local --check`

Expected: `E2E CHECK: OK` 或既有 staleness 警告，不能启动/停止 Compose。

- [ ] **Step 7: 提交**

```bash
git add scripts/e2e_target.py scripts/run_e2e.py scripts/tests/test_e2e_target.py scripts/tests/test_run_e2e.py
git commit -m "feat: target e2e runs at local or remote stacks"
```

### Task 8: 在 SSH 会话生命周期内持有云端事务锁

**Files:**
- Create: `deploy/cloud/remote-e2e-lock.sh`
- Create: `scripts/cloud_remote_lock.py`
- Modify: `scripts/run_e2e.py`
- Modify: `scripts/cloud_release_lib.py`
- Modify: `scripts/tests/test_cloud_release.py`
- Modify: `scripts/tests/test_cloud_deploy_assets.py`
- Modify: `scripts/tests/test_e2e_target.py`

- [ ] **Step 1: 写远端锁协议与连接中断释放测试**

```python
def test_remote_lock_holds_until_context_exit(fake_popen, ssh_config):
    with RemoteCloudLock(ssh=ssh_config, run_id="e2e-" + "a" * 32, popen=fake_popen) as lock:
        assert lock.identity == "e2e-" + "a" * 32
        assert fake_popen.stdin.closed is False
    assert fake_popen.stdin.closed is True
    assert fake_popen.waited is True


def test_remote_lock_rejects_busy_or_wrong_ack(fake_busy_popen, ssh_config):
    with pytest.raises(RemoteLockError, match="lock"):
        RemoteCloudLock(ssh=ssh_config, run_id="e2e-" + "b" * 32, popen=fake_busy_popen).acquire()
```

- [ ] **Step 2: 运行测试确认锁模块不存在**

Run: `python -m pytest scripts/tests/test_e2e_target.py scripts/tests/test_cloud_deploy_assets.py -q`

Expected: FAIL for missing lock files。

- [ ] **Step 3: 实现 root-owned 远端 hold 协议**

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
readonly SHARED_ROOT="/opt/car-agent/shared"
die() {
  printf 'remote-e2e-lock: %s\n' "$1" >&2
  exit "${2:-1}"
}
source "${SHARED_ROOT}/bin/transaction-lock.sh"
[[ "${EUID}" -eq 0 ]] || die "must run as root"
[[ "${1:-}" == "hold" && "${2:-}" == "--run-id" ]] \
  || die "hold requires --run-id" 2
readonly RUN_ID="${3:-}"
[[ "${RUN_ID}" =~ ^e2e-[0-9a-f]{32}$ ]] || die "invalid run id" 2
transaction_lock_acquire "e2e" \
  || die "cloud transaction lock is held by ${TRANSACTION_LOCK_HOLDER:-unknown}" "$?"
printf 'READY %s\n' "${RUN_ID}"
IFS= read -r _release_signal || true
```

SSH 断开、stdin EOF 或客户端写入换行都会让脚本退出并由内核释放 flock；不创建容易残留的本地/远端布尔锁文件。

- [ ] **Step 4: 实现 Python context manager**

```python
class RemoteCloudLock:
    def acquire(self) -> "RemoteCloudLock":
        command = f"sudo {REMOTE_E2E_LOCK} hold --run-id {self.run_id}"
        self._process = self._popen(
            self.ssh.ssh_argv(command),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        reader = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = reader.submit(self._process.stdout.readline)
        try:
            raw_ack = future.result(timeout=20)
        except TimeoutError as exc:
            self._process.terminate()
            self._process.wait(timeout=5)
            reader.shutdown(wait=True)
            raise RemoteLockError("remote lock acknowledgement timed out") from exc
        reader.shutdown(wait=True)
        ack = raw_ack.decode("utf-8", errors="replace").strip()
        if ack != f"READY {self.run_id}":
            detail = self._process.stderr.read(4096).decode("utf-8", errors="replace")
            raise RemoteLockError(redact_lock_error(detail))
        return self

    def release(self) -> None:
        if self._process is None:
            return
        try:
            if self._process.poll() is None:
                self._process.stdin.write(b"\n")
                self._process.stdin.close()
                self._process.wait(timeout=15)
        finally:
            if self._process.poll() is None:
                self._process.terminate()
                self._process.wait(timeout=5)
            self._process = None
```

run ID 由本地 `secrets.token_hex(16)` 生成，不含用户/主机信息。错误只报告 `release|rollback|backup|migration|e2e|unknown` 占用类别。

- [ ] **Step 5: 把 remote-safe 与 mutating cloud run 包在锁内**

`run_e2e.main()` 在选择、dry-run/check 和 staleness 结束后、启动任何 child 前获取锁；所有 child 完成或异常退出的 `finally` 中释放。`status`、`--check`、`--dry-run` 不取写锁。summary 示例只记录 `remote_lock={"kind":"e2e","run_id":"e2e-0123456789abcdef0123456789abcdef"}`。

- [ ] **Step 6: 纳入受控基础设施与测试**

把 `remote-e2e-lock.sh` 加入 `SHARED_SCRIPT_NAMES`、远程 preflight 的 `SCRIPTS`/`REQUIRED_INSTALLED`，目标 `/opt/car-agent/shared/bin/remote-e2e-lock.sh`、root:root `0755`。

Run: `bash -n deploy/cloud/remote-e2e-lock.sh`

Expected: exit 0。

Run: `python -m pytest scripts/tests/test_e2e_target.py scripts/tests/test_cloud_release.py scripts/tests/test_cloud_deploy_assets.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add deploy/cloud/remote-e2e-lock.sh scripts/cloud_remote_lock.py scripts/run_e2e.py scripts/cloud_release_lib.py scripts/tests/test_cloud_release.py scripts/tests/test_cloud_deploy_assets.py scripts/tests/test_e2e_target.py
git commit -m "feat: serialize remote e2e with cloud transactions"
```

### Task 9: 增加首个真正访问云端的 remote-safe 探针

**Files:**
- Create: `test/e2e_remote_safe.py`
- Modify: `test/e2e_manifest.yaml`
- Modify: `scripts/tests/test_e2e_manifest.py`
- Modify: `scripts/tests/test_e2e_target.py`

- [ ] **Step 1: 写探针端点、隔离 ID 和禁用记忆的契约测试**

```python
def test_remote_safe_probe_uses_only_runner_endpoints_and_isolated_identity():
    source = (ROOT / "test/e2e_remote_safe.py").read_text(encoding="utf-8")
    for name in ("HMI_URL", "EDGE_HTTP_URL", "WS_URL", "AUDIO_API_URL",
                 "DASHBOARD_URL", "COLLECTOR_URL", "COLLECTOR_WS_URL"):
        assert f'os.environ["{name}"]' in source
    assert "localhost" not in source
    assert '"memory_enabled": False' in source
    assert "docker" not in source.lower()
    assert "subprocess" not in source
```

- [ ] **Step 2: 运行测试确认探针不存在**

Run: `python -m pytest scripts/tests/test_e2e_target.py -q`

Expected: FAIL for missing `test/e2e_remote_safe.py`。

- [ ] **Step 3: 实现五入口 HTTPS/WSS 连通检查**

```python
def require_http_200(name: str, url: str) -> None:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RemoteSafeError(f"{name} returned HTTP {response.status}")


async def collector_stream_probe(url: str) -> None:
    async with websockets.connect(url, open_timeout=20, close_timeout=5):
        return
```

检查 HMI `/`、Edge `/healthz`、Audio `/api/llm/providers`、Dashboard `/`、Collector `/healthz` 与 Collector `/stream`；解析 provider catalog 时只记录 provider/model 名，不记录 key 状态或凭证。

- [ ] **Step 4: 实现一个隔离、非长期记忆的 Edge round-trip**

```python
async def edge_round_trip(recorder: CaseRecorder) -> str:
    ws_auth = required_secret("VITE_WS_TOKEN")
    ws_url = append_query_token(os.environ["WS_URL"], ws_auth)
    trace_id = "remote-" + uuid.uuid4().hex
    payload = {
        "text": "你好，请只回复一句简短问候",
        "session_id": recorder.session_id(1),
        "is_confirmation": False,
        "meta": {
            "trace_id": trace_id,
            "memory_enabled": False,
            "e2e_run_id": os.environ["E2E_RUN_ID"],
        },
    }
    async with websockets.connect(ws_url, open_timeout=20, ping_interval=None) as socket:
        ack = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
        if ack.get("type") != "hello_ack":
            raise RemoteSafeError("edge websocket identity acknowledgement failed")
        await socket.send(json.dumps(payload, ensure_ascii=False))
        final = await wait_final(socket, timeout_s=90)
    await wait_collector_trace(os.environ["COLLECTOR_URL"], trace_id, timeout_s=30)
    return trace_id
```

任何异常信息在写结果前用 `support.e2e._redact_text` 处理，严禁输出带 token 的完整 URL。允许留下独立 session/trace 作为 badcase 证据；不创建提醒、场景、支付草稿、声纹或长期记忆，也不清理云端数据。

- [ ] **Step 5: 加入 manifest**

```yaml
  - id: e2e_remote_safe
    path: test/e2e_remote_safe.py
    command: [python, test/e2e_remote_safe.py]
    group: default
    lanes: [milestone]
    timeout_s: 180
    profile: root
    skip_reasons: [forbid]
    signed_identity: false
    persistent_data: true
    memory_sessions: 0
    remote_safe: true
    remote_mutating: false
```

这里 `persistent_data=true` 表示 Collector/session 会留下隔离证据，不代表允许修改长期业务数据。

- [ ] **Step 6: 跑契约测试**

Run: `python -m pytest scripts/tests/test_e2e_manifest.py scripts/tests/test_e2e_target.py -q`

Expected: PASS。

Run: `python scripts/run_e2e.py --target cloud --id e2e_remote_safe --dry-run`

Expected: exit 0；selection 只有 `e2e_remote_safe`，不连接 SSH、不读取 Docker。

- [ ] **Step 7: 提交**

```bash
git add test/e2e_remote_safe.py test/e2e_manifest.yaml scripts/tests/test_e2e_manifest.py scripts/tests/test_e2e_target.py
git commit -m "test: add isolated remote-safe cloud probe"
```

### Task 10: 完成 `dev_stack verify` 与可审计结果

**Files:**
- Modify: `scripts/dev_stack.py`
- Modify: `scripts/dev_stack_lib.py`
- Modify: `scripts/tests/test_dev_stack.py`

- [ ] **Step 1: 写 verify 顺序与 local/cloud 分流测试**

```python
def test_cloud_verify_runs_release_verify_then_remote_safe_runner(tmp_path, fake_runner):
    rc = cli.main(["verify"], repo=tmp_path, runner=fake_runner, target_override="cloud")
    assert rc == 0
    assert fake_runner.calls[0].argv[-1] == "verify"
    assert fake_runner.calls[1].argv[-4:] == (
        "--target", "cloud", "--id", "e2e_remote_safe",
    )


def test_local_verify_keeps_existing_e2e_semantics(tmp_path, fake_runner):
    cli.main(["verify"], repo=tmp_path, runner=fake_runner, target_override="local")
    assert "--target" in fake_runner.calls[0].argv
    assert "local" in fake_runner.calls[0].argv
    assert "cloud_release.py" not in " ".join(fake_runner.calls[0].argv)
```

- [ ] **Step 2: 运行测试确认 verify 尚未编排**

Run: `python -m pytest scripts/tests/test_dev_stack.py -q`

Expected: FAIL on expected child ordering。

- [ ] **Step 3: 实现 cloud verify**

Cloud verify 固定顺序：

```text
1. cloud_release.py verify（远端 30 容器、五入口、WSS、数据依赖、备份 timer）。
2. run_e2e.py --target cloud --id e2e_remote_safe。
3. 合并两段脱敏 JSON，写入形如 `.artifacts/dev-stack-verifications/20260817T010203Z-abcdef0.json` 的文件。
```

第二段自己获取 remote E2E 锁；第一段 release verify 在其事务结束后释放锁。任一段失败即整体失败，不回退到 local 地址。

- [ ] **Step 4: 实现 local verify**

Local verify 调用 `python scripts/run_e2e.py --target local --check`；不自动 `make up`。需要完整本地 E2E 时由调用者显式执行现有 lane/ID 命令。

- [ ] **Step 5: 记录证据字段并脱敏**

```python
@dataclass(frozen=True)
class VerificationEvidence:
    target: str
    release_sha: str | None
    provider: str | None
    model: str | None
    case_ids: tuple[str, ...]
    lock_kind: str | None
    passed: bool
    verified_at: str
```

artifact 文件 `0600`；不得包含 token、私钥路径内容、DSN、`.env`、用户对话正文或完整远端日志。

- [ ] **Step 6: 运行测试并提交**

Run: `python -m pytest scripts/tests/test_dev_stack.py scripts/tests/test_e2e_target.py scripts/tests/test_run_e2e.py -q`

Expected: PASS。

```bash
git add scripts/dev_stack.py scripts/dev_stack_lib.py scripts/tests/test_dev_stack.py
git commit -m "feat: verify the selected development stack"
```

### Task 11: 补齐运行指南与切换边界

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `docs/dev-guide.md`
- Modify: `test/README.md`
- Modify: `deploy/cloud/README.md`

- [ ] **Step 1: 在 AGENTS.md/CLAUDE.md 固化 agent 行为**

加入以下不可歧义规则：

```text
真栈动作前先运行 python scripts/dev_stack.py target show。
target=cloud 时禁止为了“补环境”启动本地 Compose；本地只跑单测、静态门禁和 Vite。
target=local 时仍只用根 compose.yaml / make up，根 .env 仍是唯一运行时来源。
dev-stack.local 缺失即 local；损坏时 fail closed。
deploy 不自动 commit/merge/push；git push 仍单独授权。
cloud E2E 缺省只跑 remote_safe；--allow-mutating 不替代项目红线授权。
```

- [ ] **Step 2: 在开发指南给出三条日常路径**

```powershell
# A. 纯代码/单测：不需要 Docker
python -m pytest path/to/changed_tests.py -q

# B. 本地前端连接云端后端：只启动 Vite
python scripts/dev_stack.py target show
python scripts/dev_stack.py hmi
python scripts/dev_stack.py dashboard

# C. 已提交 main 的后端更新：先 dry-run，再单独授权 apply
python scripts/dev_stack.py deploy --sha HEAD
python scripts/dev_stack.py deploy --sha HEAD --apply
python scripts/dev_stack.py verify
```

另写切回 local：`target set local → 启动 Docker Desktop → make up → status`。工具本身不自动启动 Docker。

- [ ] **Step 3: 在 test/README.md 写 remote-safe 与高影响门禁**

明确三种 manifest 状态、cloud 禁用本地 Compose/profile/fixture、独立 run/user/session、证据字段和锁。给出允许命令：

```powershell
python scripts/run_e2e.py --target cloud --dry-run
python scripts/run_e2e.py --target cloud --id e2e_remote_safe
```

高影响示例只展示一个 fixture 名 `e2e_reviewed_mutation`，并注明只有 manifest 已标 `remote_mutating: true`、取得本轮精确人工授权后才可运行：

```powershell
python scripts/run_e2e.py --target cloud --id e2e_reviewed_mutation --allow-mutating
```

- [ ] **Step 4: 在云部署文档写锁和发布关系**

`status` 不取锁；release/rollback/backup/data migration/remote E2E 共用 `/opt/car-agent/shared/locks/release.lock`；冲突立即失败并报告类别。说明 HMI/Dashboard 本地 Vite 仍经 Tailnet HTTPS/WSS，KWS/VAD 在浏览器本地运行。

- [ ] **Step 5: 文档与路径一致性检查**

Run: `rg -n "dev_stack|dev-stack.local|remote_safe|remote_mutating|allow-mutating|release.lock" AGENTS.md CLAUDE.md docs/dev-guide.md test/README.md deploy/cloud/README.md`

Expected: 五份文档均有相应权威说明，没有第二份 env 或第二套云栈。

- [ ] **Step 6: 提交**

```bash
git add AGENTS.md CLAUDE.md docs/dev-guide.md test/README.md deploy/cloud/README.md
git commit -m "docs: make remote cloud stack the switchable true-stack path"
```

### Task 12: 全量专项验证、独立 review 与暂不切换

**Files:**
- Verify only; no new source file is required.

- [ ] **Step 1: 跑 Python 专项套件**

Run: `python -m pytest scripts/tests/test_dev_stack.py scripts/tests/test_e2e_target.py scripts/tests/test_run_e2e.py scripts/tests/test_e2e_manifest.py scripts/tests/test_cloud_release.py scripts/tests/test_cloud_deploy_assets.py -q`

Expected: 全绿。

- [ ] **Step 2: 跑现有 E2E 包装与 lease 回归**

Run: `python -m pytest scripts/tests/test_e2e_wrappers_ci.py scripts/tests/test_e2e_stack_lease.py scripts/tests/test_e2e_profiles.py -q`

Expected: 全绿；不得与真实 journeys 或 Docker build 并行。

- [ ] **Step 3: 跑前端与静态检查**

Run: `npm test --prefix hmi`

Expected: PASS。

Run: `npm test --prefix dashboard`

Expected: PASS。

Run: `bash -n deploy/cloud/remote-e2e-lock.sh`

Expected: exit 0。

Run: `git diff --check main...HEAD`

Expected: 无输出，exit 0。

- [ ] **Step 4: 独立安全 review**

Review 必须验证：target 缺失保持 local；损坏 fail closed；cloud status/deploy/HMI/Dashboard 不启动本地 Docker；发布仍要求干净、已提交且 main 可达；cloud E2E 未标记即拒绝；mutating 必须精确 ID+开关+锁；锁断线释放；任何 token/私钥/DSN 不进 JSON、日志或 Git；没有 `.env`、CI、schema、Tailscale 或安全组修改。

- [ ] **Step 5: 在基础设施安装或云端真跑前暂停取得授权**

列出目标 commit、`remote-e2e-lock.sh` 与公共 lock 的受控摘要、远端安装路径、预期 remote-safe case、不会执行的高影响面。没有明确授权时只保留本地实现和 dry-run。

- [ ] **Step 6: 经授权安装脚本并刷新基础设施批准锚**

只安装目标提交中的受控脚本并更新 `/opt/car-agent/shared/release-infrastructure.json`；不改 `.env`、Tailscale Serve、安全组、systemd 或 CI/CD。

- [ ] **Step 7: 在不改持久 target 的前提下真跑 remote-safe verify**

执行 `target show`（仍应 local）后，直接运行 `cloud_release.py verify` 和 `run_e2e.py --target cloud --id e2e_remote_safe`；同时用 `test_cloud_hmi_uses_local_vite_and_remote_endpoints` 锁定 HMI/Dashboard 的 cloud 环境映射。这一步不启动前端、不写 `dev-stack.local=cloud`，因此不影响另一个 agent。记录实际 release SHA、provider/model、case 与锁身份。

- [ ] **Step 8: 维持缺省 local，等待最终数据迁移检查点**

实现结束时确认 `dev-stack.local` 不存在或仍是 `target=local`，本地 Docker 不停止。只有本地数据迁云计划的第二阶段 `final` 覆盖、云端 release verify、remote-safe 验收和用户确认全部完成后，才单独执行：

```powershell
python scripts/dev_stack.py target set cloud
python scripts/dev_stack.py status
python scripts/dev_stack.py hmi
python scripts/dev_stack.py dashboard
```

切换只改变当前工作区的本地 target 文件，不删除本地容器或卷；以后可用 `target set local` 恢复本地语义。
