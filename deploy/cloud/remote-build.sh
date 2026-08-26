#!/usr/bin/env bash
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  printf 'remote-build.sh must be sourced by remote-release.sh\n' >&2
  exit 2
}

readonly MIN_DISK_BYTES=$((30 * 1024 * 1024 * 1024))
readonly MIN_MEMORY_BYTES=$((3 * 1024 * 1024 * 1024))

validate_expected_current_release() {
  local expected="${1:-}" current release_dir
  [[ "${expected}" =~ ^[0-9a-f]{40}$ ]] \
    || die "current release changed since plan" 2
  release_dir="${RELEASE_ROOT}/releases/${expected}"
  [[ -d "${release_dir}" && ! -L "${release_dir}" ]] \
    || die "current release changed since plan"
  current="$(readlink -f -- "${RELEASE_ROOT}/current")" \
    || die "current release changed since plan"
  [[ "${current}" == "${release_dir}" ]] \
    || die "current release changed since plan"
}

validate_release_manifest_baseline() {
  local manifest="${1:-}" expected="${2:-}"
  [[ "${expected}" =~ ^[0-9a-f]{40}$ \
    && -f "${manifest}" \
    && ! -L "${manifest}" ]] \
    || die "release manifest baseline mismatch" 2
  python3 - "${manifest}" "${expected}" <<'PY' \
    || die "release manifest baseline mismatch"
import json
import re
from pathlib import Path
import sys

manifest_path = Path(sys.argv[1])
expected = sys.argv[2]


def unique_object(pairs):
    payload = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate JSON key")
        payload[key] = value
    return payload


try:
    payload = json.loads(
        manifest_path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
    )
except (OSError, UnicodeError, ValueError):
    raise SystemExit(1)
if not isinstance(payload, dict):
    raise SystemExit(1)
deployed = payload.get("deployed_sha")
if not isinstance(deployed, str) or re.fullmatch(r"[0-9a-f]{40}", deployed) is None:
    raise SystemExit(1)
if deployed != expected:
    raise SystemExit(1)
PY
}

require_capacity() {
  local disk_bytes memory_bytes
  disk_bytes="$(df --output=avail -B1 "${RELEASE_ROOT}" | awk 'NR==2 {print $1}')"
  memory_bytes="$(awk '/^MemAvailable:/ {printf "%.0f\n", $2 * 1024}' /proc/meminfo)"
  [[ "${disk_bytes}" =~ ^[0-9]+$ ]] || die "could not read available disk"
  [[ "${memory_bytes}" =~ ^[0-9]+$ ]] || die "could not read available memory"
  (( disk_bytes >= MIN_DISK_BYTES )) || die "insufficient disk capacity"
  (( memory_bytes >= MIN_MEMORY_BYTES )) || die "insufficient available memory"
}

receive_and_validate_artifact() {
  local sha="$1" upload_id="$2" caller incoming transport_mode build_dir
  caller="${SUDO_USER:-}"
  incoming="${INCOMING_ROOT}/${upload_id}"
  [[ -d "${incoming}" && ! -L "${incoming}" ]] \
    || die "validated upload directory is missing"
  [[ "$(stat -c '%U' "${incoming}")" == "${caller}" ]] \
    || die "upload directory owner mismatch"
  [[ "$(stat -c '%a' "${incoming}")" == "700" ]] \
    || die "upload directory mode mismatch"
  [[ -f "${incoming}/transport.tar" && ! -L "${incoming}/transport.tar" ]] \
    || die "transport archive is missing"
  [[ "$(stat -c '%U' "${incoming}/transport.tar")" == "${caller}" ]] \
    || die "transport archive owner mismatch"
  transport_mode="$(stat -c '%a' "${incoming}/transport.tar")"
  [[ "${transport_mode}" =~ ^[0-7]{3,4}$ ]] \
    || die "transport archive mode is invalid"
  (( (8#${transport_mode} & 0022) == 0 )) \
    || die "transport archive is group or world writable"

  build_dir="${RELEASE_ROOT}/builds/${sha}"
  [[ ! -e "${build_dir}" ]] || die "build directory already exists"
  install -d -m 0700 -o root -g root \
    "${build_dir}" "${build_dir}/upload" "${build_dir}/src"
  install -m 0600 -o root -g root \
    "${incoming}/transport.tar" "${build_dir}/transport.tar"

  python3 - "${build_dir}" "${sha}" <<'PY'
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

build_dir = Path(sys.argv[1])
expected_sha = sys.argv[2]
upload_dir = build_dir / "upload"
source_dir = build_dir / "src"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    basename = path.name
    return bool(
        normalized
        and not normalized.startswith("/")
        and not path.is_absolute()
        and ".." not in path.parts
        and ".artifacts" not in path.parts
        and basename != ".env"
        and not (basename.startswith(".env.") and basename != ".env.example")
        and not basename.endswith((".pem", ".key", ".p12", ".pfx"))
    )


with tarfile.open(build_dir / "transport.tar", mode="r:") as archive:
    members = archive.getmembers()
    expected = ["source.tar", "manifest.json", "checksums.sha256"]
    if [member.name for member in members] != expected:
        raise SystemExit("invalid transport members")
    for member in members:
        if not member.isfile() or not safe_name(member.name):
            raise SystemExit("invalid transport member")
        target = upload_dir / member.name
        extracted = archive.extractfile(member)
        if extracted is None:
            raise SystemExit("transport member is unreadable")
        with target.open("xb") as output:
            output.write(extracted.read())
        target.chmod(0o600)

checksums = {}
for line in (upload_dir / "checksums.sha256").read_text(encoding="utf-8").splitlines():
    checksum, separator, name = line.partition("  ")
    if (
        not separator
        or len(checksum) != 64
        or any(char not in "0123456789abcdef" for char in checksum)
        or name not in {"source.tar", "manifest.json"}
        or name in checksums
    ):
        raise SystemExit("invalid checksum manifest")
    checksums[name] = checksum
if set(checksums) != {"source.tar", "manifest.json"}:
    raise SystemExit("incomplete checksum manifest")
for name, checksum in checksums.items():
    if digest(upload_dir / name) != checksum:
        raise SystemExit("artifact checksum mismatch")

manifest = json.loads((upload_dir / "manifest.json").read_text(encoding="utf-8"))
if manifest.get("schema_version") != 1:
    raise SystemExit("unsupported release manifest schema")
if manifest.get("target_sha") != expected_sha:
    raise SystemExit("release SHA mismatch")
if manifest.get("plan_status") != "ready" or manifest.get("blocking_changes"):
    raise SystemExit("release manifest is not ready")
if manifest.get("source_sha256") != checksums["source.tar"]:
    raise SystemExit("source checksum is not bound to manifest")

with tarfile.open(upload_dir / "source.tar", mode="r:") as archive:
    members = archive.getmembers()
    for member in members:
        if not safe_name(member.name):
            raise SystemExit("invalid source member path")
        target = source_dir / member.name
        if member.isdir():
            target.mkdir(mode=0o755, parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise SystemExit("invalid source member type")
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        extracted = archive.extractfile(member)
        if extracted is None:
            raise SystemExit("source member is unreadable")
        with target.open("xb") as output:
            output.write(extracted.read())
        target.chmod(0o755 if member.mode & 0o111 else 0o644)
PY

  [[ ! -e "${build_dir}/src/.env" ]] \
    || die "source archive unexpectedly contains .env"
  install -m 0600 -o root -g root /dev/null "${build_dir}/src/.env"
  printf '%s\n' "${build_dir}"
}

verify_shared_models() {
  local manifest="$1"
  python3 - "${manifest}" "${SHARED_ROOT}/models" <<'PY' \
    || die "bootstrap_required: shared runtime models are missing or invalid" 42
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys

manifest_path = Path(sys.argv[1])
shared_root = Path(sys.argv[2])
payload = json.loads(manifest_path.read_text(encoding="utf-8"))
if payload.get("schema_version") != 1:
    raise SystemExit(1)
for item in payload.get("models", []):
    relative = item.get("path", "")
    expected = item.get("sha256", "")
    path = PurePosixPath(relative)
    if (
        not relative.startswith("models/")
        or path.is_absolute()
        or ".." in path.parts
        or len(expected) != 64
        or any(char not in "0123456789abcdef" for char in expected)
    ):
        raise SystemExit(1)
    target = shared_root.joinpath(*path.parts[1:])
    if not target.is_file() or target.is_symlink():
        raise SystemExit(1)
    value = hashlib.sha256(target.read_bytes()).hexdigest()
    if value != expected:
        raise SystemExit(1)
PY
}

release_service_rows() {
  local manifest="$1"
  python3 - "${manifest}" <<'PY'
import json
import re
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("schema_version") != 1:
    raise SystemExit("unsupported service manifest schema")
services = payload.get("services")
if not isinstance(services, list) or len(services) != 26:
    raise SystemExit("release service count must be 26")
seen = set()
for item in services:
    service = item.get("service", "")
    image = item.get("image", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", service):
        raise SystemExit("invalid service name")
    if image != f"car-agent-release/{service}" or service in seen:
        raise SystemExit("invalid service image mapping")
    seen.add(service)
    print(f"{service}\t{image}")
PY
}

verify_release_images() {
  local sha="$1" manifest="$2"
  local row service image
  while IFS=$'\t' read -r service image; do
    [[ -n "${service}" && -n "${image}" ]] \
      || die "invalid release service row"
    docker image inspect "${image}:${sha}" >/dev/null
  done < <(release_service_rows "${manifest}")
}

write_image_inventory() {
  local sha="$1" build_dir="$2" rows="$3"
  python3 - "${sha}" "${rows}" "${build_dir}/image-inventory.json" <<'PY'
import json
from pathlib import Path
import sys

sha = sys.argv[1]
rows = Path(sys.argv[2])
target = Path(sys.argv[3])
images = []
for line in rows.read_text(encoding="utf-8").splitlines():
    service, image, image_id = line.split("\t")
    images.append({
        "service": service,
        "image": f"{image}:{sha}",
        "image_id": image_id,
    })
payload = {"schema_version": 1, "release_sha": sha, "images": images}
with target.open("x", encoding="utf-8", newline="\n") as handle:
    json.dump(
        payload,
        handle,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    handle.write("\n")
target.chmod(0o600)
PY
}

build_release() {
  local sha="$1" upload_id="$2" expected_current="$3"
  local build_dir src manifest project
  local row service image local_image image_id
  local -a compose_args release_rows
  validate_expected_current_release "${expected_current}"
  require_capacity
  build_dir="$(receive_and_validate_artifact "${sha}" "${upload_id}")"
  validate_release_manifest_baseline \
    "${build_dir}/upload/manifest.json" "${expected_current}"
  src="${build_dir}/src"
  manifest="${src}/deploy/cloud/release-services.json"
  project="car-agent-release-${sha}"
  verify_shared_models "${src}/deploy/cloud/runtime-models.json"

  mapfile -t release_rows < <(release_service_rows "${manifest}")
  [[ "${#release_rows[@]}" -eq 26 ]] \
    || die "release service count must be 26"
  install -m 0600 -o root -g root /dev/null \
    "${build_dir}/image-inventory.tsv"
  while IFS=$'\t' read -r service image; do
    [[ -n "${service}" && -n "${image}" ]] \
      || die "invalid release service row"
    if docker image inspect "${image}:${sha}" >/dev/null 2>&1; then
      die "target release image already exists: ${image}:${sha}"
    fi
    compose_args=(
      --project-name "${project}"
      --project-directory "${src}"
      -f "${src}/compose.yaml"
      -f "${src}/deploy/cloud/compose.build.yaml"
      --env-file "${src}/.env"
    )
    CAR_AGENT_MODELS_ROOT="${SHARED_ROOT}/models" \
      CAR_AGENT_HMI_MODELS_ROOT="${SHARED_ROOT}/models/hmi" \
      docker compose "${compose_args[@]}" build "${service}"
    local_image="${project}-${service}:latest"
    docker image inspect "${local_image}" >/dev/null
    docker image tag "${local_image}" "${image}:${sha}"
    image_id="$(docker image inspect --format '{{.Id}}' "${image}:${sha}")"
    printf '%s\t%s\t%s\n' "${service}" "${image}" "${image_id}" \
      >>"${build_dir}/image-inventory.tsv"
  done < <(printf '%s\n' "${release_rows[@]}")

  verify_release_images "${sha}" "${manifest}"
  write_image_inventory "${sha}" "${build_dir}" \
    "${build_dir}/image-inventory.tsv"
}
