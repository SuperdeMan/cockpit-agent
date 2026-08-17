"""Read-only remote-stack connectivity and isolated greeting probe."""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.parse
import urllib.request
import uuid

import websockets

from support.e2e import CaseRecorder, _redact_text


HMI_URL = os.environ["HMI_URL"]
EDGE_HTTP_URL = os.environ["EDGE_HTTP_URL"]
WS_URL = os.environ["WS_URL"]
AUDIO_API_URL = os.environ["AUDIO_API_URL"]
DASHBOARD_URL = os.environ["DASHBOARD_URL"]
COLLECTOR_URL = os.environ["COLLECTOR_URL"]
COLLECTOR_WS_URL = os.environ["COLLECTOR_WS_URL"]


class RemoteSafeError(RuntimeError):
    pass


def append_path(base: str, path: str) -> str:
    return base.rstrip("/") + path


def require_http_200(name: str, url: str) -> bytes:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RemoteSafeError(f"{name} returned HTTP {response.status}")
        return response.read(512 * 1024)


def provider_names(payload: bytes) -> list[dict[str, str]]:
    value = json.loads(payload.decode("utf-8"))
    found: set[tuple[str, str]] = set()
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            provider = item.get("provider") or item.get("name")
            model = item.get("model") or item.get("id")
            if isinstance(provider, str) and isinstance(model, str):
                found.add((provider, model))
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return [
        {"provider": provider, "model": model}
        for provider, model in sorted(found)
    ]


async def collector_stream_probe(url: str) -> None:
    async with websockets.connect(url, open_timeout=20, close_timeout=5):
        return


def append_query_token(url: str, token: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("token", token))
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))


async def wait_final(socket, *, timeout_s: float) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise RemoteSafeError("edge final response timed out")
        message = json.loads(await asyncio.wait_for(socket.recv(), timeout=remaining))
        if message.get("type") in {"final", "error"}:
            if message.get("type") != "final":
                raise RemoteSafeError("edge returned an error frame")
            return message


def wait_collector_trace(trace_id: str, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    url = append_path(COLLECTOR_URL, f"/api/traces/{trace_id}")
    while time.monotonic() < deadline:
        try:
            require_http_200("collector trace", url)
            return
        except Exception:
            time.sleep(0.5)
    raise RemoteSafeError("collector trace did not arrive")


async def edge_round_trip(recorder: CaseRecorder) -> str:
    token = os.environ["VITE_WS_TOKEN"]
    if not token:
        raise RemoteSafeError("websocket token is missing")
    ws_url = append_query_token(WS_URL, token)
    trace_id = "remote-" + uuid.uuid4().hex
    payload = {
        "text": "你好，请只回复一句简短问候",
        "session_id": recorder.session_id(1),
        "is_confirmation": False,
        "meta": {
            "trace_id": trace_id,
            "memory_enabled": False,
            "e2e_run_id": os.environ["E2E_RUN_ID"],
        },
    }
    async with websockets.connect(ws_url, open_timeout=20, ping_interval=None) as socket:
        ack = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
        if ack.get("type") != "hello_ack":
            raise RemoteSafeError("edge websocket acknowledgement failed")
        await socket.send(json.dumps(payload, ensure_ascii=False))
        await wait_final(socket, timeout_s=90)
    await asyncio.to_thread(wait_collector_trace, trace_id, timeout_s=30)
    return trace_id


async def main() -> int:
    recorder = CaseRecorder()
    token = os.environ.get("VITE_WS_TOKEN", "")
    with recorder:
        try:
            require_http_200("hmi", append_path(HMI_URL, "/"))
            recorder.pass_case("remote-hmi")
            require_http_200("edge", append_path(EDGE_HTTP_URL, "/healthz"))
            recorder.pass_case("remote-edge-health")
            catalog = provider_names(require_http_200(
                "audio", append_path(AUDIO_API_URL, "/api/llm/providers"),
            ))
            artifact = recorder.add_artifact(
                "remote_provider_catalog.json",
                metadata={"kind": "provider-model-names"},
            )
            artifact.write_text(json.dumps(catalog, sort_keys=True), encoding="utf-8")
            recorder.pass_case("remote-audio-catalog")
            require_http_200("dashboard", append_path(DASHBOARD_URL, "/"))
            recorder.pass_case("remote-dashboard")
            require_http_200("collector", append_path(COLLECTOR_URL, "/healthz"))
            recorder.pass_case("remote-collector-health")
            await collector_stream_probe(COLLECTOR_WS_URL)
            recorder.pass_case("remote-collector-stream")
            await edge_round_trip(recorder)
            recorder.pass_case("remote-isolated-round-trip")
        except Exception as exc:
            safe = _redact_text(str(exc), {token} if token else set())
            raise RemoteSafeError(safe) from None
    return recorder.exit_code()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
