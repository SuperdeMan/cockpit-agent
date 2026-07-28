# M-A 可信尺子 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用单一 manifest/runner、完整结果协议、synthetic owner 隔离、动态隐私/架构守卫和可复算 canonical 取代当前两套手工 E2E 数组与“skip 也绿”的口径，为 M-B 至 M-D 提供可信验收尺子。

**Architecture:** `test/e2e_manifest.yaml` 是唯一验收 inventory，`runtime/privacy_registry.py`
是生产 privacy target/adapter registry，两者由 `--check` 逐字段同步；`scripts/run_e2e.py`
负责选集、进程树、结构化结果、stale/canonical 和 stack identity lease，PS/sh 仅透传。每个
child 通过 `test/support/e2e.py` 原子写结果并使用 runner 在启动前即时签发的短期 WS token。
隐私与中央源码守卫从真实 SQL/模块注册/manifest 动态发现，不靠固定黑名单。

**Tech Stack:** Python、PyYAML、pytest、Go Edge Gateway、HMAC-SHA256、Docker Compose、GitHub Actions、Node/Go/Python test runners。

> **本轮授权已取得（2026-07-28）：** 用户已明确授权本计划范围内的 Docker/CI、两个本地提交与
> 当前分支 push；执行者无需为这些已列明动作重复停点。授权不外溢到本计划外的 `.env`、密钥、
> 数据删除或 git 历史改写。
>
> 本计划在业务实现开始前已跟踪，执行期保持只读；checkbox 进度只记录在外部任务状态中，
> 不改写、不暂存本文件。

---

### Task 1：建立 manifest schema 与现有 35 脚本加 protocol smoke 的单一 inventory

**Files:**

- Create: `test/e2e_manifest.yaml`
- Create: `scripts/e2e_contract.py`
- Create: `scripts/tests/test_e2e_manifest.py`

- [ ] 写 RED：
  - 新增 `test/e2e_new.py` 未登记；
  - path/id 重复；
  - 主分组不是 `default/security/provider_probe/acoustic_probe/manual_inspection`；
  - 把 nightly/full 误作主分组；
  - command/path 不存在；
  - 非法 skip reason、timeout、profile；
  - `timeout_s>1800`；
  - 同一路径重复登记不同 nightly 参数。

```powershell
python -m pytest scripts/tests/test_e2e_manifest.py -q
```

预期：因 contract/manifest 不存在而失败。

- [ ] 在 `e2e_contract.py` 用严格 dataclass/schema 解析；未知顶层键、未知 case 键、空选集、重复项均是 manifest error，runner 退出 2。

- [ ] 登记当前全部 35 个脚本，每个 path 恰好一次。主分组固定：
  - security：auth、mtls；
  - provider_probe：planner_toolcall、real_providers、rejection、s2s、s2s_probe、s2s_resilience、strict_stack、tts_stream、vision、voice_loop；
  - acoustic_probe：voiceprint_probe；
  - manual_inspection：observability；
  - default：其余断言型脚本，voiceprint 功能验收也在 default。

nightly 子选集不复制 manifest path：child 从 `E2E_LANE=nightly` 选择自己声明的 mock-safe cases；milestone 不带 journey corpus filter。

- [ ] 将下表逐行转录为最终 36-entry inventory（35 个现有脚本 + protocol smoke），不留实现者
  现场归类。lane 缩写 C/N/M=`ci/nightly/milestone`；skip F/P/H/R=`forbid`/
  `credential_unavailable|provider_unavailable`/`hardware_unavailable`/
  `manual_review_required`；S/P 分别是 `signed_identity/persistent_data`：

| id | group | lanes | timeout | profile | skip | S/P | memory_sessions | nightly child args |
|---|---|---|---:|---|---|---|---:|---|
| e2e_protocol_smoke | default | C,N,M | 30 | root | F | no/no | 0 | all |
| e2e_auth | security | M | 300 | auth | F | no/yes | 0 | — |
| e2e_central_hub_assertions | default | N,M | 300 | root | F | yes/yes | 0 | `--case t0_hvac_local --case safety_window_speed_gate --case cloud_chitchat_streaming` |
| e2e_context | default | N,M | 300 | root | F | yes/yes | 0 | `--case ctx_injection_blocked --case ctx_bare_confirm_no_pending --case ctx_trip_plan_fallback --case ctx_trip_modify_fallback` |
| e2e_degrade | default | N,M | 600 | root | F | yes/yes | 0 | all |
| e2e_geofence | default | M | 600 | root | F | yes/yes | 0 | — |
| e2e_journeys | default | N,M | 1800 | root | F | yes/yes | 0 | `--lane mock --no-badcase` |
| e2e_ledger | default | M | 600 | root | F | yes/yes | 0 | — |
| e2e_mcp | default | M | 900 | root | F | yes/yes | 0 | — |
| e2e_memory | default | N,M | 900 | root | F | yes/yes | 2 | `--case privacy_targeting --case compliance` |
| e2e_memory_graph | default | M | 900 | root | F | yes/yes | 2 | — |
| e2e_mtls | security | M | 600 | mtls | F | yes/yes | 0 | — |
| e2e_obs | default | M | 300 | root | F | yes/yes | 0 | — |
| e2e_observability | manual_inspection | — | 900 | root | R | yes/yes | 0 | — |
| e2e_planner_toolcall | provider_probe | M | 600 | real | P | yes/yes | 0 | — |
| e2e_proactive | default | M | 600 | root | F | yes/yes | 0 | — |
| e2e_process_region | default | M | 300 | root | F | yes/yes | 0 | — |
| e2e_real_providers | provider_probe | M | 1200 | real | P | no/no | 0 | — |
| e2e_rejection | provider_probe | M | 600 | real | P | yes/yes | 0 | — |
| e2e_reminder | default | M | 600 | root | F | yes/yes | 0 | — |
| e2e_research | default | N,M | 600 | root | F | yes/yes | 0 | all |
| e2e_research_async | default | N,M | 900 | root | F | yes/yes | 0 | all |
| e2e_resilience | default | N,M | 900 | root | F | yes/yes | 0 | all |
| e2e_s2s | provider_probe | M | 1200 | real | P | yes/yes | 0 | — |
| e2e_s2s_probe | provider_probe | M | 900 | real | P | yes/yes | 0 | — |
| e2e_s2s_resilience | provider_probe | M | 1200 | real | P | yes/yes | 0 | — |
| e2e_scene | default | M | 900 | root | F | yes/yes | 0 | — |
| e2e_strict_stack | provider_probe | M | 600 | real | P | yes/yes | 0 | — |
| e2e_trip | default | N,M | 600 | root | F | yes/yes | 0 | all |
| e2e_tts_stream | provider_probe | M | 600 | real | P | no/no | 0 | — |
| e2e_verify | default | M | 600 | root | F | yes/yes | 0 | — |
| e2e_vision | provider_probe | M | 900 | real | P | yes/yes | 0 | — |
| e2e_voice_loop | provider_probe | M | 900 | real | P | yes/yes | 0 | — |
| e2e_voiceprint | default | M | 900 | root | F | yes/yes | 4 | — |
| e2e_voiceprint_probe | acoustic_probe | — | 1800 | acoustic | H | no/no | 0 | — |
| e2e_ws | default | N,M | 300 | root | F | yes/yes | 0 | all |

`—` 表示该 lane 不选择，而不是跳过。milestone 选集不含 acoustic/manual 两组；它们分别通过
显式 `--group` 产生独立工件，不能混入自动 canonical 通过率。

- [ ] 实现 `discover_e2e_files()`，以 `test/e2e_*.py` 为无例外真值集合；helper 固定放在
  `test/support/e2e.py`，不得靠 manifest exclusion 制造逃逸口。新增/删除脚本都会让
  `--check` 失败。

- [ ] 复测：

```powershell
python -m pytest scripts/tests/test_e2e_manifest.py -q
```

预期：全部通过，inventory 恰覆盖 35 个现有脚本和后续新增 protocol smoke。

### Task 2：实现 staged privacy inventory 与动态完整性检查

**Files:**

- Modify: `test/e2e_manifest.yaml`
- Create: `runtime/privacy_registry.py`
- Modify: `scripts/e2e_contract.py`
- Modify: `scripts/tests/test_e2e_manifest.py`
- Modify: `memory/store.py`
- Modify: `agents/_sdk/ledger.py`
- Modify: `agents/reminder/src/store.py`
- Modify: `agents/scene_orchestrator/src/store.py`
- Modify: `payment-gateway/store.py`
- Modify: `proactive/governor.py`
- Modify: `agents/mcp_bridge/src/agent.py`
- Modify: `observability/collector/db.py`

- [ ] 写 RED：
  - SQL 新增带 user_id/occupant_id 的表但未登记；
  - SQLite 新增 `user_text/speech/prompt_tail/content_head/msg/attrs/note/error` 任一原文列，
    即使没有 owner 列也未登记；
  - 非 SQL `PERSONAL_DATA_TARGETS` 候选未登记；
  - lifecycle 非法；
  - `enforced_from=M-X`；
  - deletable 缺 seed/delete、retained/external 缺 reason/action；
  - future target 缺精确未来 case/action id；
  - M-A 试图执行 enforced_from=M-B 的 target；
  - M-B 到期后 target action 仍不可解析；
  - manifest privacy target 与 `runtime/privacy_registry.py` 的 id/adapter_key/lifecycle/enforced/
    adapter/storage_variants 任一漂移。

- [ ] 新建 `runtime/privacy_registry.py`，只含生产可导入的 `PrivacyTargetSpec`、adapter key、
  全里程碑 target registry 与显式顺序映射驱动的 `targets_for_milestone(name)`；不 import
  `test/`、YAML 或 llm-gateway 实现，不用字符串大小比较里程碑。生产 saga 后续只读这份
  registry；manifest 是验收镜像，`--check` 逐字段校验同步。

- [ ] 在模块中声明静态可读的 `PERSONAL_DATA_TARGETS`；scanner 同时读 SQL/schema/migration、
  原文列候选和这些常量。不得按列名自动决定法律 lifecycle，只负责“必须有且仅有一个分类”。
  manifest 的 `sql_sources` 必含 `**/*.sql`、`**/migrations/**/*.py`、`**/pg_store.py`、
  `**/db.py`、`**/store.py`，`personal_content_columns` 固定为
  `user_text/speech/prompt_tail/content_head/msg/attrs/note/error`，确保 Python 内嵌 SQLite
  schema 也进入发现集。
  `observability/collector/db.py` 必须把 `turns/spans/llm_calls/logs` 声明为同一个
  `observability_raw_content` storage variants，并固定 M-B 的 owner 补齐、无 owner 入库前脱敏、
  legacy 脱敏与四表 probe action。

- [ ] 初始 manifest 至少登记：

| target | adapter | lifecycle / enforced | seed | count | read | action | verify |
|---|---|---|---|---|---|---|---|
| `memory_item` | memory | deletable / M-A | `gdpr_ma_memory_item_seed` | `gdpr_ma_memory_item_count` | `gdpr_ma_memory_item_read` | `forget_user` | `gdpr_ma_memory_item_verify` |
| `memory_relation` | memory | deletable / M-A | `gdpr_ma_memory_relation_seed` | `gdpr_ma_memory_relation_count` | `gdpr_ma_memory_relation_read` | `forget_user` | `gdpr_ma_memory_relation_verify` |
| `voiceprint` | memory | deletable / M-A | `gdpr_ma_voiceprint_seed` | `gdpr_ma_voiceprint_count` | `gdpr_ma_voiceprint_read` | `forget_user` | `gdpr_ma_voiceprint_verify` |
| `profile_identity` | memory | deletable / M-A | `gdpr_ma_profile_identity_seed` | `gdpr_ma_profile_identity_count` | `gdpr_ma_profile_identity_read` | `forget_user` | `gdpr_ma_profile_identity_verify` |
| `session_history` | memory | deletable / M-A | `gdpr_ma_session_history_seed` | `gdpr_ma_session_history_count` | `gdpr_ma_session_history_read` | `forget_user` | `gdpr_ma_session_history_verify` |
| `profile_places` | memory | deletable / M-B | `gdpr_mb_profile_places_seed` | `gdpr_mb_profile_places_count` | `gdpr_mb_profile_places_read` | `privacy_user_all` | `gdpr_mb_profile_places_verify` |
| `reminder_item` | reminder | deletable / M-B | `gdpr_mb_reminder_item_seed` | `gdpr_mb_reminder_item_count` | `gdpr_mb_reminder_item_read` | `privacy_user_all` | `gdpr_mb_reminder_item_verify` |
| `reminder_shared_state` | reminder | deletable / M-B | `gdpr_mb_reminder_state_seed` | `gdpr_mb_reminder_state_count` | `gdpr_mb_reminder_state_read` | `privacy_user_all` | `gdpr_mb_reminder_state_verify` |
| `scene_item` | scene | deletable / M-B | `gdpr_mb_scene_item_seed` | `gdpr_mb_scene_item_count` | `gdpr_mb_scene_item_read` | `privacy_user_all` | `gdpr_mb_scene_item_verify` |
| `observability_raw_content` | observability | retained_audit / M-B | `gdpr_mb_observability_seed` | `gdpr_mb_observability_count` | `gdpr_mb_observability_read` | `observability_redact_owner` | `gdpr_mb_observability_verify` |
| `task_ledger` | ledger | deletable / M-C | `gdpr_mc_task_ledger_seed` | `gdpr_mc_task_ledger_count` | `gdpr_mc_task_ledger_read` | `privacy_user_all` | `gdpr_mc_task_ledger_verify` |
| `proactive_process_queue` | proactive | deletable / M-C | `gdpr_mc_proactive_queue_seed` | `gdpr_mc_proactive_queue_count` | `gdpr_mc_proactive_queue_read` | `privacy_user_all` | `gdpr_mc_proactive_queue_verify` |
| `payment_order` | payment | retained_audit / M-D | `gdpr_md_payment_order_seed` | `gdpr_md_payment_order_count` | `gdpr_md_payment_order_read` | `payment_redact_owner` | `gdpr_md_payment_order_verify` |
| `mcp_demo_order` | mcp | external_reference / M-D | `gdpr_md_mcp_external_seed` | `gdpr_md_mcp_external_count` | `gdpr_md_mcp_external_read` | `mcp_external_unlink` | `gdpr_md_mcp_external_verify` |

`observability_raw_content.retention_reason` 固定为
`diagnostic_metrics_without_raw_owner_content`：M-B 清空 owner/session 直接映射与原文，只保留
不可反查原 owner 的聚合诊断字段；badcase 标记不得阻止 owner 删除时脱敏。
`payment_order.retention_reason` 固定为
`financial_audit_and_chargeback_window`，只保留获准交易审计字段并移除可选 owner 文本；
`mcp_demo_order.retention_reason` 固定为
`external_merchant_is_system_of_record`，删除时解除本地 owner 映射并返回外部商户处置口径。
两者都必须先建立非零前置，但不得伪装成物理删除。

不得让 scanner 自行决定 lifecycle。Redis/进程内 fallback 与 PG 主存储属于同一个 target 的
`storage_variants`，必须由同一 probe/adapter 全覆盖，不能把 fallback 当成清单外例外。
MCP operation/delivery/report 等尚未创建的目标由对应里程碑按同样字段新增。

- [ ] `--check --milestone M-A` 验证所有分类字段，但执行集只包含 enforced_from≤M-A；`--milestone M-D` 覆盖全清单。未来 case id 可在到期前不存在，但必须是非空稳定 id。

- [ ] 复测：

```powershell
python -m pytest scripts/tests/test_e2e_manifest.py -k "privacy or enforced or raw_content or runtime_registry" -q
python -c "from runtime.privacy_registry import PRIVACY_TARGETS; assert PRIVACY_TARGETS"
```

预期：全部通过。

### Task 3：实现 child 结果协议 helper

**Files:**

- Create: `test/support/__init__.py`
- Create: `test/support/e2e.py`
- Create: `test/test_e2e_support.py`

- [ ] 写 RED，覆盖：
  - pass、pass_with_skips、whole skip、assertion fail；
  - counts 两条守恒式；
  - skip reason 必须稳定 code；
  - 结果 temp file + `os.replace` 原子写；
  - 异常也写 fail；
  - cleanup 失败升级 fail；
  - token/secret/sentinel 不进入 detail/artifact metadata；
  - namespace/user/session/control user 派生；
  - `ws_url()` 只消费预签 token，不读取 secret。

- [ ] 实现 `E2EResult`/`CaseRecorder` context manager。退出规则固定：

```text
PASS/PASS_WITH_SKIPS -> 0
whole SKIP -> 77
FAIL -> 1
```

不得解析 stdout 中的“PASS/SKIP”。

- [ ] 暴露 `run_id()`, `user_id(suffix="")`, `session_id(n)`, `artifact_path()`, `ws_url()` 和精确 cleanup helpers。缺标准 env 直接 fail protocol，不回用 u1。

- [ ] 复测：

```powershell
python -m pytest test/test_e2e_support.py -q
```

预期：全部通过。

### Task 4：实现单一 Python runner 与 0/77/other 校验

**Files:**

- Create: `scripts/run_e2e.py`
- Create: `scripts/tests/test_run_e2e.py`
- Create: `test/e2e_protocol_smoke.py`

- [ ] 用 fake child 写 RED：
  - rc0+完整 pass；
  - rc77+全 skip；
  - rc0+partial；
  - rc0 无/坏 result；
  - rc77 但 executed>0；
  - 非 0/77 声明 pass；
  - counts 不守恒；
  - timeout 杀整个进程树且继续后项；
  - 空选集退出 2；
  - default/milestone 不允许 skip；
  - stale error 退出 3。

- [ ] CLI 精确支持 spec 的 `--check/--group/--lane/--id/--full/--profile/--canonical/--provider/--model/--stale-policy/--milestone/--dry-run`。重复 `--id` 可选择多个局部脚本；任何 `--id` 运行都不是 full/canonical。

- [ ] runner 每 child 创建独立 result/artifact 目录，注入标准 env，验证 JSON schema、run/test id、counts、status/rc 映射；失败不短路后续脚本。

- [ ] `e2e_protocol_smoke.py` 不依赖 Docker，真实启动 helper 的 pass/skip/fail 子进程，用于普通 CI 证明 runner 不是只靠单测 mock。

- [ ] 复测：

```powershell
python -m pytest scripts/tests/test_run_e2e.py test/test_e2e_support.py -q
python scripts/run_e2e.py --id e2e_protocol_smoke
```

预期：全部通过；smoke 总态 PASS。

### Task 5a：实现 signed `e2e.v1` identity gate 与共享 stack lease

**Files:**

- Create: `gateway/edge/e2e_identity.go`
- Create: `gateway/edge/e2e_identity_test.go`
- Create: `llm-gateway/e2e_identity.py`
- Create: `llm-gateway/tests/test_e2e_identity.py`
- Create: `scripts/e2e_identity.py`
- Create: `scripts/e2e_stack_lease.py`
- Create: `scripts/tests/test_e2e_identity.py`
- Create: `scripts/tests/test_e2e_stack_lease.py`
- Create: `scripts/run_go_tests.ps1`
- Create: `scripts/tests/test_run_go_tests_wrapper.py`
- Create: `test/fixtures/e2e_identity_vectors.json`
- Modify: `gateway/edge/auth.go`
- Modify: `gateway/edge/auth_test.go`
- Modify: `gateway/edge/main.go`
- Modify: `llm-gateway/http_server.py`
- Modify: `llm-gateway/tests/test_s2s.py`
- Modify: `deploy/docker-compose.yaml`
- Modify: `.env.example`
- Modify: `scripts/run_e2e.py`
- Modify: `test/support/e2e.py`

- [ ] 先建立 Python/Go 共享向量：valid、payload/signature tamper、expired、
  `exp-iat=1920`、`exp-iat=1921`、未来 iat=+5s/+6s、非 e2e user/run、malformed base64、
  wrong version、missing iat/其他字段；fake clock 另覆盖
  `iat+timeout_s+119` 有效与 `now==exp` 过期。

- [ ] Python signer 与 Go verifier 都实现：

```text
secret = 32 random bytes
payload = canonical JSON(run_id,user_id,vehicle_id,scopes,iat,exp)
signed = ASCII "e2e.v1." + base64url_no_pad(payload)
signature = base64url_no_pad(HMAC-SHA256(secret,signed))
token = signed + "." + signature
```

Go 使用常量时间比较。manifest 固定 `timeout_s<=1800`，runner 在 child 真正启动前即时签发
`iat=now, exp=iat+timeout_s+120`；verifier 检查 `iat<=now+5s`、`now<exp`、
`0<exp-iat<=1920`，只容忍 iat 因秒级取整领先最多 5 秒。禁止在选集/排队阶段预签，也禁止用
`min()` 截掉 120 秒宽限，禁止用 `exp-now` 代替签名内 `exp-iat` 判断最大 TTL。

- [ ] auth RED/GREEN：
  - 默认关闭；
  - enabled 但 secret 空视为配置错误，不退成信任前缀；
  - 正常 auth token 优先；
  - enabled 时才验 e2e.v1；
  - gate 关闭时 e2e.v1 与普通 unknown token 一样走既有 AUTH_REQUIRED/anonymous；
  - gate 开启且 token 以 `e2e.v1.` 开头时，任一验证失败都在 WS upgrade 前硬 401，绝不回落
    anonymous/u1；
  - scopes 只来自签名 payload，客户端 meta 被剥离；
  - 有效测试 token upgrade 后先收到 `e2e_identity_ack`，其中 run/user/vehicle 与签名 payload
    一致；destructive child setup 前必须完成这次 owner 自证；
  - `/api/s2s` 的 `session.start` 在 gate 开启时必须携带同一
    `identity_token`；llm-gateway 用同一向量验签并以 token user 覆盖客户端 `user_id`，
    畸形/过期/跨 user token 在创建 `S2SSession` 前关闭连接；
  - gate 关闭时 S2S 生产行为逐字不变；gate 开启时客户端只传 `user_id` 不构成测试身份；
  - timeout=1800 的 child 在 fake clock `+1919s` 新建/重连 WS 仍通过，`+1920s` 按过期拒绝；
    声明 `exp-iat=1921` 的 token 在签发/验证两侧均拒绝；
  - token/secret 不进日志。

- [ ] Compose 只追加：

```yaml
E2E_IDENTITY_ENABLED: ${E2E_IDENTITY_ENABLED:-false}
E2E_IDENTITY_SECRET: ${E2E_IDENTITY_SECRET-}
```

Edge Gateway 与 llm-gateway 都接收这两个变量；Memory 的抽取能力使用 Task 6 的独立
`E2E_CAPABILITY_*`。只改 `.env.example` 文档，不改实际根 `.env`。

- [ ] runner 对 `signed_identity: true` 选集取得 identity stack lease：
  - 单 runner 是 owner：生成一次 secret，根 Compose force-recreate
    edge-gateway+llm-gateway+memory，三者 ready 后再跑 child，finally 清空临时 process env
    并以默认关闭态重建；
  - 并发协调模式的用户入口固定为
    `python scripts/run_e2e.py --milestone M-A --parallel-isolation 2 --id e2e_memory --id e2e_voiceprint`；
  - 外层 owner 生成 secret、重建一次 edge-gateway+llm-gateway+memory，并在 ACL 受限临时目录
    为两个 sub-run 预签逐 case WS token/control token/memory session bundle；
  - sub-runner 内部参数名固定为 `--lease-child`、`--lease-id`、`--token-bundle`；owner 传实际
    opaque lease id 与已解析绝对 JSON 路径。sub-runner 只拿无 secret bundle 与
    `E2E_STACK_LEASE_ID/E2E_STACK_LEASE_ROLE=child`，检测到 secret 或试图重建即 fail protocol；
  - owner 收齐两个独立退出码与 result 后才恢复默认关闭态；只有 owner 可恢复；
  - child 只拿 `E2E_IDENTITY_TOKEN`，需要时另拿 control user/token，绝不拿 secret；
  - enable/cleanup 失败分别阻断 child/升级 `FAIL(identity_cleanup)`。

- [ ] `scripts/tests/test_e2e_stack_lease.py` 用 fake compose/subprocess 覆盖：单 owner、两个 child
  不重建、bundle 无 secret、两个退出码聚合、一个 child 崩溃仍等待另一 child、restore 恰好一次、
  restore 失败覆盖原 PASS。

- [ ] 为当前无本机 Go 工具链的 Windows 环境实现 `scripts/run_go_tests.ps1`：仓库只读挂载到
  `golang:1.24-bookworm` 的 `/src`，容器内复制到 `/work` 后执行 `go mod tidy` 与传入的
  `go test` package；宿主 `go.mod/go.sum` hash 前后必须不变。空 package 默认 `./...`，Docker
  不可用则非零失败，不假 SKIP。

- [ ] 复测：

```powershell
python -m pytest scripts/tests/test_e2e_identity.py scripts/tests/test_e2e_stack_lease.py llm-gateway/tests/test_e2e_identity.py llm-gateway/tests/test_s2s.py -q
python -m pytest scripts/tests/test_run_go_tests_wrapper.py -q
.\scripts\run_go_tests.ps1 ./gateway/edge
```

预期：共享向量一致，普通 auth 无回归。

### Task 5b：实现可恢复的 root/auth/mTLS/real profile epoch

**Files:**

- Create: `scripts/e2e_profiles.py`
- Create: `scripts/tests/test_e2e_profiles.py`
- Modify: `scripts/run_e2e.py`
- Modify: `test/e2e_auth.py`
- Modify: `test/e2e_mtls.py`
- Modify: `test/e2e_manifest.yaml`

- [ ] 写 RED，使用 fake compose/child 覆盖：
  - `--profile auth` 只选 auth entry；milestone full 则按 manifest 把
    `root/real`、`auth`、`mtls` 三个 epoch 串行执行；
  - 任一 epoch 都不并发重建共享栈；
  - auth epoch 生成随机普通 token，把它映射到本 run synthetic user，并只把 token 交给
    `e2e_auth` child；
  - mTLS epoch 在证书缺失时调用 `powershell -File scripts/gen-certs.ps1`，失败为 preflight
    failure，不是 skip；
  - root/real epoch 可复用同一默认栈，但 real credential/provider preflight 仍逐 entry 执行；
  - epoch 启动失败不跑 child；child 失败后仍恢复默认 profile；恢复失败覆盖原结果；
  - profile/env secret 不写 result、artifact 或命令回显。

```powershell
python -m pytest scripts/tests/test_e2e_profiles.py -q
```

预期：因 profile coordinator 尚不存在而失败。

- [ ] 实现 `ProfileEpoch` 与 `ProfileCoordinator`。环境冻结为：

```text
root/real: 根 Compose 默认值 + 按选集启用 E2E identity/capability gate
auth: AUTH_REQUIRED=true, PERMISSIONS_FAIL_OPEN=false,
      AUTH_TOKENS=${auth_token}:${e2e_user_id}:${e2e_vehicle_id}:vehicle.control,media.control,navigation,food.ordering,location.read,navigation.control,network.external,payment.invoke,
      CLOUD_CHANNEL_TOKEN=${channel_token}, CLOUD_CHANNEL_TOKENS=${channel_token}
mtls: GRPC_TLS=on + 按选集启用 E2E identity/capability gate
```

  `e2e_auth` 的普通 auth token仍映射到 synthetic user，manifest 标记
  `persistent_data=true`；`e2e_mtls` 用签名 `e2e.v1` token 和 synthetic session。

- [ ] 每个 epoch 只通过根 `docker compose -f compose.yaml up -d --build` 重建所需服务：
  auth 重建 edge-gateway/cloud-gateway/edge-orchestrator/cloud-planner；mTLS 是全 mesh profile，
  必须重建根 Compose 全栈。进入 mTLS 前确认 `certs/ca.crt`、`server.crt`、`server.key`
  存在且不在 git；离开 auth/mTLS 后用无临时 env 的根 Compose 恢复默认栈并跑 ready probes。
  临时进程 env 用 `try/finally` 清除，不写实际根 `.env`。

- [ ] 复测：

```powershell
python -m pytest scripts/tests/test_e2e_profiles.py scripts/tests/test_run_e2e.py -q
python scripts/run_e2e.py --milestone M-A --lane milestone --full --dry-run
```

预期：dry-run 明确显示 epoch 顺序、每个 entry 的 profile 和唯一 restore；不显示 token。

### Task 6：允许声明式 E2E memory extraction

**Files:**

- Modify: `memory/server.py`
- Create: `memory/e2e_capability.py`
- Modify: `memory/tests/test_server_rpc.py`
- Create: `memory/tests/test_e2e_capability.py`
- Modify: `scripts/e2e_identity.py`
- Modify: `scripts/run_e2e.py`
- Modify: `scripts/tests/test_e2e_identity.py`
- Modify: `test/support/e2e.py`
- Modify: `test/e2e_manifest.yaml`
- Modify: `deploy/docker-compose.yaml`
- Modify: `.env.example`

- [ ] 写 RED：普通 `e2e-*` session 仍不触发昂贵抽取；runner 预签的 synthetic extraction
  session 会真实触发；篡改 payload/signature、过期、绑定到另一 user/run 的 session 继续被跳过，
  不能靠客户端自填 marker 生效。

- [ ] 不给 AppendTurn proto 增加可伪造 bool。复用 stack lease 的 32-byte
  `E2E_CAPABILITY_SECRET`，以独立 domain `e2emem.v1` 预签 canonical JSON
  `{run_id,user_id,session_id,capability:"memory_extraction",exp}`；最终 session id 为
  `e2e-mem.v1.<payload_base64url>.<signature_base64url>`。Memory 用常量时间比较、校验
  `request.user_id` 与 exp 后才允许绕过 synthetic skip；生产普通会话行为不变。

- [ ] manifest 每个需要抽取的 case 声明 `memory_sessions` 数量。lease owner 预签该数量并只把
  `E2E_MEMORY_SESSION_IDS` JSON 数组交给对应 child；helper 暴露 `memory_session_id(n)`，缺少、
  越界或 user 不匹配均 fail protocol。child 与 sub-runner 都拿不到 capability secret。

- [ ] Compose 只给 Memory 增加默认关闭的
  `E2E_CAPABILITY_ENABLED/E2E_CAPABILITY_SECRET` 透传；identity lease 同时重建
  edge-gateway+llm-gateway+memory，finally 恢复三者关闭。实际根 `.env` 不改。

- [ ] 复测：

```powershell
python -m pytest memory/tests/test_server_rpc.py memory/tests/test_e2e_capability.py scripts/tests/test_e2e_identity.py test/test_e2e_support.py -q
```

预期：全部通过。

### Task 7：迁移高风险 persistent E2E 到 run namespace

**Files:**

- Modify: `test/e2e_memory.py`
- Modify: `test/e2e_memory_graph.py`
- Modify: `test/e2e_voiceprint.py`
- Modify: `test/e2e_ledger.py`
- Modify: `test/e2e_reminder.py`
- Modify: `test/e2e_geofence.py`
- Modify: `test/e2e_scene.py`
- Modify: `test/e2e_proactive.py`
- Modify: `test/e2e_mcp.py`
- Modify: `test/e2e_research_async.py`
- Modify: `test/e2e_s2s.py`
- Modify: `test/e2e_s2s_resilience.py`

- [ ] 每个脚本先写/抽出 contract test，确认当前 `u1`、latest row、全表 count、宽前缀 cleanup 会被 source guard 或 helper preflight 拒绝。

- [ ] 逐脚本改成：
  1. setup 前本 namespace count=0；
  2. 所有 user/session/task/key 由 helper 派生；
  3. 查询带精确 run/test/owner；
  4. finally 只清本 namespace；
  5. cleanup 后 count=0；
  6. cleanup 失败升级 isolation failure。

- [ ] 禁止 `DELETE` 无 WHERE、`ORDER BY created_at DESC LIMIT 1` 猜目标、清整个 u1/Redis DB、按全局测试前缀跨 run 清理。

- [ ] `e2e_memory`：先让 source contract 因 `u1/latest/global cleanup` 失败，再迁移；增加 repeated
  `--case`，nightly 精确选 `privacy_targeting/compliance` 且不产生 skip；运行
  `python scripts/run_e2e.py --id e2e_memory`。
- [ ] `e2e_memory_graph`：同样 RED→GREEN，运行
  `python scripts/run_e2e.py --id e2e_memory_graph`。
- [ ] `e2e_voiceprint`：同样 RED→GREEN，运行
  `python scripts/run_e2e.py --id e2e_voiceprint`。
- [ ] `e2e_ledger`：同样 RED→GREEN，运行
  `python scripts/run_e2e.py --id e2e_ledger`。
- [ ] `e2e_reminder`：同样 RED→GREEN，运行
  `python scripts/run_e2e.py --id e2e_reminder`。
- [ ] `e2e_geofence`：同样 RED→GREEN，运行
  `python scripts/run_e2e.py --id e2e_geofence`。
- [ ] `e2e_scene`：同样 RED→GREEN，运行
  `python scripts/run_e2e.py --id e2e_scene`。
- [ ] `e2e_proactive`：同样 RED→GREEN，运行
  `python scripts/run_e2e.py --id e2e_proactive`。
- [ ] `e2e_mcp`：同样 RED→GREEN，运行
  `python scripts/run_e2e.py --id e2e_mcp`。
- [ ] `e2e_research_async`：同样 RED→GREEN，运行
  `python scripts/run_e2e.py --id e2e_research_async`。
- [ ] `e2e_s2s`：`session.start.identity_token` 使用 helper token，`user_id` 与 token owner
  精确一致；同样 RED→GREEN，运行 `python scripts/run_e2e.py --id e2e_s2s`。
- [ ] `e2e_s2s_resilience`：同样 RED→GREEN，运行
  `python scripts/run_e2e.py --id e2e_s2s_resilience`。

预期：每个 result protocol 合法；可用环境下 PASS；缺环境只在 manifest 允许的 probe group 显式 SKIP。

### Task 8：迁移其余 E2E 到结果协议

**Files:**

- Modify: `test/e2e_auth.py`
- Modify: `test/e2e_central_hub_assertions.py`
- Modify: `test/e2e_context.py`
- Modify: `test/e2e_degrade.py`
- Modify: `test/e2e_journeys.py`
- Modify: `test/e2e_mtls.py`
- Modify: `test/e2e_obs.py`
- Modify: `test/e2e_observability.py`
- Modify: `test/e2e_planner_toolcall.py`
- Modify: `test/e2e_process_region.py`
- Modify: `test/e2e_real_providers.py`
- Modify: `test/e2e_rejection.py`
- Modify: `test/e2e_research.py`
- Modify: `test/e2e_resilience.py`
- Modify: `test/e2e_s2s_probe.py`
- Modify: `test/e2e_strict_stack.py`
- Modify: `test/e2e_trip.py`
- Modify: `test/e2e_tts_stream.py`
- Modify: `test/e2e_verify.py`
- Modify: `test/e2e_vision.py`
- Modify: `test/e2e_voice_loop.py`
- Modify: `test/e2e_voiceprint_probe.py`
- Modify: `test/e2e_ws.py`

- [ ] 先迁移现有“SKIP+return/sys.exit(0)”脚本，确保 whole skip=77、partial=rc0+PASS_WITH_SKIPS；业务断言失败/timeout/provider 返回错误不得改成 skip。

- [ ] `e2e_real_providers.py` 是 pytest 聚合：新增 plugin/入口读取 pytest selected/executed/skipped，不得只看 pytest rc。

- [ ] 无 case-level skip 的脚本可先记录一个 top-level case，但仍必须写完整 counts/result。

- [ ] `e2e_auth`：删除 `demo-u1/u1/auth-*` 默认值，强制消费 profile coordinator 生成的
  `WS_TOKEN`、`E2E_USER_ID` 与 helper session；迁移 whole/partial skip 后运行
  `python scripts/run_e2e.py --profile auth --id e2e_auth`。
- [ ] `e2e_central_hub_assertions`：迁移 case recorder 后运行
  `python scripts/run_e2e.py --id e2e_central_hub_assertions`。
- [ ] `e2e_context`：迁移 case recorder 后运行 `python scripts/run_e2e.py --id e2e_context`。
- [ ] `e2e_degrade`：迁移 case recorder 后运行 `python scripts/run_e2e.py --id e2e_degrade`。
- [ ] `e2e_journeys`：让 selected/executed/skipped 来自真实语料结果，运行
  `python scripts/run_e2e.py --id e2e_journeys`。
- [ ] `e2e_mtls`：WebSocket 使用 `ws_url()` 的签名 token 与 helper session，迁移 profile
  skip 后运行 `python scripts/run_e2e.py --profile mtls --id e2e_mtls`。
- [ ] `e2e_obs`：迁移后运行 `python scripts/run_e2e.py --id e2e_obs`。
- [ ] `e2e_observability`：迁移人工待判状态后运行
  `python scripts/run_e2e.py --id e2e_observability`。
- [ ] `e2e_planner_toolcall`：迁移 provider 结果后运行
  `python scripts/run_e2e.py --id e2e_planner_toolcall`。
- [ ] `e2e_process_region`：迁移后运行 `python scripts/run_e2e.py --id e2e_process_region`。
- [ ] `e2e_real_providers`：接入 pytest plugin 后运行
  `python scripts/run_e2e.py --id e2e_real_providers`。
- [ ] `e2e_rejection`：迁移 provider 结果后运行
  `python scripts/run_e2e.py --id e2e_rejection`。
- [ ] `e2e_research`：迁移后运行 `python scripts/run_e2e.py --id e2e_research`。
- [ ] `e2e_resilience`：迁移后运行 `python scripts/run_e2e.py --id e2e_resilience`。
- [ ] `e2e_s2s_probe`：迁移 provider 结果后运行
  `python scripts/run_e2e.py --id e2e_s2s_probe`。
- [ ] `e2e_strict_stack`：WebSocket 使用 helper token/session，迁移 provider 结果后运行
  `python scripts/run_e2e.py --profile real --id e2e_strict_stack`。
- [ ] `e2e_trip`：迁移后运行 `python scripts/run_e2e.py --id e2e_trip`。
- [ ] `e2e_tts_stream`：迁移 provider 结果后运行
  `python scripts/run_e2e.py --id e2e_tts_stream`。
- [ ] `e2e_verify`：迁移后运行 `python scripts/run_e2e.py --id e2e_verify`。
- [ ] `e2e_vision`：迁移 provider 结果后运行 `python scripts/run_e2e.py --id e2e_vision`。
- [ ] `e2e_voice_loop`：迁移 provider 结果后运行
  `python scripts/run_e2e.py --id e2e_voice_loop`。
- [ ] `e2e_voiceprint_probe`：迁移 acoustic 结果后运行
  `python scripts/run_e2e.py --id e2e_voiceprint_probe`。
- [ ] `e2e_ws`：迁移后运行 `python scripts/run_e2e.py --id e2e_ws`。全部完成前 runner
  不保留旧脚本兼容翻译，任一未迁移即 `FAIL(result_protocol)`。

### Task 9：声纹真值与 GDPR bootstrap

**Files:**

- Create: `scripts/prepare_voiceprint_fixtures.py`
- Create: `scripts/tests/test_prepare_voiceprint_fixtures.py`
- Create: `test/fixtures/voiceprint/README.md`
- Modify: `test/e2e_voiceprint.py`
- Modify: `test/e2e_memory_graph.py`
- Modify: `test/e2e_manifest.yaml`

- [ ] 不把外部 TTS 生成音频提交进仓库。`prepare_voiceprint_fixtures.py` 在每个 run 的独占
  artifact 目录调用当前 `/api/voices` 与 `/api/tts`：选择两个不同 voice id（优先不同 gender），
  对冻结的三句注册文本与一句 probe 文本各生成一份 16k/mono/s16le PCM。少于两个可用音色、
  格式错误或空音频在 milestone 都是 FAIL，不降级成同一音色自证。

- [ ] 同目录写 `voiceprint-fixtures.json`，包含 provider/voice ids、冻结文本、sample rate/
  channels/bit depth、每文件字节数与 SHA-256、生成时间和
  `synthetic_functional_only=true/no_human_biometric=true`；不含凭证。`--verify` 只根据 manifest
  校验格式/hash。README 明确工件不提交、不作为真人识别率，也不主张外部 TTS 音频的再分发许可。

- [ ] 先跑生成器单测，再由 runner 在 `e2e_voiceprint` 前执行真实准备与 verify：

```powershell
python -m pytest scripts/tests/test_prepare_voiceprint_fixtures.py -q
python scripts/prepare_voiceprint_fixtures.py --artifact-dir "$env:TEMP\car-agent-voiceprint-fixtures"
python scripts/prepare_voiceprint_fixtures.py --artifact-dir "$env:TEMP\car-agent-voiceprint-fixtures" --verify
```

- [ ] 声纹 E2E 必做：A/B 各 decision=accept 且 occupant 精确；各自 sentinel；存储和用户可见 recall 双层双向隔离；Forget B 后 B memory/relation/voiceprint=0 且 A 保持；Forget user 后 profile/session 也为零；A/B 危险动作同确认闸。

- [ ] GDPR bootstrap 创建目标 T/对照 C；对 enforced_from=M-A 的每个 deletable target 逐项 seed>0/read sentinel/delete/持久层=0/消费面不可读，对照前后不变，二次删除幂等。未来目标只验证分类，不假执行。

- [ ] 定向：

```powershell
python scripts/run_e2e.py --id e2e_voiceprint
python scripts/run_e2e.py --id e2e_memory_graph
```

预期：两个 default case PASS，无 skip。

### Task 10：把固定源码黑名单替换为动态 manifest+AST 守卫

**Files:**

- Create: `scripts/tests/test_e2e_arch_guard.py`
- Modify: `scripts/e2e_contract.py`
- Modify: `orchestrator/cloud/tests/test_verify.py`
- Modify: `proactive/tests/test_governor.py`
- Modify: `llm-gateway/tests/test_s2s.py`

- [ ] 写 RED：
  - 临时 manifest 新增 `brandnew.query`，中央源码加对应可执行字符串分支，无需改守卫即可失败；
  - 注释/docstring/格式换行不误报；
  - Constant/JoinedStr/container/match/compare/arg 均可检测；
  - 主动 type 通过局部 dict/常量/helper 间接传给 publish 也可发现。

- [ ] 动态词汇来源包括 `agents/*/manifest.yaml`、`orchestrator/edge/knowledge/commands.yaml`、skills/route manifests 和 `runtime/proactive.py` 生产方调用图。AST 只查可执行语义，不扫注释。

- [ ] 守卫目标固定为中央通用机制：
  - `orchestrator/cloud/verify.py`
  - `orchestrator/cloud/executor.py` 的 verify 决策函数
  - `proactive/governor.py`
  - `llm-gateway/s2s/session.py`

- [ ] 复测：

```powershell
python -m pytest scripts/tests/test_e2e_arch_guard.py orchestrator/cloud/tests/test_verify.py proactive/tests/test_governor.py llm-gateway/tests/test_s2s.py -q
```

预期：全部通过。

### Task 11：实现 journeys metadata、digest 与 canonical 资格

**Files:**

- Create: `scripts/tests/test_e2e_canonical.py`
- Modify: `scripts/e2e_contract.py`
- Modify: `scripts/run_e2e.py`
- Modify: `test/e2e_journeys.py`
- Verify: `docs/reviews/eval/journeys_report.json`
- Verify: `docs/reviews/eval/journeys_report.md`

- [ ] 写 RED：
  - 8 pass/1 fail/1 skip 必须显示 pass/selected=8/10；
  - 五类 digest、LF 规范化和 import closure 包含 `test/eval_common.py`；
  - 局部、journey filter、provider/model 未锁、provider 漂移、staged/unstaged canonical input、canonical glob 内 untracked 都拒绝覆盖；
  - glob 外以下四个受保护用户文件不阻断，也不得被读取、改写或暂存：
    `docs/reviews/badcase/2026-07-26.md`、
    `docs/reviews/badcase/2026-07-27.md`、
    `docs/design/README.md`、
    `docs/design/2026-07-28-intent-accuracy-data-flywheel.md`；
  - 只提交报告时 code SHA 是祖先且 digest 相同，不自陈旧；
  - stale warn/error 分别为 warning/退出3。

- [ ] canonical input 至少覆盖 specs 定义的 journeys YAML、runner/manifest/support、相关 test Python import closure、agents/skills/manifests、orchestrator/llm/gateway、proto、hmi、Go mod/sum 和非敏感 runtime config。secret 的值/长度/存在性/hash 均不进 digest。

- [ ] 删除 `--force-report`。不具资格时只写 run artifact；具资格时 temp+atomic replace canonical。写后立即重新计算 digest/freshness。

- [ ] 结果 metadata 保存 resolved runner selection、最终 journey filters、provider/model/revisions、code SHA、tracked input digest、dirty paths 和各状态计数。

- [ ] 复测：

```powershell
python -m pytest scripts/tests/test_e2e_canonical.py -q
```

预期：全部通过。

### Task 12：保存历史复核证据，不重复造实现

**Files:**

- Modify: `orchestrator/cloud/tests/test_planning_toolcall.py`
- Modify: `gateway/cloud/main_test.go`
- Verify: `orchestrator/cloud/tests/test_skills.py`

- [ ] 新增“同一个 PlanBuilder 第一次 env off、第二次 on”的测试，证明 `PLANNER_TOOLCALL` 每次 build 重读，不需重启。

- [ ] 用 Go AST 检查 cloud gateway `handleRequest`：`MarkIfNew` 恰好一次；首次 Handle UNAVAILABLE 后同函数内第二次 Handle；两次间无第二幂等闸。

- [ ] 运行既有 few_shots 消费测试，不改业务实现：

```powershell
python -m pytest orchestrator/cloud/tests/test_planning_toolcall.py orchestrator/cloud/tests/test_skills.py -q
.\scripts\run_go_tests.ps1 ./gateway/cloud
```

预期：全部通过。

### Task 13：收敛 wrapper、Makefile 与 CI

**Files:**

- Modify: `scripts/run_e2e.ps1`
- Modify: `scripts/run_e2e.sh`
- Modify: `Makefile`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/nightly-e2e.yml`

- [ ] PS/sh 只保留定位 repo root、调用 Python、透传 argv/rc；删除脚本数组。

- [ ] 比较：

```powershell
python scripts/run_e2e.py --dry-run --group default
powershell -File scripts/run_e2e.ps1 --dry-run --group default
if (Get-Command bash -ErrorAction SilentlyContinue) {
    bash scripts/run_e2e.sh --dry-run --group default
} else {
    Write-Host '本机无 bash；sh wrapper 等价性由 Linux CI 硬门禁'
}
```

预期：选集顺序、child argv、退出码一致。

- [ ] 普通 CI 固定两步：

```text
python scripts/run_e2e.py --check --stale-policy warn
python scripts/run_e2e.py --lane ci --full --stale-policy warn
```

nightly 固定：

```text
python scripts/run_e2e.py --lane nightly --full --stale-policy warn
```

普通 CI 只选无需 Docker 的 protocol smoke/静态门禁；live-stack case 进入 nightly/milestone，
不宣称 PR CI 已跑真栈。Linux CI 必须比较 Python/PS/sh 三个 wrapper 的 dry-run JSON 与退出码；
Windows 无 bash 不降低 CI 门禁。

- [ ] 定向验证 workflow YAML 和 runner dry-run；不写 actual `.env` 或 secret。

### Task 14：真栈、并发隔离与 milestone canonical

**Files:**

- Modify: `test/README.md`
- Modify: `docs/reviews/eval/README.md`
- Modify: `docs/design/2026-07-14-journey-e2e-test-system.md`
- Modify: `docs/conventions.md`
- Modify: `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`
- Modify: `docs/reviews/eval/journeys_report.json`
- Modify: `docs/reviews/eval/journeys_report.md`
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/specs/2026-07-28-acceptance-residuals-ma-test-truth-design.md`

- [ ] 根 Compose 栈确认：

```powershell
docker compose -f compose.yaml ps postgres redis nats registry
docker compose -f compose.yaml up -d --build --no-deps edge-gateway llm-gateway memory
docker compose -f compose.yaml ps edge-gateway llm-gateway memory
```

预期：running/healthy；根 `.env` hash/mtime 在测试前后不变。

- [ ] 用共享 identity stack lease 并发启动两个 persistent runner（不同 run id），证明
  DB/Redis/session/cleanup 互不读改删；只由 lease owner 启停 Edge/LLM Gateway 与 Memory
  测试能力：

```powershell
python scripts/run_e2e.py --milestone M-A --parallel-isolation 2 --id e2e_memory --id e2e_voiceprint
```

预期：两个 sub-run 都 PASS；服务侧 `e2e_identity_ack.user_id` 分别等于各自
`E2E_USER_ID`；restore 恰好一次。

- [ ] 先运行非 canonical 回归：

```powershell
python -m pytest scripts/tests test/test_e2e_support.py -q
.\scripts\gen-proto.ps1
.\scripts\run_go_tests.ps1 ./gateway/edge ./gateway/cloud
python -m pytest --import-mode=importlib -q
npm --prefix hmi test
npm --prefix hmi run build
npm --prefix dashboard test
npm --prefix dashboard run build
python scripts/run_e2e.py --check --milestone M-A --stale-policy warn
python scripts/run_e2e.py --milestone M-A --lane milestone --full --stale-policy warn
```

预期：0 failed，M-A milestone 无 SKIP/PASS_WITH_SKIPS；identity/capability gate 已恢复关闭。

- [ ] `git diff --check` 后显式暂存 M-A 实现、测试与文档文件；禁止 `git add .`，并确认上述
  四个受保护用户文件不在 index。先提交 canonical inputs：

```powershell
$planPath = 'docs/superpowers/plans/2026-07-28-acceptance-residuals-ma-test-truth.md'
git ls-files --error-unmatch -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-A plan must be tracked before execution' }
git diff --exit-code -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-A plan is execution-time read-only' }
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-ma-test-truth-design.md',
    'AGENTS.md'
)
$implementationPaths = @(
    Select-String -Path $planPath -Encoding utf8 -Pattern '^-\s+(?:Create|Modify):\s+`([^`]+)`' |
        ForEach-Object { $_.Matches[0].Groups[1].Value } |
        Where-Object { $_ -notin $evidencePaths } |
        Sort-Object -Unique
)
$missing = @($implementationPaths | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($missing.Count -ne 0) { throw "M-A planned paths missing: $($missing -join ', ')" }
git diff --check -- $implementationPaths
if ($LASTEXITCODE -ne 0) { throw 'M-A implementation diff check failed' }
git add -- $implementationPaths
$staged = @(git diff --cached --name-only)
$unexpected = @($staged | Where-Object { $_ -notin $implementationPaths })
if ($staged.Count -eq 0 -or $unexpected.Count -ne 0) {
    throw "M-A implementation staging invalid: $($unexpected -join ', ')"
}
git commit -m 'test(e2e): make acceptance evidence explicit and reproducible'
if ($LASTEXITCODE -ne 0) { throw 'M-A implementation commit failed' }
$allowedUserPaths = @(
    'docs/reviews/badcase/2026-07-26.md',
    'docs/reviews/badcase/2026-07-27.md',
    'docs/design/README.md',
    'docs/design/2026-07-28-intent-accuracy-data-flywheel.md'
)
$statusLines = @(git -c core.quotepath=false status --porcelain=v1 --untracked-files=all)
$unexpectedStatus = @(
    foreach ($line in $statusLines) {
        $path = $line.Substring(3)
        if ($path -match ' -> ') { $path = ($path -split ' -> ', 2)[1] }
        if ($path -notin $allowedUserPaths) { $line }
    }
)
if ($unexpectedStatus.Count -ne 0) {
    throw "M-A unexpected worktree state:`n$($unexpectedStatus -join "`n")"
}
```

提交后要求 `git diff --name-only` 与 `git diff --cached --name-only` 对所有
`canonical_inputs` 都为空；只有上述四个受保护用户文件可以留在工作区。

- [ ] 从只读控制面取得当前 runtime active，而不是读取启动默认 `.env`：

```powershell
$runtime = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
$provider = [string]$runtime.active.provider
$model = [string]$runtime.active.model
if ([string]::IsNullOrWhiteSpace($provider) -or [string]::IsNullOrWhiteSpace($model)) {
    throw 'runtime active provider/model unavailable'
}
python scripts/run_e2e.py --milestone M-A --lane milestone --full --canonical --provider $provider --model $model --stale-policy error
if ($LASTEXITCODE -ne 0) { throw 'M-A canonical runner failed' }
$runtimeAfter = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
if ($provider -ne [string]$runtimeAfter.active.provider -or $model -ne [string]$runtimeAfter.active.model) {
    throw 'M-A active provider/model drifted during canonical run'
}
```

M-A metadata 的 `capability_source` 固定为 `bootstrap_static`，revision 从本次提交中的 Gateway/
Planner 非敏感静态配置计算。runner 在执行前后再次 GET 并断言 provider/model 未漂移；M-D
上线前不得引用尚不存在的 GetCapabilities。

预期：canonical 写入成功并立即复算 fresh；不存在 SKIP/PASS_WITH_SKIPS。

- [ ] 只在新鲜证据通过后回写验收报告：
  - P1-06 journeys canonical → 已修；
  - P2-03 SKIP 第三态 → 已修；
  - P2-04 动态 AST 守卫 → 已修；
  - P2-06 few_shots → 历史已修；
  - PLANNER_TOOLCALL/gateway retry → 误判更正。

- [ ] 只把 canonical 报告、验收回写与落地记录显式暂存为第二个提交，再推送两个提交。提交信息：

```powershell
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-ma-test-truth-design.md',
    'AGENTS.md'
)
git add -- $evidencePaths
$staged = @(git diff --cached --name-only)
$unexpected = @($staged | Where-Object { $_ -notin $evidencePaths })
if ($staged.Count -eq 0 -or $unexpected.Count -ne 0) {
    throw "M-A evidence staging invalid: $($unexpected -join ', ')"
}
git commit -m 'docs(review): refresh M-A canonical evidence'
if ($LASTEXITCODE -ne 0) { throw 'M-A evidence commit failed' }
git push origin codex/acceptance-m0a-m4-residuals
if ($LASTEXITCODE -ne 0) { throw 'M-A push failed' }
$statusLines = @(git -c core.quotepath=false status --porcelain=v1 --untracked-files=all)
$unexpectedStatus = @(
    foreach ($line in $statusLines) {
        $path = $line.Substring(3)
        if ($path -match ' -> ') { $path = ($path -split ' -> ', 2)[1] }
        if ($path -notin @(
            'docs/reviews/badcase/2026-07-26.md',
            'docs/reviews/badcase/2026-07-27.md',
            'docs/design/README.md',
            'docs/design/2026-07-28-intent-accuracy-data-flywheel.md'
        )) { $line }
    }
)
if ($unexpectedStatus.Count -ne 0) { throw 'M-A post-push worktree is not clean' }
```

再次确认上述四个受保护用户文件未进 index；本轮 push 已获授权，不重复停点。

## M-A 完成定义

- [ ] 单一 manifest 覆盖全部 E2E；五个主分组精确，lane/full 不混入 group。
- [ ] 所有 child 遵守结构化结果与 0/77/other；SKIP/PASS_WITH_SKIPS 不再显示 PASS。
- [ ] persistent E2E 全用 Gateway 签名裁决的 `e2e-*` owner，失败后精确清理；并发 run 不交叉。
- [ ] privacy inventory 分类立即完整、按 enforced_from 分期执行；M-A memory bootstrap 非零删除与对照证据通过。
- [ ] `runtime/privacy_registry.py` 可在 llm-gateway 镜像的 `/app` PYTHONPATH 下导入，manifest
  只做逐字段同步校验；observability 四表原文目标不会因缺 owner 列逃过分类。
- [ ] signed token 在 child 启动前即时签发，严格覆盖 timeout+120，1920/1921 与过期边界测试通过。
- [ ] 动态架构守卫和 canonical digest/freshness 可复算，dirty/filtered/provider 漂移不能覆盖基线。
- [ ] 原验收卡逐项回写、全量通过、fresh canonical 与分支推送完成。
