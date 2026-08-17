#!/usr/bin/env bash
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  printf 'activate-release.sh must be sourced by remote-release.sh\n' >&2
  exit 2
}

readonly RUNTIME_PROJECT_NAME_FILE="/opt/car-agent/shared/runtime-project-name"
RUNTIME_PROJECT_NAME=""

load_runtime_project_name() {
  local mode owner
  local -a names
  [[ -f "${RUNTIME_PROJECT_NAME_FILE}" && ! -L "${RUNTIME_PROJECT_NAME_FILE}" ]] \
    || die "runtime project name file is missing"
  owner="$(stat -c '%U:%G' "${RUNTIME_PROJECT_NAME_FILE}")"
  [[ "${owner}" == "root:root" ]] || die "runtime project name owner mismatch"
  mode="$(stat -c '%a' "${RUNTIME_PROJECT_NAME_FILE}")"
  [[ "${mode}" =~ ^[0-7]{3,4}$ ]] || die "runtime project name mode is invalid"
  (( (8#${mode} & 0022) == 0 )) \
    || die "runtime project name is group or world writable"
  mapfile -t names <"${RUNTIME_PROJECT_NAME_FILE}"
  [[ "${#names[@]}" -eq 1 ]] || die "runtime project name must be one line"
  [[ "${names[0]}" =~ ^[a-z0-9][a-z0-9_-]*$ ]] \
    || die "runtime project name is invalid"
  RUNTIME_PROJECT_NAME="${names[0]}"
}

validate_shared_env() {
  [[ -f "${SHARED_ROOT}/.env" && ! -L "${SHARED_ROOT}/.env" ]] \
    || die "shared runtime env is missing"
  [[ "$(stat -c '%U:%G' "${SHARED_ROOT}/.env")" == "root:root" ]] \
    || die "shared runtime env owner mismatch"
  [[ "$(stat -c '%a' "${SHARED_ROOT}/.env")" == "600" ]] \
    || die "shared runtime env mode must be 600"
}

extract_release_source() {
  local source_tar="$1" target="$2"
  python3 - "${source_tar}" "${target}" <<'PY'
from pathlib import Path, PurePosixPath
import sys
import tarfile

source_tar = Path(sys.argv[1])
target_root = Path(sys.argv[2])


def safe_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    basename = path.name
    return bool(
        normalized
        and not normalized.startswith("/")
        and not path.is_absolute()
        and ".." not in path.parts
        and ".artifacts" not in path.parts
        and basename != ".env"
        and not (basename.startswith(".env.") and basename != ".env.example")
        and not basename.endswith((".pem", ".key", ".p12", ".pfx"))
    )


with tarfile.open(source_tar, mode="r:") as archive:
    for member in archive.getmembers():
        if not safe_name(member.name):
            raise SystemExit("invalid release source path")
        target = target_root / member.name
        if member.isdir():
            target.mkdir(mode=0o755, parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise SystemExit("invalid release source type")
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        extracted = archive.extractfile(member)
        if extracted is None:
            raise SystemExit("release source member is unreadable")
        with target.open("xb") as output:
            output.write(extracted.read())
        target.chmod(0o755 if member.mode & 0o111 else 0o644)
PY
}

link_runtime_models() {
  local release_dir="$1" manifest="$2" relative shared target
  while IFS= read -r relative; do
    shared="${SHARED_ROOT}/models/${relative#models/}"
    target="${release_dir}/${relative}"
    [[ -f "${shared}" && ! -L "${shared}" ]] \
      || die "validated shared model disappeared"
    install -d -m 0755 -o root -g root "$(dirname "${target}")"
    [[ ! -e "${target}" ]] || die "runtime model target already exists"
    ln "${shared}" "${target}"
  done < <(
    python3 - "${manifest}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for item in payload["models"]:
    print(item["path"])
PY
  )
}

assemble_release() {
  local sha="$1" build_dir release_dir staging
  build_dir="${RELEASE_ROOT}/builds/${sha}"
  release_dir="${RELEASE_ROOT}/releases/${sha}"
  staging="${RELEASE_ROOT}/releases/.staging-${sha}-${BASHPID}"
  [[ -d "${build_dir}" && ! -L "${build_dir}" ]] \
    || die "validated build directory is missing"
  [[ ! -e "${release_dir}" ]] || die "release directory already exists"
  [[ ! -e "${staging}" ]] || die "release staging directory already exists"
  install -d -m 0700 -o root -g root "${staging}"
  extract_release_source "${build_dir}/upload/source.tar" "${staging}"
  link_runtime_models \
    "${staging}" "${staging}/deploy/cloud/runtime-models.json"
  validate_shared_env
  ln -s "${SHARED_ROOT}/.env" "${staging}/.env"
  install -m 0600 -o root -g root \
    "${build_dir}/upload/manifest.json" "${staging}/.release-manifest.json"
  mv -T "${staging}" "${release_dir}"
  printf '%s\n' "${release_dir}"
}

run_required_backup() {
  local release_dir="$1"
  CAR_AGENT_BACKUP_RELEASE_DIR="${release_dir}" \
    "${SHARED_ROOT}/bin/backup.sh" \
    --transaction-lock-fd "${TRANSACTION_LOCK_FD}"
}

switch_current() {
  local target="$1" temporary
  [[ -d "${target}" && ! -L "${target}" ]] || die "release target is invalid"
  temporary="${RELEASE_ROOT}/.current.${BASHPID}.${RANDOM}"
  [[ ! -e "${temporary}" ]] || die "temporary current link already exists"
  ln -s "${target}" "${temporary}"
  mv -Tf "${temporary}" "${RELEASE_ROOT}/current"
}

compose_up_release() {
  local release_dir="$1" sha="$2"
  local -a compose_args
  compose_args=(
    --project-name "${RUNTIME_PROJECT_NAME}"
    --project-directory "${release_dir}"
    -f "${release_dir}/compose.yaml"
    -f "${SHARED_ROOT}/compose.cloud.yaml"
    --env-file "${SHARED_ROOT}/.env"
  )
  RELEASE_SHA="${sha}" docker compose "${compose_args[@]}" config --quiet
  RELEASE_SHA="${sha}" docker compose "${compose_args[@]}" \
    up -d --no-build --pull never
}

write_release_state() {
  local state="$1" sha="$2" previous_sha="$3" timestamp evidence_dir target
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  evidence_dir="${SHARED_ROOT}/evidence/releases/${sha}"
  target="${evidence_dir}/state-${timestamp}-${state}.json"
  install -d -m 0700 -o root -g root "${evidence_dir}"
  python3 - "${target}" "${state}" "${sha}" "${previous_sha}" <<'PY'
import json
from pathlib import Path
import sys

target = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "state": sys.argv[2],
    "release_sha": sys.argv[3],
    "previous_sha": sys.argv[4] or None,
}
with target.open("x", encoding="utf-8", newline="\n") as handle:
    json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
target.chmod(0o600)
PY
}

restore_previous_release() {
  local previous_dir="$1" previous_sha="$2" failed_sha="$3" state="$4"
  if ! switch_current "${previous_dir}"; then
    write_release_state "ROLLBACK_FAILED" "${failed_sha}" "${previous_sha}"
    return 1
  fi
  if ! compose_up_release "${previous_dir}" "${previous_sha}"; then
    write_release_state "ROLLBACK_FAILED" "${failed_sha}" "${previous_sha}"
    return 1
  fi
  if ! ( verify_release "${previous_sha}" ); then
    write_release_state "ROLLBACK_FAILED" "${failed_sha}" "${previous_sha}"
    return 1
  fi
  write_release_state "${state}" "${failed_sha}" "${previous_sha}"
}

validate_runtime_release() {
  local release_dir="$1"
  [[ -d "${release_dir}" && ! -L "${release_dir}" ]] \
    || die "runtime release directory is invalid"
  [[ -L "${release_dir}/.env" ]] || die "runtime release env link is missing"
  [[ "$(readlink "${release_dir}/.env")" == "${SHARED_ROOT}/.env" ]] \
    || die "runtime release env link target is invalid"
}

activate_release() {
  local sha="$1" build_dir release_dir previous_dir previous_sha
  load_runtime_project_name
  build_dir="${RELEASE_ROOT}/builds/${sha}"
  verify_release_images \
    "${sha}" "${build_dir}/src/deploy/cloud/release-services.json"
  release_dir="$(assemble_release "${sha}")"
  validate_runtime_release "${release_dir}"
  previous_dir="$(readlink -f "${RELEASE_ROOT}/current")"
  previous_sha="$(basename "${previous_dir}")"
  validate_release_selector "${previous_sha}"
  run_required_backup "${release_dir}"
  switch_current "${release_dir}"
  if ! compose_up_release "${release_dir}" "${sha}"; then
    restore_previous_release \
      "${previous_dir}" "${previous_sha}" "${sha}" \
      "VERIFY_FAILED_ROLLED_BACK" \
      || die "ROLLBACK_FAILED"
    return 1
  fi
  if ! ( verify_release "${sha}" ); then
    restore_previous_release \
      "${previous_dir}" "${previous_sha}" "${sha}" \
      "VERIFY_FAILED_ROLLED_BACK" \
      || die "ROLLBACK_FAILED"
    return 1
  fi
  write_release_state "VERIFIED" "${sha}" "${previous_sha}"
}

rollback_release() {
  local target_sha="$1" target_dir previous_dir previous_sha manifest
  load_runtime_project_name
  target_dir="${RELEASE_ROOT}/releases/${target_sha}"
  validate_runtime_release "${target_dir}"
  previous_dir="$(readlink -f "${RELEASE_ROOT}/current")"
  previous_sha="$(basename "${previous_dir}")"
  [[ "${target_dir}" != "${previous_dir}" ]] || die "release is already current"
  manifest="${target_dir}/deploy/cloud/release-services.json"
  if [[ ! -f "${manifest}" ]]; then
    manifest="${previous_dir}/deploy/cloud/release-services.json"
  fi
  [[ -f "${manifest}" ]] || die "release service manifest is unavailable"
  verify_release_images "${target_sha}" "${manifest}"
  run_required_backup "${previous_dir}"
  switch_current "${target_dir}"
  if ! compose_up_release "${target_dir}" "${target_sha}" \
      || ! ( verify_release "${target_sha}" ); then
    restore_previous_release \
      "${previous_dir}" "${previous_sha}" "${target_sha}" \
      "ROLLBACK_FAILED" \
      || die "ROLLBACK_FAILED"
    return 1
  fi
  write_release_state "ROLLED_BACK" "${target_sha}" "${previous_sha}"
}
