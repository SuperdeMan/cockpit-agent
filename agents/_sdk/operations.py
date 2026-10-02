"""CA2-08 receiver-side durable admission for the SDK Agent servicer.

The loop is shared with the vehicle executor since CA2-11 and lives in
``runtime.operation_gate``; this module keeps the SDK import path.
"""
from runtime.operation_gate import (  # noqa: F401
    DEFAULT_AWAIT_TTL_S, DEFAULT_EXECUTING_STALE_S, NOOP, OperationGate, admit, await_ttl_s,
    executing_stale_s,
)
