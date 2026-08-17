#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly CAR_ROOT="/opt/car-agent"
readonly SHARED_ROOT="${CAR_ROOT}/shared"
readonly RELEASE_ROOT="${CAR_ROOT}/current"
readonly RELEASE_DIR="$(readlink -f "${RELEASE_ROOT}")"
readonly ACTIVE_RELEASE_SHA="$(basename "${RELEASE_DIR}")"
readonly RUNTIME_PROJECT_NAME_FILE="/opt/car-agent/shared/runtime-project-name"
mapfile -t runtime_project_names <"${RUNTIME_PROJECT_NAME_FILE}"
[[ "${#runtime_project_names[@]}" -eq 1 ]]
readonly RUNTIME_PROJECT_NAME="${runtime_project_names[0]}"
[[ "${RUNTIME_PROJECT_NAME}" =~ ^[a-z0-9][a-z0-9_-]*$ ]]
readonly CLOUD_COMPOSE="${SHARED_ROOT}/compose.cloud.yaml"
readonly SHARED_ENV="${SHARED_ROOT}/.env"
readonly BACKUP_ROOT="${SHARED_ROOT}/backups"
readonly POSTGRES_DIR="${BACKUP_ROOT}/postgres"
readonly REDIS_DIR="${BACKUP_ROOT}/redis"
readonly OBS_DIR="${BACKUP_ROOT}/observability"
readonly POSTGRES_VOLUME="car-agent-postgres-data"
readonly REDIS_VOLUME="car-agent-redis-data"
readonly COLLECTOR_VOLUME="car-agent-obs-data"
WRITERS_QUIESCED=0

source "${SHARED_ROOT}/bin/transaction-lock.sh"

lock_code=0
if [[ "$#" -eq 0 ]]; then
  transaction_lock_acquire "backup" || lock_code=$?
  if [[ "${lock_code}" -eq 75 ]]; then
    printf 'backup skipped: cloud transaction busy\n'
    exit 0
  fi
  [[ "${lock_code}" -eq 0 ]] || {
    printf 'backup failed: transaction lock error\n' >&2
    exit "${lock_code}"
  }
elif [[ "$#" -ge 2 && "$1" == "--transaction-lock-fd" ]]; then
  TRANSACTION_LOCK_FD="$2"
  export TRANSACTION_LOCK_FD
  transaction_lock_validate_inherited "${TRANSACTION_LOCK_FD}" || {
    printf 'backup failed: inherited transaction lock is invalid\n' >&2
    exit 2
  }
  if [[ "$#" -eq 3 && "$3" == "--writers-quiesced" ]]; then
    WRITERS_QUIESCED=1
  elif [[ "$#" -ne 2 ]]; then
    printf 'backup failed: invalid arguments\n' >&2
    exit 2
  fi
else
  printf 'backup failed: invalid arguments\n' >&2
  exit 2
fi

compose=(
  docker compose
  --project-name "${RUNTIME_PROJECT_NAME}"
  --project-directory "${RELEASE_DIR}"
  -f "${RELEASE_DIR}/compose.yaml"
  -f "${CLOUD_COMPOSE}"
  --env-file "${SHARED_ENV}"
)
export RELEASE_SHA="${ACTIVE_RELEASE_SHA}"

fsync_backup_artifact() {
  python3 - "$1" <<'PY'
import os,sys
from pathlib import Path
path=Path(sys.argv[1])
fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
try: os.fsync(fd)
finally: os.close(fd)
parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
try: os.fsync(parent)
finally: os.close(parent)
PY
}

if [[ "${WRITERS_QUIESCED}" -eq 1 ]]; then
  mapfile -t backup_services < <("${compose[@]}" config --services)
  [[ "${#backup_services[@]}" -gt 2 ]]
  for backup_service in "${backup_services[@]}"; do
    [[ "${backup_service}" =~ ^[a-z0-9-]+$ ]]
    if [[ "${backup_service}" != "postgres" && "${backup_service}" != "redis" ]]; then
      [[ -z "$("${compose[@]}" ps -q --status running "${backup_service}")" ]]
    fi
  done
fi

postgres_container="$("${compose[@]}" ps -a -q postgres)"
redis_container="$("${compose[@]}" ps -a -q redis)"
collector_container="$("${compose[@]}" ps -a -q observability-collector)"
[[ "${postgres_container}" =~ ^[0-9a-f]{12,64}$ ]]
[[ "${redis_container}" =~ ^[0-9a-f]{12,64}$ ]]
[[ "${collector_container}" =~ ^[0-9a-f]{12,64}$ ]]
postgres_image="$(docker inspect --format '{{.Image}}' "${postgres_container}")"
redis_image="$(docker inspect --format '{{.Image}}' "${redis_container}")"
collector_image="$(docker inspect --format '{{.Image}}' "${collector_container}")"
[[ "${postgres_image}" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "${redis_image}" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "${collector_image}" =~ ^sha256:[0-9a-f]{64}$ ]]
postgres_volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "${postgres_container}")"
redis_volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "${redis_container}")"
collector_volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "${collector_container}")"
[[ "${postgres_volume}" == "${POSTGRES_VOLUME}" ]]
[[ "${redis_volume}" == "${REDIS_VOLUME}" ]]
[[ "${collector_volume}" == "${COLLECTOR_VOLUME}" ]]

install -d -m 0700 "${POSTGRES_DIR}" "${REDIS_DIR}" "${OBS_DIR}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
postgres_partial="${POSTGRES_DIR}/${timestamp}.dump.partial"
postgres_target="${POSTGRES_DIR}/${timestamp}.dump"
redis_partial="${REDIS_DIR}/${timestamp}.rdb.partial"
redis_target="${REDIS_DIR}/${timestamp}.rdb"
obs_partial="${OBS_DIR}/${timestamp}.sql.gz.partial"
obs_target="${OBS_DIR}/${timestamp}.sql.gz"
candidates_partial="${BACKUP_ROOT}/cleanup-candidates.txt.partial"
candidates_target="${BACKUP_ROOT}/cleanup-candidates.txt"

"${compose[@]}" exec -T postgres \
  pg_dump -U cockpit -d cockpit -Fc >"${postgres_partial}"
test -s "${postgres_partial}"
mv "${postgres_partial}" "${postgres_target}"
fsync_backup_artifact "${postgres_target}"

"${compose[@]}" exec -T redis redis-cli SAVE >/dev/null
redis_digest_key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
[[ "${redis_digest_key}" =~ ^[0-9a-f]{64}$ ]]
redis_container="$("${compose[@]}" ps -a -q redis)"
[[ "${redis_container}" =~ ^[0-9a-f]{12,64}$ ]]
"${compose[@]}" cp redis:/data/dump.rdb "${redis_partial}" >/dev/null
test -s "${redis_partial}"
mv "${redis_partial}" "${redis_target}"
fsync_backup_artifact "${redis_target}"

docker run --pull never --rm=true --mount "type=volume,source=${COLLECTOR_VOLUME},target=/data,readonly" \
  --entrypoint python "${collector_image}" -c '
import sqlite3
import sys

connection = sqlite3.connect("file:/data/obs.db?mode=ro", uri=True)
try:
    connection.execute("BEGIN")
    for statement in connection.iterdump():
        sys.stdout.write(statement)
        sys.stdout.write("\n")
finally:
    connection.close()
' | gzip -c >"${obs_partial}"
test -s "${obs_partial}"
gzip -t "${obs_partial}"
mv "${obs_partial}" "${obs_target}"
fsync_backup_artifact "${obs_target}"

readonly postgres_mount="type=bind,source=${POSTGRES_DIR},target=/backup,readonly"
readonly redis_mount="type=bind,source=${REDIS_DIR},target=/backup,readonly"
readonly obs_mount="type=bind,source=${OBS_DIR},target=/backup,readonly"
docker run --pull never --rm=true --mount "${postgres_mount}" --entrypoint pg_restore \
  "${postgres_image}" --list "/backup/${timestamp}.dump" >/dev/null
docker run --pull never --rm=true --mount "${redis_mount}" --entrypoint redis-check-rdb \
  "${redis_image}" "/backup/${timestamp}.rdb" | grep -F 'CRC64 checksum is OK' >/dev/null
docker run --pull never --rm=true --mount "${obs_mount}" --tmpfs /restore:rw,noexec,nosuid \
  --mount "type=bind,source=${RELEASE_DIR}/deploy/cloud/sqlite_stream_restore.py,target=/tool.py,readonly" \
  --entrypoint python "${collector_image}" /tool.py "/backup/${timestamp}.sql.gz" /restore/collector.db

backup_manifest="${BACKUP_ROOT}/${timestamp}.backup-manifest.json"
cold_redis_container="car-agent-backup-redis-${timestamp}"
docker run -d --rm=true --pull never --name "${cold_redis_container}" \
  --mount "type=bind,source=${REDIS_DIR},target=/snapshot,readonly" \
  --entrypoint redis-server "${redis_image}" \
  --dir /snapshot --dbfilename "${timestamp}.rdb" --appendonly no --save "" \
  --protected-mode no >/dev/null
trap 'docker stop --time 5 "${cold_redis_container}" >/dev/null 2>&1' EXIT
cold_redis_ready=0
for attempt in $(seq 1 60); do
  if docker exec "${cold_redis_container}" redis-cli PING 2>/dev/null | grep -Fx PONG >/dev/null; then
    cold_redis_ready=1
    break
  fi
  sleep 1
done
[[ "${cold_redis_ready}" -eq 1 ]]
printf '%s\n' "${redis_digest_key}" | \
  python3 "${RELEASE_DIR}/deploy/cloud/store_identity_evidence.py" redis \
    --container "${cold_redis_container}" --key-stdin --include-key --output - | \
  python3 "${RELEASE_DIR}/deploy/cloud/build_backup_manifest.py" \
    "${backup_manifest}" "${timestamp}" "${postgres_target}" "${redis_target}" "${obs_target}"
docker stop --time 5 "${cold_redis_container}" >/dev/null
trap - EXIT
fsync_backup_artifact "${backup_manifest}"

find "${BACKUP_ROOT}" -mindepth 2 -type f -mtime +7 -print \
  | sort >"${candidates_partial}"
mv "${candidates_partial}" "${candidates_target}"

printf 'backup completed: %s\n' "${timestamp}"
