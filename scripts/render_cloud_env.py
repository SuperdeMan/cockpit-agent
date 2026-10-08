from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Sequence


class CloudEnvError(ValueError):
    """Cloud runtime environment validation failed."""


_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_TAILNET_RE = re.compile(r"^car-agent-dev\.[a-z0-9.-]+\.ts\.net$")
_SAMPLE_TOKENS = {
    "sample",
    "change-me",
    "changeme",
    "your-token",
    "replace-me",
    "demo-token",
}
DEMO_AUTH_SCOPES = (
    "vehicle.control",
    "vehicle.read.state",
    "location.read",
    "location.precise",
    "navigation.control",
    "media.control",
    "network.external",
    "payment.invoke",
    "profile.read",
    "profile.write",
    "microphone.read",
    "camera.frame",
    "merchant.read",
    "merchant.write",
)
#: 4 段形式的 token 原样透传上云，最少要多少字符。渲染自己生成的是 64 字符（token_urlsafe(48)）。
#: 本机开发 .env 里的 `u1:u1:v1:…` 形式合法、强度为零，曾经过得了这里和部署形态闸（2026-08-27 登记）：
#: 闸只查「不是示例值」，查不出强度；云端那份恰好是渲染时生成的强 token，重渲染就会把 `u1` 带上去。
_MIN_PASSTHROUGH_TOKEN_CHARS = 32


def _assignment(raw: str) -> tuple[str, str] | None:
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    if not _KEY_RE.fullmatch(key):
        return None
    return key, value.strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _effective_values(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in lines:
        parsed = _assignment(raw)
        if parsed:
            key, value = parsed
            values[key] = _unquote(value)
    return values


def _auth_config(auth_tokens: str) -> tuple[str, str | None]:
    entries = [entry.strip() for entry in auth_tokens.split(";") if entry.strip()]
    if not entries:
        raise CloudEnvError("AUTH_TOKENS is missing or empty")
    parts = entries[0].split(":", 3)
    if len(parts) == 3 and all(part.strip() for part in parts):
        legacy_token, user_id, vehicle_id = (part.strip() for part in parts)
        if legacy_token.lower() in _SAMPLE_TOKENS:
            raise CloudEnvError("AUTH_TOKENS contains a sample token")
        token = secrets.token_urlsafe(48)
        upgraded = ":".join(
            (token, user_id, vehicle_id, ",".join(DEMO_AUTH_SCOPES))
        )
        return token, upgraded
    if len(parts) != 4 or any(not part.strip() for part in parts):
        raise CloudEnvError("AUTH_TOKENS first entry has an invalid shape")
    token, _user_id, _vehicle_id, scopes = (part.strip() for part in parts)
    if not all(scope.strip() for scope in scopes.split(",")):
        raise CloudEnvError("AUTH_TOKENS first entry has invalid scopes")
    # 每一条都是凭据、都原样上云，不只首条（首条另外还当 HMI 的 VITE_WS_TOKEN）
    for index, entry in enumerate(entries, start=1):
        fields = [part.strip() for part in entry.split(":", 3)]
        if fields[0].lower() in _SAMPLE_TOKENS:
            raise CloudEnvError("AUTH_TOKENS contains a sample token")
        if len(fields) == 4 and (
            len(fields[0]) < _MIN_PASSTHROUGH_TOKEN_CHARS or fields[0] == fields[1]
        ):
            raise CloudEnvError(
                f"AUTH_TOKENS entry {index} token is too weak to pass through: need at least "
                f"{_MIN_PASSTHROUGH_TOKEN_CHARS} characters and not equal to its user_id "
                "(or use the 3-part token:user_id:vehicle_id form to get a generated token)"
            )
    return token, None


def _render_lines(lines: list[str], updates: dict[str, str]) -> str:
    last_index: dict[str, int] = {}
    for index, raw in enumerate(lines):
        parsed = _assignment(raw)
        if parsed:
            last_index[parsed[0]] = index

    rendered: list[str] = []
    emitted: set[str] = set()
    for index, raw in enumerate(lines):
        parsed = _assignment(raw)
        if not parsed:
            rendered.append(raw)
            continue
        key, _value = parsed
        if last_index[key] != index:
            continue
        if key in updates:
            rendered.append(f"{key}={updates[key]}")
            emitted.add(key)
        else:
            rendered.append(raw)

    missing = [key for key in updates if key not in emitted and key not in last_index]
    if missing:
        if rendered and rendered[-1] != "":
            rendered.append("")
        rendered.append("# Tencent Cloud private demo runtime")
        rendered.extend(f"{key}={updates[key]}" for key in missing)
    return "\n".join(rendered).rstrip("\n") + "\n"


def _atomic_write(output: Path, content: str) -> None:
    if not output.parent.is_dir():
        raise CloudEnvError("output parent directory does not exist")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def render_cloud_env(
    *, source: Path, output: Path, release_sha: str, tailnet_fqdn: str
) -> None:
    source = Path(source)
    output = Path(output)
    if source.resolve() == output.resolve():
        raise CloudEnvError("source and output must be different files")
    if not source.is_file():
        raise CloudEnvError("source env file does not exist")
    if not _SHA_RE.fullmatch(release_sha):
        raise CloudEnvError("RELEASE_SHA must be a lowercase Git SHA")
    if not _TAILNET_RE.fullmatch(tailnet_fqdn) or ".." in tailnet_fqdn:
        raise CloudEnvError("TAILNET_FQDN does not match the private demo host")

    lines = source.read_text(encoding="utf-8").splitlines()
    current = _effective_values(lines)
    token, upgraded_auth_tokens = _auth_config(current.get("AUTH_TOKENS", ""))
    channel_token = secrets.token_urlsafe(48)
    postgres_password = secrets.token_urlsafe(48)
    while postgres_password == channel_token:
        postgres_password = secrets.token_urlsafe(48)

    updates = {
        "RELEASE_SHA": release_sha,
        "TAILNET_FQDN": tailnet_fqdn,
        "DEPLOY_PROFILE": "demo",
        "AUTH_REQUIRED": "true",
        "VITE_WS_TOKEN": token,
        "PERMISSIONS_FAIL_OPEN": "false",
        "CLOUD_CHANNEL_TOKEN": channel_token,
        "CLOUD_CHANNEL_TOKENS": channel_token,
        "POSTGRES_PASSWORD": postgres_password,
        "POSTGRES_DSN": (
            f"postgresql://cockpit:{postgres_password}@postgres:5432/cockpit"
        ),
        "DEBUG_VEHICLE_CONTROL": "true",
        "OBS_CONTENT_CAPTURE": "on",
        "GRPC_TLS": "off",
    }
    if upgraded_auth_tokens is not None:
        updates["AUTH_TOKENS"] = upgraded_auth_tokens
    _atomic_write(output, _render_lines(lines, updates))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a fail-closed cloud runtime env without printing secrets."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--tailnet-fqdn", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        render_cloud_env(
            source=args.source,
            output=args.output,
            release_sha=args.release_sha,
            tailnet_fqdn=args.tailnet_fqdn,
        )
    except (CloudEnvError, OSError) as exc:
        error_type = type(exc).__name__
        print(f"cloud env render failed: {error_type}", file=os.sys.stderr)
        return 2
    print(json.dumps({"status": "ok", "output": args.output.name}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
