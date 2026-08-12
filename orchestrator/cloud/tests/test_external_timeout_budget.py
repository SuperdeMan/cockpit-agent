"""External-agent timeout declarations consumed by the cloud planner."""

from pathlib import Path

from agents._sdk.manifest import load_manifest
from orchestrator.cloud.planning import PlanBuilder


_REPO_ROOT = Path(__file__).resolve().parents[3]


class _RegisteredAgent:
    def __init__(self, manifest_path: Path):
        self.manifest = load_manifest(str(manifest_path))
        self.endpoint = "nearby:50051"


def test_nearby_search_budget_covers_provider_retry_envelope():
    """The outer DAG deadline must not pre-empt nearby's bounded HTTP retries.

    ``AsyncHttpClient`` allows two attempts.  With an egress proxy, each
    attempt may spend 3 s on the proxy and another 3 s on its direct fallback,
    plus 150 ms backoff: about 12.15 s before gRPC overhead.  The manifest is
    the timeout authority, and PlanBuilder must preserve a budget above that
    envelope instead of falling back to the historic 4 s declaration.
    """
    registered = _RegisteredAgent(
        _REPO_ROOT / "agents" / "nearby" / "manifest.yaml",
    )

    steps = PlanBuilder._validated_steps(
        [{
            "id": "s1",
            "agent_id": "nearby",
            "intent": "nearby.search",
            "slots": {"keyword": "瑞幸咖啡"},
            "depends_on": [],
            "slot_refs": {},
        }],
        {"nearby": registered},
    )

    assert len(steps) == 1
    assert steps[0].latency_budget_ms == registered.manifest.latency_budget_ms
    assert steps[0].latency_budget_ms >= 15_000
