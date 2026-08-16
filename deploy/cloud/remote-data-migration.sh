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
STEP_RC=0
STEP_OUTPUT=""
APPLY_MIGRATION_ID=""
APPLY_BACKUP_STAMP=""
APPLY_REPLACEMENT_STARTED=0
APPLY_FAILURE_ACTIVE=0

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
  require_sealed_inbound_bundle "${migration_id}"
}

require_sealed_inbound_bundle() {
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
    if stat.S_IMODE(directory.st_mode) != 0o700:
        raise SystemExit("sealed migration directory is invalid")
    entries = set(os.listdir(root))
    if entries != required:
        raise SystemExit("sealed inbound file set is invalid")
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
  validate_import_manifest "${migration_id}"
}

require_preapply_batch() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}"
  require_migration_id "${migration_id}"
  python3 - "${directory}" "${migration_id}" <<'PY'
import hashlib, json, os, stat, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
required = {"manifest.json", "postgres.dump", "redis.rdb", "collector.db"}
allowed = required | {"preflight-current.json"}
root = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
descriptors = {}
try:
    metadata = os.fstat(root)
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700):
        raise SystemExit("preapply batch root is invalid")
    entries = set(os.listdir(root))
    if entries != allowed:
        raise SystemExit("preapply batch file set is invalid")
    for name in sorted(entries):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
        descriptors[name] = descriptor
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_uid != 0 or metadata.st_gid != 0
                or stat.S_IMODE(metadata.st_mode) != 0o600):
            raise SystemExit("preapply batch file is invalid")
    manifest_bytes = os.read(descriptors["manifest.json"], os.fstat(descriptors["manifest.json"]).st_size)
    marker_bytes = os.read(descriptors["preflight-current.json"], os.fstat(descriptors["preflight-current.json"]).st_size)
    manifest = json.loads(manifest_bytes)
    marker = json.loads(marker_bytes)
    if set(marker) != {"schema_version","migration_id","manifest_sha256","archive_fingerprint","inspected_at","current"}:
        raise SystemExit("preflight marker is forged")
    if (marker["schema_version"] != 1 or marker["migration_id"] != sys.argv[2]
            or marker["manifest_sha256"] != hashlib.sha256(manifest_bytes).hexdigest()
            or marker["archive_fingerprint"] != manifest["postgres"]["archive_fingerprint"]):
        raise SystemExit("preflight marker is forged")
    try:
        inspected = datetime.fromisoformat(marker["inspected_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise SystemExit("preflight marker is forged") from exc
    canonical = inspected.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    now = datetime.now(timezone.utc)
    if canonical != marker["inspected_at"] or inspected > now + timedelta(seconds=30):
        raise SystemExit("preflight marker is forged")
    if datetime.now(timezone.utc) - inspected > timedelta(minutes=5):
        raise SystemExit("preflight marker is stale")
    current = marker["current"]
    if (not isinstance(current, dict) or set(current) != {"current_release","runtime_project_name","disk_available_bytes","stores","status"}
            or marker["current"]["status"] != "inspect_only"):
        raise SystemExit("preflight marker is forged")
    stores = current["stores"]
    if (not isinstance(stores, dict) or set(stores) != {"postgres","redis","collector"}
            or set(stores["postgres"]) != {"major","vector_version","schema_fingerprint","running"}
            or set(stores["redis"]) != {"version","schema_fingerprint","running"}
            or set(stores["collector"]) != {"user_version","schema_fingerprint","running"}
            or not all(store["running"] is True for store in stores.values())):
        raise SystemExit("preflight marker is forged")
    if (Path(current["current_release"]).name != manifest["source_sha"]
            or stores["postgres"]["major"] != manifest["postgres"]["major"]
            or stores["postgres"]["vector_version"] != manifest["postgres"]["vector_version"]
            or stores["postgres"]["schema_fingerprint"] != manifest["postgres"]["schema_fingerprint"]
            or str(stores["redis"]["version"]).split(".",1)[0] != str(manifest["redis"]["version"]).split(".",1)[0]
            or stores["collector"]["user_version"] != manifest["collector"]["user_version"]
            or stores["collector"]["schema_fingerprint"] != manifest["collector"]["schema_fingerprint"]):
        raise SystemExit("preflight marker is forged")
    needed = max(sum(item["size_bytes"] for item in manifest["files"].values()) * 3, 1024 * 1024)
    if type(current["disk_available_bytes"]) is not int or current["disk_available_bytes"] < needed:
        raise SystemExit("preflight marker is forged")
finally:
    for descriptor in descriptors.values(): os.close(descriptor)
    os.close(root)
PY
  validate_import_manifest "${migration_id}"
}

require_runtime_batch() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}"
  require_migration_id "${migration_id}"
  python3 - "${directory}" <<'PY'
import os, re, stat, sys
required = {"manifest.json", "postgres.dump", "redis.rdb", "collector.db"}
allowed_files = required | {"status.json", "preflight-current.json", "evidence-pre-start.json", "evidence-post-start.json"}
allowed_directories = {"rollback", "rollback-generated"}
redis_buckets = {"redis-volume", "failed-import-redis-volume"}
collector_buckets = {"collector-volume", "failed-import-collector-volume"}

def opened(parent, name, directory=False):
    flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except OSError as exc:
        raise SystemExit("runtime batch symlink or special file is forbidden") from exc
    metadata = os.fstat(descriptor)
    expected_mode = 0o700 if directory else 0o600
    valid_type = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if (not valid_type or (not directory and metadata.st_nlink != 1)
            or metadata.st_uid != 0 or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != expected_mode):
        os.close(descriptor)
        raise SystemExit("runtime batch entry owner, mode, or type is invalid")
    return descriptor

def validate_regular_set(parent, entries, allowed, required_names=frozenset()):
    if not required_names.issubset(entries) or not entries.issubset(allowed):
        raise SystemExit("unknown runtime batch entry")
    for name in sorted(entries):
        os.close(opened(parent, name))

root = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    metadata = os.fstat(root)
    expected_mode = 0o700
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != expected_mode):
        raise SystemExit("runtime batch root is invalid")
    entries = set(os.listdir(root))
    if not required.issubset(entries) or not entries.issubset(allowed_files | allowed_directories):
        raise SystemExit("unknown runtime batch entry")
    validate_regular_set(root, entries & allowed_files, allowed_files, required)
    if "rollback-generated" in entries:
        generated = opened(root, "rollback-generated", directory=True)
        try:
            validate_regular_set(generated, set(os.listdir(generated)), {"collector.db"}, {"collector.db"})
        finally:
            os.close(generated)
    if "rollback" in entries:
        rollback = opened(root, "rollback", directory=True)
        try:
            buckets = set(os.listdir(rollback))
            if not buckets.issubset(redis_buckets | collector_buckets):
                raise SystemExit("unknown runtime batch entry")
            for bucket in sorted(buckets):
                bucket_fd = opened(rollback, bucket, directory=True)
                try:
                    bucket_entries = set(os.listdir(bucket_fd))
                    if bucket in collector_buckets:
                        validate_regular_set(bucket_fd, bucket_entries, {"obs.db", "obs.db-wal", "obs.db-shm"}, {"obs.db"})
                    else:
                        if not bucket_entries.issubset({"dump.rdb", "appendonlydir"}):
                            raise SystemExit("unknown runtime batch entry")
                        if "dump.rdb" in bucket_entries:
                            os.close(opened(bucket_fd, "dump.rdb"))
                        if "appendonlydir" in bucket_entries:
                            appendonly = opened(bucket_fd, "appendonlydir", directory=True)
                            try:
                                aof_entries = set(os.listdir(appendonly))
                                for name in sorted(aof_entries):
                                    if re.fullmatch(r"appendonly[.]aof(?:[.]manifest|[.][0-9]+[.](?:base[.](?:rdb|aof)|incr[.]aof))", name) is None:
                                        raise SystemExit("unknown runtime batch entry")
                                    os.close(opened(appendonly, name))
                            finally:
                                os.close(appendonly)
                finally:
                    os.close(bucket_fd)
        finally:
            os.close(rollback)
finally:
    os.close(root)
PY
  validate_import_manifest "${migration_id}"
}

validate_import_manifest() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}"
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
  output="$("${SHARED_ROOT}/bin/backup.sh" --transaction-lock-fd "${TRANSACTION_LOCK_FD}" --writers-quiesced)" \
    || return $?
  stamp="${output##*backup completed: }"
  [[ "${stamp}" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || return 1
  [[ -s "${BACKUP_ROOT}/postgres/${stamp}.dump" \
     && -s "${BACKUP_ROOT}/redis/${stamp}.rdb" \
     && -s "${BACKUP_ROOT}/observability/${stamp}.sql.gz" \
     && -s "${BACKUP_ROOT}/${stamp}.backup-manifest.json" ]] || return 1
  validate_backup_manifest "${stamp}" || return $?
  printf '%s\n' "${stamp}"
}

validate_backup_manifest() {
  local stamp="$1"
  python3 - "${BACKUP_ROOT}" "${stamp}" <<'PY' || return $?
import hashlib, json, os, re, stat, sys
from pathlib import Path

root=Path(sys.argv[1]); stamp=sys.argv[2]
if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z",stamp) is None: raise SystemExit("invalid backup stamp")
path=root/f"{stamp}.backup-manifest.json"
if path.is_symlink() or path.stat().st_size > 64*1024: raise SystemExit("invalid backup manifest")
def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError("duplicate key")
        result[key]=value
    return result
payload=json.loads(path.read_text(encoding="utf-8"),object_pairs_hook=unique)
if set(payload)!={"schema_version","backup_stamp","files"} or payload["schema_version"]!=1 or payload["backup_stamp"]!=stamp:
    raise SystemExit("backup manifest contract mismatch")
expected={"postgres.dump":root/"postgres"/f"{stamp}.dump","redis.rdb":root/"redis"/f"{stamp}.rdb","collector.sql.gz":root/"observability"/f"{stamp}.sql.gz"}
if set(payload["files"])!=set(expected): raise SystemExit("backup manifest file set mismatch")
for name,file_path in expected.items():
    info=os.lstat(file_path)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1: raise SystemExit("unsafe backup file")
    digest=hashlib.sha256(); size=0
    with file_path.open("rb") as source:
        while chunk:=source.read(1024*1024): size+=len(chunk); digest.update(chunk)
    record=payload["files"][name]
    if set(record)!={"size_bytes","sha256"} or record!={"size_bytes":size,"sha256":digest.hexdigest()}:
        raise SystemExit("backup hash mismatch")
PY
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

write_preflight_current() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}" current_file
  local postgres_id postgres_image redis_id redis_image archive_fingerprint archive_listing redis_check current_json
  local partial run_id
  current_file="${directory}/preflight-current.json"
  postgres_id="$("${compose[@]}" ps -a -q postgres)" || return $?
  redis_id="$("${compose[@]}" ps -a -q redis)" || return $?
  [[ "${postgres_id}" =~ ^[0-9a-f]{12,64}$ && "${redis_id}" =~ ^[0-9a-f]{12,64}$ ]] \
    || die "store container identity is invalid"
  postgres_image="$(docker inspect --format '{{.Image}}' "${postgres_id}")"
  redis_image="$(docker inspect --format '{{.Image}}' "${redis_id}")"
  archive_listing="$(docker run --pull never --rm --mount "type=bind,source=${directory},target=/snapshot,readonly" \
    --entrypoint pg_restore "${postgres_image}" --list /snapshot/postgres.dump)" || return $?
  archive_fingerprint="$(printf '%s\n' "${archive_listing}" | sed '/^; Archive created at/d' | sha256sum | cut -d' ' -f1)"
  redis_check="$(docker run --pull never --rm --mount "type=bind,source=${directory},target=/snapshot,readonly" \
    --entrypoint redis-check-rdb "${redis_image}" /snapshot/redis.rdb)" || return $?
  [[ "${redis_check}" == *"CRC64 checksum is OK"* ]] || die "Redis import format validation failed"
  current_json="$(inspect_current)" || return $?
  if compgen -G "${directory}/preflight.*.json.partial" >/dev/null; then return 1; fi
  run_id="$(python3 -c 'import secrets; print(secrets.token_hex(12))')" || return $?
  partial="${directory}/preflight.${run_id}.json.partial"
  python3 - "${directory}/manifest.json" "${partial}" "${archive_fingerprint}" \
    "${current_json}" "${migration_id}" <<'PY' || return $?
import hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
manifest_bytes=Path(sys.argv[1]).read_bytes(); manifest=json.loads(manifest_bytes); current=json.loads(sys.argv[4])
if set(current)!={"current_release","runtime_project_name","disk_available_bytes","stores","status"} or current["status"]!="inspect_only":
    raise SystemExit("current inspection contract mismatch")
stores=current["stores"]
if (set(stores)!={"postgres","redis","collector"}
        or set(stores["postgres"])!={"major","vector_version","schema_fingerprint","running"}
        or set(stores["redis"])!={"version","schema_fingerprint","running"}
        or set(stores["collector"])!={"user_version","schema_fingerprint","running"}
        or not all(store["running"] is True for store in stores.values())):
    raise SystemExit("current store inspection contract mismatch")
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
inspected_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
marker={"schema_version":1,"migration_id":sys.argv[5],"manifest_sha256":hashlib.sha256(manifest_bytes).hexdigest(),
        "archive_fingerprint":sys.argv[3],"inspected_at":inspected_at,"current":current}
encoded=(json.dumps(marker,sort_keys=True,separators=(",", ":"))+"\n").encode("utf-8")
descriptor=os.open(sys.argv[2],os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o600)
try:
    os.write(descriptor,encoded); os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
  chmod 0600 -- "${partial}" || return $?
  mv -T "${partial}" "${current_file}" || return $?
  "${compose[@]}" config --services >/dev/null
}

preflight_migration() {
  local migration_id="$1"
  load_runtime || return $?
  require_sealed_inbound_bundle "${migration_id}" || return $?
  write_preflight_current "${migration_id}" || return $?
  printf '{"migration_id":"%s","status":"preflight_ok"}\n' "${migration_id}"
}

refresh_preflight_after_backup() {
  local migration_id="$1"
  require_runtime_batch "${migration_id}" || return $?
  write_preflight_current "${migration_id}" || return $?
}

refresh_preflight_before_stop() {
  refresh_preflight_after_backup "$1" || return $?
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

wait_for_compose_redis() {
  local attempt reply
  for attempt in $(seq 1 60); do
    reply="$("${compose[@]}" exec -T redis redis-cli PING 2>/dev/null)" || reply=""
    if [[ "${reply}" == "PONG" ]]; then return 0; fi
    sleep 1
  done
  return 1
}

assert_redis_container_matches_manifest() {
  local container_id="$1" manifest="$2" aggregate
  aggregate="$(docker exec "${container_id}" redis-cli --json EVAL '
redis.setresp(3); local c="0"; local n,p,e=0,0,0; local px={}; local ty={};
repeat local r=redis.call("SCAN",c,"COUNT",1000); c=r[1]; for _,k in ipairs(r[2]) do n=n+1; local h=string.match(k,"^([A-Za-z0-9_-]+):") or "other"; if string.len(h)>32 then h="other" end; px[h]=(px[h] or 0)+1; local t=redis.call("TYPE",k); if type(t)=="table" then t=t.ok end; ty[t]=(ty[t] or 0)+1; local ttl=redis.call("PTTL",k); if ttl<0 then p=p+1 else e=e+1 end end until c=="0";
local a={}; local b={}; for k,v in pairs(px) do table.insert(a,k);table.insert(a,v) end; for k,v in pairs(ty) do table.insert(b,k);table.insert(b,v) end; return {map={"key_count",n,"prefixes",{map=a},"types",{map=b},"persistent",p,"expiring",e}}' 0)" || return $?
  python3 - "${manifest}" "${aggregate}" <<'PY' || return $?
import json, sys
from pathlib import Path
m=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["redis"]
a=json.loads(sys.argv[2])
if a["key_count"] <= 0:
    raise SystemExit("empty Redis import")
for key in ("key_count", "prefixes", "types", "persistent", "expiring"):
    if a.get(key) != m.get(key):
        raise SystemExit("Redis import aggregate mismatch")
PY
}

validate_redis_aof_volume() {
  local image_id="$1"
  docker run --rm --pull never --mount "type=volume,source=${REDIS_VOLUME},target=/data,readonly" \
    --entrypoint sh "${image_id}" -ceu '
      manifest=/data/appendonlydir/appendonly.aof.manifest
      test -s "${manifest}"
      grep -Eq "^file appendonly[.]aof[.][0-9]+[.]base[.](rdb|aof) seq [0-9]+ type b$" "${manifest}"
      grep -Eq "^file appendonly[.]aof[.][0-9]+[.]incr[.]aof seq [0-9]+ type i$" "${manifest}"
      test -z "$(find /data/appendonlydir -maxdepth 1 -type l -print -quit)"
      while read -r marker filename rest; do
        test "${marker}" = file
        test -s "/data/appendonlydir/${filename}"
      done <"${manifest}"
      redis-check-aof "${manifest}" >/dev/null
    ' || return $?
}

restore_redis_rdb() {
  local rdb="$1" migration_id="$2" bucket="${3:-redis-volume}"
  local rollback_dir="${IMPORT_ROOT}/${migration_id}/rollback/${bucket}"
  local redis_container actual_volume image_id loader info dbsize rc attempt
  [[ -s "${rdb}" && ! -L "${rdb}" ]] || { printf 'Redis restore source is invalid\n' >&2; return 1; }
  resolve_redis_identity || return $?
  redis_container="${RESOLVED_REDIS_CONTAINER}"
  actual_volume="${RESOLVED_REDIS_VOLUME}"
  image_id="${RESOLVED_REDIS_IMAGE}"
  "${compose[@]}" stop redis || return $?
  resolve_redis_identity || return $?
  [[ "${RESOLVED_REDIS_CONTAINER}" == "${redis_container}" \
     && "${RESOLVED_REDIS_VOLUME}" == "${actual_volume}" \
     && "${RESOLVED_REDIS_IMAGE}" == "${image_id}" ]] \
    || { printf 'redis identity changed after stop\n' >&2; return 1; }
  install -d -m 0700 -o root -g root "${rollback_dir}" || return $?
  docker run --rm --pull never --mount "type=volume,source=${REDIS_VOLUME},target=/data" \
    --mount "type=bind,source=${rollback_dir},target=/rollback" \
    --mount "type=bind,source=$(dirname "${rdb}"),target=/incoming,readonly" \
    --entrypoint sh "${image_id}" -ceu '
      test ! -e /rollback/dump.rdb
      test ! -e /rollback/appendonlydir
      test ! -e /data/dump.rdb || mv /data/dump.rdb /rollback/dump.rdb
      test ! -e /data/appendonlydir || mv /data/appendonlydir /rollback/appendonlydir
      test -z "$(find /rollback -type l -print -quit)"
      chown -R 0:0 /rollback
      find /rollback -type d -exec chmod 0700 {} +
      find /rollback -type f -exec chmod 0600 {} +
      install -m 0600 /incoming/'"$(basename "${rdb}")"' /data/dump.rdb
    ' || return $?
  loader="car-agent-migration-${migration_id//-/}-redis-loader"
  docker run -d --pull never --name "${loader}" \
    --mount "type=volume,source=${REDIS_VOLUME},target=/data" \
    --entrypoint redis-server "${image_id}" \
    "--dir" "/data" "--dbfilename" "dump.rdb" "--appendonly" "no" \
    "--protected-mode" "no" >/dev/null || return $?
  rc=1
  for attempt in $(seq 1 60); do
    if docker exec "${loader}" redis-cli PING | grep -Fx PONG >/dev/null; then rc=0; break; fi
    sleep 1
  done
  if [[ "${rc}" -ne 0 ]]; then docker rm -f "${loader}" >/dev/null 2>&1; return 1; fi
  dbsize="$(docker exec "${loader}" redis-cli --raw DBSIZE)" || { docker rm -f "${loader}" >/dev/null 2>&1; return 1; }
  if [[ ! "${dbsize}" =~ ^[1-9][0-9]*$ ]]; then
    printf 'empty Redis import is forbidden\n' >&2
    docker rm -f "${loader}" >/dev/null 2>&1
    return 1
  fi
  assert_redis_container_matches_manifest "${loader}" "${IMPORT_ROOT}/${migration_id}/manifest.json" \
    || { docker rm -f "${loader}" >/dev/null 2>&1; return 1; }
  docker exec "${loader}" redis-cli CONFIG SET appendonly yes | grep -Fx OK >/dev/null \
    || { docker rm -f "${loader}" >/dev/null 2>&1; return 1; }
  rc=1
  for attempt in $(seq 1 120); do
    info="$(docker exec "${loader}" redis-cli --raw INFO persistence)" \
      || { docker rm -f "${loader}" >/dev/null 2>&1; return 1; }
    if grep -F 'aof_rewrite_in_progress:0' <<<"${info}" >/dev/null \
       && grep -F 'aof_last_bgrewrite_status:ok' <<<"${info}" >/dev/null; then rc=0; break; fi
    sleep 1
  done
  if [[ "${rc}" -ne 0 ]]; then docker rm -f "${loader}" >/dev/null 2>&1; return 1; fi
  docker exec "${loader}" redis-cli SAVE >/dev/null \
    || { docker rm -f "${loader}" >/dev/null 2>&1; return 1; }
  docker stop "${loader}" >/dev/null || { docker rm -f "${loader}" >/dev/null 2>&1; return 1; }
  validate_redis_aof_volume "${image_id}" || { docker rm -f "${loader}" >/dev/null 2>&1; return 1; }
  docker rm "${loader}" >/dev/null || return $?
  "${compose[@]}" up -d --no-build --pull never redis || return $?
  wait_for_compose_redis || return $?
  resolve_redis_identity || return $?
  [[ "${RESOLVED_REDIS_VOLUME}" == "${actual_volume}" && "${RESOLVED_REDIS_IMAGE}" == "${image_id}" ]] \
    || return 1
  assert_redis_container_matches_manifest "${RESOLVED_REDIS_CONTAINER}" \
    "${IMPORT_ROOT}/${migration_id}/manifest.json" || return $?
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
        os.chown(destination, 0, 0)
        os.chmod(destination, 0o600)
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
  chown root:root -- "${directory}/collector.db" || return $?
  chmod 0600 -- "${directory}/collector.db" || return $?
  install_collector_db "${directory}/collector.db" "${migration_id}" "failed-import-collector-volume" || return $?
}

collect_target_attestation() {
  local migration_id="$1" stage="$2" directory="${IMPORT_ROOT}/${1}"
  local pg_json redis_json collector_json collector_ids_text collector_container collector_image
  local evidence_partial evidence_final baseline_file run_id
  [[ "${stage}" == "pre-start" || "${stage}" == "post-start" ]] || return 2
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
)" || return $?
  redis_json="$("${compose[@]}" exec -T redis redis-cli --json EVAL '
redis.setresp(3); local c="0"; local n,p,e=0,0,0; local lo=nil; local hi=0; local px={}; local ty={}; local pp={};
repeat local r=redis.call("SCAN",c,"COUNT",1000); c=r[1]; for _,k in ipairs(r[2]) do n=n+1; local h=string.match(k,"^([A-Za-z0-9_-]+):") or "other"; if string.len(h)>32 then h="other" end; px[h]=(px[h] or 0)+1; local t=redis.call("TYPE",k); if type(t)=="table" then t=t.ok end; ty[t]=(ty[t] or 0)+1; local ttl=redis.call("PTTL",k); if ttl<0 then p=p+1; pp[h]=(pp[h] or 0)+1 else e=e+1; if lo==nil or ttl<lo then lo=ttl end; if ttl>hi then hi=ttl end end end until c=="0";
local info=redis.call("INFO","server"); local version=string.match(info,"redis_version:([^\r\n]+)") or "unknown"; local a={}; local b={}; local d={}; for k,v in pairs(px) do table.insert(a,k);table.insert(a,v) end; for k,v in pairs(ty) do table.insert(b,k);table.insert(b,v) end; for k,v in pairs(pp) do table.insert(d,k);table.insert(d,v) end; return {map={"version",version,"key_count",n,"prefixes",{map=a},"types",{map=b},"persistent_prefixes",{map=d},"persistent",p,"expiring",e,"min_ttl_ms",lo or 0,"max_ttl_ms",hi}}' 0)" || return $?
  collector_ids_text="$("${compose[@]}" ps -a -q observability-collector)" || return $?
  mapfile -t collector_ids <<<"${collector_ids_text}" || return $?
  [[ "${#collector_ids[@]}" -eq 1 ]] || return 1
  collector_container="${collector_ids[0]}"
  collector_image="$(docker inspect --format '{{.Image}}' "${collector_container}")" || return $?
  collector_json="$(docker run --rm --mount "type=volume,source=${COLLECTOR_VOLUME},target=/data,readonly" \
    --entrypoint python "${collector_image}" -c '
import hashlib,json,sqlite3
with sqlite3.connect("file:/data/obs.db?mode=ro",uri=True) as c:
 rows=c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE \"sqlite_%\" ORDER BY type,name").fetchall(); tables={n:c.execute(f"SELECT count(*) FROM {n}").fetchone()[0] for n in ("turns","spans","llm_calls","logs")}; ok=c.execute("PRAGMA integrity_check").fetchall()==[("ok",)]; version=c.execute("PRAGMA user_version").fetchone()[0]
encoded=json.dumps(rows,ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(",", ":")).encode("ascii"); print(json.dumps({"user_version":version,"schema_fingerprint":hashlib.sha256(encoded).hexdigest(),"tables":tables,"integrity_check":"ok" if ok else "failed"},sort_keys=True,separators=(",", ":")))')" || return $?
  if compgen -G "${directory}/evidence.*.json.partial" >/dev/null; then
    return 1
  fi
  baseline_file="${directory}/evidence-pre-start.json"
  if [[ "${stage}" == "pre-start" ]]; then
    evidence_final="${directory}/evidence-pre-start.json"
    [[ ! -e "${evidence_final}" ]] || return 1
  else
    evidence_final="${directory}/evidence-post-start.json"
    [[ -f "${baseline_file}" ]] || return 1
  fi
  run_id="$(python3 -c 'import secrets; print(secrets.token_hex(12))')" || return $?
  evidence_partial="${directory}/evidence.${run_id}.json.partial"
  python3 - "${directory}/manifest.json" "${evidence_partial}" "${pg_json}" "${redis_json}" \
    "${collector_json}" "${stage}" "${baseline_file}" "${migration_id}" <<'PY' || return $?
import hashlib,json,os,sys
from pathlib import Path
m=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")); pg=json.loads(sys.argv[3]); r=json.loads(sys.argv[4]); c=json.loads(sys.argv[5])
stage=sys.argv[6]
if stage not in {"pre-start", "post-start"}: raise SystemExit("invalid attestation stage")
schema=pg.pop("schema"); schema_hash=hashlib.sha256(json.dumps(schema,ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(",", ":")).encode("ascii")).hexdigest()
pg["schema_fingerprint"]=schema_hash
if not isinstance(r.get("persistent_prefixes"),dict) or not all(type(v) is int and v>=0 for v in r["persistent_prefixes"].values()):
    raise SystemExit("Redis persistent prefix aggregate is invalid")
if stage == "pre-start":
    if schema_hash!=m["postgres"]["schema_fingerprint"]: raise SystemExit("PostgreSQL schema aggregate mismatch")
    if pg["tables"]!=m["postgres"]["tables"] or pg["states"]!=m["postgres"]["states"]: raise SystemExit("PostgreSQL aggregate mismatch")
    if r["version"]!=m["redis"]["version"]: raise SystemExit("Redis version mismatch")
    for key in ("key_count","prefixes","types","persistent","expiring"):
        if r[key]!=m["redis"][key]: raise SystemExit("Redis aggregate mismatch")
    for key in ("min_ttl_ms","max_ttl_ms"):
        if not isinstance(r[key],int) or r[key]<0 or r[key]>m["redis"][key]: raise SystemExit("Redis TTL aggregate mismatch")
    if c!=m["collector"]: raise SystemExit("Collector aggregate mismatch")
else:
    baseline=json.loads(Path(sys.argv[7]).read_text(encoding="utf-8"))
    if set(baseline)!={"schema_version","migration_id","stage","postgres","redis","collector"} or baseline["schema_version"]!=1 or baseline["migration_id"]!=sys.argv[8] or baseline["stage"]!="pre-start":
        raise SystemExit("pre-start attestation baseline is invalid")
    if pg["schema_fingerprint"]!=baseline["postgres"]["schema_fingerprint"]:
        raise SystemExit("PostgreSQL schema changed after start")
    persistent_tables=("memory_item","memory_relation","reminder_item","task_ledger","proactive_delivery","scene_item","voiceprint")
    for table in persistent_tables:
        baseline_count=baseline["postgres"]["tables"][table]; current_count=pg["tables"].get(table,-1)
        if current_count < baseline_count: raise SystemExit("PostgreSQL persistent table count decreased")
    allowed_states={
      "reminder_item.status":{"pending","fired","cancelled","expired","failed"},
      "task_ledger.status":{"pending","confirmed","executing","done","failed","cancelled","expired"},
      "proactive_delivery.state":{"pending","presented","accepted","dismissed","expired","failed"},
      "scene_item.status":{"enabled","disabled"},
    }
    for state_name,baseline_counts in baseline["postgres"]["states"].items():
        current_counts=pg["states"].get(state_name,{})
        if not set(baseline_counts).issubset(allowed_states[state_name]) or not set(current_counts).issubset(allowed_states[state_name]):
            raise SystemExit("PostgreSQL state transition set is invalid")
        table=state_name.split(".",1)[0]
        if sum(current_counts.values()) != pg["tables"][table] or sum(baseline_counts.values()) != baseline["postgres"]["tables"][table]:
            raise SystemExit("PostgreSQL state entity conservation failed")
    if r["version"] != baseline["redis"]["version"]:
        raise SystemExit("Redis version changed after start")
    if set(r["types"]) != set(baseline["redis"]["types"]):
        raise SystemExit("Redis type schema changed after start")
    for prefix,baseline_count in baseline["redis"]["persistent_prefixes"].items():
        current_count=r["persistent_prefixes"].get(prefix,-1)
        if current_count < baseline_count: raise SystemExit("Redis persistent prefix count decreased")
    if c["user_version"]!=baseline["collector"]["user_version"] or c["schema_fingerprint"] != baseline["collector"]["schema_fingerprint"]:
        raise SystemExit("Collector schema or version changed after start")
    retention_deleted={}
    for table,baseline_count in baseline["collector"]["tables"].items():
        current_count=c["tables"].get(table,-1)
        if current_count < 0: raise SystemExit("Collector table aggregate is missing")
        retention_deleted[table]=max(0,baseline_count-current_count)
    c["retention_deleted"]=retention_deleted
evidence={"schema_version":1,"migration_id":sys.argv[8],"stage":stage,"postgres":pg,"redis":r,"collector":c}
encoded=(json.dumps(evidence,sort_keys=True,separators=(",", ":"))+"\n").encode("utf-8")
descriptor=os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    os.write(descriptor, encoded)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
  chmod 0600 -- "${evidence_partial}" || return $?
  mv -T "${evidence_partial}" "${evidence_final}" || return $?
}

verify_store_group() {
  collect_target_attestation "$1" "$2" || return $?
}

start_current_release() {
  "${compose[@]}" up -d --no-build --pull never
}

write_migration_state() {
  local state="$1" migration_id="$2" backup_stamp="$3" failed_step="${4:-}"
  local directory="${IMPORT_ROOT}/${migration_id}" partial run_id
  if compgen -G "${directory}/status.*.json.partial" >/dev/null; then
    return 1
  fi
  run_id="$(python3 -c 'import secrets; print(secrets.token_hex(12))')" || return $?
  partial="${directory}/status.${run_id}.json.partial"
  python3 - "${partial}" "${directory}/status.json" "${state}" "${migration_id}" \
    "${backup_stamp}" "${failed_step}" "${BACKUP_ROOT}/${backup_stamp}.backup-manifest.json" <<'PY' || return $?
import json, os, sys
from pathlib import Path
target=Path(sys.argv[2]); state=sys.argv[3]; migration_id=sys.argv[4]; stamp=sys.argv[5]
allowed=dict([
 (None,{"BACKED_UP"}), ("BACKED_UP",{"APPLIED","ROLLBACK_IN_PROGRESS","ROLLBACK_FAILED"}),
 ("APPLIED",{"ROLLBACK_IN_PROGRESS"}), ("ROLLBACK_IN_PROGRESS",{"ROLLED_BACK","ROLLBACK_FAILED"}),
 ("ROLLBACK_FAILED",{"ROLLBACK_IN_PROGRESS"}), ("ROLLED_BACK",set()),
])
current=None
if target.exists():
    if target.is_symlink() or target.stat().st_size>64*1024: raise SystemExit("unsafe migration state")
    current=json.loads(target.read_text(encoding="utf-8"))
    if current.get("migration_id")!=migration_id or current.get("backup_stamp")!=stamp: raise SystemExit("migration state identity mismatch")
    if current.get("status")==state=="ROLLED_BACK": raise SystemExit(0)
previous=current.get("status") if current else None
if state not in allowed.get(previous,set()): raise SystemExit("invalid migration state transition")
if current is None:
    backup=json.loads(Path(sys.argv[7]).read_text(encoding="utf-8"))
    current={"schema_version":1,"migration_id":migration_id,"backup_stamp":stamp,
             "backup_files":backup["files"],"failed_step":None,
             "stores":{name:{"started":False,"restored":False,"verified":False}
                       for name in ("postgres","redis","collector")}}
current["status"]=state
current["failed_step"]=sys.argv[6] or None
encoded=(json.dumps(current,sort_keys=True,separators=(",", ":"))+"\n").encode("utf-8")
descriptor=os.open(sys.argv[1],os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o600)
try:
    os.write(descriptor,encoded); os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
  chmod 0600 -- "${partial}" || return $?
  mv -T "${partial}" "${directory}/status.json" || return $?
}

record_store_progress() {
  local migration_id="$1" store="$2" field="$3" directory="${IMPORT_ROOT}/${1}"
  local partial run_id
  [[ "${store}" == "postgres" || "${store}" == "redis" || "${store}" == "collector" ]] || return 2
  [[ "${field}" == "started" || "${field}" == "restored" || "${field}" == "verified" ]] || return 2
  run_id="$(python3 -c 'import secrets; print(secrets.token_hex(12))')" || return $?
  partial="${directory}/status.${run_id}.json.partial"
  python3 - "${directory}/status.json" "${partial}" "${migration_id}" "${store}" "${field}" <<'PY' || return $?
import json,os,sys
from pathlib import Path
source=Path(sys.argv[1]); payload=json.loads(source.read_text(encoding="utf-8"))
if payload.get("migration_id")!=sys.argv[3] or payload.get("status") not in {"BACKED_UP","ROLLBACK_IN_PROGRESS"}: raise SystemExit("invalid progress state")
progress=payload["stores"][sys.argv[4]]; field=sys.argv[5]
if field=="restored" and not progress["started"]: raise SystemExit("store restore was not started")
if field=="verified" and not progress["restored"]: raise SystemExit("store restore was not completed")
progress[field]=True
encoded=(json.dumps(payload,sort_keys=True,separators=(",", ":"))+"\n").encode()
fd=os.open(sys.argv[2],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try: os.write(fd,encoded); os.fsync(fd)
finally: os.close(fd)
PY
  chmod 0600 -- "${partial}" || return $?
  mv -T "${partial}" "${directory}/status.json" || return $?
}

rollback_all() {
  local migration_id="$1" backup_stamp="$2"
  write_migration_state "ROLLBACK_IN_PROGRESS" "${migration_id}" "${backup_stamp}" || return 1
  stop_application_writers || { write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"; return 1; }
  record_store_progress "${migration_id}" postgres started || return 1
  restore_postgres_dump "${BACKUP_ROOT}/postgres/${backup_stamp}.dump" \
    || { write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"; return 1; }
  record_store_progress "${migration_id}" postgres restored || return 1
  record_store_progress "${migration_id}" redis started || return 1
  restore_redis_rdb "${BACKUP_ROOT}/redis/${backup_stamp}.rdb" "${migration_id}" "failed-import-redis-volume" \
    || { write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"; return 1; }
  record_store_progress "${migration_id}" redis restored || return 1
  record_store_progress "${migration_id}" collector started || return 1
  restore_collector_sql "${BACKUP_ROOT}/observability/${backup_stamp}.sql.gz" "${migration_id}" \
    || { write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"; return 1; }
  record_store_progress "${migration_id}" collector restored || return 1
  start_current_release || { write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"; return 1; }
  verify_current_release || { write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}"; return 1; }
  record_store_progress "${migration_id}" postgres verified || return 1
  record_store_progress "${migration_id}" redis verified || return 1
  record_store_progress "${migration_id}" collector verified || return 1
  write_migration_state "ROLLED_BACK" "${migration_id}" "${backup_stamp}" || return 1
}

fail_and_rollback() {
  local migration_id="$1" backup_stamp="$2" failed_step="$3"
  if rollback_all "${migration_id}" "${backup_stamp}"; then
    printf 'migration step %s failed and the store group was rolled back\n' "${failed_step}" >&2
    return 1
  fi
  write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}" "${failed_step}" || true
  printf 'migration step %s and automatic rollback failed\n' "${failed_step}" >&2
  return 1
}

run_recoverable_step() {
  set +e
  "$@"
  STEP_RC=$?
  set -e
  return 0
}

run_recoverable_step_capture() {
  set +e
  STEP_OUTPUT="$("$@")"
  STEP_RC=$?
  set -e
  return 0
}

apply_failure_trap() {
  local signal_rc="${1:-1}"
  trap - ERR INT TERM
  if [[ "${APPLY_FAILURE_ACTIVE}" -eq 1 ]]; then return "${signal_rc}"; fi
  APPLY_FAILURE_ACTIVE=1
  if [[ "${APPLY_REPLACEMENT_STARTED}" -eq 1 && -n "${APPLY_BACKUP_STAMP}" ]]; then
    set +e
    rollback_all "${APPLY_MIGRATION_ID}" "${APPLY_BACKUP_STAMP}"
    set -e
  else
    set +e
    start_current_release
    set -e
  fi
  return "${signal_rc}"
}

install_apply_failure_trap() {
  trap 'apply_failure_trap $?' ERR
  trap 'apply_failure_trap 130' INT
  trap 'apply_failure_trap 143' TERM
}

clear_apply_failure_trap() {
  trap - ERR INT TERM
  APPLY_FAILURE_ACTIVE=0
}

apply_migration() {
  local migration_id="$1" backup_stamp
  run_recoverable_step load_runtime
  if [[ "${STEP_RC}" -ne 0 ]]; then return "${STEP_RC}"; fi
  run_recoverable_step require_preapply_batch "${migration_id}"
  if [[ "${STEP_RC}" -ne 0 ]]; then return "${STEP_RC}"; fi
  run_recoverable_step refresh_preflight_before_stop "${migration_id}"
  if [[ "${STEP_RC}" -ne 0 ]]; then return "${STEP_RC}"; fi
  run_recoverable_step stop_application_writers
  if [[ "${STEP_RC}" -ne 0 ]]; then
    run_recoverable_step start_current_release
    return 1
  fi
  run_recoverable_step_capture run_required_backup
  if [[ "${STEP_RC}" -ne 0 ]]; then
    run_recoverable_step start_current_release
    return 1
  fi
  backup_stamp="${STEP_OUTPUT}"
  run_recoverable_step write_migration_state "BACKED_UP" "${migration_id}" "${backup_stamp}"
  if [[ "${STEP_RC}" -ne 0 ]]; then
    run_recoverable_step start_current_release
    return 1
  fi
  APPLY_MIGRATION_ID="${migration_id}"
  APPLY_BACKUP_STAMP="${backup_stamp}"
  APPLY_REPLACEMENT_STARTED=1
  install_apply_failure_trap
  run_recoverable_step record_store_progress "${migration_id}" postgres started
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "postgres-start-state"; return 1; fi
  run_recoverable_step restore_postgres_dump "${IMPORT_ROOT}/${migration_id}/postgres.dump"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "postgres-restore"; return 1; fi
  run_recoverable_step record_store_progress "${migration_id}" postgres restored
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "postgres-state"; return 1; fi
  run_recoverable_step record_store_progress "${migration_id}" redis started
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "redis-start-state"; return 1; fi
  run_recoverable_step restore_redis_rdb "${IMPORT_ROOT}/${migration_id}/redis.rdb" "${migration_id}"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "redis-restore"; return 1; fi
  run_recoverable_step record_store_progress "${migration_id}" redis restored
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "redis-state"; return 1; fi
  run_recoverable_step record_store_progress "${migration_id}" collector started
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "collector-start-state"; return 1; fi
  run_recoverable_step install_collector_db "${IMPORT_ROOT}/${migration_id}/collector.db" "${migration_id}"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "collector-restore"; return 1; fi
  run_recoverable_step record_store_progress "${migration_id}" collector restored
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "collector-state"; return 1; fi
  run_recoverable_step verify_store_group "${migration_id}" "pre-start"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "store-verification"; return 1; fi
  for store in postgres redis collector; do
    run_recoverable_step record_store_progress "${migration_id}" "${store}" verified
    if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "${store}-verify-state"; return 1; fi
  done
  run_recoverable_step start_current_release
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "start-release"; return 1; fi
  run_recoverable_step verify_current_release
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "release-verification"; return 1; fi
  run_recoverable_step verify_store_group "${migration_id}" "post-start"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "post-start-verification"; return 1; fi
  run_recoverable_step write_migration_state "APPLIED" "${migration_id}" "${backup_stamp}"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "state-write"; return 1; fi
  clear_apply_failure_trap
  printf '{"migration_id":"%s","status":"APPLIED"}\n' "${migration_id}"
}

verify_migration() {
  local migration_id="$1"
  load_runtime || return $?
  require_runtime_batch "${migration_id}" || return $?
  verify_store_group "${migration_id}" "post-start" || return $?
  verify_current_release || return $?
  printf '{"migration_id":"%s","status":"verified"}\n' "${migration_id}"
}

rollback_migration() {
  local migration_id="$1" backup_stamp status state_line
  load_runtime || return $?
  require_runtime_batch "${migration_id}" || return $?
  state_line="$(python3 - "${IMPORT_ROOT}/${migration_id}/status.json" "${migration_id}" <<'PY'
import json, re, sys
from pathlib import Path
p=Path(sys.argv[1])
if p.is_symlink() or p.stat().st_size>64*1024: raise SystemExit("invalid migration state")
value=json.loads(p.read_text(encoding="utf-8"))
if value.get("migration_id")!=sys.argv[2]: raise SystemExit("migration state identity mismatch")
stamp=value.get("backup_stamp",""); status=value.get("status","")
if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z",stamp) is None: raise SystemExit("invalid backup stamp")
if status not in {"APPLIED","ROLLED_BACK","ROLLBACK_FAILED"}: raise SystemExit("rollback is not allowed from current state")
print(status,stamp)
PY
)" || return $?
  read -r status backup_stamp <<<"${state_line}" || return $?
  if [[ "${status}" == "ROLLED_BACK" ]]; then
    printf '{"migration_id":"%s","status":"ROLLED_BACK"}\n' "${migration_id}"
    return 0
  fi
  if [[ "${status}" == "ROLLBACK_FAILED" ]]; then
    printf 'rollback continuation requires an audited operator recovery\n' >&2
    return 1
  fi
  rollback_all "${migration_id}" "${backup_stamp}" || return $?
  printf '{"migration_id":"%s","status":"ROLLED_BACK"}\n' "${migration_id}"
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
