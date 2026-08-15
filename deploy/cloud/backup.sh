#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly RELEASE_ROOT="/opt/car-agent/current"
readonly RELEASE_DIR="$(readlink -f "${RELEASE_ROOT}")"
readonly COMPOSE_PROJECT_NAME="$(basename "${RELEASE_DIR}")"
readonly CLOUD_COMPOSE="/opt/car-agent/shared/compose.cloud.yaml"
readonly BACKUP_ROOT="/opt/car-agent/shared/backups"
readonly POSTGRES_DIR="${BACKUP_ROOT}/postgres"
readonly REDIS_DIR="${BACKUP_ROOT}/redis"
readonly OBS_DIR="${BACKUP_ROOT}/observability"

compose=(
  docker compose
  --project-name "${COMPOSE_PROJECT_NAME}"
  --project-directory "${RELEASE_DIR}"
  -f "${RELEASE_DIR}/compose.yaml"
  -f "${CLOUD_COMPOSE}"
)

install -d -m 0700 "${POSTGRES_DIR}" "${REDIS_DIR}" "${OBS_DIR}"
exec 9>"${BACKUP_ROOT}/.backup.lock"
flock -n 9

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
