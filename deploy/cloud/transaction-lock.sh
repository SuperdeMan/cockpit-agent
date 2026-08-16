#!/usr/bin/env bash
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  printf 'transaction-lock.sh must be sourced\n' >&2
  exit 2
}

readonly TRANSACTION_LOCK="${SHARED_ROOT}/locks/release.lock"

transaction_lock_acquire() {
  local kind="$1" holder
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
  : >"${TRANSACTION_LOCK}"
  printf '%s\n' "${kind}" >&"${TRANSACTION_LOCK_FD}"
  export TRANSACTION_LOCK_FD
}

transaction_lock_validate_inherited() {
  local descriptor="$1"
  [[ "${descriptor}" =~ ^[0-9]+$ ]] || return 2
  [[ "$(readlink "/proc/$$/fd/${descriptor}")" == "${TRANSACTION_LOCK}" ]] || return 2
}
