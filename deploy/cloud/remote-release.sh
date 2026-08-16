#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly RELEASE_ROOT="/opt/car-agent"
readonly SHARED_ROOT="${RELEASE_ROOT}/shared"
readonly RELEASE_LOCK="${SHARED_ROOT}/locks/release.lock"
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
  [[ "${EUID}" -eq 0 ]] || die "must run as root"
  install -d -m 0700 -o root -g root "${SHARED_ROOT}/locks"
  exec 9>"${RELEASE_LOCK}"
  flock -n 9 || die "another release transaction holds the lock" 75

  source "${SCRIPT_ROOT}/remote-build.sh"
  source "${SCRIPT_ROOT}/activate-release.sh"
  source "${SCRIPT_ROOT}/verify-release.sh"

  case "${1:-}" in
    deploy)
      [[ "${2:-}" == "--sha" && "${4:-}" == "--upload-id" ]] \
        || die "deploy requires --sha and --upload-id" 2
      validate_full_sha "${3:-}"
      validate_upload_id "${3}" "${5:-}"
      build_release "${3}" "${5}"
      activate_release "${3}"
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
      ;;
    *)
      die "unknown action" 2
      ;;
  esac
}

main "$@"
