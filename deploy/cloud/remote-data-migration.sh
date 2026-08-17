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
readonly POSTGRES_VOLUME="car-agent-postgres-data"
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
declare -A LOCKED_STORE_CID=()
declare -A LOCKED_STORE_IMAGE=()
declare -A LOCKED_STORE_VOLUME=()

die() {
  printf 'cloud-data-migration: %s\n' "$1" >&2
  exit "${2:-1}"
}

migration_fail() {
  printf 'cloud-data-migration: %s\n' "$1" >&2
  return "${2:-1}"
}

require_migration_id() {
  [[ "${1:-}" =~ ${MIGRATION_ID_PATTERN} ]] || { migration_fail "invalid migration id" 2; return 2; }
}

load_runtime() {
  local -a project_names
  [[ -n "${CURRENT_RELEASE:-}" ]] && return 0
  CURRENT_RELEASE="$(readlink -f "${CURRENT_LINK}")"
  [[ "${CURRENT_RELEASE}" =~ ^/opt/car-agent/releases/[0-9a-f]{7,40}$ ]] \
    || { migration_fail "current release is invalid"; return 1; }
  mapfile -t project_names <"${RUNTIME_PROJECT_NAME_FILE}"
  [[ "${#project_names[@]}" -eq 1 && "${project_names[0]}" =~ ^[a-z0-9][a-z0-9_-]*$ ]] \
    || { migration_fail "runtime project name is invalid"; return 1; }
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
    || { migration_fail "prepare-upload requires a valid sudo caller"; return 1; }
  caller_group="$(id -gn "${caller}")"
  target="${IMPORT_ROOT}/${migration_id}"
  install -d -m 0711 -o root -g root "${IMPORT_ROOT}"
  [[ ! -e "${target}" ]] || { migration_fail "migration upload directory already exists"; return 1; }
  install -d -m 0700 -o "${caller}" -g "${caller_group}" "${target}"
  printf '%s\n' "${target}"
}

seal_upload() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}" caller caller_uid caller_gid
  require_migration_id "${migration_id}"
  caller="${SUDO_USER:-}"
  [[ "${caller}" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] \
    || { migration_fail "seal-upload requires a valid sudo caller"; return 1; }
  caller_uid="$(id -u "${caller}")" || return $?
  caller_gid="$(id -g "${caller}")" || return $?
  python3 - "${directory}" "${caller_uid}" "${caller_gid}" <<'PY'
import os, secrets, stat, sys
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
        temporary = f".sealed.{secrets.token_hex(16)}"
        replacement = None
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SystemExit("migration upload entry is not a private regular file")
            if metadata.st_uid != int(sys.argv[2]) or metadata.st_gid != int(sys.argv[3]):
                raise SystemExit("migration upload file owner is invalid")
            replacement = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=root,
            )
            copied = 0
            while chunk := os.read(descriptor, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    written = os.write(replacement, view)
                    view = view[written:]
                    copied += written
            after = os.fstat(descriptor)
            if copied != metadata.st_size or (after.st_size, after.st_mtime_ns) != (metadata.st_size, metadata.st_mtime_ns):
                raise SystemExit("migration upload changed while sealing")
            os.fchown(replacement, 0, 0)
            os.fchmod(replacement, 0o600)
            os.fsync(replacement)
        finally:
            if replacement is not None:
                os.close(replacement)
            os.close(descriptor)
        os.replace(temporary, name, src_dir_fd=root, dst_dir_fd=root)
        os.fsync(root)
finally:
    os.close(root)
PY
  require_sealed_inbound_bundle "${migration_id}"
}

require_sealed_inbound_bundle() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}"
  require_migration_id "${migration_id}"
  python3 - "${directory}" <<'PY' || return $?
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
  python3 - "${directory}" "${migration_id}" <<'PY' || return $?
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
  validate_import_manifest "${migration_id}" || return $?
}

require_runtime_batch() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}"
  require_migration_id "${migration_id}"
  python3 - "${directory}" <<'PY' || return $?
import os, re, stat, sys
required = {"manifest.json", "postgres.dump", "redis.rdb", "collector.db"}
allowed_files = required | {"status.json", "journal.json", "preflight-current.json", "evidence-pre-start.json", "evidence-post-start.json"}
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
    transient = {name for name in entries if re.fullmatch(
        r"(?:[.]attestation[.][0-9a-f]{24}[.](?:pg|pg-identity|redis|collector)|[.]redis-verify[.][0-9a-f]{24})[.]json[.]partial", name
    )}
    if not required.issubset(entries) or not entries.issubset(allowed_files | allowed_directories | transient):
        raise SystemExit("unknown runtime batch entry")
    validate_regular_set(root, entries & allowed_files, allowed_files, required)
    for name in sorted(transient):
        os.close(opened(root, name))
    if "rollback-generated" in entries:
        generated = opened(root, "rollback-generated", directory=True)
        try:
            generated_entries=set(os.listdir(generated))
            if not generated_entries or not generated_entries.issubset({"collector.db","collector.db.partial","collector-restore.json",".collector-restore.json.partial"}):
                raise SystemExit("unknown runtime batch entry")
            for name in generated_entries:
                os.close(opened(generated,name))
            if "collector-restore.json" in generated_entries and generated_entries!={"collector.db","collector-restore.json"}:
                raise SystemExit("collector completion state is invalid")
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
                        marker_partials={name for name in bucket_entries if re.fullmatch(
                            r"[.]redis-aof-complete[.]json[.][0-9]+[.]partial",name
                        )}
                        backup_partials={name for name in bucket_entries if re.fullmatch(
                            r"[.]redis-backup-(?:dump[.]rdb|appendonlydir)[.]partial",name
                        )}
                        if not bucket_entries.issubset({"dump.rdb", "appendonlydir", "redis-replace.json", "redis-aof-complete.json"}|marker_partials|backup_partials):
                            raise SystemExit("unknown runtime batch entry")
                        for name in marker_partials:
                            os.close(opened(bucket_fd,name))
                        for name in backup_partials:
                            os.close(opened(bucket_fd,name,directory=name.endswith("appendonlydir.partial")))
                        if "dump.rdb" in bucket_entries:
                            os.close(opened(bucket_fd, "dump.rdb"))
                        if "redis-replace.json" in bucket_entries:
                            os.close(opened(bucket_fd, "redis-replace.json"))
                        if "redis-aof-complete.json" in bucket_entries:
                            os.close(opened(bucket_fd, "redis-aof-complete.json"))
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
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

directory = Path(sys.argv[1])
migration_id = sys.argv[2]
manifest_fd=os.open(directory / "manifest.json",os.O_RDONLY | os.O_NOFOLLOW)
try:
    if os.fstat(manifest_fd).st_size > 16*1024*1024: raise SystemExit("manifest exceeds byte limit")
    manifest_raw=os.read(manifest_fd,16*1024*1024+1)
finally:
    os.close(manifest_fd)
def unique(pairs):
    value={}
    for key,item in pairs:
        if key in value: raise ValueError("duplicate manifest key")
        value[key]=item
    return value
payload = json.loads(manifest_raw.decode("utf-8"),object_pairs_hook=unique)
keys = {"schema_version", "migration_id", "phase", "source_sha", "created_at",
        "files", "postgres", "redis", "collector", "identity_hmac_key"}
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
expected_id=f'{created.strftime("%Y%m%dT%H%M%SZ")}-{payload["source_sha"][:7]}-{payload["phase"]}'
if migration_id!=expected_id: raise SystemExit("migration id is not bound to source SHA and creation time")
names = {"postgres.dump", "redis.rdb", "collector.db"}
if not isinstance(payload.get("files"), dict) or set(payload["files"]) != names:
    raise SystemExit("manifest file set is invalid")
for name in names:
    record = payload["files"][name]
    if set(record) != {"size_bytes", "sha256"} or isinstance(record["size_bytes"], bool):
        raise SystemExit("manifest file record is invalid")
    path = directory / name
    with path.open("rb") as source:
        digest = hashlib.file_digest(source,"sha256").hexdigest()
    if record["size_bytes"] != path.stat().st_size or record["sha256"] != digest:
        raise SystemExit("migration file checksum mismatch")
pg = payload.get("postgres")
redis = payload.get("redis")
collector = payload.get("collector")
if not isinstance(pg, dict) or set(pg) != {"major", "vector_version", "tables", "states", "schema_fingerprint", "archive_fingerprint", "source_identity"}:
    raise SystemExit("PostgreSQL evidence keys are invalid")
pg_tables = {"memory_item", "memory_relation", "reminder_item", "task_ledger",
             "proactive_delivery", "scene_item", "voiceprint", "agents", "agent_capability_vec"}
pg_states = {"reminder_item.status", "task_ledger.status", "proactive_delivery.state", "scene_item.status"}
if set(pg.get("tables", {})) != pg_tables or set(pg.get("states", {})) != pg_states:
    raise SystemExit("PostgreSQL aggregate set is invalid")
if not re.fullmatch(r"[0-9]{1,3}", pg.get("major", "")) or not re.fullmatch(r"[0-9]+(?:[.][0-9]+){1,3}", pg.get("vector_version", "")):
    raise SystemExit("PostgreSQL version evidence is invalid")
if not isinstance(redis, dict) or set(redis) != {"version", "rdb_version", "key_count", "prefixes", "types", "persistent", "expiring", "min_ttl_ms", "max_ttl_ms", "rdb_sha256", "source_identity"}:
    raise SystemExit("Redis evidence keys are invalid")
if not isinstance(collector, dict) or set(collector) != {"user_version", "schema_fingerprint", "tables", "integrity_check", "source_identity"}:
    raise SystemExit("Collector evidence keys are invalid")
if set(collector.get("tables", {})) != {"turns", "spans", "llm_calls", "logs"} or collector.get("integrity_check") != "ok":
    raise SystemExit("Collector aggregate set is invalid")
def safe_counts(value):
    return isinstance(value, dict) and all(re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key) and type(count) is int and count >= 0 for key, count in value.items())
if not safe_counts(pg["tables"]) or not all(safe_counts(item) for item in pg["states"].values()):
    raise SystemExit("PostgreSQL aggregate counts are invalid")
if sum(pg["tables"][name] for name in pg_tables-{"agents","agent_capability_vec"})>20000:
    raise SystemExit("PostgreSQL identity count limit exceeded")
allowed_states={
 "reminder_item.status":{"pending","fired","done","cancelled"},
 "task_ledger.status":{"accepted","running","done","failed","cancelled","orphaned"},
 "proactive_delivery.state":{"pending","dispatched","presented","dropped","expired"},
 "scene_item.status":{"enabled","disabled"},
}
state_tables={"reminder_item.status":"reminder_item","task_ledger.status":"task_ledger",
              "proactive_delivery.state":"proactive_delivery","scene_item.status":"scene_item"}
for name,counts in pg["states"].items():
    if not set(counts).issubset(allowed_states[name]) or sum(counts.values())!=pg["tables"][state_tables[name]]:
        raise SystemExit("PostgreSQL state contract is invalid")
if not safe_counts(redis.get("prefixes")) or not safe_counts(redis.get("types")) or not safe_counts(collector.get("tables")):
    raise SystemExit("aggregate category counts are invalid")
if not re.fullmatch(r"[0-9]+(?:[.][0-9]+){1,3}", redis.get("version", "")):
    raise SystemExit("Redis version is invalid")
for key in ("rdb_version", "key_count", "persistent", "expiring", "min_ttl_ms", "max_ttl_ms"):
    if type(redis.get(key)) is not int or redis[key] < 0:
        raise SystemExit("Redis numeric evidence is invalid")
if redis["rdb_version"] < 1 or redis["persistent"] + redis["expiring"] != redis["key_count"]:
    raise SystemExit("Redis aggregate total is invalid")
if redis["key_count"]>20000: raise SystemExit("Redis identity count limit exceeded")
if sum(redis["prefixes"].values()) != redis["key_count"] or sum(redis["types"].values()) != redis["key_count"]:
    raise SystemExit("Redis category totals are invalid")
if type(collector.get("user_version")) is not int or collector["user_version"] < 0:
    raise SystemExit("Collector user_version is invalid")
if sum(collector["tables"].values())>50000:
    raise SystemExit("Collector identity count limit exceeded")
for value in (pg["schema_fingerprint"], pg["archive_fingerprint"], redis.get("rdb_sha256"), collector["schema_fingerprint"]):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SystemExit("manifest fingerprint is invalid")
if not isinstance(payload["identity_hmac_key"],str) or not re.fullmatch(r"[0-9a-f]{64}",payload["identity_hmac_key"]):
    raise SystemExit("manifest identity HMAC key is invalid")
hex64=lambda value:isinstance(value,str) and re.fullmatch(r"[0-9a-f]{64}",value) is not None
pg_identity=pg["source_identity"]
business=pg_tables-{"agents","agent_capability_vec"}
if (not isinstance(pg_identity,dict) or set(pg_identity)!={"identity_sets","logical_rows","state_by_identity"}
    or set(pg_identity.get("identity_sets",{}))!=business or set(pg_identity.get("logical_rows",{}))!=business
    or set(pg_identity.get("state_by_identity",{}))!=pg_states):
    raise SystemExit("PostgreSQL source identity schema is invalid")
for table in business:
    ids=pg_identity["identity_sets"][table]; rows=pg_identity["logical_rows"][table]
    if (not isinstance(ids,list) or len(ids)!=pg["tables"][table] or len(ids)!=len(set(ids))
        or not all(hex64(item) for item in ids) or not isinstance(rows,dict) or set(rows)!=set(ids)
        or not all(hex64(item) for item in rows.values())):
        raise SystemExit("PostgreSQL source identity is invalid")
for name,states in pg_identity["state_by_identity"].items():
    table=state_tables[name]
    if (not isinstance(states,dict) or set(states)!=set(pg_identity["identity_sets"][table])
        or not set(states.values()).issubset(allowed_states[name])):
        raise SystemExit("PostgreSQL source state identity is invalid")
redis_identity=redis["source_identity"]
if (not isinstance(redis_identity,dict) or set(redis_identity)!={"rows","checked_at_ms"}
    or type(redis_identity["checked_at_ms"]) is not int or redis_identity["checked_at_ms"]<0
    or not isinstance(redis_identity["rows"],dict) or len(redis_identity["rows"])!=redis["key_count"]):
    raise SystemExit("Redis source identity schema is invalid")
redis_persistent=redis_expiring=0
for digest,record in redis_identity["rows"].items():
    if (not hex64(digest) or not isinstance(record,dict) or set(record)!={"logical","deadline_ms"}
        or not hex64(record["logical"]) or type(record["deadline_ms"]) is not int or record["deadline_ms"] < -1):
        raise SystemExit("Redis source identity is invalid")
    if record["deadline_ms"] == -1: redis_persistent+=1
    else: redis_expiring+=1
if (redis_persistent,redis_expiring)!=(redis["persistent"],redis["expiring"]):
    raise SystemExit("Redis source identity TTL total is invalid")
collector_identity=collector["source_identity"]
if (not isinstance(collector_identity,dict) or set(collector_identity)!={"rows"}
    or not isinstance(collector_identity["rows"],dict) or set(collector_identity["rows"])!={"turns","spans","llm_calls","logs"}):
    raise SystemExit("Collector source identity schema is invalid")
for table,rows in collector_identity["rows"].items():
    if (not isinstance(rows,dict) or len(rows)!=collector["tables"][table]
        or not all(hex64(key) and hex64(value) for key,value in rows.items())):
        raise SystemExit("Collector source identity is invalid")
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
if path.is_symlink() or path.stat().st_size > 16*1024*1024: raise SystemExit("invalid backup manifest")
def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError("duplicate key")
        result[key]=value
    return result
payload=json.loads(path.read_text(encoding="utf-8"),object_pairs_hook=unique)
if set(payload)!={"schema_version","backup_stamp","files","redis_aggregate","redis_identity"} or payload["schema_version"]!=1 or payload["backup_stamp"]!=stamp:
    raise SystemExit("backup manifest contract mismatch")
aggregate=payload["redis_aggregate"]
if set(aggregate)!={"key_count","prefixes","types","persistent","expiring"} or aggregate["key_count"]<=0:
    raise SystemExit("backup Redis aggregate is invalid")
if sum(aggregate["prefixes"].values())!=aggregate["key_count"] or sum(aggregate["types"].values())!=aggregate["key_count"] or aggregate["persistent"]+aggregate["expiring"]!=aggregate["key_count"]:
    raise SystemExit("backup Redis aggregate totals mismatch")
identity=payload["redis_identity"]
hex64=lambda value:isinstance(value,str) and re.fullmatch(r"[0-9a-f]{64}",value) is not None
if (set(identity)!={"digest_key","persistent_digests","expiring_deadlines_ms","checked_at_ms"}
        or not hex64(identity["digest_key"])
        or not isinstance(identity["persistent_digests"],list)
        or len(identity["persistent_digests"])!=aggregate["persistent"]
        or len(identity["persistent_digests"])!=len(set(identity["persistent_digests"]))
        or not all(hex64(value) for value in identity["persistent_digests"])
        or not isinstance(identity["expiring_deadlines_ms"],dict)
        or len(identity["expiring_deadlines_ms"])!=aggregate["expiring"]
        or not all(hex64(key) and type(value) is int and value>=0 for key,value in identity["expiring_deadlines_ms"].items())
        or type(identity["checked_at_ms"]) is not int or identity["checked_at_ms"]<0):
    raise SystemExit("backup Redis identity evidence is invalid")
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
  [[ -n "$("${compose[@]}" ps -q --status running postgres)" ]] || { migration_fail "postgres is not running"; return 1; }
  [[ -n "$("${compose[@]}" ps -q --status running redis)" ]] || { migration_fail "redis is not running"; return 1; }
  [[ -n "$("${compose[@]}" ps -q --status running observability-collector)" ]] \
    || { migration_fail "collector is not running"; return 1; }
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
  [[ "${redis_version}" =~ ^[0-9]+([.][0-9]+){1,3}$ ]] || { migration_fail "redis_version is invalid"; return 1; }
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

assert_expected_cloud_topology() {
  local services_text service cid image volume serve_status serve_count state config_image timer_result
  local expected_service expected_image expected_rows inspection_text _status service_set expected_set
  local -a services inspection fixed_infra=(postgres redis nats http-proxy)
  local -A expected_images=()
  services_text="$("${compose[@]}" config --services)" || return $?
  mapfile -t services <<<"${services_text}" || return $?
  [[ "${#services[@]}" -eq 30 ]] || { migration_fail "cloud compose topology is incomplete"; return 1; }
  expected_rows="$(python3 - "${CURRENT_RELEASE}/deploy/cloud/release-services.json" <<'PY'
import json,re,sys
from pathlib import Path
payload=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if set(payload)!={"schema_version","services"} or payload["schema_version"]!=1 or len(payload["services"])!=26:
    raise SystemExit("release-services.json is invalid")
for item in payload["services"]:
    if set(item)!={"service","image"}: raise SystemExit("release image manifest entry is invalid")
    print(f'{item["service"]}\t{item["image"]}')
PY
)" || return $?
  while IFS=$'\t' read -r expected_service expected_image; do
    [[ "${expected_service}" =~ ^[a-z0-9-]+$ && "${expected_image}" =~ ^[A-Za-z0-9./_-]+$ ]] || return 1
    expected_images["${expected_service}"]="${expected_image}:${RELEASE_SHA}"
  done <<<"${expected_rows}"
  [[ "${#expected_images[@]}" -eq 26 ]] || return 1
  service_set="$(printf '%s\n' "${services[@]}" | sort)" || return $?
  expected_set="$(
    printf '%s\n' "${!expected_images[@]}"
    printf '%s\n' "${fixed_infra[@]}"
  )" || return $?
  expected_set="$(sort <<<"${expected_set}")" || return $?
  [[ "${service_set}" == "${expected_set}" ]] \
    || { migration_fail "cloud compose service set does not match release manifest"; return 1; }
  for service in "${services[@]}"; do
    [[ "${service}" =~ ^[a-z0-9-]+$ ]] || return 1
    cid="$("${compose[@]}" ps -a -q "${service}")" || return $?
    [[ "${cid}" =~ ^[0-9a-f]{12,64}$ ]] || return 1
    inspection_text="$(docker inspect --format '{{.State.Running}} {{.State.Status}} {{.Config.Image}} {{.Image}}' "${cid}")" || return $?
    mapfile -t inspection <<<"${inspection_text}" || return $?
    [[ "${#inspection[@]}" -eq 1 ]] || return 1
    read -r state _status config_image image <<<"${inspection[0]}" || return $?
    [[ "${state}" == "true" && "${_status}" == "running" ]] || return 1
    [[ "${image}" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
    if [[ -n "${expected_images[${service}]:-}" ]]; then
      [[ "${config_image}" == "${expected_images[${service}]}" ]] || return 1
    fi
  done
  for service in postgres redis observability-collector; do
    cid="$("${compose[@]}" ps -a -q "${service}")" || return $?
    image="$(docker inspect --format '{{.Image}}' "${cid}")" || return $?
    volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "${cid}")" || return $?
    if [[ "${service}" == "postgres" ]]; then
      volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "${cid}")" || return $?
      [[ "${volume}" == "${POSTGRES_VOLUME}" ]] || return 1
    elif [[ "${service}" == "redis" ]]; then
      [[ "${volume}" == "${REDIS_VOLUME}" ]] || return 1
    else
      [[ "${volume}" == "${COLLECTOR_VOLUME}" ]] || return 1
    fi
    LOCKED_STORE_CID["${service}"]="${cid}"
    LOCKED_STORE_IMAGE["${service}"]="${image}"
    LOCKED_STORE_VOLUME["${service}"]="${volume}"
  done
  systemctl is-active --quiet car-agent-backup.timer || return $?
  systemctl is-enabled --quiet car-agent-backup.timer || return $?
  timer_result="$(systemctl show car-agent-backup.service --property=Result --value)" || return $?
  [[ "${timer_result}" == "success" ]] || return 1
  serve_status="$(tailscale serve status)" || return $?
  serve_count="$(grep -Fic '(tailnet only)' <<<"${serve_status}")" || return $?
  [[ "${serve_count}" -eq 5 ]] || return 1
  if grep -Fqi funnel <<<"${serve_status}"; then return 1; fi
}

assert_locked_store_identity() {
  local service="$1" destination cid image volume
  [[ "${service}" == "postgres" || "${service}" == "redis" || "${service}" == "observability-collector" ]] || return 2
  cid="$("${compose[@]}" ps -a -q "${service}")" || return $?
  [[ "${cid}" == "${LOCKED_STORE_CID[${service}]:-}" ]] || return 1
  image="$(docker inspect --format '{{.Image}}' "${cid}")" || return $?
  [[ "${image}" == "${LOCKED_STORE_IMAGE[${service}]:-}" ]] || return 1
  if [[ "${service}" == "postgres" ]]; then destination="/var/lib/postgresql/data"; else destination="/data"; fi
  volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "'"${destination}"'"}}{{.Name}}{{end}}{{end}}' "${cid}")" || return $?
  [[ "${volume}" == "${LOCKED_STORE_VOLUME[${service}]:-}" ]] || return 1
}

lock_store_identities() {
  local service cid image volume destination expected
  for service in postgres redis observability-collector; do
    cid="$("${compose[@]}" ps -a -q "${service}")" || return $?
    [[ "${cid}" =~ ^[0-9a-f]{12,64}$ ]] || return 1
    image="$(docker inspect --format '{{.Image}}' "${cid}")" || return $?
    [[ "${image}" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
    if [[ "${service}" == "postgres" ]]; then
      destination="/var/lib/postgresql/data"; expected="${POSTGRES_VOLUME}"
    elif [[ "${service}" == "redis" ]]; then
      destination="/data"; expected="${REDIS_VOLUME}"
    else
      destination="/data"; expected="${COLLECTOR_VOLUME}"
    fi
    volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "'"${destination}"'"}}{{.Name}}{{end}}{{end}}' "${cid}")" || return $?
    [[ "${volume}" == "${expected}" ]] || return 1
    LOCKED_STORE_CID["${service}"]="${cid}"
    LOCKED_STORE_IMAGE["${service}"]="${image}"
    LOCKED_STORE_VOLUME["${service}"]="${volume}"
  done
}

write_preflight_current() {
  local migration_id="$1" directory="${IMPORT_ROOT}/${1}" current_file
  local postgres_id postgres_image redis_id redis_image archive_fingerprint archive_listing redis_check current_json
  local partial run_id
  current_file="${directory}/preflight-current.json"
  assert_expected_cloud_topology || return $?
  postgres_id="$("${compose[@]}" ps -a -q postgres)" || return $?
  redis_id="$("${compose[@]}" ps -a -q redis)" || return $?
  [[ "${postgres_id}" =~ ^[0-9a-f]{12,64}$ && "${redis_id}" =~ ^[0-9a-f]{12,64}$ ]] \
    || { migration_fail "store container identity is invalid"; return 1; }
  postgres_image="$(docker inspect --format '{{.Image}}' "${postgres_id}")"
  redis_image="$(docker inspect --format '{{.Image}}' "${redis_id}")"
  archive_listing="$(docker run --pull never --rm --mount "type=bind,source=${directory},target=/snapshot,readonly" \
    --entrypoint pg_restore "${postgres_image}" --list /snapshot/postgres.dump)" || return $?
  archive_fingerprint="$(printf '%s\n' "${archive_listing}" | sed -e '/^; Archive created at/d' -e 's/[[:space:]]*$//' | sha256sum | cut -d' ' -f1)"
  redis_check="$(docker run --pull never --rm --mount "type=bind,source=${directory},target=/snapshot,readonly" \
    --entrypoint redis-check-rdb "${redis_image}" /snapshot/redis.rdb)" || return $?
  [[ "${redis_check}" == *"CRC64 checksum is OK"* \
    || "${redis_check}" == *"Checksum OK"* ]] \
    || { migration_fail "Redis import format validation failed"; return 1; }
  current_json="$(inspect_current)" || return $?
  if compgen -G "${directory}/preflight.*.json.partial" >/dev/null; then return 1; fi
  run_id="$(python3 -c 'import secrets; print(secrets.token_hex(12))')" || return $?
  partial="${directory}/preflight.${run_id}.json.partial"
  python3 - "${directory}/manifest.json" "${partial}" "${archive_fingerprint}" \
    "${current_json}" "${migration_id}" <<'PY' || return $?
import hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
manifest_path=Path(sys.argv[1]); descriptor=os.open(manifest_path,os.O_RDONLY|os.O_NOFOLLOW)
try:
    if os.fstat(descriptor).st_size>16*1024*1024: raise SystemExit("manifest exceeds byte limit")
    manifest_bytes=os.read(descriptor,16*1024*1024+1)
finally: os.close(descriptor)
manifest=json.loads(manifest_bytes); current=json.loads(sys.argv[4])
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
  [[ "${#services[@]}" -gt 2 ]] || { migration_fail "compose service list is incomplete"; return 1; }
  for service in "${services[@]}"; do
    [[ "${service}" =~ ^[a-z0-9-]+$ ]] || { migration_fail "compose service name is invalid"; return 1; }
    [[ "${service}" == "postgres" || "${service}" == "redis" ]] || writers+=("${service}")
  done
  "${compose[@]}" stop "${writers[@]}" || return $?
  for service in "${writers[@]}"; do
    running_id="$("${compose[@]}" ps -q --status running "${service}")" || return $?
    [[ -z "${running_id}" ]] || { migration_fail "application writer did not stop: ${service}"; return 1; }
  done
}

assert_named_volume() {
  local actual="$1" expected="$2"
  [[ "${expected}" == "car-agent-redis-data" || "${expected}" == "car-agent-obs-data" ]] \
    || { migration_fail "unapproved migration volume"; return 1; }
  [[ "${actual}" == "${expected}" ]] || { migration_fail "runtime named volume mismatch"; return 1; }
}

restore_postgres_dump() {
  local dump="$1"
  [[ -s "${dump}" && ! -L "${dump}" ]] || { migration_fail "PostgreSQL restore source is invalid"; return 1; }
  assert_locked_store_identity postgres || return $?
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
    || { migration_fail "redis container identity is not unique"; return 1; }
  RESOLVED_REDIS_CONTAINER="${ids[0]}"
  RESOLVED_REDIS_VOLUME="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "${RESOLVED_REDIS_CONTAINER}")"
  RESOLVED_REDIS_IMAGE="$(docker inspect --format '{{.Image}}' "${RESOLVED_REDIS_CONTAINER}")"
  assert_named_volume "${RESOLVED_REDIS_VOLUME}" "${REDIS_VOLUME}"
  [[ "${RESOLVED_REDIS_IMAGE}" =~ ^sha256:[0-9a-f]{64}$ ]] || { migration_fail "redis image identity is invalid"; return 1; }
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
  local container_id="$1" manifest="$2" run_id evidence
  run_id="$(python3 -c 'import secrets; print(secrets.token_hex(12))')" || return $?
  evidence="$(dirname "${manifest}")/.redis-verify.${run_id}.json.partial"
  python3 "${CURRENT_RELEASE}/deploy/cloud/store_identity_evidence.py" redis \
    --container "${container_id}" --key-control "${manifest}" --output "${evidence}" || return $?
  python3 - "${manifest}" "${evidence}" <<'PY' || return $?
import json, os, sys
from pathlib import Path
m=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
a=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")); rows=a["rows"]
if not rows:
    raise SystemExit("empty Redis import")
if "redis_identity" in m:
    source=m["redis_identity"]; checked_at_ms=a["checked_at_ms"]
    persistent={key for key,value in rows.items() if value["deadline_ms"]==-1}
    if persistent != set(source["persistent_digests"]):
        raise SystemExit("Redis persistent identity mismatch")
    current=set(rows)
    for digest,source_deadline in source["expiring_deadlines_ms"].items():
        if source_deadline > checked_at_ms and digest not in current:
            raise SystemExit("unexpired Redis identity mismatch")
else:
    source=m["redis"]["source_identity"]["rows"]
    if not set(rows).issubset(source): raise SystemExit("Redis import contains unknown identity")
    for digest,record in source.items():
        if record["deadline_ms"]==-1 or record["deadline_ms"]>a["checked_at_ms"]:
            if rows.get(digest)!=record: raise SystemExit("Redis import logical identity mismatch")
os.unlink(sys.argv[2])
if hasattr(os,"O_DIRECTORY"):
    fd=os.open(str(Path(sys.argv[2]).parent),os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
PY
}

validate_redis_aof_volume() {
  local image_id="$1" append_directory="${2:-appendonlydir}"
  docker run --rm --pull never --mount "type=volume,source=${REDIS_VOLUME},target=/data" \
    --entrypoint sh "${image_id}" -ceu '
      manifest="/data/${1}/appendonly.aof.manifest"
      test -s "${manifest}"
      grep -Eq "^file appendonly[.]aof[.][0-9]+[.]base[.](rdb|aof) seq [0-9]+ type b$" "${manifest}"
      grep -Eq "^file appendonly[.]aof[.][0-9]+[.]incr[.]aof seq [0-9]+ type i$" "${manifest}"
      test -z "$(find "/data/${1}" -maxdepth 1 -type l -print -quit)"
      while read -r marker filename rest; do
        test "${marker}" = file
        candidate="/data/${1}/${filename}"
        test -f "${candidate}"
        test ! -L "${candidate}"
        test "$(stat -c %u:%g "${candidate}")" = 0:0
        test "$(stat -c %a "${candidate}")" = 600
        case "${filename}" in
          *.base.rdb|*.base.aof)
            test -s "${candidate}" || { echo "base file is empty" >&2; false; }
            ;;
          *.incr.aof)
            test -f "${candidate}" || { echo "incr file is not regular private" >&2; false; }
            ;;
          *) false ;;
        esac
      done <"${manifest}"
      digest_tree() {
        {
          sha256sum "${manifest}"
          while read -r marker filename rest; do
            sha256sum "/data/${1}/${filename}"
          done <"${manifest}"
        } | sha256sum
      }
      before="$(digest_tree)"
      redis-check-aof "${manifest}" >/dev/null
      after="$(digest_tree)"
      test "${before}" = "${after}"
    ' sh "${append_directory}" || return $?
}

restore_redis_rdb() {
  local rdb="$1" migration_id="$2" bucket="${3:-redis-volume}"
  local expected_manifest="${4:-${IMPORT_ROOT}/${migration_id}/manifest.json}"
  local rollback_dir="${IMPORT_ROOT}/${migration_id}/rollback/${bucket}"
  local redis_container actual_volume image_id helper_image prepare_state loader info dbsize rc attempt aof_ready=0
  local manifest_sha existing_loader loader_identity
  [[ -s "${rdb}" && ! -L "${rdb}" ]] || { printf 'Redis restore source is invalid\n' >&2; return 1; }
  assert_locked_store_identity redis || return $?
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
  install -d -m 0700 -o root -g root \
    "${IMPORT_ROOT}/${migration_id}/rollback" "${rollback_dir}" || return $?
  helper_image="${LOCKED_STORE_IMAGE[observability-collector]:-}"
  [[ "${helper_image}" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
  manifest_sha="$(python3 - "${expected_manifest}" <<'PY'
import hashlib,sys
value=hashlib.sha256()
with open(sys.argv[1],"rb") as source:
    for chunk in iter(lambda:source.read(1024*1024),b""): value.update(chunk)
print(value.hexdigest())
PY
)" || return $?
  [[ "${manifest_sha}" =~ ^[0-9a-f]{64}$ ]] || return 1
  loader="car-agent-migration-${migration_id//-/}-redis-loader"
  existing_loader="$(docker ps -a -q --filter "name=^/${loader}$")" || return $?
  if [[ -n "${existing_loader}" ]]; then
    [[ "${existing_loader}" =~ ^[0-9a-f]{12,64}$ ]] || return 1
    loader_identity="$(docker inspect --format '{{.Image}} {{index .Config.Labels "com.car-agent.migration-id"}} {{index .Config.Labels "com.car-agent.role"}}' "${existing_loader}")" || return $?
    [[ "${loader_identity}" == "${image_id} ${migration_id} redis-loader" ]] || return 1
    docker rm -f "${existing_loader}" >/dev/null || return $?
  fi
  prepare_state="$(docker run --rm --pull never --mount "type=volume,source=${REDIS_VOLUME},target=/data" \
    --mount "type=bind,source=${rollback_dir},target=/rollback" \
    --mount "type=bind,source=$(dirname "${rdb}"),target=/incoming,readonly" \
    --mount "type=bind,source=${SCRIPT_ROOT}/redis_volume_prepare.py,target=/tool.py,readonly" \
    -e "MIGRATION_KILL_POINT=${MIGRATION_KILL_POINT:-}" \
    --entrypoint python "${helper_image}" /tool.py \
    "/incoming/$(basename "${rdb}")" /data /rollback "${manifest_sha}")" || return $?
  if [[ "${prepare_state}" == "resume-aof" ]]; then
    validate_redis_aof_volume "${image_id}" || return $?
    aof_ready=1
  fi
  if [[ "${aof_ready}" -eq 0 ]]; then
    [[ "${prepare_state}" == "prepared" || "${prepare_state}" == "resume-rdb" ]] || return 1
    docker run -d --pull never --name "${loader}" \
      --label "com.car-agent.migration-id=${migration_id}" --label "com.car-agent.role=redis-loader" \
      --mount "type=volume,source=${REDIS_VOLUME},target=/data" \
      --entrypoint sh "${image_id}" -c 'umask 077; exec redis-server "$@"' sh \
      "--dir" "/data" "--dbfilename" "dump.rdb" "--appendonly" "no" \
      "--appenddirname" "appendonlydir.migration.partial" \
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
    assert_redis_container_matches_manifest "${loader}" "${expected_manifest}" \
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
    validate_redis_aof_volume "${image_id}" "appendonlydir.migration.partial" || { docker rm -f "${loader}" >/dev/null 2>&1; return 1; }
    docker rm "${loader}" >/dev/null || return $?
    docker run --rm --pull never --mount "type=volume,source=${REDIS_VOLUME},target=/data" \
      --mount "type=bind,source=${rollback_dir},target=/rollback" \
      --mount "type=bind,source=$(dirname "${rdb}"),target=/incoming,readonly" \
      --mount "type=bind,source=${SCRIPT_ROOT}/redis_volume_prepare.py,target=/tool.py,readonly" \
      --entrypoint python "${helper_image}" /tool.py --complete \
      "/incoming/$(basename "${rdb}")" /data /rollback "${manifest_sha}" || return $?
    validate_redis_aof_volume "${image_id}" || return $?
  fi
  # Compose cold-start from the persisted multipart AOF is authoritative; only
  # its post-start aggregate may satisfy apply or rollback verification.
  "${compose[@]}" up -d --no-build --pull never redis || return $?
  wait_for_compose_redis || return $?
  resolve_redis_identity || return $?
  [[ "${RESOLVED_REDIS_VOLUME}" == "${actual_volume}" && "${RESOLVED_REDIS_IMAGE}" == "${image_id}" ]] \
    || return 1
  assert_redis_container_matches_manifest "${RESOLVED_REDIS_CONTAINER}" \
    "${expected_manifest}" || return $?
}

install_collector_db() {
  local database="$1" migration_id="$2" bucket="${3:-collector-volume}"
  local rollback_dir="${IMPORT_ROOT}/${migration_id}/rollback/${bucket}"
  local collector_container actual_volume image_id
  assert_locked_store_identity observability-collector || return $?
  collector_container="$("${compose[@]}" ps -a -q observability-collector)"
  actual_volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "${collector_container}")"
  assert_named_volume "${actual_volume}" "${COLLECTOR_VOLUME}"
  image_id="$(docker inspect --format '{{.Image}}' "${collector_container}")"
  install -d -m 0700 -o root -g root \
    "${IMPORT_ROOT}/${migration_id}/rollback" "${rollback_dir}"
  docker run --rm --mount "type=volume,source=${COLLECTOR_VOLUME},target=/data" \
    --mount "type=bind,source=${rollback_dir},target=/rollback" \
    --mount "type=bind,source=$(dirname "${database}"),target=/incoming,readonly" \
    --mount "type=bind,source=${CURRENT_RELEASE}/deploy/cloud/collector_volume_replace.py,target=/tool.py,readonly" \
    -e "MIGRATION_KILL_POINT=${MIGRATION_KILL_POINT:-}" \
    --entrypoint python "${image_id}" /tool.py \
    "/incoming/$(basename "${database}")" /data /rollback
  [[ "$?" -eq 0 ]] || return $?
}

restore_collector_sql() {
  local sql_gz="$1" migration_id="$2" directory="${IMPORT_ROOT}/${migration_id}/rollback-generated"
  local collector_container image_id prepare_state
  collector_container="$("${compose[@]}" ps -a -q observability-collector)"
  image_id="$(docker inspect --format '{{.Image}}' "${collector_container}")"
  install -d -m 0700 -o root -g root "${directory}"
  prepare_state="$(python3 - "${sql_gz}" "${directory}" <<'PY'
import hashlib,json,os,sqlite3,stat,sys
from pathlib import Path
source=Path(sys.argv[1]); root=Path(sys.argv[2]); target=root/"collector.db"
partial=root/"collector.db.partial"; marker=root/"collector-restore.json"; marker_partial=root/".collector-restore.json.partial"
def digest(path):
    value=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""): value.update(chunk)
    return value.hexdigest()
source_sha=digest(source)
if marker.exists():
    payload=json.loads(marker.read_text(encoding="utf-8"))
    if set(payload)!={"schema_version","source_sha256","database_sha256","phase"} or payload!={"schema_version":1,"source_sha256":source_sha,"database_sha256":payload["database_sha256"],"phase":"complete"}:
        raise SystemExit("collector completion marker identity mismatch")
    if not target.is_file() or target.is_symlink() or digest(target)!=payload["database_sha256"]:
        raise SystemExit("collector completed database identity mismatch")
    db=sqlite3.connect(f"file:{target.as_posix()}?mode=ro",uri=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchall()!=[("ok",)]: raise SystemExit("collector completed database corrupt")
    finally: db.close()
    print("reuse")
else:
    for path in (partial,target,marker_partial):
        if path.exists():
            meta=path.lstat()
            if not stat.S_ISREG(meta.st_mode) or meta.st_nlink!=1: raise SystemExit("unsafe collector partial")
            path.unlink()
    print("rebuild")
PY
)" || return $?
  if [[ "${prepare_state}" == "rebuild" ]]; then
    docker run --rm --mount "type=bind,source=$(dirname "${sql_gz}"),target=/backup,readonly" \
      --mount "type=bind,source=${CURRENT_RELEASE}/deploy/cloud/sqlite_stream_restore.py,target=/tool.py,readonly" \
      --mount "type=bind,source=${directory},target=/restore" --entrypoint python "${image_id}" \
      /tool.py "/backup/$(basename "${sql_gz}")" /restore/collector.db.partial || return $?
    python3 - "${sql_gz}" "${directory}" <<'PY' || return $?
import hashlib,json,os,sqlite3,sys
from pathlib import Path
source=Path(sys.argv[1]); root=Path(sys.argv[2]); partial=root/"collector.db.partial"; target=root/"collector.db"
def digest(path):
    value=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""): value.update(chunk)
    return value.hexdigest()
db=sqlite3.connect(f"file:{partial.as_posix()}?mode=ro",uri=True)
try:
    if db.execute("PRAGMA integrity_check").fetchall()!=[("ok",)]: raise SystemExit("collector restore corrupt")
finally: db.close()
os.chmod(partial,0o600); database_sha=digest(partial); os.replace(partial,target)
payload={"schema_version":1,"source_sha256":digest(source),"database_sha256":database_sha,"phase":"complete"}
marker=root/"collector-restore.json"; temporary=root/".collector-restore.json.partial"
fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try:
    os.write(fd,(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n").encode()); os.fsync(fd)
finally: os.close(fd)
os.replace(temporary,marker)
if hasattr(os,"O_DIRECTORY"):
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
PY
  elif [[ "${prepare_state}" != "reuse" ]]; then
    return 1
  fi
  install_collector_db "${directory}/collector.db" "${migration_id}" "failed-import-collector-volume" || return $?
}

collect_target_attestation() {
  local migration_id="$1" stage="$2" directory="${IMPORT_ROOT}/${1}"
  local collector_ids_text collector_container collector_image postgres_id redis_id
  local evidence_partial evidence_final baseline_file run_id retention_days
  local pg_file pg_identity_file redis_file collector_file
  [[ "${stage}" == "pre-start" || "${stage}" == "post-start" ]] || return 2
  if compgen -G "${directory}/.attestation.*.json.partial" >/dev/null; then return 1; fi
  run_id="$(python3 -c 'import secrets; print(secrets.token_hex(12))')" || return $?
  pg_file="${directory}/.attestation.${run_id}.pg.json.partial"
  pg_identity_file="${directory}/.attestation.${run_id}.pg-identity.json.partial"
  redis_file="${directory}/.attestation.${run_id}.redis.json.partial"
  collector_file="${directory}/.attestation.${run_id}.collector.json.partial"
  postgres_id="$("${compose[@]}" ps -a -q postgres)" || return $?
  redis_id="$("${compose[@]}" ps -a -q redis)" || return $?
  [[ "${postgres_id}" =~ ^[0-9a-f]{12,64}$ && "${redis_id}" =~ ^[0-9a-f]{12,64}$ ]] || return 1
  "${compose[@]}" exec -T postgres psql -U cockpit -d cockpit -At >"${pg_file}" <<'SQL'
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
  [[ "$?" -eq 0 ]] || return $?
  chmod 0600 -- "${pg_file}" || return $?
  python3 "${CURRENT_RELEASE}/deploy/cloud/store_identity_evidence.py" postgres \
    --container "${postgres_id}" --db-user cockpit --database-name cockpit \
    --key-control "${directory}/manifest.json" --output "${pg_identity_file}" || return $?
  python3 "${CURRENT_RELEASE}/deploy/cloud/store_identity_evidence.py" redis \
    --container "${redis_id}" --key-control "${directory}/manifest.json" \
    --output "${redis_file}" || return $?
  collector_ids_text="$("${compose[@]}" ps -a -q observability-collector)" || return $?
  mapfile -t collector_ids <<<"${collector_ids_text}" || return $?
  [[ "${#collector_ids[@]}" -eq 1 ]] || return 1
  collector_container="${collector_ids[0]}"
  collector_image="$(docker inspect --format '{{.Image}}' "${collector_container}")" || return $?
  retention_days="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${collector_container}" \
    | sed -n 's/^OBS_RETENTION_DAYS=//p')" || return $?
  retention_days="${retention_days:-7}"
  [[ "${retention_days}" =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1
  docker run --rm --pull never \
    --mount "type=volume,source=${COLLECTOR_VOLUME},target=/data,readonly" \
    --mount "type=bind,source=${directory},target=/evidence" \
    --mount "type=bind,source=${CURRENT_RELEASE}/deploy/cloud/store_identity_evidence.py,target=/tool.py,readonly" \
    --entrypoint python "${collector_image}" /tool.py collector --database /data/obs.db \
    --key-control /evidence/manifest.json --retention-days "${retention_days}" \
    --output "/evidence/$(basename "${collector_file}")" || return $?
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
  if [[ "${stage}" == "pre-start" ]]; then
    python3 "${CURRENT_RELEASE}/deploy/cloud/assemble_store_attestation.py" \
      --manifest "${directory}/manifest.json" --pg-aggregate "${pg_file}" \
      --pg-identity "${pg_identity_file}" --redis "${redis_file}" \
      --collector "${collector_file}" --stage "${stage}" \
      --migration-id "${migration_id}" --output "${evidence_partial}" || return $?
  else
    python3 "${CURRENT_RELEASE}/deploy/cloud/assemble_store_attestation.py" \
      --manifest "${directory}/manifest.json" --pg-aggregate "${pg_file}" \
      --pg-identity "${pg_identity_file}" --redis "${redis_file}" \
      --collector "${collector_file}" --stage "${stage}" --baseline "${baseline_file}" \
      --migration-id "${migration_id}" --output "${evidence_partial}" || return $?
  fi
  chmod 0600 -- "${evidence_partial}" || return $?
  mv -T "${evidence_partial}" "${evidence_final}" || return $?
  python3 - "${pg_file}" "${pg_identity_file}" "${redis_file}" "${collector_file}" <<'PY' || return $?
import os,stat,sys
parents=set()
for raw in sys.argv[1:]:
    path=os.path.abspath(raw); meta=os.lstat(path)
    if not stat.S_ISREG(meta.st_mode) or meta.st_nlink!=1 or stat.S_IMODE(meta.st_mode)!=0o600:
        raise SystemExit("unsafe attestation temporary file")
    os.unlink(path); parents.add(os.path.dirname(path))
for parent in parents:
    if hasattr(os,"O_DIRECTORY"):
        fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
PY
}

verify_store_group() {
  collect_target_attestation "$1" "$2" || return $?
}

start_current_release() {
  "${compose[@]}" up -d --no-build --pull never
}

start_verification_services() {
  [[ "${#compose[@]}" -gt 0 ]] || return 0
  "${compose[@]}" up -d --no-build --pull never postgres redis observability-collector || return $?
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
    "${backup_stamp}" "${failed_step}" "${BACKUP_ROOT}/${backup_stamp}.backup-manifest.json" \
    "${CURRENT_RELEASE}/deploy/cloud/migration-state-machine.json" <<'PY' || return $?
import json, os, sys
from pathlib import Path
target=Path(sys.argv[2]); state=sys.argv[3]; migration_id=sys.argv[4]; stamp=sys.argv[5]
machine=json.loads(Path(sys.argv[8]).read_text(encoding="utf-8"))
if machine.get("schema_version")!=1 or not isinstance(machine.get("states"),dict): raise SystemExit("invalid migration state table")
states=machine["states"]
current=None
if target.exists():
    if target.is_symlink() or target.stat().st_size>64*1024: raise SystemExit("unsafe migration state")
    current=json.loads(target.read_text(encoding="utf-8"))
    if current.get("migration_id")!=migration_id or current.get("backup_stamp")!=stamp: raise SystemExit("migration state identity mismatch")
    if current.get("status")==state: raise SystemExit(0)
previous=current.get("status") if current else None
if state not in states or (previous is None and state!="BACKED_UP"): raise SystemExit("invalid migration state transition")
if previous is not None and state!=previous and state not in states[previous]["next"]: raise SystemExit("invalid migration state transition")
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

update_crash_journal() {
  local migration_id="$1" direction="$2" state="$3" backup_stamp="${4:-}"
  local store="${5:-}" phase="${6:-}" failed_step="${7:-}" failed_rc="${8:-0}"
  local directory="${IMPORT_ROOT}/${migration_id}" partial run_id
  run_id="$(python3 -c 'import secrets; print(secrets.token_hex(12))')" || return $?
  partial="${directory}/journal.${run_id}.json.partial"
  python3 - "${directory}/journal.json" "${partial}" "${migration_id}" "${direction}" \
    "${state}" "${backup_stamp}" "${store}" "${phase}" "${failed_step}" "${failed_rc}" \
    "${BACKUP_ROOT}" <<'PY' || return $?
import json,os,secrets,sys
from datetime import datetime,timezone
from pathlib import Path
target=Path(sys.argv[1]); partial=Path(sys.argv[2]); migration_id=sys.argv[3]
direction=sys.argv[4]; state=sys.argv[5]; stamp=sys.argv[6]; store=sys.argv[7]; phase=sys.argv[8]
failed_step=sys.argv[9] or None; failed_rc=int(sys.argv[10]); now=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")
if direction not in {"apply","rollback","recover"}: raise SystemExit("invalid journal direction")
if store and store not in {"postgres","redis","collector"}: raise SystemExit("invalid journal store")
if phase and phase not in {"pending","started","restored","verified"}: raise SystemExit("invalid journal phase")
payload=None
if target.exists():
    if target.is_symlink() or target.stat().st_size>128*1024: raise SystemExit("unsafe crash journal")
    payload=json.loads(target.read_text(encoding="utf-8"))
    if payload.get("migration_id")!=migration_id: raise SystemExit("journal identity mismatch")
if payload is None:
    payload={"schema_version":1,"migration_id":migration_id,"operation_id":secrets.token_hex(16),
             "direction":direction,"state":state,"backup_stamp":stamp or None,
             "backup_files":None,
             "stores":{name:{"phase":"pending"} for name in ("postgres","redis","collector")},
              "failed_step":None,"failed_rc":None,"origin_failure":None,"attempts":[]}
if payload["direction"]!=direction:
    payload["operation_id"]=secrets.token_hex(16)
    payload["direction"]=direction
    payload["stores"]={name:{"phase":"pending"} for name in ("postgres","redis","collector")}
payload["attempts"].append({"operation_id":payload["operation_id"],"direction":direction,
                            "state":state,"recorded_at":now,"store":store or None,
                            "phase":phase or None,"failed_step":failed_step,
                            "failed_rc":failed_rc if failed_step else None})
payload["state"]=state
if direction=="apply" and failed_step and payload.get("origin_failure") is None:
    payload["origin_failure"]={"operation_id":payload["operation_id"],"step":failed_step,
                               "rc":failed_rc,"recorded_at":now}
if stamp:
    if payload.get("backup_stamp")!=stamp or payload.get("backup_files") is None:
        manifest=Path(sys.argv[11])/f"{stamp}.backup-manifest.json"
        if manifest.is_symlink() or manifest.stat().st_size>16*1024*1024: raise SystemExit("unsafe journal backup manifest")
        backup=json.loads(manifest.read_text(encoding="utf-8"))
        if backup.get("backup_stamp")!=stamp or set(backup.get("files",{}))!={"postgres.dump","redis.rdb","collector.sql.gz"}:
            raise SystemExit("journal backup manifest is invalid")
        payload["backup_stamp"]=stamp
        payload["backup_files"]=backup["files"]
if store and phase: payload["stores"][store]["phase"]=phase
payload["failed_step"]=failed_step
payload["failed_rc"]=failed_rc if failed_step else None
encoded=(json.dumps(payload,sort_keys=True,separators=(",", ":"))+"\n").encode()
fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try: os.write(fd,encoded); os.fsync(fd)
finally: os.close(fd)
os.replace(partial,target)
directory_fd=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
try: os.fsync(directory_fd)
finally: os.close(directory_fd)
PY
  chmod 0600 -- "${directory}/journal.json" || return $?
}

begin_crash_journal() {
  update_crash_journal "$1" apply STOPPING_WRITERS || return $?
}

read_recovery_journal() {
  local migration_id="$1"
  python3 - "${IMPORT_ROOT}/${migration_id}/journal.json" "${migration_id}" \
    "${CURRENT_RELEASE}/deploy/cloud/migration-state-machine.json" <<'PY' || return $?
import json,re,sys
from pathlib import Path
path=Path(sys.argv[1])
if path.is_symlink() or path.stat().st_size>128*1024: raise SystemExit("invalid recovery journal")
payload=json.loads(path.read_text(encoding="utf-8"))
if payload.get("schema_version")!=1 or payload.get("migration_id")!=sys.argv[2]: raise SystemExit("recovery journal identity mismatch")
state=payload.get("state"); stamp=payload.get("backup_stamp") or ""
machine=json.loads(Path(sys.argv[3]).read_text(encoding="utf-8")); states=machine.get("states",{})
if machine.get("schema_version")!=1 or state not in states: raise SystemExit("journal is not recoverable")
if states[state]["backup_required"] and re.fullmatch(r"[0-9]{8}T[0-9]{6}Z",stamp) is None: raise SystemExit("journal backup stamp is invalid")
print(state,stamp)
PY
}

read_store_phase() {
  local migration_id="$1" direction="$2" store="$3"
  [[ "${direction}" == "rollback" || "${direction}" == "recover" ]] || return 2
  [[ "${store}" == "postgres" || "${store}" == "redis" || "${store}" == "collector" ]] || return 2
  python3 - "${IMPORT_ROOT}/${migration_id}/journal.json" "${migration_id}" \
    "${direction}" "${store}" <<'PY' || return $?
import json,sys
from pathlib import Path
path=Path(sys.argv[1])
if path.is_symlink() or path.stat().st_size>128*1024: raise SystemExit("invalid recovery journal")
payload=json.loads(path.read_text(encoding="utf-8"))
if payload.get("migration_id")!=sys.argv[2] or payload.get("direction")!=sys.argv[3]:
    raise SystemExit("journal operation identity mismatch")
stores=payload.get("stores")
if not isinstance(stores,dict) or set(stores)!={"postgres","redis","collector"}:
    raise SystemExit("journal store set is invalid")
record=stores.get(sys.argv[4])
if not isinstance(record,dict) or set(record)!={"phase"} or record["phase"] not in {"pending","started","restored","verified"}:
    raise SystemExit("journal store phase is invalid")
print(record["phase"])
PY
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

rollback_run_step() {
  local migration_id="$1" backup_stamp="$2" direction="$3" store="$4" step="$5" rc journal_rc state_rc
  shift 5
  run_recoverable_step "$@"
  rc="${STEP_RC}"
  if [[ "${rc}" -ne 0 ]]; then
    if update_crash_journal "${migration_id}" "${direction}" ROLLBACK_FAILED "${backup_stamp}" \
      "${store}" "" "${step}" "${rc}"; then journal_rc=0; else journal_rc=$?; fi
    if write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}" "${step}"; then
      state_rc=0
    else
      state_rc=$?
    fi
    if [[ "${journal_rc}" -ne 0 ]]; then return "${journal_rc}"; fi
    if [[ "${state_rc}" -ne 0 ]]; then return "${state_rc}"; fi
    return "${rc}"
  fi
  return 0
}

rollback_all() {
  local migration_id="$1" backup_stamp="$2" direction="${3:-rollback}" store phase
  rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "" backup-manifest \
    validate_backup_manifest "${backup_stamp}" || return $?
  rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "" store-identities \
    lock_store_identities || return $?
  rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "" journal-start \
    update_crash_journal "${migration_id}" "${direction}" ROLLBACK_IN_PROGRESS "${backup_stamp}" || return $?
  rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "" state-start \
    write_migration_state "ROLLBACK_IN_PROGRESS" "${migration_id}" "${backup_stamp}" || return $?
  rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "" stop-writers stop_application_writers || return $?
  for store in postgres redis collector; do
    phase="$(read_store_phase "${migration_id}" "${direction}" "${store}")" || return $?
    if [[ "${phase}" != "restored" && "${phase}" != "verified" ]]; then
      rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "${store}" "${store}-started" \
        record_store_progress "${migration_id}" "${store}" started || return $?
      if [[ "${phase}" == "pending" ]]; then
        rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "${store}" "${store}-journal-started" \
          update_crash_journal "${migration_id}" "${direction}" ROLLBACK_IN_PROGRESS "${backup_stamp}" "${store}" started || return $?
      fi
      case "${store}" in
        postgres)
          rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" postgres postgres-restore \
            restore_postgres_dump "${BACKUP_ROOT}/postgres/${backup_stamp}.dump" || return $? ;;
        redis)
          rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" redis redis-restore \
            restore_redis_rdb "${BACKUP_ROOT}/redis/${backup_stamp}.rdb" "${migration_id}" \
            "failed-import-redis-volume" "${BACKUP_ROOT}/${backup_stamp}.backup-manifest.json" || return $? ;;
        collector)
          rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" collector collector-restore \
            restore_collector_sql "${BACKUP_ROOT}/observability/${backup_stamp}.sql.gz" "${migration_id}" || return $? ;;
      esac
      rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "${store}" "${store}-restored" \
        record_store_progress "${migration_id}" "${store}" restored || return $?
      rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "${store}" "${store}-journal-restored" \
        update_crash_journal "${migration_id}" "${direction}" ROLLBACK_IN_PROGRESS "${backup_stamp}" "${store}" restored || return $?
    fi
  done
  rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "" start-release start_current_release || return $?
  rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "" verify-release verify_current_release || return $?
  for store in postgres redis collector; do
    phase="$(read_store_phase "${migration_id}" "${direction}" "${store}")" || return $?
    if [[ "${phase}" != "verified" ]]; then
      rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "${store}" "${store}-verified" \
        record_store_progress "${migration_id}" "${store}" verified || return $?
      rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "${store}" "${store}-journal-verified" \
        update_crash_journal "${migration_id}" "${direction}" ROLLBACK_IN_PROGRESS "${backup_stamp}" "${store}" verified || return $?
    fi
  done
  rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "" journal-complete \
    update_crash_journal "${migration_id}" "${direction}" ROLLED_BACK "${backup_stamp}" || return $?
  rollback_run_step "${migration_id}" "${backup_stamp}" "${direction}" "" state-complete \
    write_migration_state "ROLLED_BACK" "${migration_id}" "${backup_stamp}" || return $?
}

fail_and_rollback() {
  local migration_id="$1" backup_stamp="$2" failed_step="$3" failed_rc="${4:-1}"
  local journal_rc=0 rollback_rc=0
  run_recoverable_step update_crash_journal "${migration_id}" apply APPLY_FAILED \
    "${backup_stamp}" "" "" "${failed_step}" "${failed_rc}"
  journal_rc="${STEP_RC}"
  run_recoverable_step rollback_all "${migration_id}" "${backup_stamp}"
  rollback_rc="${STEP_RC}"
  if [[ "${rollback_rc}" -eq 0 ]]; then
    [[ "${journal_rc}" -eq 0 ]] \
      || printf 'migration origin failure journal write failed (rc=%s)\n' "${journal_rc}" >&2
    printf 'migration step %s failed and the store group was rolled back\n' "${failed_step}" >&2
    return 1
  fi
  write_migration_state "ROLLBACK_FAILED" "${migration_id}" "${backup_stamp}" "${failed_step}" || true
  printf 'migration step %s and automatic rollback failed (rc=%s, journal_rc=%s)\n' \
    "${failed_step}" "${rollback_rc}" "${journal_rc}" >&2
  return 1
}

run_recoverable_step() {
  local restore_errexit=0
  [[ "$-" == *e* ]] && restore_errexit=1
  set +e
  "$@"
  STEP_RC=$?
  [[ "${restore_errexit}" -eq 0 ]] || set -e
  return 0
}

run_recoverable_step_capture() {
  local restore_errexit=0
  [[ "$-" == *e* ]] && restore_errexit=1
  set +e
  STEP_OUTPUT="$("$@")"
  STEP_RC=$?
  [[ "${restore_errexit}" -eq 0 ]] || set -e
  return 0
}

apply_failure_trap() {
  local signal_rc="${1:-1}" journal_rc=0 recovery_rc=0
  trap - EXIT HUP INT TERM
  if [[ "${APPLY_FAILURE_ACTIVE}" -eq 1 ]]; then return "${signal_rc}"; fi
  APPLY_FAILURE_ACTIVE=1
  if [[ -n "${APPLY_MIGRATION_ID}" && -n "${APPLY_BACKUP_STAMP}" ]]; then
    run_recoverable_step update_crash_journal "${APPLY_MIGRATION_ID}" apply INTERRUPTED \
      "${APPLY_BACKUP_STAMP}" "" "" signal "${signal_rc}"
    journal_rc="${STEP_RC}"
  fi
  if [[ "${APPLY_REPLACEMENT_STARTED}" -eq 1 && -n "${APPLY_BACKUP_STAMP}" ]]; then
    run_recoverable_step rollback_all "${APPLY_MIGRATION_ID}" "${APPLY_BACKUP_STAMP}"
  else
    run_recoverable_step start_current_release
  fi
  recovery_rc="${STEP_RC}"
  if [[ "${recovery_rc}" -ne 0 ]]; then
    if [[ "${APPLY_REPLACEMENT_STARTED}" -eq 1 && -n "${APPLY_BACKUP_STAMP}" ]]; then
      printf 'cloud-data-migration: interrupt rollback failed (rc=%s)\n' "${recovery_rc}" >&2
    else
      printf 'cloud-data-migration: interrupt writer restart failed (rc=%s)\n' "${recovery_rc}" >&2
    fi
  elif [[ "${APPLY_REPLACEMENT_STARTED}" -eq 0 && -n "${APPLY_MIGRATION_ID}" ]]; then
    run_recoverable_step clear_migration_fence "${APPLY_MIGRATION_ID}"
    if [[ "${STEP_RC}" -ne 0 ]]; then
      printf 'cloud-data-migration: interrupt fence clear failed (rc=%s)\n' "${STEP_RC}" >&2
    elif [[ -z "${APPLY_BACKUP_STAMP}" ]]; then
      run_recoverable_step update_crash_journal "${APPLY_MIGRATION_ID}" recover \
        RECOVERED_WITHOUT_REPLACE
      journal_rc="${STEP_RC}"
    fi
  fi
  [[ "${journal_rc}" -eq 0 ]] \
    || printf 'cloud-data-migration: interrupt journal update failed (rc=%s)\n' "${journal_rc}" >&2
  return "${signal_rc}"
}

install_apply_failure_trap() {
  trap 'apply_failure_trap $?' EXIT
  trap 'apply_failure_trap 129' HUP
  trap 'apply_failure_trap 130' INT
  trap 'apply_failure_trap 143' TERM
}

clear_apply_failure_trap() {
  trap - EXIT HUP INT TERM
  APPLY_FAILURE_ACTIVE=0
}

begin_migration_fence() {
  local migration_id="$1" fence_schema
  declare -F transaction_fence_begin >/dev/null || return 0
  run_recoverable_step_capture python3 - "${IMPORT_ROOT}/${migration_id}/preflight-current.json" <<'PY'
import hashlib,json,re,sys
from pathlib import Path
marker=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
stores=marker["current"]["stores"]
parts=[stores[name]["schema_fingerprint"] for name in ("postgres","redis","collector")]
if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in parts):
    raise SystemExit("preflight store fingerprint is invalid")
print(hashlib.sha256(":".join(parts).encode()).hexdigest())
PY
  [[ "${STEP_RC}" -eq 0 ]] || return "${STEP_RC}"
  fence_schema="${STEP_OUTPUT}"
  transaction_fence_begin "${migration_id}" "${RELEASE_SHA}" \
    "${LOCKED_STORE_IMAGE[postgres]#sha256:}" "${LOCKED_STORE_IMAGE[redis]#sha256:}" \
    "${LOCKED_STORE_IMAGE[observability-collector]#sha256:}" "${fence_schema}" || return $?
}

clear_migration_fence() {
  declare -F transaction_fence_clear >/dev/null || return 0
  transaction_fence_clear "$1" || return $?
}

apply_migration() {
  local migration_id="$1" backup_stamp
  run_recoverable_step load_runtime
  if [[ "${STEP_RC}" -ne 0 ]]; then return "${STEP_RC}"; fi
  run_recoverable_step require_preapply_batch "${migration_id}"
  if [[ "${STEP_RC}" -ne 0 ]]; then return "${STEP_RC}"; fi
  run_recoverable_step refresh_preflight_before_stop "${migration_id}"
  if [[ "${STEP_RC}" -ne 0 ]]; then return "${STEP_RC}"; fi
  APPLY_MIGRATION_ID="${migration_id}"
  APPLY_BACKUP_STAMP=""
  APPLY_REPLACEMENT_STARTED=0
  run_recoverable_step begin_migration_fence "${migration_id}"
  if [[ "${STEP_RC}" -ne 0 ]]; then return "${STEP_RC}"; fi
  run_recoverable_step begin_crash_journal "${migration_id}"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_migration_fence "${migration_id}"; return "${STEP_RC}"; fi
  install_apply_failure_trap
  run_recoverable_step stop_application_writers
  if [[ "${STEP_RC}" -ne 0 ]]; then
    update_crash_journal "${migration_id}" apply STOP_FAILED "" "" "" stop-writers "${STEP_RC}"
    return 1
  fi
  run_recoverable_step_capture run_required_backup
  if [[ "${STEP_RC}" -ne 0 ]]; then
    update_crash_journal "${migration_id}" apply BACKUP_FAILED "" "" "" backup "${STEP_RC}"
    return 1
  fi
  backup_stamp="${STEP_OUTPUT}"
  APPLY_BACKUP_STAMP="${backup_stamp}"
  run_recoverable_step update_crash_journal "${migration_id}" apply BACKED_UP "${backup_stamp}"
  if [[ "${STEP_RC}" -ne 0 ]]; then return 1; fi
  run_recoverable_step write_migration_state "BACKED_UP" "${migration_id}" "${backup_stamp}"
  if [[ "${STEP_RC}" -ne 0 ]]; then
    run_recoverable_step start_current_release
    return 1
  fi
  APPLY_REPLACEMENT_STARTED=1
  run_recoverable_step record_store_progress "${migration_id}" postgres started
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "postgres-start-state" "${STEP_RC}"; return 1; fi
  update_crash_journal "${migration_id}" apply REPLACING "${backup_stamp}" postgres started || return 1
  run_recoverable_step restore_postgres_dump "${IMPORT_ROOT}/${migration_id}/postgres.dump"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "postgres-restore" "${STEP_RC}"; return 1; fi
  run_recoverable_step record_store_progress "${migration_id}" postgres restored
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "postgres-state" "${STEP_RC}"; return 1; fi
  update_crash_journal "${migration_id}" apply REPLACING "${backup_stamp}" postgres restored || return 1
  run_recoverable_step record_store_progress "${migration_id}" redis started
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "redis-start-state" "${STEP_RC}"; return 1; fi
  update_crash_journal "${migration_id}" apply REPLACING "${backup_stamp}" redis started || return 1
  run_recoverable_step restore_redis_rdb "${IMPORT_ROOT}/${migration_id}/redis.rdb" "${migration_id}"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "redis-restore" "${STEP_RC}"; return 1; fi
  run_recoverable_step record_store_progress "${migration_id}" redis restored
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "redis-state" "${STEP_RC}"; return 1; fi
  update_crash_journal "${migration_id}" apply REPLACING "${backup_stamp}" redis restored || return 1
  run_recoverable_step record_store_progress "${migration_id}" collector started
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "collector-start-state" "${STEP_RC}"; return 1; fi
  update_crash_journal "${migration_id}" apply REPLACING "${backup_stamp}" collector started || return 1
  run_recoverable_step install_collector_db "${IMPORT_ROOT}/${migration_id}/collector.db" "${migration_id}"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "collector-restore" "${STEP_RC}"; return 1; fi
  run_recoverable_step record_store_progress "${migration_id}" collector restored
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "collector-state" "${STEP_RC}"; return 1; fi
  update_crash_journal "${migration_id}" apply REPLACING "${backup_stamp}" collector restored || return 1
  run_recoverable_step verify_store_group "${migration_id}" "pre-start"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "store-verification" "${STEP_RC}"; return 1; fi
  for store in postgres redis collector; do
    run_recoverable_step record_store_progress "${migration_id}" "${store}" verified
    if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "${store}-verify-state" "${STEP_RC}"; return 1; fi
    update_crash_journal "${migration_id}" apply REPLACING "${backup_stamp}" "${store}" verified || return 1
  done
  run_recoverable_step start_verification_services
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "start-verification-services" "${STEP_RC}"; return 1; fi
  run_recoverable_step verify_store_group "${migration_id}" "post-start"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "post-start-verification" "${STEP_RC}"; return 1; fi
  run_recoverable_step start_current_release
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "start-release" "${STEP_RC}"; return 1; fi
  run_recoverable_step verify_current_release
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "release-verification" "${STEP_RC}"; return 1; fi
  run_recoverable_step write_migration_state "APPLIED" "${migration_id}" "${backup_stamp}"
  if [[ "${STEP_RC}" -ne 0 ]]; then clear_apply_failure_trap; fail_and_rollback "${migration_id}" "${backup_stamp}" "state-write" "${STEP_RC}"; return 1; fi
  update_crash_journal "${migration_id}" apply APPLIED "${backup_stamp}" || return 1
  clear_migration_fence "${migration_id}" || return $?
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
    clear_migration_fence "${migration_id}" || return $?
    printf '{"migration_id":"%s","status":"ROLLED_BACK"}\n' "${migration_id}"
    return 0
  fi
  if [[ "${status}" == "ROLLBACK_FAILED" ]]; then
    printf 'rollback continuation requires an audited operator recovery\n' >&2
    return 1
  fi
  rollback_all "${migration_id}" "${backup_stamp}" || return $?
  clear_migration_fence "${migration_id}" || return $?
  printf '{"migration_id":"%s","status":"ROLLED_BACK"}\n' "${migration_id}"
}

recover_migration() {
  local migration_id="$1" state backup_stamp journal_line
  load_runtime || return $?
  require_runtime_batch "${migration_id}" || return $?
  journal_line="$(read_recovery_journal "${migration_id}")" || return $?
  read -r state backup_stamp <<<"${journal_line}" || return $?
  if [[ "${state}" == "RECOVERED_WITHOUT_REPLACE" ]]; then
    printf '{"migration_id":"%s","status":"RECOVERED_WITHOUT_REPLACE"}\n' "${migration_id}"
    return 0
  fi
  if [[ "${state}" == "STOPPING_WRITERS" || "${state}" == "STOP_FAILED" || "${state}" == "BACKUP_FAILED" ]]; then
    start_current_release || return $?
    clear_migration_fence "${migration_id}" || return $?
    update_crash_journal "${migration_id}" recover RECOVERED_WITHOUT_REPLACE || return $?
    printf '{"migration_id":"%s","status":"RECOVERED_WITHOUT_REPLACE"}\n' "${migration_id}"
    return 0
  fi
  if [[ "${state}" == "ROLLED_BACK" ]]; then
    write_migration_state "ROLLED_BACK" "${migration_id}" "${backup_stamp}" || return $?
    clear_migration_fence "${migration_id}" || return $?
    printf '{"migration_id":"%s","status":"ROLLED_BACK"}\n' "${migration_id}"
    return 0
  fi
  validate_backup_manifest "${backup_stamp}" || return $?
  rollback_all "${migration_id}" "${backup_stamp}" recover || return $?
  clear_migration_fence "${migration_id}" || return $?
  printf '{"migration_id":"%s","status":"ROLLED_BACK"}\n' "${migration_id}"
}

inspect_rollback_plan() {
  local migration_id="$1" meta state backup_required backup_stamp services_text backup_manifest="-"
  load_runtime || return $?
  require_runtime_batch "${migration_id}" || return $?
  meta="$(python3 - "${IMPORT_ROOT}/${migration_id}/status.json" \
    "${IMPORT_ROOT}/${migration_id}/journal.json" "${migration_id}" \
    "${CURRENT_RELEASE}/deploy/cloud/migration-state-machine.json" <<'PY'
import json,re,sys
from pathlib import Path
status_path=Path(sys.argv[1])
status=json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else None
journal=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
machine=json.loads(Path(sys.argv[4]).read_text(encoding="utf-8")); states=machine.get("states",{})
if journal.get("migration_id")!=sys.argv[3] or (status is not None and status.get("migration_id")!=sys.argv[3]):
    raise SystemExit("rollback plan identity mismatch")
state=journal.get("state")
if machine.get("schema_version")!=1 or state not in states: raise SystemExit("rollback plan state is invalid")
stamp=(status or {}).get("backup_stamp") or journal.get("backup_stamp") or ""
required=states[state]["backup_required"]
if required and re.fullmatch(r"[0-9]{8}T[0-9]{6}Z",stamp) is None: raise SystemExit("rollback plan backup stamp is invalid")
if not required and stamp: raise SystemExit("pre-backup state unexpectedly claims a backup")
print(state, "1" if required else "0", stamp or "-")
PY
)" || return $?
  read -r state backup_required backup_stamp <<<"${meta}" || return $?
  if [[ "${backup_required}" == "1" ]]; then
    validate_backup_manifest "${backup_stamp}" || return $?
    backup_manifest="${BACKUP_ROOT}/${backup_stamp}.backup-manifest.json"
  elif [[ "${backup_required}" != "0" || "${backup_stamp}" != "-" ]]; then
    return 1
  fi
  services_text="$("${compose[@]}" config --services)" || return $?
  python3 - "${IMPORT_ROOT}/${migration_id}/status.json" \
    "${IMPORT_ROOT}/${migration_id}/journal.json" \
    "${backup_manifest}" "${migration_id}" \
    "${CURRENT_RELEASE}" "${CURRENT_RELEASE}/deploy/cloud/migration-state-machine.json" \
    "${services_text}" <<'PY' || return $?
import json,re,sys
from pathlib import Path
status_path=Path(sys.argv[1])
status=json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else None
journal=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
machine=json.loads(Path(sys.argv[6]).read_text(encoding="utf-8")); states=machine.get("states",{})
services=sys.argv[7].splitlines()
writers=[name for name in services if name not in {"postgres","redis"}]
if not writers or len(writers)!=len(set(writers)) or any(re.fullmatch(r"[a-z0-9-]+",name) is None for name in writers):
    raise SystemExit("rollback writer plan is invalid")
if journal.get("migration_id")!=sys.argv[4] or (status is not None and status.get("migration_id")!=sys.argv[4]):
    raise SystemExit("rollback plan identity mismatch")
state=journal.get("state")
if machine.get("schema_version")!=1 or state not in states:
    raise SystemExit("rollback status is invalid")
status_state=(status or {}).get("status",state)
if status_state not in states: raise SystemExit("rollback status is invalid")
operation_id=journal.get("operation_id","")
if re.fullmatch(r"[0-9a-f]{32}",operation_id) is None: raise SystemExit("journal operation identity is invalid")
backup_required=states[state]["backup_required"]
if backup_required:
    backup=json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    backup_stamp=backup["backup_stamp"]
    backup_files=backup["files"]
else:
    backup_stamp=None
    backup_files=None
print(json.dumps({
  "schema_version":1,"migration_id":sys.argv[4],"status":status_state,
  "journal_state":state,"operation_id":operation_id,
  "current_release":sys.argv[5],"backup_stamp":backup_stamp,
  "backup_files":backup_files,"would_stop":writers,
},sort_keys=True,separators=(",",":")))
PY
}

main() {
  local code=0
  [[ "${EUID}" -eq 0 ]] || die "must run as root"
  case "${1:-}" in
    inspect-current)
      [[ "$#" -eq 1 ]] || die "inspect-current takes no migration id" 2
      inspect_current
      return $? ;;
    rollback-plan)
      [[ "$#" -eq 3 && "${2:-}" == "--migration-id" ]] || die "action requires --migration-id" 2
      require_migration_id "${3:-}"
      inspect_rollback_plan "$3"
      return $? ;;
  esac
  source "${SHARED_ROOT}/bin/transaction-lock.sh"
  transaction_lock_acquire "migration" || {
    code=$?
    die "cloud transaction lock is held by ${TRANSACTION_LOCK_HOLDER:-unknown}" "${code}"
  }
  transaction_fence_assert_clear "${3:-}" || {
    code=$?
    die "cloud transaction is fenced by an unfinished migration; run recover --apply" "${code}"
  }
  source "${SCRIPT_ROOT}/verify-release.sh"
  case "${1:-}" in
    prepare-upload|seal-upload|preflight|apply|verify|rollback|recover)
      [[ "$#" -eq 3 && "${2:-}" == "--migration-id" ]] || die "action requires --migration-id" 2
      require_migration_id "${3:-}"
      case "$1" in
        prepare-upload) prepare_upload "$3" ;;
        seal-upload) seal_upload "$3" ;;
        preflight) preflight_migration "$3" ;;
        apply) apply_migration "$3" ;;
        verify) verify_migration "$3" ;;
        rollback) rollback_migration "$3" ;;
        recover) recover_migration "$3" ;;
      esac ;;
    *) die "unknown data migration action" 2 ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
