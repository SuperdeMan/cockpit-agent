from server import _ensure_trace_id


class _Request:
    def __init__(self, meta):
        self.meta = meta


def test_preserves_frontend_trace_id():
    request = _Request({"trace_id": "front-123"})

    trace_id = _ensure_trace_id(request)

    assert trace_id == "front-123"
    assert request.meta["trace_id"] == "front-123"


def test_generates_trace_id_when_absent():
    request = _Request({})

    trace_id = _ensure_trace_id(request)

    assert trace_id
    assert request.meta["trace_id"] == trace_id
