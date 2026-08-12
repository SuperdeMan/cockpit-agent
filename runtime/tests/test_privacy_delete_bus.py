from __future__ import annotations

import json

import pytest

from runtime import privacy_delete_bus as bus


class FakeNats:
    def __init__(self):
        self.callbacks = {}
        self.published = []
        self.flushed = False

    async def subscribe(self, subject, queue, cb):
        assert queue == f"privacy-delete-{subject.rsplit('.', 1)[-1]}"
        self.callbacks[subject] = cb

    async def publish(self, subject, data):
        self.published.append((subject, data))

    async def flush(self, timeout):
        assert timeout == 1.0
        self.flushed = True


class Message:
    def __init__(self, data: bytes, reply: str = "_INBOX.privacy"):
        self.data = data
        self.reply = reply


KEY = b"mesh-private-key-material" * 4
NOW = 1_800_000_000
NONCE = "ab" * 16


def _request(*, user_id="user-1", target=bus.MCP_TARGET, now=NOW,
             nonce=NONCE):
    return bus.encode_delete_request(
        user_id,
        target=target,
        key=KEY,
        now=now,
        nonce=nonce,
    )


def _claims(payload: bytes, *, target=bus.MCP_TARGET, now=NOW,
            replay_guard=None):
    return bus.decode_delete_request(
        payload,
        expected_target=target,
        key=KEY,
        now=now,
        replay_guard=replay_guard,
    )


def test_delete_request_is_signed_target_bound_strict_bounded_json():
    payload = _request()
    claims = _claims(payload)
    assert claims.user_id == "user-1"
    assert claims.target == bus.MCP_TARGET
    assert claims.nonce == NONCE

    invalid = (
        b'{"action":"privacy_user_all","action":"privacy_user_all","user_id":"u"}',
        b'{"action":"wrong","user_id":"u"}',
        b'{"action":"privacy_user_all","user_id":"u","extra":1}',
        payload.replace(b'"user-1"', b'"user-2"'),
        payload.replace(b'"target":"mcp"', b'"target":"cloud"'),
        b"not-json",
        b"x" * (bus.MAX_REQUEST_BYTES + 1),
    )
    for candidate in invalid:
        with pytest.raises(bus.PrivacyDeleteProtocolError):
            _claims(candidate)


def test_delete_request_rejects_wrong_key_expiry_and_replay():
    payload = _request()
    with pytest.raises(bus.PrivacyDeleteProtocolError):
        bus.decode_delete_request(
            payload,
            expected_target=bus.MCP_TARGET,
            key=b"wrong-key" * 8,
            now=NOW,
        )
    with pytest.raises(bus.PrivacyDeleteProtocolError):
        _claims(payload, now=NOW + bus.MAX_CLOCK_SKEW_S + 1)

    guard = bus.ReplayGuard()
    assert _claims(payload, replay_guard=guard).user_id == "user-1"
    with pytest.raises(bus.PrivacyDeleteProtocolError, match="replay"):
        _claims(payload, replay_guard=guard)


def test_delete_response_binds_the_expected_responder():
    request_data = _request()
    request = _claims(request_data)
    payload = bus.encode_delete_response(
        ok=True,
        target=bus.MCP_TARGET,
        request=request,
        key=KEY,
        now=NOW,
    )
    assert bus.decode_delete_response(
        payload,
        expected_target=bus.MCP_TARGET,
        request_data=request_data,
        key=KEY,
        now=NOW,
    ) is True

    for candidate in (
        payload + b" ",
        payload.replace(b'"target":"mcp"', b'"target":"cloud"'),
        payload.replace(NONCE.encode(), ("cd" * 16).encode()),
        payload.replace(b'"ok":true', b'"ok":false'),
    ):
        with pytest.raises(bus.PrivacyDeleteProtocolError):
            bus.decode_delete_response(
                candidate,
                expected_target=bus.MCP_TARGET,
                request_data=request_data,
                key=KEY,
                now=NOW,
            )


def test_delete_response_rejects_forged_ack_and_response_for_other_request():
    request_data = _request()
    request = _claims(request_data)
    forged = json.dumps({
        "action": bus.DELETE_ACTION,
        "issued_at": NOW,
        "nonce": NONCE,
        "ok": True,
        "request_digest": request.request_digest,
        "signature": "00" * 32,
        "target": bus.MCP_TARGET,
    }, separators=(",", ":"), sort_keys=True).encode()
    with pytest.raises(bus.PrivacyDeleteProtocolError):
        bus.decode_delete_response(
            forged,
            expected_target=bus.MCP_TARGET,
            request_data=request_data,
            key=KEY,
            now=NOW,
        )

    response = bus.encode_delete_response(
        ok=True, target=bus.MCP_TARGET, request=request, key=KEY, now=NOW,
    )
    other_request = _request(nonce="cd" * 16)
    with pytest.raises(bus.PrivacyDeleteProtocolError):
        bus.decode_delete_response(
            response,
            expected_target=bus.MCP_TARGET,
            request_data=other_request,
            key=KEY,
            now=NOW,
        )


def test_control_key_is_domain_separated_and_missing_file_fails_closed(tmp_path):
    key_file = tmp_path / "server.key"
    key_file.write_bytes(KEY)
    derived = bus.load_control_key(environ={
        "PRIVACY_DELETE_KEY_FILE": str(key_file),
    })
    assert derived != KEY
    assert len(derived) == 32
    assert derived == bus.load_control_key(environ={
        "PRIVACY_DELETE_KEY_FILE": str(key_file),
    })

    with pytest.raises(bus.PrivacyDeleteProtocolError):
        bus.load_control_key(environ={
            "PRIVACY_DELETE_KEY_FILE": str(tmp_path / "missing.key"),
        })


@pytest.mark.asyncio
async def test_responder_without_global_backend_proof_never_subscribes():
    """One queue ACK cannot prove deletion from two process-local replicas."""
    replicas = (FakeNats(), FakeNats())

    async def delete(_user_id, _action):
        return True

    for nc in replicas:
        with pytest.raises(bus.PrivacyDeleteProtocolError, match="shared backend"):
            await bus.install_delete_responder(
                nc,
                subject=bus.MCP_SUBJECT,
                target=bus.MCP_TARGET,
                delete=delete,
                key=KEY,
            )
        assert nc.callbacks == {}


@pytest.mark.asyncio
async def test_responder_and_request_reject_crossed_subject_target_pairs():
    nc = FakeNats()

    async def delete(_user_id, _action):
        return True

    with pytest.raises(bus.PrivacyDeleteProtocolError):
        await bus.install_delete_responder(
            nc,
            subject=bus.CLOUD_SUBJECT,
            target=bus.MCP_TARGET,
            delete=delete,
            key=KEY,
        )
    with pytest.raises(bus.PrivacyDeleteProtocolError):
        await bus.request_delete(
            nc,
            subject=bus.CLOUD_SUBJECT,
            target=bus.MCP_TARGET,
            user_id="user-1",
            timeout=1,
            key=KEY,
        )


@pytest.mark.asyncio
async def test_responder_only_calls_delete_for_a_valid_request_and_fails_closed():
    calls = []

    async def delete(user_id, action):
        calls.append((user_id, action))
        return user_id != "fails"

    nc = FakeNats()
    assert await bus.install_delete_responder(
        nc,
        subject=bus.MCP_SUBJECT,
        target=bus.MCP_TARGET,
        delete=delete,
        globally_shared_backend=True,
        key=KEY,
        now=lambda: NOW,
    ) is True
    assert nc.flushed is True
    callback = nc.callbacks[bus.MCP_SUBJECT]

    await callback(Message(_request()))
    assert calls == [("user-1", bus.DELETE_ACTION)]
    assert bus.decode_delete_response(
        nc.published[-1][1], expected_target=bus.MCP_TARGET,
        request_data=_request(), key=KEY, now=NOW,
    ) is True

    published_before = len(nc.published)
    await callback(Message(b'{"action":"wrong","user_id":"user-1"}'))
    assert calls == [("user-1", bus.DELETE_ACTION)]
    # Malformed/unsigned frames cannot receive a request-bound reply.
    assert len(nc.published) == published_before

    failed_request = _request(user_id="fails", nonce="ef" * 16)
    await callback(Message(failed_request))
    assert bus.decode_delete_response(
        nc.published[-1][1], expected_target=bus.MCP_TARGET,
        request_data=failed_request, key=KEY, now=NOW,
    ) is False

    # Exact request replay is rejected before the adapter is called again.
    published_before = len(nc.published)
    await callback(Message(failed_request))
    assert calls == [("user-1", bus.DELETE_ACTION), ("fails", bus.DELETE_ACTION)]
    assert len(nc.published) == published_before


@pytest.mark.asyncio
async def test_responder_converts_adapter_exception_to_generic_negative_ack():
    async def explode(_user_id, _action):
        raise RuntimeError("must-not-leak")

    nc = FakeNats()
    await bus.install_delete_responder(
        nc,
        subject=bus.CLOUD_SUBJECT,
        target=bus.CLOUD_TARGET,
        delete=explode,
        globally_shared_backend=True,
        key=KEY,
        now=lambda: NOW,
    )
    request_data = _request(
        user_id="secret-user", target=bus.CLOUD_TARGET, nonce="12" * 16,
    )
    await nc.callbacks[bus.CLOUD_SUBJECT](Message(request_data))
    decoded = json.loads(nc.published[-1][1])
    assert decoded["action"] == bus.DELETE_ACTION
    assert decoded["ok"] is False
    assert decoded["target"] == bus.CLOUD_TARGET
    assert "secret-user" not in nc.published[-1][1].decode()
    assert "must-not-leak" not in nc.published[-1][1].decode()


@pytest.mark.asyncio
async def test_missing_control_key_never_subscribes_or_sends(monkeypatch, tmp_path):
    monkeypatch.setenv("PRIVACY_DELETE_KEY_FILE", str(tmp_path / "missing"))
    nc = FakeNats()

    with pytest.raises(bus.PrivacyDeleteProtocolError):
        await bus.install_delete_responder(
            nc,
            subject=bus.MCP_SUBJECT,
            target=bus.MCP_TARGET,
            delete=lambda _user, _action: True,
            globally_shared_backend=True,
        )
    assert nc.callbacks == {}

    with pytest.raises(bus.PrivacyDeleteProtocolError):
        await bus.request_delete(
            nc,
            subject=bus.MCP_SUBJECT,
            target=bus.MCP_TARGET,
            user_id="user-1",
            timeout=1,
        )
