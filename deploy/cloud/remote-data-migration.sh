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
  install -d -m 0711 -o root -g root "${IMPORT_ROOT}"
  [[ ! -e "${target}" ]] || die "migration upload directory already exists"
  install -d -m 0700 -o "${caller}" -g "${caller_group}" "${target}"
  printf '%s\n' "${target}"
}

seal_upload() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}" caller caller_uid caller_gid
  require_migration_id "${migration_id}"
  caller="${SUDO_USER:-}"
  [[ "${caller}" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] \
    || die "seal-upload requires a valid sudo caller"
  caller_uid="$(id -u "${caller}")" || return $?
  caller_gid="$(id -g "${caller}")" || return $?
  python3 - "${directory}" "${caller_uid}" "${caller_gid}" <<'PY'
import os, stat, sys
required = {"manifest.json", "postgres.dump", "redis.rdb", "collector.db"}
root = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    directory = os.fstat(root)
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != int(sys.argv[2]) or directory.st_gid != int(sys.argv[3]):
        raise SystemExit("migration directory owner is invalid")
    if stat.S_IMODE(directory.st_mode) != 0o700:
        raise SystemExit("migration directory mode is invalid")
    os.fchown(root, 0, 0)
    os.fchmod(root, 0o700)
    if set(os.listdir(root)) != required:
        raise SystemExit("migration upload file set is invalid")
    for name in sorted(required):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SystemExit("migration upload entry is not a private regular file")
            if metadata.st_uid != int(sys.argv[2]) or metadata.st_gid != int(sys.argv[3]):
                raise SystemExit("migration upload file owner is invalid")
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
finally:
    os.close(root)
PY
  require_import_bundle "${migration_id}"
}

require_import_bundle() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}"
  require_migration_id "${migration_id}"
  python3 - "${directory}" <<'PY'
import os, stat, sys
required = {"manifest.json", "postgres.dump", "redis.rdb", "collector.db"}
root = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    directory = os.fstat(root)
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0 or directory.st_gid != 0:
        raise SystemExit("sealed migration directory owner is invalid")
    if stat.S_IMODE(directory.st_mode) != 0o700 or set(os.listdir(root)) != required:
        raise SystemExit("sealed migration directory is invalid")
    for name in sorted(required):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SystemExit("sealed migration entry is invalid")
            if metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600:
                raise SystemExit("sealed migration entry permissions are invalid")
        finally:
            os.close(descriptor)
finally:
    os.close(root)
PY
  python3 - "${directory}" "${migration_id}" <<'PY'
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
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
if not re.fullmatch(r"[0-9a-f]{40}", payload.get("source_sha", "")):
    raise SystemExit("manifest source SHA is invalid")
if payload.get("phase") not in {"online", "final"} or not migration_id.endswith("-" + payload["phase"]):
    raise SystemExit("manifest phase is invalid")
created_at = payload.get("created_at", "")
if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", created_at):
    raise SystemExit("manifest created_at is not UTC")
try:
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
except ValueError as exc:
    raise SystemExit("manifest created_at is invalid") from exc
canonical = created.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
if created.utcoffset() != timezone.utc.utcoffset(created) or canonical != created_at:
    raise SystemExit("manifest created_at is not canonical UTC")
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
pg = payload.get("postgres")
redis = payload.get("redis")
collector = payload.get("collector")
if not isinstance(pg, dict) or set(pg) != {"major", "vector_version", "tables", "states", "schema_fingerprint", "archive_fingerprint"}:
    raise SystemExit("PostgreSQL evidence keys are invalid")
pg_tables = {"memory_item", "memory_relation", "reminder_item", "task_ledger",
             "proactive_delivery", "scene_item", "voiceprint", "agents", "agent_capability_vec"}
pg_states = {"reminder_item.status", "task_ledger.status", "proactive_delivery.state", "scene_item.status"}
if set(pg.get("tables", {})) != pg_tables or set(pg.get("states", {})) != pg_states:
    raise SystemExit("PostgreSQL aggregate set is invalid")
if not re.fullmatch(r"[0-9]{1,3}", pg.get("major", "")) or not re.fullmatch(r"[0-9]+(?:[.][0-9]+){1,3}", pg.get("vector_version", "")):
    raise SystemExit("PostgreSQL version evidence is invalid")
if not isinstance(redis, dict) or set(redis) != {"version", "rdb_version", "key_count", "prefixes", "types", "persistent", "expiring", "min_ttl_ms", "max_ttl_ms", "rdb_sha256"}:
    raise SystemExit("Redis evidence keys are invalid")
if not isinstance(collector, dict) or set(collector) != {"user_version", "schema_fingerprint", "tables", "integrity_check"}:
    raise SystemExit("Collector evidence keys are invalid")
if set(collector.get("tables", {})) != {"turns", "spans", "llm_calls", "logs"} or collector.get("integrity_check") != "ok":
    raise SystemExit("Collector aggregate set is invalid")
def safe_counts(value):
    return isinstance(value, dict) and all(re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key) and type(count) is int and count >= 0 for key, count in value.items())
if not safe_counts(pg["tables"]) or not all(safe_counts(item) for item in pg["states"].values()):
    raise SystemExit("PostgreSQL aggregate counts are invalid")
if not safe_counts(redis.get("prefixes")) or not safe_counts(redis.get("types")) or not safe_counts(collector.get("tables")):
    raise SystemExit("aggregate category counts are invalid")
if not re.fullmatch(r"[0-9]+(?:[.][0-9]+){1,3}", redis.get("version", "")):
    raise SystemExit("Redis version is invalid")
for key in ("rdb_version", "key_count", "persistent", "expiring", "min_ttl_ms", "max_ttl_ms"):
    if type(redis.get(key)) is not int or redis[key] < 0:
        raise SystemExit("Redis numeric evidence is invalid")
if redis["rdb_version"] < 1 or redis["persistent"] + redis["expiring"] != redis["key_count"]:
    raise SystemExit("Redis aggregate total is invalid")
if sum(redis["prefixes"].values()) != redis["key_count"] or sum(redis["types"].values()) != redis["key_count"]:
    raise SystemExit("Redis category totals are invalid")
if type(collector.get("user_version")) is not int or collector["user_version"] < 0:
    raise SystemExit("Collector user_version is invalid")
for value in (pg["schema_fingerprint"], pg["archive_fingerprint"], redis.get("rdb_sha256"), collector["schema_fingerprint"]):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SystemExit("manifest fingerprint is invalid")
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
  local postgres_version_num vector_version postgres_schema_json postgres_schema_fingerprint
  local redis_info redis_version redis_fingerprint collector_json disk_available
  load_runtime
  [[ -n "$("${compose[@]}" ps -q --status running postgres)" ]] || die "postgres is not running"
  [[ -n "$("${compose[@]}" ps -q --status running redis)" ]] || die "redis is not running"
  [[ -n "$("${compose[@]}" ps -q --status running observability-collector)" ]] \
    || die "collector is not running"
  postgres_version_num="$("${compose[@]}" exec -T postgres \
    psql -U cockpit -d cockpit -At -c "SHOW server_version_num")"
  vector_version="$("${compose[@]}" exec -T postgres psql -U cockpit -d cockpit -At \
    -c "SELECT extversion FROM pg_extension WHERE extname='vector'")"
  postgres_schema_json="$("${compose[@]}" exec -T postgres psql -U cockpit -d cockpit -At <<'SQL'
SELECT json_build_object(
  'columns', COALESCE((SELECT json_agg(json_build_array(table_name,column_name,ordinal_position,
    data_type,udt_name,is_nullable,column_default) ORDER BY table_name,ordinal_position)
    FROM information_schema.columns WHERE table_schema='public'), '[]'::json),
  'primary_keys', COALESCE((SELECT json_agg(json_build_array(tc.table_name,tc.constraint_name,kcu.column_name)
    ORDER BY tc.table_name,tc.constraint_name,kcu.ordinal_position)
    FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu
    ON tc.constraint_schema=kcu.constraint_schema AND tc.constraint_name=kcu.constraint_name
    WHERE tc.table_schema='public' AND tc.constraint_type='PRIMARY KEY'), '[]'::json),
  'indexes', COALESCE((SELECT json_agg(json_build_array(tablename,indexname,indexdef) ORDER BY tablename,indexname)
    FROM pg_indexes WHERE schemaname='public'), '[]'::json)
);
SQL
)"
  postgres_schema_fingerprint="$(printf '%s' "${postgres_schema_json}" | python3 -c '
import hashlib,json,sys
value=json.load(sys.stdin)
encoded=json.dumps(value,ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(",", ":")).encode("ascii")
print(hashlib.sha256(encoded).hexdigest())
')"
  redis_info="$("${compose[@]}" exec -T redis redis-cli --raw INFO server)"
  redis_version="$(printf '%s\n' "${redis_info}" | sed -n 's/^redis_version://p' | tr -d '\r')"
  [[ "${redis_version}" =~ ^[0-9]+([.][0-9]+){1,3}$ ]] || die "redis_version is invalid"
  redis_fingerprint="$(printf '%s' "${redis_version}" | sha256sum | cut -d' ' -f1)"
  collector_json="$("${compose[@]}" exec -T observability-collector python -c '
import hashlib,json,sqlite3
with sqlite3.connect("file:/data/obs.db?mode=ro", uri=True) as connection:
    version=connection.execute("PRAGMA user_version").fetchone()[0]
    rows=connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE \"sqlite_%\" ORDER BY type,name").fetchall()
encoded=json.dumps(rows,ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(",", ":")).encode("ascii")
print(json.dumps({"user_version":version,"schema_fingerprint":hashlib.sha256(encoded).hexdigest()},sort_keys=True,separators=(",", ":")))
')"
  disk_available="$(df --output=avail -B1 "${RELEASE_ROOT}" | tail -n 1 | tr -d ' ')"
  python3 - "${CURRENT_RELEASE}" "${RUNTIME_PROJECT_NAME}" "${disk_available}" \
    "${postgres_version_num}" "${vector_version}" "${postgres_schema_fingerprint}" \
    "${redis_version}" "${redis_fingerprint}" "${collector_json}" <<'PY'
import json, re, sys
collector=json.loads(sys.argv[9])
if re.fullmatch(r"[0-9a-f]{64}", sys.argv[6]) is None: raise SystemExit("invalid postgres fingerprint")
if re.fullmatch(r"[0-9a-f]{64}", sys.argv[8]) is None: raise SystemExit("invalid redis fingerprint")
print(json.dumps({
    "current_release": sys.argv[1],
    "runtime_project_name": sys.argv[2],
    "disk_available_bytes": int(sys.argv[3]),
    "stores": {
        "postgres": {"major": str(int(sys.argv[4]) // 10000), "vector_version": sys.argv[5],
                     "schema_fingerprint": sys.argv[6], "running": True},
        "redis": {"version": sys.argv[7], "schema_fingerprint": sys.argv[8], "running": True},
        "collector": {**collector, "running": True},
    },
    "status": "inspect_only",
}, sort_keys=True, separators=(",", ":")))
PY
}

preflight_migration() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}" current_file
  local postgres_id postgres_image redis_id redis_image archive_fingerprint archive_listing redis_check
  load_runtime
  require_import_bundle "${migration_id}"
  current_file="${directory}/preflight-current.json"
  postgres_id="$("${compose[@]}" ps -a -q postgres)" || return $?
  redis_id="$("${compose[@]}" ps -a -q redis)" || return $?
  [[ "${postgres_id}" =~ ^[0-9a-f]{12,64}$ && "${redis_id}" =~ ^[0-9a-f]{12,64}$ ]] \
    || die "store container identity is invalid"
  postgres_image="$(docker inspect --format '{{.Image}}' "${postgres_id}")"
  redis_image="$(docker inspect --format '{{.Image}}' "${redis_id}")"
  archive_listing="$(docker run --rm --mount "type=bind,source=${directory},target=/snapshot,readonly" \
    --entrypoint pg_restore "${postgres_image}" --list /snapshot/postgres.dump)" || return $?
  archive_fingerprint="$(printf '%s\n' "${archive_listing}" | sed '/^; Archive created at/d' | sha256sum | cut -d' ' -f1)"
  redis_check="$(docker run --rm --mount "type=bind,source=${directory},target=/snapshot,readonly" \
    --entrypoint redis-check-rdb "${redis_image}" /snapshot/redis.rdb)" || return $?
  [[ "${redis_check}" == *"CRC64 checksum is OK"* ]] || die "Redis import format validation failed"
  inspect_current >"${current_file}.partial"
  chmod 0600 -- "${current_file}.partial"
  mv -T "${current_file}.partial" "${current_file}"
  python3 - "${directory}/manifest.json" "${current_file}" "${archive_fingerprint}" <<'PY'
import json, sys
from pathlib import Path
manifest=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
current=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
if manifest["postgres"]["archive_fingerprint"] != sys.argv[3]:
    raise SystemExit("PostgreSQL archive fingerprint mismatch")
if Path(current["current_release"]).name != manifest["source_sha"]:
    raise SystemExit("current release does not match migration source")
if current["stores"]["postgres"]["schema_fingerprint"] != manifest["postgres"]["schema_fingerprint"]:
    raise SystemExit("PostgreSQL schema fingerprint mismatch")
if current["stores"]["postgres"]["major"] != manifest["postgres"]["major"]:
    raise SystemExit("PostgreSQL major version mismatch")
if current["stores"]["postgres"]["vector_version"] != manifest["postgres"]["vector_version"]:
    raise SystemExit("PostgreSQL vector version mismatch")
if current["stores"]["collector"]["schema_fingerprint"] != manifest["collector"]["schema_fingerprint"]:
    raise SystemExit("Collector schema fingerprint mismatch")
if current["stores"]["collector"]["user_version"] != manifest["collector"]["user_version"]:
    raise SystemExit("Collector user_version mismatch")
source_redis_major=str(manifest["redis"]["version"]).split(".",1)[0]
target_redis_major=str(current["stores"]["redis"]["version"]).split(".",1)[0]
if source_redis_major != target_redis_major:
    raise SystemExit("Redis major version mismatch")
required=max(sum(item["size_bytes"] for item in manifest["files"].values()) * 3, 1024 * 1024)
if current["disk_available_bytes"] < required:
    raise SystemExit("insufficient migration disk space")
PY
  "${compose[@]}" config --services >/dev/null
  printf '{"migration_id":"%s","status":"preflight_ok"}\n' "${migration_id}"
}

stop_application_writers() {
  local service services_text running_id
  local -a services writers
  services_text="$("${compose[@]}" config --services)" || return $?
  mapfile -t services <<<"${services_text}"
  [[ "${#services[@]}" -gt 2 ]] || die "compose service list is incomplete"
  for service in "${services[@]}"; do
    [[ "${service}" =~ ^[a-z0-9-]+$ ]] || die "compose service name is invalid"
    [[ "${service}" == "postgres" || "${service}" == "redis" ]] || writers+=("${service}")
  done
  "${compose[@]}" stop "${writers[@]}" || return $?
  for service in "${writers[@]}"; do
    running_id="$("${compose[@]}" ps -q --status running "${service}")" || return $?
    [[ -z "${running_id}" ]] || die "application writer did not stop: ${service}"
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
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='cockpit' AND pid <> pg_backend_pid()" >/dev/null || return $?
  "${compose[@]}" exec -T postgres pg_restore -U cockpit -d cockpit \
    --clean --if-exists --no-owner --no-privileges --exit-on-error <"${dump}" || return $?
}

resolve_redis_identity() {
  local ids_text
  local -a ids
  ids_text="$("${compose[@]}" ps -a -q redis)" || return $?
  mapfile -t ids <<<"${ids_text}"
  [[ "${#ids[@]}" -eq 1 && "${ids[0]}" =~ ^[0-9a-f]{12,64}$ ]] \
    || die "redis container identity is not unique"
  RESOLVED_REDIS_CONTAINER="${ids[0]}"
  RESOLVED_REDIS_VOLUME="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "${RESOLVED_REDIS_CONTAINER}")"
  RESOLVED_REDIS_IMAGE="$(docker inspect --format '{{.Image}}' "${RESOLVED_REDIS_CONTAINER}")"
  assert_named_volume "${RESOLVED_REDIS_VOLUME}" "${REDIS_VOLUME}"
  [[ "${RESOLVED_REDIS_IMAGE}" =~ ^sha256:[0-9a-f]{64}$ ]] || die "redis image identity is invalid"
}

restore_redis_rdb() {
  local rdb="$1" migration_id="$2" bucket="${3:-redis-volume}"
  local rollback_dir="${IMPORT_ROOT}/${migration_id}/rollback/${bucket}"
  local redis_container actual_volume image_id
  [[ -s "${rdb}" && ! -L "${rdb}" ]] || die "Redis restore source is invalid"
  resolve_redis_identity
  redis_container="${RESOLVED_REDIS_CONTAINER}"
  actual_volume="${RESOLVED_REDIS_VOLUME}"
  image_id="${RESOLVED_REDIS_IMAGE}"
  "${compose[@]}" stop redis || return $?
  resolve_redis_identity
  [[ "${RESOLVED_REDIS_CONTAINER}" == "${redis_container}" \
     && "${RESOLVED_REDIS_VOLUME}" == "${actual_volume}" \
     && "${RESOLVED_REDIS_IMAGE}" == "${image_id}" ]] \
    || die "redis identity changed after stop"
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
    ' || return $?
  "${compose[@]}" up -d --no-build --pull never redis || return $?
  "${compose[@]}" exec -T redis redis-cli PING | grep -Fx PONG >/dev/null || return $?
  "${compose[@]}" exec -T redis redis-check-rdb /data/dump.rdb | grep -F "CRC64 checksum is OK" >/dev/null || return $?
  "${compose[@]}" exec -T redis test -d /data/appendonlydir || return $?
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
  [[ "$?" -eq 0 ]] || return $?
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
  [[ "$?" -eq 0 ]] || return $?
  install_collector_db "${directory}/collector.db" "${migration_id}" "failed-import-collector-volume" || return $?
}

collect_target_attestation() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}"
  local pg_json redis_json collector_json collector_container collector_image evidence_partial
  pg_json="$("${compose[@]}" exec -T postgres psql -U cockpit -d cockpit -At <<'SQL'
SELECT json_build_object(
 'tables', json_build_object(
  'memory_item',(SELECT count(*) FROM memory_item),'memory_relation',(SELECT count(*) FROM memory_relation),
  'reminder_item',(SELECT count(*) FROM reminder_item),'task_ledger',(SELECT count(*) FROM task_ledger),
  'proactive_delivery',(SELECT count(*) FROM proactive_delivery),'scene_item',(SELECT count(*) FROM scene_item),
  'voiceprint',(SELECT count(*) FROM voiceprint),'agents',(SELECT count(*) FROM agents),
  'agent_capability_vec',(SELECT count(*) FROM agent_capability_vec)),
 'states', json_build_object(
  'reminder_item.status',(SELECT COALESCE(json_object_agg(status,count),'{}'::json) FROM (SELECT status,count(*) count FROM reminder_item GROUP BY status)x),
  'task_ledger.status',(SELECT COALESCE(json_object_agg(status,count),'{}'::json) FROM (SELECT status,count(*) count FROM task_ledger GROUP BY status)x),
  'proactive_delivery.state',(SELECT COALESCE(json_object_agg(state,count),'{}'::json) FROM (SELECT state,count(*) count FROM proactive_delivery GROUP BY state)x),
  'scene_item.status',(SELECT COALESCE(json_object_agg(status,count),'{}'::json) FROM (SELECT status,count(*) count FROM scene_item GROUP BY status)x)),
 'schema', json_build_object(
  'columns',COALESCE((SELECT json_agg(json_build_array(table_name,column_name,ordinal_position,data_type,udt_name,is_nullable,column_default) ORDER BY table_name,ordinal_position) FROM information_schema.columns WHERE table_schema='public'),'[]'::json),
  'primary_keys',COALESCE((SELECT json_agg(json_build_array(tc.table_name,tc.constraint_name,kcu.column_name) ORDER BY tc.table_name,tc.constraint_name,kcu.ordinal_position) FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_schema=kcu.constraint_schema AND tc.constraint_name=kcu.constraint_name WHERE tc.table_schema='public' AND tc.constraint_type='PRIMARY KEY'),'[]'::json),
  'indexes',COALESCE((SELECT json_agg(json_build_array(tablename,indexname,indexdef) ORDER BY tablename,indexname) FROM pg_indexes WHERE schemaname='public'),'[]'::json)));
SQL
)"
  redis_json="$("${compose[@]}" exec -T redis redis-cli --json EVAL '
redis.setresp(3); local c="0"; local n,p,e=0,0,0; local lo=nil; local hi=0; local px={}; local ty={};
repeat local r=redis.call("SCAN",c,"COUNT",1000); c=r[1]; for _,k in ipairs(r[2]) do n=n+1; local h=string.match(k,"^([A-Za-z0-9_-]+):") or "other"; if string.len(h)>32 then h="other" end; px[h]=(px[h] or 0)+1; local t=redis.call("TYPE",k); if type(t)=="table" then t=t.ok end; ty[t]=(ty[t] or 0)+1; local ttl=redis.call("PTTL",k); if ttl<0 then p=p+1 else e=e+1; if lo==nil or ttl<lo then lo=ttl end; if ttl>hi then hi=ttl end end end until c=="0";
local a={}; local b={}; for k,v in pairs(px) do table.insert(a,k);table.insert(a,v) end; for k,v in pairs(ty) do table.insert(b,k);table.insert(b,v) end; return {map={"key_count",n,"prefixes",{map=a},"types",{map=b},"persistent",p,"expiring",e,"min_ttl_ms",lo or 0,"max_ttl_ms",hi}}' 0)"
  mapfile -t collector_ids < <("${compose[@]}" ps -a -q observability-collector)
  [[ "${#collector_ids[@]}" -eq 1 ]] || return 1
  collector_container="${collector_ids[0]}"
  collector_image="$(docker inspect --format '{{.Image}}' "${collector_container}")"
  collector_json="$(docker run --rm --mount "type=volume,source=${COLLECTOR_VOLUME},target=/data,readonly" \
    --entrypoint python "${collector_image}" -c '
import hashlib,json,sqlite3
with sqlite3.connect("file:/data/obs.db?mode=ro",uri=True) as c:
 rows=c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE \"sqlite_%\" ORDER BY type,name").fetchall(); tables={n:c.execute(f"SELECT count(*) FROM {n}").fetchone()[0] for n in ("turns","spans","llm_calls","logs")}; ok=c.execute("PRAGMA integrity_check").fetchall()==[("ok",)]; version=c.execute("PRAGMA user_version").fetchone()[0]
encoded=json.dumps(rows,ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(",", ":")).encode("ascii"); print(json.dumps({"user_version":version,"schema_fingerprint":hashlib.sha256(encoded).hexdigest(),"tables":tables,"integrity_check":"ok" if ok else "failed"},sort_keys=True,separators=(",", ":")))')"
  evidence_partial="${directory}/evidence.json.partial"
  python3 - "${directory}/manifest.json" "${evidence_partial}" "${pg_json}" "${redis_json}" "${collector_json}" <<'PY'
import hashlib,json,sys
from pathlib import Path
m=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")); pg=json.loads(sys.argv[3]); r=json.loads(sys.argv[4]); c=json.loads(sys.argv[5])
schema=pg.pop("schema"); schema_hash=hashlib.sha256(json.dumps(schema,ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(",", ":")).encode("ascii")).hexdigest()
if schema_hash!=m["postgres"]["schema_fingerprint"]: raise SystemExit("PostgreSQL schema aggregate mismatch")
if pg["tables"]!=m["postgres"]["tables"] or pg["states"]!=m["postgres"]["states"]: raise SystemExit("PostgreSQL aggregate mismatch")
for key in ("key_count","prefixes","types","persistent","expiring"):
    if r[key]!=m["redis"][key]: raise SystemExit("Redis aggregate mismatch")
for key in ("min_ttl_ms","max_ttl_ms"):
    if not isinstance(r[key],int) or r[key]<0 or r[key]>m["redis"][key]: raise SystemExit("Redis TTL aggregate mismatch")
if c!=m["collector"]: raise SystemExit("Collector aggregate mismatch")
Path(sys.argv[2]).write_text(json.dumps({"postgres":pg,"redis":r,"collector":c},sort_keys=True,separators=(",", ":"))+"\n",encoding="utf-8")
PY
  chmod 0600 -- "${evidence_partial}"
  mv -T "${evidence_partial}" "${directory}/evidence.json"
}

verify_store_group() {
  collect_target_attestation "$1"
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
  stop_application_writers || { write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"; return 1; }
  restore_postgres_dump "${BACKUP_ROOT}/postgres/${backup_stamp}.dump" \
    || { write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"; return 1; }
  restore_redis_rdb "${BACKUP_ROOT}/redis/${backup_stamp}.rdb" "${migration_id}" "failed-import-redis-volume" \
    || { write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"; return 1; }
  restore_collector_sql "${BACKUP_ROOT}/observability/${backup_stamp}.sql.gz" "${migration_id}" \
    || { write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"; return 1; }
  start_current_release || { write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"; return 1; }
  verify_current_release || { write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"; return 1; }
  write_migration_state "ROLLED_BACK" "${migration_id}" "${backup_stamp}" || return 1
}

fail_and_rollback() {
  local migration_id="$1" backup_stamp="$2" failed_step="$3"
  if rollback_all "${migration_id}" "${backup_stamp}"; then
    die "migration step ${failed_step} failed and the store group was rolled back"
  fi
  write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}" || true
  die "migration step ${failed_step} and automatic rollback failed"
}

apply_migration() {
  local migration_id="$1" backup_stamp
  load_runtime
  require_import_bundle "${migration_id}"
  preflight_migration "${migration_id}"
  backup_stamp="$(run_required_backup)"
  write_migration_state "BACKED_UP" "${migration_id}" "${backup_stamp}"
  preflight_migration "${migration_id}"
  stop_application_writers || fail_and_rollback "${migration_id}" "${backup_stamp}" "stop-writers"
  restore_postgres_dump "${IMPORT_ROOT}/${migration_id}/postgres.dump" \
    || fail_and_rollback "${migration_id}" "${backup_stamp}" "postgres-restore"
  restore_redis_rdb "${IMPORT_ROOT}/${migration_id}/redis.rdb" "${migration_id}" \
    || fail_and_rollback "${migration_id}" "${backup_stamp}" "redis-restore"
  install_collector_db "${IMPORT_ROOT}/${migration_id}/collector.db" "${migration_id}" \
    || fail_and_rollback "${migration_id}" "${backup_stamp}" "collector-restore"
  verify_store_group "${migration_id}" \
    || fail_and_rollback "${migration_id}" "${backup_stamp}" "store-verification"
  start_current_release || fail_and_rollback "${migration_id}" "${backup_stamp}" "start-release"
  verify_current_release || fail_and_rollback "${migration_id}" "${backup_stamp}" "release-verification"
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
    prepare-upload|seal-upload|preflight|apply|verify|rollback)
      [[ "$#" -eq 3 && "${2:-}" == "--migration-id" ]] || die "action requires --migration-id" 2
      require_migration_id "${3:-}"
      case "$1" in
        prepare-upload) prepare_upload "$3" ;;
        seal-upload) seal_upload "$3" ;;
        preflight) preflight_migration "$3" ;;
        apply) apply_migration "$3" ;;
        verify) verify_migration "$3" ;;
        rollback) rollback_migration "$3" ;;
      esac ;;
    *) die "unknown data migration action" 2 ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
