#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly RELEASE_ROOT="/opt/car-agent"
readonly SHARED_ROOT="${RELEASE_ROOT}/shared"
readonly IMPORT_ROOT="${SHARED_ROOT}/imports"
readonly BACKUP_ROOT="${SHARED_ROOT}/backups"
readonly SCRIPT_ROOT="${SHARED_ROOT}/bin"
readonly CURRENT_LINK="${RELEASE_ROOT}/current"
readonly CLOUD_COMPOSE="${SHARED_ROOT}/compose.cloud.yaml"
readonly SHARED_ENV="${SHARED_ROOT}/.env"
readonly RUNTIME_PROJECT_NAME_FILE="${SHARED_ROOT}/runtime-project-name"
readonly MIGRATION_ID_PATTERN='^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7}-(online|final)$'
readonly REDIS_VOLUME="car-agent-redis-data"
readonly COLLECTOR_VOLUME="car-agent-obs-data"
readonly REQUIRED_IMPORT_FILES=("manifest.json" "postgres.dump" "redis.rdb" "collector.db")
CURRENT_RELEASE=""
RUNTIME_PROJECT_NAME=""
declare -a compose=()

die() {
  printf 'cloud-data-migration: %s\n' "$1" >&2
  exit "${2:-1}"
}

require_migration_id() {
  [[ "${1:-}" =~ ${MIGRATION_ID_PATTERN} ]] || die "invalid migration id" 2
}

load_runtime() {
  local -a project_names
  [[ -n "${CURRENT_RELEASE:-}" ]] && return 0
  CURRENT_RELEASE="$(readlink -f "${CURRENT_LINK}")"
  [[ "${CURRENT_RELEASE}" =~ ^/opt/car-agent/releases/[0-9a-f]{7,40}$ ]] \
    || die "current release is invalid"
  mapfile -t project_names <"${RUNTIME_PROJECT_NAME_FILE}"
  [[ "${#project_names[@]}" -eq 1 && "${project_names[0]}" =~ ^[a-z0-9][a-z0-9_-]*$ ]] \
    || die "runtime project name is invalid"
  RUNTIME_PROJECT_NAME="${project_names[0]}"
  compose=(
    docker compose --project-name "${RUNTIME_PROJECT_NAME}"
    --project-directory "${CURRENT_RELEASE}"
    -f "${CURRENT_RELEASE}/compose.yaml" -f "${CLOUD_COMPOSE}"
    --env-file "${SHARED_ENV}"
  )
  export RELEASE_SHA="$(basename "${CURRENT_RELEASE}")"
}

prepare_upload() {
  local migration_id="$1" caller caller_group target
  require_migration_id "${migration_id}"
  caller="${SUDO_USER:-}"
  [[ "${caller}" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] \
    || die "prepare-upload requires a valid sudo caller"
  caller_group="$(id -gn "${caller}")"
  target="${IMPORT_ROOT}/${migration_id}"
  install -d -m 0700 -o root -g root "${IMPORT_ROOT}"
  [[ ! -e "${target}" ]] || die "migration upload directory already exists"
  install -d -m 0700 -o "${caller}" -g "${caller_group}" "${target}"
  printf '%s\n' "${target}"
}

require_import_bundle() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}" name
  require_migration_id "${migration_id}"
  [[ -d "${directory}" && ! -L "${directory}" ]] || die "migration directory is invalid"
  [[ "$(readlink -f "${directory}")" == "${directory}" ]] || die "migration path escaped import root"
  for name in "${REQUIRED_IMPORT_FILES[@]}"; do
    [[ -f "${directory}/${name}" && ! -L "${directory}/${name}" ]] \
      || die "required migration file is invalid: ${name}"
  done
  chown root:root -- "${directory}" "${directory}/manifest.json" \
    "${directory}/postgres.dump" "${directory}/redis.rdb" "${directory}/collector.db"
  chmod 0700 -- "${directory}"
  chmod 0600 -- "${directory}/manifest.json" "${directory}/postgres.dump" \
    "${directory}/redis.rdb" "${directory}/collector.db"
  python3 - "${directory}" "${migration_id}" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

directory = Path(sys.argv[1])
migration_id = sys.argv[2]
payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
keys = {"schema_version", "migration_id", "phase", "source_sha", "created_at",
        "files", "postgres", "redis", "collector"}
if set(payload) != keys or payload.get("schema_version") != 1:
    raise SystemExit("manifest key set is invalid")
if payload.get("migration_id") != migration_id:
    raise SystemExit("manifest migration id mismatch")
names = {"postgres.dump", "redis.rdb", "collector.db"}
if not isinstance(payload.get("files"), dict) or set(payload["files"]) != names:
    raise SystemExit("manifest file set is invalid")
for name in names:
    record = payload["files"][name]
    if set(record) != {"size_bytes", "sha256"} or isinstance(record["size_bytes"], bool):
        raise SystemExit("manifest file record is invalid")
    path = directory / name
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if record["size_bytes"] != path.stat().st_size or record["sha256"] != digest:
        raise SystemExit("migration file checksum mismatch")
PY
}

run_required_backup() {
  local output stamp
  output="$("${SHARED_ROOT}/bin/backup.sh" --transaction-lock-fd "${TRANSACTION_LOCK_FD}")" \
    || die "migration pre-backup failed"
  stamp="${output##*backup completed: }"
  [[ "${stamp}" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || die "backup timestamp is invalid"
  [[ -s "${BACKUP_ROOT}/postgres/${stamp}.dump" \
     && -s "${BACKUP_ROOT}/redis/${stamp}.rdb" \
     && -s "${BACKUP_ROOT}/observability/${stamp}.sql.gz" ]] \
    || die "migration pre-backup triplet is incomplete"
  printf '%s\n' "${stamp}"
}

inspect_current() {
  load_runtime
  python3 - "${CURRENT_RELEASE}" "${RUNTIME_PROJECT_NAME}" <<'PY'
import json, shutil, sys
print(json.dumps({
    "current_release": sys.argv[1],
    "runtime_project_name": sys.argv[2],
    "disk_available_bytes": shutil.disk_usage("/opt/car-agent").free,
    "stores": {"postgres": {"schema_fingerprint": "inspect-required"},
               "redis": {"schema_fingerprint": "inspect-required"},
               "collector": {"schema_fingerprint": "inspect-required"}},
    "status": "inspect_only",
}, sort_keys=True, separators=(",", ":")))
PY
}

preflight_migration() {
  local migration_id="$1"
  load_runtime
  require_import_bundle "${migration_id}"
  [[ "$(df --output=avail -B1 "${IMPORT_ROOT}/${migration_id}" | tail -n 1)" =~ ^[[:space:]]*[0-9]+$ ]] \
    || die "could not determine migration disk availability"
  "${compose[@]}" config --services >/dev/null
  printf '{"migration_id":"%s","status":"preflight_ok"}\n' "${migration_id}"
}

stop_application_writers() {
  local service
  local -a services writers
  mapfile -t services < <("${compose[@]}" config --services)
  [[ "${#services[@]}" -gt 2 ]] || die "compose service list is incomplete"
  for service in "${services[@]}"; do
    [[ "${service}" =~ ^[a-z0-9-]+$ ]] || die "compose service name is invalid"
    [[ "${service}" == "postgres" || "${service}" == "redis" ]] || writers+=("${service}")
  done
  "${compose[@]}" stop "${writers[@]}"
  for service in "${writers[@]}"; do
    [[ -z "$("${compose[@]}" ps -q --status running "${service}")" ]] \
      || die "application writer did not stop: ${service}"
  done
}

assert_named_volume() {
  local actual="$1" expected="$2"
  [[ "${expected}" == "car-agent-redis-data" || "${expected}" == "car-agent-obs-data" ]] \
    || die "unapproved migration volume"
  [[ "${actual}" == "${expected}" ]] || die "runtime named volume mismatch"
}

restore_postgres_dump() {
  local dump="$1"
  [[ -s "${dump}" && ! -L "${dump}" ]] || die "PostgreSQL restore source is invalid"
  "${compose[@]}" exec -T postgres psql -U cockpit -d cockpit -v ON_ERROR_STOP=1 \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='cockpit' AND pid <> pg_backend_pid()" >/dev/null
  "${compose[@]}" exec -T postgres pg_restore -U cockpit -d cockpit \
    --clean --if-exists --no-owner --no-privileges --exit-on-error <"${dump}"
}

restore_redis_rdb() {
  local rdb="$1" migration_id="$2" bucket="${3:-redis-volume}"
  local rollback_dir="${IMPORT_ROOT}/${migration_id}/rollback/${bucket}"
  local redis_container actual_volume image_id
  [[ -s "${rdb}" && ! -L "${rdb}" ]] || die "Redis restore source is invalid"
  redis_container="$("${compose[@]}" ps -q redis)"
  actual_volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "${redis_container}")"
  assert_named_volume "${actual_volume}" "${REDIS_VOLUME}"
  image_id="$(docker inspect --format '{{.Image}}' "${redis_container}")"
  "${compose[@]}" stop redis
  install -d -m 0700 -o root -g root "${rollback_dir}"
  docker run --rm --mount "type=volume,source=${REDIS_VOLUME},target=/data" \
    --mount "type=bind,source=${rollback_dir},target=/rollback" \
    --mount "type=bind,source=$(dirname "${rdb}"),target=/incoming,readonly" \
    --entrypoint sh "${image_id}" -ceu '
      test ! -e /rollback/dump.rdb
      test ! -e /rollback/appendonlydir
      test ! -e /data/dump.rdb || mv /data/dump.rdb /rollback/dump.rdb
      test ! -e /data/appendonlydir || mv /data/appendonlydir /rollback/appendonlydir
      install -m 0600 /incoming/'"$(basename "${rdb}")"' /data/dump.rdb
    '
  "${compose[@]}" up -d --no-build --pull never redis
  "${compose[@]}" exec -T redis redis-cli PING | grep -Fx PONG >/dev/null
  "${compose[@]}" exec -T redis redis-check-rdb /data/dump.rdb | grep -F "CRC64 checksum is OK" >/dev/null
  "${compose[@]}" exec -T redis test -d /data/appendonlydir
}

install_collector_db() {
  local database="$1" migration_id="$2" bucket="${3:-collector-volume}"
  local rollback_dir="${IMPORT_ROOT}/${migration_id}/rollback/${bucket}"
  local collector_container actual_volume image_id
  collector_container="$("${compose[@]}" ps -a -q observability-collector)"
  actual_volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "${collector_container}")"
  assert_named_volume "${actual_volume}" "${COLLECTOR_VOLUME}"
  image_id="$(docker inspect --format '{{.Image}}' "${collector_container}")"
  install -d -m 0700 -o root -g root "${rollback_dir}"
  docker run --rm --mount "type=volume,source=${COLLECTOR_VOLUME},target=/data" \
    --mount "type=bind,source=${rollback_dir},target=/rollback" \
    --mount "type=bind,source=$(dirname "${database}"),target=/incoming,readonly" \
    --entrypoint python "${image_id}" - "$(basename "${database}")" <<'PY'
import os, sqlite3, sys
from pathlib import Path
source = Path("/incoming") / sys.argv[1]
target = Path("/data/obs.db")
rollback = Path("/rollback")
for name in ("obs.db", "obs.db-wal", "obs.db-shm"):
    current = Path("/data") / name
    if current.exists():
        destination = rollback / name
        if destination.exists(): raise SystemExit("collector rollback target exists")
        os.replace(current, destination)
temporary = Path("/data/obs.db.migration.partial")
with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise SystemExit("collector import integrity failed")
with source.open("rb") as incoming, temporary.open("xb") as output:
    output.write(incoming.read())
os.chmod(temporary, 0o600)
os.replace(temporary, target)
PY
}

restore_collector_sql() {
  local sql_gz="$1" migration_id="$2" directory="${IMPORT_ROOT}/${migration_id}/rollback-generated"
  local collector_container image_id
  collector_container="$("${compose[@]}" ps -a -q observability-collector)"
  image_id="$(docker inspect --format '{{.Image}}' "${collector_container}")"
  install -d -m 0700 -o root -g root "${directory}"
  docker run --rm --mount "type=bind,source=$(dirname "${sql_gz}"),target=/backup,readonly" \
    --mount "type=bind,source=${directory},target=/restore" --entrypoint python "${image_id}" \
    - "$(basename "${sql_gz}")" <<'PY'
import gzip, sqlite3, sys
from pathlib import Path
target = Path("/restore/collector.db")
with gzip.open(Path("/backup") / sys.argv[1], "rt", encoding="utf-8") as source:
    sql = source.read()
with sqlite3.connect(target) as connection:
    connection.executescript(sql)
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise SystemExit("restored collector integrity failed")
PY
  install_collector_db "${directory}/collector.db" "${migration_id}" "failed-import-collector-volume"
}

verify_store_group() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}"
  "${compose[@]}" exec -T postgres psql -U cockpit -d cockpit -At \
    -c "SELECT count(*) FROM memory_item" >/dev/null
  "${compose[@]}" exec -T redis redis-cli DBSIZE >/dev/null
  python3 - "${directory}/collector.db" <<'PY'
import sqlite3, sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as connection:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise SystemExit("PRAGMA integrity_check failed")
PY
}

start_current_release() {
  "${compose[@]}" up -d --no-build --pull never
}

write_migration_state() {
  local state="$1" migration_id="$2" backup_stamp="$3"
  python3 - "${IMPORT_ROOT}/${migration_id}/status.json" "${state}" "${migration_id}" "${backup_stamp}" <<'PY'
import json, os, sys
from pathlib import Path
target = Path(sys.argv[1]); partial = target.with_suffix(".json.partial")
partial.write_text(json.dumps({"status": sys.argv[2], "migration_id": sys.argv[3],
                               "backup_stamp": sys.argv[4]}, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(partial, 0o600); os.replace(partial, target)
PY
}

rollback_all() {
  local migration_id="$1" backup_stamp="$2"
  stop_application_writers
  restore_postgres_dump "${BACKUP_ROOT}/postgres/${backup_stamp}.dump"
  restore_redis_rdb "${BACKUP_ROOT}/redis/${backup_stamp}.rdb" "${migration_id}" "failed-import-redis-volume"
  restore_collector_sql "${BACKUP_ROOT}/observability/${backup_stamp}.sql.gz" "${migration_id}"
  start_current_release
  verify_current_release
  write_migration_state "ROLLED_BACK" "${migration_id}" "${backup_stamp}"
}

apply_migration() {
  local migration_id="$1" backup_stamp
  load_runtime
  require_import_bundle "${migration_id}"
  preflight_migration "${migration_id}"
  backup_stamp="$(run_required_backup)"
  write_migration_state "BACKED_UP" "${migration_id}" "${backup_stamp}"
  stop_application_writers
  if ! (
    restore_postgres_dump "${IMPORT_ROOT}/${migration_id}/postgres.dump"
    restore_redis_rdb "${IMPORT_ROOT}/${migration_id}/redis.rdb" "${migration_id}"
    install_collector_db "${IMPORT_ROOT}/${migration_id}/collector.db" "${migration_id}"
    verify_store_group "${migration_id}"
    start_current_release
    verify_current_release
  ); then
    if ! rollback_all "${migration_id}" "${backup_stamp}"; then
      write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"
      die "migration and automatic rollback failed"
    fi
    die "migration failed and the store group was rolled back"
  fi
  write_migration_state "APPLIED" "${migration_id}" "${backup_stamp}"
}

verify_migration() {
  local migration_id="$1"
  load_runtime
  require_import_bundle "${migration_id}"
  verify_store_group "${migration_id}"
  verify_current_release
  printf '{"migration_id":"%s","status":"verified"}\n' "${migration_id}"
}

rollback_migration() {
  local migration_id="$1" backup_stamp
  load_runtime
  require_import_bundle "${migration_id}"
  backup_stamp="$(python3 - "${IMPORT_ROOT}/${migration_id}/status.json" <<'PY'
import json, re, sys
value = json.load(open(sys.argv[1], encoding="utf-8")).get("backup_stamp", "")
if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", value) is None: raise SystemExit("invalid backup stamp")
print(value)
PY
)"
  rollback_all "${migration_id}" "${backup_stamp}"
}

main() {
  local code=0
  [[ "${EUID}" -eq 0 ]] || die "must run as root"
  source "${SHARED_ROOT}/bin/transaction-lock.sh"
  transaction_lock_acquire "migration" || {
    code=$?
    die "cloud transaction lock is held by ${TRANSACTION_LOCK_HOLDER:-unknown}" "${code}"
  }
  source "${SCRIPT_ROOT}/verify-release.sh"
  case "${1:-}" in
    inspect-current)
      [[ "$#" -eq 1 ]] || die "inspect-current takes no migration id" 2
      inspect_current ;;
    prepare-upload|preflight|apply|verify|rollback)
      [[ "$#" -eq 3 && "${2:-}" == "--migration-id" ]] || die "action requires --migration-id" 2
      require_migration_id "${3:-}"
      case "$1" in
        prepare-upload) prepare_upload "$3" ;;
        preflight) preflight_migration "$3" ;;
        apply) apply_migration "$3" ;;
        verify) verify_migration "$3" ;;
        rollback) rollback_migration "$3" ;;
      esac ;;
    *) die "unknown data migration action" 2 ;;
  esac
}

main "$@"
