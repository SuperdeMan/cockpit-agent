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
# The runner strips AUDIO_API_URL from every case child on purpose; the name it
# does hand down for the audio/LLM endpoint is E2E_AUDIO_API_ORIGIN.
AUDIO_API_ORIGIN = os.environ["E2E_AUDIO_API_ORIGIN"]
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


def active_provider_model(payload: bytes) -> dict[str, str]:
    value = json.loads(payload.decode("utf-8"))
    active = value.get("active") if isinstance(value, dict) else None
    provider = active.get("provider") if isinstance(active, dict) else None
    model = active.get("model") if isinstance(active, dict) else None
    if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
        raise RemoteSafeError("active provider/model is missing")
    return {"provider": provider, "model": model}


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
    token = os.environ.get("VITE_WS_TOKEN", "")
    if not token:
        raise RemoteSafeError("websocket token is missing")
    ws_url = append_query_token(WS_URL, token)
    trace_id = "remote-" + uuid.uuid4().hex
    payload = {
        "text": "你好，请只回复一句简短问候",
        "session_id": recorder.session_id(1),
        "is_confirmation": False,
        # The gateway decodes meta as map[string]string; a non-string value fails
        # the whole unmarshal and the request is dropped without a frame or a log.
        "meta": {
            "trace_id": trace_id,
            "memory_enabled": "false",
            "e2e_run_id": os.environ["E2E_RUN_ID"],
        },
    }
    async with websockets.connect(ws_url, open_timeout=20, ping_interval=None) as socket:
        # An unsigned client gets no handshake frame from the edge WS: a bad token
        # fails the upgrade above, and the mirror/proactive frames the gateway may
        # push first are skipped by wait_final.
        await socket.send(json.dumps(payload, ensure_ascii=False))
        await wait_final(socket, timeout_s=90)
    await asyncio.to_thread(wait_collector_trace, trace_id, timeout_s=30)
    return trace_id


#: 「这一轮引用到了长期记忆」的两条判据，取或。
#:
#: **为什么非要这条探针**：2026-08-19 实测，云端 `AUTH_TOKENS` 漏写 user_id 段导致网关把
#: user_id 解析成 `v1`，而全部长期记忆在 `u1` 名下 ⇒ **一条都召不回**。而当时
#: 端点全 healthy、`verify` 全绿、权限全通——因为**这条 E2E 只发一句问候，验证面里
#: 没有记忆**。「切云验证通过」于是与「记忆全废」同时成立了三天。
#:
#: 判据取或是刻意的：`with_provenance` 会**确定性追加**出处尾巴，但它有一条
#: 「模型自己说了就别再叠」的短路，所以出处标记会漏；而实体词只在真的引用了记忆时出现。
#: 两条任一命中即算召回到了。**宁可假红不要假绿**——假红有人看，假绿没人看。
_MEMORY_EVIDENCE = ("提过的）", "提过", "记得")


async def memory_recall_probe(recorder: CaseRecorder) -> None:
    """只读：问一句只有长期记忆才答得出的问题，断言当前身份确实看得见记忆。

    **不写入任何数据**（remote_safe 契约）：只发一句问句，不产生动作、不下单、不建提醒。

    ⚠ 前提：当前 owner 名下存在可召回的长期记忆。前提不成立时报错要说清是前提问题，
    别让下一个人把「库是空的」读成「召回坏了」。
    """
    token = os.environ.get("VITE_WS_TOKEN", "")
    if not token:
        raise RemoteSafeError("websocket token is missing")
    ws_url = append_query_token(WS_URL, token)
    payload = {
        "text": "我女儿在哪上学",
        "session_id": recorder.session_id(2),
        "is_confirmation": False,
        "meta": {
            "trace_id": "remote-mem-" + uuid.uuid4().hex,
            "memory_enabled": "true",
            "e2e_run_id": os.environ["E2E_RUN_ID"],
        },
    }
    async with websockets.connect(ws_url, open_timeout=20, ping_interval=None) as socket:
        await socket.send(json.dumps(payload, ensure_ascii=False))
        final = await wait_final(socket, timeout_s=90)
    speech = str(final.get("speech") or "")
    if final.get("actions"):
        raise RemoteSafeError("memory probe must stay read-only but produced actions")
    if not any(mark in speech for mark in _MEMORY_EVIDENCE):
        raise RemoteSafeError(
            "long-term memory was not recalled for the current identity; "
            "check the AUTH_TOKENS user_id segment before assuming recall is broken "
            "(this probe also fails if the owner simply has no memories yet)")


async def main() -> int:
    recorder = CaseRecorder()
    token = os.environ.get("VITE_WS_TOKEN", "")
    with recorder:
        try:
            require_http_200("hmi", append_path(HMI_URL, "/"))
            recorder.pass_case("remote-hmi")
            require_http_200("edge", append_path(EDGE_HTTP_URL, "/healthz"))
            recorder.pass_case("remote-edge-health")
            catalog = active_provider_model(require_http_200(
                "audio", append_path(AUDIO_API_ORIGIN, "/api/llm/providers"),
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
            await memory_recall_probe(recorder)
            recorder.pass_case("remote-memory-recall")
        except Exception as exc:
            safe = _redact_text(str(exc), {token} if token else set())
            raise RemoteSafeError(safe) from None
    return recorder.exit_code()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
