# Cloud HMI Local Model Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put Silero VAD and the four sherpa-onnx KWS runtime files under the same hash-pinned cloud model governance as voiceprint and Edge NLU, build them into the immutable HMI image, verify the live Tailnet HMI, and then remove the obsolete local release builder.

**Architecture:** Keep inference client-side: the cloud HMI image only serves the model/runtime assets, while the browser executes VAD/KWS locally. Store the ignored binary assets under `/opt/car-agent/shared/models/hmi/public/**`, validate exact SHA-256 values before any build, and use a cloud-only Compose build override plus a dedicated HMI Dockerfile and Dockerfile-specific ignore contract so clean-clone/local Compose behavior is not changed.

**Tech Stack:** Docker Compose build overrides, BuildKit named contexts, Python/pytest release-contract tests, Bash remote release helpers, Vite HMI, SSH/Tailscale verification.

---

### Task 1: Lock the client model contract in tests

**Files:**
- Modify: `scripts/tests/test_cloud_deploy_assets.py`
- Modify: `scripts/tests/test_cloud_release.py`

- [x] **Step 1: Write failing manifest and cloud-build tests**

Add assertions that `runtime-models.json` contains these exact additional paths and SHA-256 values:

```python
CLIENT_MODELS = {
    "models/hmi/public/models/silero_vad.onnx": "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    "models/hmi/public/kws/sherpa-onnx-kws.js": "d2113885f82cf307f52906ddf2a8786315db86fca53209c2d1e54c7fff8c6c76",
    "models/hmi/public/kws/sherpa-onnx-wasm-kws-main.data": "b91b148aa19d386fe27624867c21111c6a6bfa739a619538bb705408a8eb7165",
    "models/hmi/public/kws/sherpa-onnx-wasm-kws-main.js": "93899d72cbb9a8e2ba7e71cc1143fdc7639098107e771860070bd507d8edfd87",
    "models/hmi/public/kws/sherpa-onnx-wasm-kws-main.wasm": "ca2a000807ab83b20a37b512ff4613872528471a227f738dd30d07efaf563492",
}
```

Assert the cloud build override selects `deploy/cloud/hmi.Dockerfile`, supplies `hmi_runtime_models`, and that `remote-build.sh` passes both Compose files and `CAR_AGENT_HMI_MODELS_ROOT` before `docker compose ... build`.

- [x] **Step 2: Write failing bootstrap-report tests**

Extend `test_preflight_reports_exact_bootstrap_candidates` to require the five `/opt/car-agent/shared/models/hmi/public/**` targets and assert their source is the approved local `hmi/public/**` artifact rather than the immutable current release.

- [x] **Step 3: Run tests and verify RED**

Run:

```powershell
& 'C:\Users\Super\AppData\Local\Programs\Python\Python312\python.exe' -m pytest --import-mode=importlib `
  scripts/tests/test_cloud_release.py scripts/tests/test_cloud_deploy_assets.py -q
```

Expected: failures naming the five missing model paths, missing cloud build override/Dockerfile, and missing bootstrap candidates.

### Task 2: Implement hash-pinned cloud HMI model delivery

**Files:**
- Modify: `deploy/cloud/runtime-models.json`
- Create: `deploy/cloud/compose.build.yaml`
- Create: `deploy/cloud/hmi.Dockerfile`
- Create: `deploy/cloud/hmi.Dockerfile.dockerignore`
- Modify: `deploy/cloud/remote-build.sh`
- Modify: `scripts/cloud_release_lib.py`

- [x] **Step 1: Add the five model records**

Append the five `CLIENT_MODELS` entries from Task 1 to `runtime-models.json`. Keep model binaries ignored and out of Git.

- [x] **Step 2: Add a cloud-only HMI build**

Create `compose.build.yaml`:

```yaml
services:
  hmi:
    build:
      context: .
      dockerfile: deploy/cloud/hmi.Dockerfile
      additional_contexts:
        hmi_runtime_models: ${CAR_AGENT_HMI_MODELS_ROOT:?CAR_AGENT_HMI_MODELS_ROOT required}
```

Create `hmi.Dockerfile` that installs the committed HMI source and copies only the exact Silero/KWS runtime paths from `hmi_runtime_models` into `/app/public/models` and `/app/public/kws`. Add a Dockerfile-specific ignore file that retains the root ignore rules and removes all `hmi/public/{models,kws}` content from the primary context before the five approved files return through the named context.

- [x] **Step 3: Wire remote build and bootstrap checks**

Add `-f "${src}/deploy/cloud/compose.build.yaml"` to the remote build Compose arguments, export `CAR_AGENT_HMI_MODELS_ROOT="${SHARED_ROOT}/models/hmi"`, and extend both the bootstrap model table and inline remote preflight hashes. Mark the client artifacts' bootstrap source as `approved local asset:hmi/public/...`.

- [x] **Step 4: Run tests and verify GREEN**

Run the Task 1 command. Expected: all targeted cloud-release tests pass.

- [x] **Step 5: Validate Compose resolution and shell syntax**

Run:

```powershell
docker compose --project-directory . -f compose.yaml -f deploy/cloud/compose.build.yaml config --quiet
& 'C:\Program Files\Git\bin\bash.exe' -n deploy/cloud/remote-build.sh
```

Expected: exit 0 for both.

### Task 3: Document and independently verify the release contract

**Files:**
- Modify: `deploy/cloud/README.md`
- Modify: `scripts/tests/test_cloud_deploy_assets.py`

- [x] **Step 1: Document client/server model placement**

Document that `/opt/car-agent/shared/models/hmi/public/**` is build input, that browsers execute VAD/KWS locally, and that changing any model hash is an infrastructure reapproval event.

- [x] **Step 2: Add image-content verification assertions**

Add static tests that the cloud HMI Dockerfile copies exactly one VAD model and the four KWS runtime files, with no wildcard copying of ignored model directories. Lock the Dockerfile-specific ignore rules and require the manifest, bootstrap table, and inline remote-preflight hash map to remain identical.

- [x] **Step 3: Run the targeted suites again**

Expected: all targeted tests pass with no warnings or collection errors.

- [x] **Step 4: Commit the isolated change**

Stage only the plan, cloud model/build assets, release library, tests, and README. Run `git diff --cached --check`, then commit with:

```text
fix: deliver HMI local voice models in cloud releases
```

### Task 4: Bootstrap, deploy, verify, and clean the obsolete builder

**Files:**
- No tracked file changes.

- [ ] **Step 1: Obtain separate authorization for main integration and push**

Present the commit, tests, exact changed paths, and the infrastructure digest change. Do not push until the user explicitly approves `git push`.

- [ ] **Step 2: Upload the five local assets to a temporary server staging directory**

Use the existing SSH identity. Verify all five hashes before installing them as root-owned mode `0644` files below `/opt/car-agent/shared/models/hmi/public/**`; do not modify `/opt/car-agent/shared/.env` or the immutable current release.

- [ ] **Step 3: Reapprove/install changed cloud infrastructure**

Install the reviewed `deploy/cloud/**` files and update `/opt/car-agent/shared/release-infrastructure.json` to the exact committed aggregate digest. Preserve Tailscale, systemd, security-group, database, and secret state.

- [ ] **Step 4: Deploy the committed main SHA**

Run `cloud_release.py plan`, dry-run deploy, then `deploy --apply`. The transactional release must build all required immutable images while the current release remains live and must pass the existing full release verification before switching.

- [ ] **Step 5: Verify HMI assets and local execution prerequisites**

Verify inside the live HMI container that all five files exist with exact hashes. Through Tailnet HTTPS verify status 200, binary content type/size, COOP `same-origin`, COEP `credentialless`, and that `/api/voiceprint/info` still reports real CAM++.

- [ ] **Step 6: Remove the obsolete local builder**

After live verification succeeds, run:

```powershell
docker buildx rm car-agent-release-builder
```

Verify the builder and `buildx_buildkit_car-agent-release-builder0` container no longer exist and that unrelated local Compose containers remain running.
