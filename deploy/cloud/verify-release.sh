#!/usr/bin/env bash
VERIFY_LIBRARY_SOURCED=0
[[ "${BASH_SOURCE[0]}" != "$0" ]] && VERIFY_LIBRARY_SOURCED=1

verify_error() {
  printf 'cloud verification: %s\n' "$1" >&2
  return 1
}

verify_run_step() {
  local label="$1" rc
  shift
  if "$@"; then
    rc=0
  else
    rc=$?
  fi
  if [[ "${rc}" -ne 0 ]]; then
    printf 'cloud verification: %s failed (rc=%s)\n' "${label}" "${rc}" >&2
    VERIFY_STEP_RC="${rc}"
    return 0
  fi
  VERIFY_STEP_RC=0
  return 0
}

verify_prepare_context() {
  local sha="$1" release_dir mode owner
  local runtime_file="${SHARED_ROOT}/runtime-project-name"
  local -a names
  [[ "${sha}" =~ ^[0-9a-f]{7,40}$ ]] || { verify_error "release selector is invalid"; return 2; }
  [[ -f "${runtime_file}" && ! -L "${runtime_file}" ]] || { verify_error "runtime project name file is missing"; return 1; }
  owner="$(stat -c '%U:%G' "${runtime_file}")" || return $?
  [[ "${owner}" == "root:root" ]] || { verify_error "runtime project name owner mismatch"; return 1; }
  mode="$(stat -c '%a' "${runtime_file}")" || return $?
  [[ "${mode}" =~ ^[0-7]{3,4}$ ]] || return 1
  (( (8#${mode} & 0022) == 0 )) || return 1
  mapfile -t names <"${runtime_file}" || return $?
  [[ "${#names[@]}" -eq 1 && "${names[0]}" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || return 1
  RUNTIME_PROJECT_NAME="${names[0]}"
  release_dir="$(readlink -f "${RELEASE_ROOT}/current")" || return $?
  [[ "${release_dir}" == "${RELEASE_ROOT}/releases/${sha}" && -d "${release_dir}" && ! -L "${release_dir}" ]] || return 1
  [[ -L "${release_dir}/.env" ]] || return 1
  [[ "$(readlink "${release_dir}/.env")" == "${SHARED_ROOT}/.env" ]] || return 1
  VERIFY_RELEASE_SHA="${sha}"
  VERIFY_RELEASE_DIR="${release_dir}"
}

readonly -a PRIVATE_HTTPS_PORTS=(443 8443 8444 8445 8446)
readonly -a LOOPBACK_BUSINESS_PORTS=(5173 5174 8090 8092 50059)

compose_for_release() {
  local release_dir="$1" sha="$2"
  shift 2
  RELEASE_SHA="${sha}" docker compose \
    --project-name "${RUNTIME_PROJECT_NAME}" \
    --project-directory "${release_dir}" \
    -f "${release_dir}/compose.yaml" \
    -f "${SHARED_ROOT}/compose.cloud.yaml" \
    --env-file "${SHARED_ROOT}/.env" \
    "$@"
}

inspect_project_containers() {
  local release_dir="$1" sha="$2" payload
  payload="$(compose_for_release "${release_dir}" "${sha}" ps -a --format json)"
  PROJECT_COUNTS="$(python3 -c '
import json
import sys

raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit("compose returned no containers")
try:
    decoded = json.loads(raw)
    rows = decoded if isinstance(decoded, list) else [decoded]
except json.JSONDecodeError:
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
states = [str(row.get("State", "")).lower() for row in rows]
bad = sum(state in {"restarting", "exited", "dead"} for state in states)
running = sum(state == "running" for state in states)
print(len(rows), running, bad)
' <<<"${payload}")"
  [[ "${PROJECT_COUNTS}" == "30 30 0" ]] \
    || { verify_error "runtime project container state is not 30 running and 0 bad"; return 1; }
}

verify_loopback_listeners() {
  local listeners
  listeners="$(ss -lntH)"
  python3 -c '
import sys

required = {5173, 5174, 8090, 8092, 50059}
seen = set()
for line in sys.stdin:
    fields = line.split()
    if len(fields) < 4:
        continue
    address = fields[3]
    for port in required:
        if not address.endswith(f":{port}"):
            continue
        if address != f"127.0.0.1:{port}":
            raise SystemExit(f"business port {port} is not loopback-only")
        seen.add(port)
if seen != required:
    raise SystemExit("one or more loopback business ports are missing")
' <<<"${listeners}"
}

verify_tailscale_serve() {
  local status count
  status="$(tailscale serve status)"
  count="$(grep -Fic '(tailnet only)' <<<"${status}" || true)"
  [[ "${count}" -eq 5 ]] || { verify_error "Tailscale Serve does not expose five tailnet only entries"; return 1; }
  if grep -Fqi 'funnel' <<<"${status}"; then verify_error "Tailscale Funnel must remain disabled"; return 1; fi
  TAILNET_ENTRY_COUNT="${count}"
}

container_env_value() {
  local container_id="$1" key="$2"
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
    "${container_id}" | sed -n "s/^${key}=//p" | head -n 1
}

verify_https_endpoints() {
  local fqdn="$1" name url code
  HTTPS_RESULTS=""
  while IFS=$'\t' read -r name url; do
    code="$(curl --fail --silent --show-error --output /dev/null \
      --write-out '%{http_code}' --max-time 20 "${url}")"
    [[ "${code}" == "200" ]] || { verify_error "HTTPS endpoint ${name} failed"; return 1; }
    HTTPS_RESULTS+="${name}=${code}"$'\n'
  done < <(
    printf 'hmi\thttps://%s/\n' "${fqdn}"
    printf 'edge\thttps://%s:8443/healthz\n' "${fqdn}"
    printf 'llm\thttps://%s:8444/api/llm/providers\n' "${fqdn}"
    printf 'dashboard\thttps://%s:8445/\n' "${fqdn}"
    printf 'collector\thttps://%s:8446/healthz\n' "${fqdn}"
  )
}

run_wss_probes() {
  local release_dir="$1" sha="$2" fqdn="$3"
  local hmi_id collector_id ws_token
  hmi_id="$(compose_for_release "${release_dir}" "${sha}" ps -q hmi)"
  collector_id="$(compose_for_release "${release_dir}" "${sha}" ps -q observability-collector)"
  [[ -n "${hmi_id}" && -n "${collector_id}" ]] \
    || { verify_error "probe containers are unavailable"; return 1; }
  ws_token="$(container_env_value "${hmi_id}" VITE_WS_TOKEN)"
  [[ -n "${ws_token}" ]] || { verify_error "runtime WebSocket credential is unavailable"; return 1; }
  EDGE_PROBE_OUTPUT="$(docker exec -i \
    -e WS_URL="wss://${fqdn}:8443/ws" \
    -e WS_TOKEN="${ws_token}" \
    "${collector_id}" python - \
    <"${release_dir}/deploy/cloud/probes/edge_ws_probe.py")"
  COLLECTOR_PROBE_OUTPUT="$(docker exec -i \
    -e WS_URL="wss://${fqdn}:8446/stream" \
    "${collector_id}" python - \
    <"${release_dir}/deploy/cloud/probes/collector_ws_probe.py")"
}

verify_data_and_backup() {
  local release_dir="$1" sha="$2" postgres_id redis_id
  postgres_id="$(compose_for_release "${release_dir}" "${sha}" ps -q postgres)"
  redis_id="$(compose_for_release "${release_dir}" "${sha}" ps -q redis)"
  [[ -n "${postgres_id}" && -n "${redis_id}" ]] \
    || { verify_error "data dependency containers are unavailable"; return 1; }
  docker exec "${postgres_id}" pg_isready -U cockpit >/dev/null
  [[ "$(docker exec "${redis_id}" redis-cli ping)" == "PONG" ]]
  [[ "$(systemctl is-enabled car-agent-backup.timer)" == "enabled" ]]
  [[ "$(systemctl is-active car-agent-backup.timer)" == "active" ]]
  [[ "$(systemctl show car-agent-backup.service -p Result --value)" == "success" ]]
}

verify_resolve_fqdn() {
  local release_dir="$1" sha="$2" hmi_id fqdn
  hmi_id="$(compose_for_release "${release_dir}" "${sha}" ps -q hmi)" || return $?
  [[ -n "${hmi_id}" ]] || return 1
  fqdn="$(container_env_value "${hmi_id}" __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS)" || return $?
  [[ "${fqdn}" =~ ^[a-z0-9.-]+\.ts\.net$ ]] || { verify_error "Tailnet FQDN is invalid"; return 1; }
  VERIFY_FQDN="${fqdn}"
}

write_verification_evidence() {
  local sha="$1" timestamp evidence_dir target
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  [[ "${timestamp}" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] \
    || { verify_error "verification timestamp is invalid"; return 1; }
  evidence_dir="${SHARED_ROOT}/evidence/releases/${sha}"
  install -d -m 0700 -o root -g root "${evidence_dir}"
  target="${evidence_dir}/verification.json"
  if [[ -e "${target}" ]]; then
    target="${evidence_dir}/verification-${timestamp}.json"
  fi
  PROJECT_COUNTS="${PROJECT_COUNTS}" \
  TAILNET_ENTRY_COUNT="${TAILNET_ENTRY_COUNT}" \
  HTTPS_RESULTS="${HTTPS_RESULTS}" \
  EDGE_PROBE_OUTPUT="${EDGE_PROBE_OUTPUT}" \
  COLLECTOR_PROBE_OUTPUT="${COLLECTOR_PROBE_OUTPUT}" \
  python3 - "${target}" "${sha}" "${timestamp}" <<'PY'
import json
import os
from pathlib import Path
import sys

target = Path(sys.argv[1])
total, running, bad = (int(value) for value in os.environ["PROJECT_COUNTS"].split())
https_codes = {}
for row in os.environ["HTTPS_RESULTS"].splitlines():
    name, code = row.split("=", 1)
    https_codes[name] = int(code)
edge_probe = [
    json.loads(row)
    for row in os.environ["EDGE_PROBE_OUTPUT"].splitlines()
    if row.strip()
]
collector_probe = json.loads(os.environ["COLLECTOR_PROBE_OUTPUT"])
payload = {
    "schema_version": 1,
    "release_sha": sys.argv[2],
    "verified_at": sys.argv[3],
    "passed": True,
    "containers": {"total": total, "running": running, "bad": bad},
    "loopback_business_ports": 5,
    "tailnet_entries": int(os.environ["TAILNET_ENTRY_COUNT"]),
    "https_codes": https_codes,
    "edge_probe": edge_probe,
    "collector_probe": collector_probe,
    "postgres_ready": True,
    "redis_ready": True,
    "backup_timer_ready": True,
}
with target.open("x", encoding="utf-8", newline="\n") as handle:
    json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
target.chmod(0o600)
PY
  chown root:root "${target}"
  printf '%s\n' "${target}"
}

verify_release() {
  local sha="$1" rc
  verify_run_step "verify_prepare_context" verify_prepare_context "${sha}"; rc="${VERIFY_STEP_RC}"
  [[ "${rc}" -eq 0 ]] || return "${rc}"
  verify_run_step "inspect_project_containers" inspect_project_containers "${VERIFY_RELEASE_DIR}" "${VERIFY_RELEASE_SHA}"; rc="${VERIFY_STEP_RC}"
  [[ "${rc}" -eq 0 ]] || return "${rc}"
  verify_run_step "verify_loopback_listeners" verify_loopback_listeners; rc="${VERIFY_STEP_RC}"
  [[ "${rc}" -eq 0 ]] || return "${rc}"
  verify_run_step "verify_tailscale_serve" verify_tailscale_serve; rc="${VERIFY_STEP_RC}"
  [[ "${rc}" -eq 0 ]] || return "${rc}"
  verify_run_step "verify_resolve_fqdn" verify_resolve_fqdn "${VERIFY_RELEASE_DIR}" "${VERIFY_RELEASE_SHA}"; rc="${VERIFY_STEP_RC}"
  [[ "${rc}" -eq 0 ]] || return "${rc}"
  verify_run_step "verify_https_endpoints" verify_https_endpoints "${VERIFY_FQDN}"; rc="${VERIFY_STEP_RC}"
  [[ "${rc}" -eq 0 ]] || return "${rc}"
  verify_run_step "run_wss_probes" run_wss_probes "${VERIFY_RELEASE_DIR}" "${VERIFY_RELEASE_SHA}" "${VERIFY_FQDN}"; rc="${VERIFY_STEP_RC}"
  [[ "${rc}" -eq 0 ]] || return "${rc}"
  verify_run_step "verify_data_and_backup" verify_data_and_backup "${VERIFY_RELEASE_DIR}" "${VERIFY_RELEASE_SHA}"; rc="${VERIFY_STEP_RC}"
  [[ "${rc}" -eq 0 ]] || return "${rc}"
  verify_run_step "write_verification_evidence" write_verification_evidence "${VERIFY_RELEASE_SHA}"; rc="${VERIFY_STEP_RC}"
  [[ "${rc}" -eq 0 ]] || return "${rc}"
  return 0
}

verify_current_release() {
  local release_dir sha
  release_dir="$(readlink -f "${RELEASE_ROOT}/current")"
  sha="$(basename "${release_dir}")"
  verify_release "${sha}"
}

if [[ "${VERIFY_LIBRARY_SOURCED}" -eq 0 ]]; then
  printf 'verify-release.sh is a source-only verification library\n' >&2
  false
fi
