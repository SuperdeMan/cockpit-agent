#!/usr/bin/env bash
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  printf 'transaction-lock.sh must be sourced\n' >&2
  exit 2
}

readonly TRANSACTION_LOCK="${SHARED_ROOT}/locks/release.lock"
readonly TRANSACTION_FENCE="${SHARED_ROOT}/locks/active-migration.json"

transaction_fence_assert_clear() {
  local allowed_owner="${1:-}" rc=0
  [[ -e "${TRANSACTION_FENCE}" ]] || return 0
  python3 - "${TRANSACTION_FENCE}" "${allowed_owner}" <<'PY' || rc=$?
import json, os, re, stat, sys
path=sys.argv[1]; allowed=sys.argv[2]
if os.path.islink(path): raise SystemExit(2)
fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
try:
    meta=os.fstat(fd)
    if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_size > 64*1024:
        raise SystemExit(2)
    payload=json.loads(os.read(fd,meta.st_size).decode("utf-8"))
finally:
    os.close(fd)
required={"schema_version","migration_id","release_sha","store_images","schema_fingerprint"}
if set(payload)!=required or payload.get("schema_version")!=1:
    raise SystemExit(2)
if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7}-(online|final)",payload.get("migration_id","")) is None:
    raise SystemExit(2)
if allowed and payload["migration_id"]==allowed:
    raise SystemExit(0)
raise SystemExit(75)
PY
  if [[ "${rc}" -eq 75 ]]; then
    printf 'cloud transaction fenced by unfinished migration; run recover --apply\n' >&2
  elif [[ "${rc}" -ne 0 ]]; then
    printf 'cloud transaction fence is invalid; audited recovery is required\n' >&2
  fi
  return "${rc}"
}

transaction_fence_begin() {
  local migration_id="$1" release_sha="$2" postgres_image="$3" redis_image="$4"
  local collector_image="$5" schema_fingerprint="$6"
  python3 - "${TRANSACTION_FENCE}" "${migration_id}" "${release_sha}" \
    "${postgres_image}" "${redis_image}" "${collector_image}" "${schema_fingerprint}" <<'PY' || return $?
import json, os, re, stat, sys
path=sys.argv[1]
parent=os.path.dirname(path)
os.makedirs(parent,mode=0o700,exist_ok=True)
os.chmod(parent,0o700)
payload={"schema_version":1,"migration_id":sys.argv[2],"release_sha":sys.argv[3],
         "store_images":{"postgres":sys.argv[4],"redis":sys.argv[5],"collector":sys.argv[6]},
         "schema_fingerprint":sys.argv[7]}
if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7}-(online|final)",payload["migration_id"]) is None:
    raise SystemExit(2)
if re.fullmatch(r"[0-9a-f]{7,40}",payload["release_sha"]) is None:
    raise SystemExit(2)
if any(re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}",value) is None for value in payload["store_images"].values()):
    raise SystemExit(2)
if re.fullmatch(r"[0-9a-f]{64}",payload["schema_fingerprint"]) is None:
    raise SystemExit(2)
encoded=(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n").encode()
try:
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
except FileExistsError:
    if os.path.islink(path): raise SystemExit(2)
    fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
    try: current=json.loads(os.read(fd,64*1024+1).decode())
    finally: os.close(fd)
    if current != payload: raise SystemExit(75)
else:
    try: os.write(fd,encoded); os.fsync(fd)
    finally: os.close(fd)
    if hasattr(os,"O_DIRECTORY"):
        directory=os.open(os.path.dirname(path),os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
PY
}

transaction_fence_clear() {
  local migration_id="$1"
  python3 - "${TRANSACTION_FENCE}" "${migration_id}" <<'PY' || return $?
import json,os,sys
path=sys.argv[1]
if os.path.islink(path): raise SystemExit(75)
try: fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
except FileNotFoundError: raise SystemExit(0)
try: payload=json.loads(os.read(fd,64*1024+1).decode())
finally: os.close(fd)
if payload.get("migration_id")!=sys.argv[2]: raise SystemExit(75)
os.unlink(path)
if hasattr(os,"O_DIRECTORY"):
    directory=os.open(os.path.dirname(path),os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
PY
}

transaction_lock_acquire() {
  local kind="$1" holder fence_rc=0
  [[ "${kind}" =~ ^(release|rollback|backup|migration|e2e)$ ]] || return 2
  install -d -m 0700 -o root -g root "${SHARED_ROOT}/locks"
  exec {TRANSACTION_LOCK_FD}<>"${TRANSACTION_LOCK}"
  if ! flock -n "${TRANSACTION_LOCK_FD}"; then
    holder="$(head -c 32 "${TRANSACTION_LOCK}" 2>/dev/null || true)"
    [[ "${holder}" =~ ^(release|rollback|backup|migration|e2e)$ ]] || holder="unknown"
    TRANSACTION_LOCK_HOLDER="${holder}"
    export TRANSACTION_LOCK_HOLDER
    return 75
  fi
  if [[ "${kind}" != "migration" ]]; then
    transaction_fence_assert_clear || fence_rc=$?
    [[ "${fence_rc}" -eq 0 ]] || return "${fence_rc}"
  fi
  : >"${TRANSACTION_LOCK}"
  printf '%s\n' "${kind}" >&"${TRANSACTION_LOCK_FD}"
  export TRANSACTION_LOCK_FD
}

transaction_lock_validate_inherited() {
  local descriptor="$1"
  [[ "${descriptor}" =~ ^[0-9]+$ ]] || return 2
  [[ "$(readlink "/proc/$$/fd/${descriptor}")" == "${TRANSACTION_LOCK}" ]] || return 2
}
