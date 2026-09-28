#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly RELEASE_ROOT="/opt/car-agent"
readonly SHARED_ROOT="${RELEASE_ROOT}/shared"
readonly SCRIPT_ROOT="${SHARED_ROOT}/bin"
readonly INCOMING_ROOT="${RELEASE_ROOT}/incoming/releases"

die() {
  printf 'cloud-release: %s\n' "$1" >&2
  exit "${2:-1}"
}

validate_full_sha() {
  [[ "${1:-}" =~ ^[0-9a-f]{40}$ ]] || die "invalid full release SHA" 2
}

validate_release_selector() {
  [[ "${1:-}" =~ ^[0-9a-f]{7,40}$ ]] || die "invalid release selector" 2
}

validate_upload_id() {
  local sha="$1" upload_id="$2"
  [[ "${upload_id}" =~ ^${sha}-[0-9a-f]{32}$ ]] \
    || die "invalid upload ID" 2
}

# 保留策略（retention-policy.json）只在 retention.py 里执行删除；本函数只在事务内、发布已经
# VERIFIED / ROLLED_BACK 之后调用。回收失败不影响已完成的发布，只留警告与 retention.py 的证据。
run_release_retention() {
  local reason="$1"
  python3 "${SCRIPT_ROOT}/retention.py" releases --mode apply --reason "${reason}" \
    --lock-fd "${TRANSACTION_LOCK_FD}" >&2 \
    || printf 'cloud-release: retention did not complete; the release itself is unaffected\n' >&2
}

prepare_upload() {
  local upload_id="$1" caller caller_group target
  caller="${SUDO_USER:-}"
  [[ "${caller}" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] \
    || die "prepare-upload requires a valid sudo caller"
  caller_group="$(id -gn "${caller}")"
  target="${INCOMING_ROOT}/${upload_id}"
  install -d -m 0755 -o root -g root "${INCOMING_ROOT}"
  [[ ! -e "${target}" ]] || die "upload directory already exists"
  install -d -m 0700 -o "${caller}" -g "${caller_group}" "${target}"
  printf '%s\n' "${target}"
}

main() {
  local kind="release" code=0
  [[ "${EUID}" -eq 0 ]] || die "must run as root"

  source "${SCRIPT_ROOT}/transaction-lock.sh"
  [[ "${1:-}" == "rollback" ]] && kind="rollback"
  transaction_lock_acquire "${kind}" || {
    code=$?
    die "cloud transaction lock is held by ${TRANSACTION_LOCK_HOLDER:-unknown}" "${code}"
  }

  source "${SCRIPT_ROOT}/remote-build.sh"
  source "${SCRIPT_ROOT}/activate-release.sh"
  source "${SCRIPT_ROOT}/verify-release.sh"

  case "${1:-}" in
    deploy)
      [[ "$#" -eq 7 \
        && "${2:-}" == "--sha" \
        && "${4:-}" == "--upload-id" \
        && "${6:-}" == "--expected-current" ]] \
        || die "deploy requires --sha, --upload-id, and --expected-current" 2
      validate_full_sha "${3:-}"
      validate_upload_id "${3}" "${5:-}"
      validate_full_sha "${7:-}"
      build_release "${3}" "${5}" "${7}"
      activate_release "${3}"
      run_release_retention "deploy"
      ;;
    prepare-upload)
      [[ "${2:-}" == "--sha" && "${4:-}" == "--upload-id" ]] \
        || die "prepare-upload requires --sha and --upload-id" 2
      validate_full_sha "${3:-}"
      validate_upload_id "${3}" "${5:-}"
      prepare_upload "${5}"
      ;;
    verify-current)
      verify_current_release
      ;;
    rollback)
      [[ "${2:-}" == "--to" ]] || die "rollback requires --to" 2
      validate_release_selector "${3:-}"
      rollback_release "${3}"
      run_release_retention "rollback"
      ;;
    retention)
      [[ "$#" -eq 2 && ( "${2}" == "--dry-run" || "${2}" == "--apply" ) ]] \
        || die "retention requires --dry-run or --apply" 2
      # 两类各自独立：一类失败不挡另一类，两份记录都输出，退出码取最后一个非零值。
      python3 "${SCRIPT_ROOT}/retention.py" releases --mode "${2#--}" \
        --reason manual --lock-fd "${TRANSACTION_LOCK_FD}" || code=$?
      python3 "${SCRIPT_ROOT}/retention.py" backups --mode "${2#--}" \
        --reason manual --lock-fd "${TRANSACTION_LOCK_FD}" || code=$?
      return "${code}"
      ;;
    *)
      die "unknown action" 2
      ;;
  esac
}

main "$@"
