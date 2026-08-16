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
elif [[ "$#" -eq 2 && "$1" == "--transaction-lock-fd" ]]; then
  TRANSACTION_LOCK_FD="$2"
  export TRANSACTION_LOCK_FD
  transaction_lock_validate_inherited "${TRANSACTION_LOCK_FD}" || {
    printf 'backup failed: inherited transaction lock is invalid\n' >&2
    exit 2
  }
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

"${compose[@]}" exec -T redis redis-cli SAVE >/dev/null
"${compose[@]}" cp redis:/data/dump.rdb "${redis_partial}" >/dev/null
test -s "${redis_partial}"
mv "${redis_partial}" "${redis_target}"

"${compose[@]}" exec -T observability-collector python -c '
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

find "${BACKUP_ROOT}" -mindepth 2 -type f -mtime +7 -print \
  | sort >"${candidates_partial}"
mv "${candidates_partial}" "${candidates_target}"

printf 'backup completed: %s\n' "${timestamp}"
