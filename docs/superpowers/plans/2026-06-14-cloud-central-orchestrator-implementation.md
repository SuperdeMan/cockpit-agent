# Cloud Central Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the remaining P1, P2, and P3 work from `docs/design/2026-06-14-cloud-central-orchestrator.md`: edge dispatch, bounded adaptive planning, and deterministic in-process tools.

**Architecture:** Preserve T0 and the existing T1 execution path. Route every DAG step through a `UnifiedDispatcher`; cloud agents keep using gRPC, edge steps use a Cloud Gateway unary RPC that multiplexes onto the requesting vehicle's active bidi stream, and tools execute in-process. Adaptive plans use a bounded `LoopController` that reuses `DagExecutor`, session suspension, and aggregation.

**Tech Stack:** Python 3.11+/asyncio/grpcio, Go/grpc-go, protobuf/buf, pytest.

---

### Task 1: Unified Step Dispatch

**Files:**
- Create: `orchestrator/cloud/dispatch.py`
- Modify: `orchestrator/cloud/executor.py`
- Modify: `orchestrator/cloud/main.py`
- Test: `orchestrator/cloud/tests/test_dispatch.py`
- Test: `orchestrator/cloud/tests/test_executor.py`

- [ ] Write failing tests proving cloud, edge, and tool steps select different dispatcher paths.
- [ ] Run the focused tests and confirm the missing dispatcher/API failure.
- [ ] Implement `UnifiedDispatcher.dispatch(step, ctx)` and keep `DagExecutor(call_agent_fn=...)` compatibility for existing tests.
- [ ] Wire production startup through the dispatcher.
- [ ] Run dispatcher and executor tests.

### Task 2: Edge Call Contract and Cloud Gateway Pairing

**Files:**
- Modify: `proto/cockpit/channel/v1/channel.proto`
- Modify: `gateway/cloud/main.go`
- Create: `gateway/cloud/main_test.go`
- Modify generated files through `buf generate proto`

- [ ] Add `EdgeCallEnvelope` and `DispatchToEdge`.
- [ ] Write Go tests for no active vehicle stream, result pairing, and deadline timeout.
- [ ] Run the Go tests and confirm they fail before the gateway implementation.
- [ ] Store the active per-vehicle sender, send a uniquely correlated `DownFrame.edge_call`, and pair `UpFrame.edge_result`.
- [ ] Regenerate protobuf code and run Go tests.

### Task 3: Edge Execution Through VAL

**Files:**
- Create: `orchestrator/edge/edge_call.py`
- Modify: `orchestrator/edge/cloud_client.py`
- Modify: `orchestrator/edge/server.py`
- Test: `orchestrator/edge/tests/test_edge_call.py`

- [ ] Write failing tests for successful HVAC execution, safety rejection, unsupported intent, and confirmation-required commands.
- [ ] Run the focused test and confirm the new executor is missing.
- [ ] Implement deterministic intent-to-VAL translation and protobuf `ExecuteResponse` creation.
- [ ] Handle `DownFrame.edge_call` inside the active bidi request stream and echo the correlation ID in `UpFrame.edge_result`.
- [ ] Run edge-call tests and the edge smoke suite.

### Task 4: Edge Capability Registration

**Files:**
- Create: `orchestrator/edge/capabilities.py`
- Modify: `orchestrator/edge/main.py`
- Modify: `agents/_sdk/manifest.py`
- Test: `orchestrator/edge/tests/test_capabilities.py`

- [ ] Write failing tests for `deployment=edge`, `kind=edge_fast`, permission separation, and SDK `kind` loading.
- [ ] Implement vehicle/media manifests and best-effort Registry registration after the edge server starts.
- [ ] Run capability and SDK tests.

### Task 5: Planner Metadata and Replanning

**Files:**
- Modify: `orchestrator/cloud/models.py`
- Modify: `orchestrator/cloud/planning.py`
- Test: `orchestrator/cloud/tests/test_planning.py`

- [ ] Write failing tests for `complexity`, `goal`, manifest-derived routing metadata, and `replan(done|steps)`.
- [ ] Extend the initial planning prompt without adding another LLM call.
- [ ] Reuse plan validation for replan batches and default malformed/missing complexity to `simple`.
- [ ] Run planning tests.

### Task 6: Bounded T2 Loop

**Files:**
- Create: `orchestrator/cloud/loop.py`
- Modify: `orchestrator/cloud/engine.py`
- Test: `orchestrator/cloud/tests/test_loop.py`
- Test: `orchestrator/cloud/tests/test_engine_adaptive.py`

- [ ] Write failing tests for adaptive execution/replan, `NEED_CONFIRM` suspension, budget best-effort, and simple-path non-regression.
- [ ] Implement observation compression, iteration and wall-clock limits, one immediate thinking delta, replan failure fallback, and aggregation.
- [ ] Dispatch adaptive plans to the loop and reactively upgrade simple plans on `data.replan` or a failed step with alternatives.
- [ ] Persist/restore `complexity`, `goal`, and dispatch metadata across confirmation.
- [ ] Run loop, engine, and existing confirmation/stream tests.

### Task 7: Deterministic Tool Registry

**Files:**
- Create: `orchestrator/cloud/tools/__init__.py`
- Create: `orchestrator/cloud/tools/registry.py`
- Create: `orchestrator/cloud/tools/builtin.py`
- Modify: `orchestrator/cloud/clients.py`
- Modify: `orchestrator/cloud/main.py`
- Test: `orchestrator/cloud/tests/test_tools.py`

- [ ] Write failing tests for date parsing, unit conversion, arithmetic evaluation, tool dispatch, and vehicle-control denial.
- [ ] Implement safe deterministic built-ins and a `kind=tool` manifest.
- [ ] Register the tool manifest with Registry at planner startup.
- [ ] Hard-reject tool steps requiring `vehicle.control`.
- [ ] Run tool, dispatcher, planning, and permission tests.

### Task 8: Integration, Documentation, and Regression

**Files:**
- Modify: `deploy/docker-compose.yaml`
- Modify: `.env.example`
- Modify: `docs/conventions.md`
- Modify: `docs/design/2026-06-14-cloud-central-orchestrator.md`
- Add/modify focused integration tests under `test/`

- [ ] Add `CLOUD_GATEWAY_ADDR` and loop budget configuration to cloud-planner.
- [ ] Add an in-process integration test proving edge result data can feed a later cloud step through `slot_refs`.
- [ ] Regenerate proto code and compile Python files.
- [ ] Run cloud and edge tests, `test/smoke_edge.py`, full pytest, Go tests/build, and Docker E2E when the local daemon permits it.
- [ ] Record landed scope, verification evidence, and remaining production boundaries in the design document.
# 状态更新（2026-06-14）

P0-P3 全部验收通过。权威交接状态、已有证据和剩余待办见
`docs/design/2026-06-14-cloud-central-orchestrator.md` 的”落地记录”。
