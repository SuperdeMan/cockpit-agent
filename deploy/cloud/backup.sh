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
collector_volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "${collector_container}")"
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

"${compose[@]}" exec -T redis redis-cli SAVE >/dev/null
redis_aggregate="$("${compose[@]}" exec -T redis redis-cli --json EVAL '
redis.setresp(3); local c="0"; local n,p,e=0,0,0; local px={}; local ty={}; repeat local r=redis.call("SCAN",c,"COUNT",1000); c=r[1]; for _,k in ipairs(r[2]) do n=n+1; local h=string.match(k,"^([A-Za-z0-9_-]+):") or "other"; px[h]=(px[h] or 0)+1; local t=redis.call("TYPE",k); if type(t)=="table" then t=t.ok end; ty[t]=(ty[t] or 0)+1; if redis.call("PTTL",k)<0 then p=p+1 else e=e+1 end end until c=="0"; local a={};local b={};for k,v in pairs(px) do table.insert(a,k);table.insert(a,v) end;for k,v in pairs(ty) do table.insert(b,k);table.insert(b,v) end;return {map={"key_count",n,"prefixes",{map=a},"types",{map=b},"persistent",p,"expiring",e}}' 0)"
"${compose[@]}" cp redis:/data/dump.rdb "${redis_partial}" >/dev/null
test -s "${redis_partial}"
mv "${redis_partial}" "${redis_target}"

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

readonly postgres_mount="type=bind,source=${POSTGRES_DIR},target=/backup,readonly"
readonly redis_mount="type=bind,source=${REDIS_DIR},target=/backup,readonly"
readonly obs_mount="type=bind,source=${OBS_DIR},target=/backup,readonly"
docker run --pull never --rm=true --mount "${postgres_mount}" --entrypoint pg_restore \
  "${postgres_image}" --list "/backup/${timestamp}.dump" >/dev/null
docker run --pull never --rm=true --mount "${redis_mount}" --entrypoint redis-check-rdb \
  "${redis_image}" "/backup/${timestamp}.rdb" | grep -F 'CRC64 checksum is OK' >/dev/null
docker run --pull never --rm=true --mount "${obs_mount}" --tmpfs /restore:rw,noexec,nosuid \
  --entrypoint python "${collector_image}" - "${timestamp}.sql.gz" <<'PY'
import gzip
import sqlite3
import sys
from pathlib import Path

target = Path("/restore/collector.db")
with gzip.open(Path("/backup") / sys.argv[1], "rt", encoding="utf-8") as source:
    with sqlite3.connect(target) as connection:
        statement = []
        expanded = 0
        for line in source:
            expanded += len(line.encode("utf-8"))
            if expanded > 16 * 1024 * 1024 * 1024:
                raise SystemExit("collector backup expands beyond limit")
            statement.append(line)
            sql = "".join(statement)
            if len(sql.encode("utf-8")) > 64 * 1024 * 1024:
                raise SystemExit("collector backup statement exceeds limit")
            if sqlite3.complete_statement(sql):
                connection.execute(sql)
                statement.clear()
        if statement:
            raise SystemExit("collector backup has incomplete SQL")
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise SystemExit("backup collector restore integrity failed")
PY

backup_manifest="${BACKUP_ROOT}/${timestamp}.backup-manifest.json"
python3 - "${backup_manifest}" "${timestamp}" "${postgres_target}" "${redis_target}" "${obs_target}" "${redis_aggregate}" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

def record(path_text):
    path = Path(path_text)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return {"size_bytes": size, "sha256": digest.hexdigest()}

target = Path(sys.argv[1])
payload = {"schema_version": 1, "backup_stamp": sys.argv[2], "redis_aggregate": json.loads(sys.argv[6]), "files": {
    "postgres.dump": record(sys.argv[3]),
    "redis.rdb": record(sys.argv[4]),
    "collector.sql.gz": record(sys.argv[5]),
}}
partial = target.with_suffix(target.suffix + ".partial")
fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    os.write(fd, (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
    os.fsync(fd)
finally:
    os.close(fd)
os.replace(partial, target)
PY

find "${BACKUP_ROOT}" -mindepth 2 -type f -mtime +7 -print \
  | sort >"${candidates_partial}"
mv "${candidates_partial}" "${candidates_target}"

printf 'backup completed: %s\n' "${timestamp}"
