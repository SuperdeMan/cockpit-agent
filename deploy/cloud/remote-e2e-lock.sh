#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly SHARED_ROOT="/opt/car-agent/shared"

die() {
  printf 'remote-e2e-lock: %s\n' "$1" >&2
  exit "${2:-1}"
}

source "${SHARED_ROOT}/bin/transaction-lock.sh"
[[ "${EUID}" -eq 0 ]] || die "must run as root"
[[ "${1:-}" == "hold" && "${2:-}" == "--run-id" ]] \
  || die "hold requires --run-id" 2
readonly RUN_ID="${3:-}"
[[ "${RUN_ID}" =~ ^e2e-[0-9a-f]{32}$ ]] || die "invalid run id" 2

transaction_lock_acquire "e2e" \
  || die "cloud transaction lock is held by ${TRANSACTION_LOCK_HOLDER:-unknown}" "$?"
printf 'READY %s\n' "${RUN_ID}"
IFS= read -r _release_signal || true
