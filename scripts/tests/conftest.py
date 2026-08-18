"""Keep unit tests blind to the operator's cloud connection settings.

The cloud CLIs default ``--host`` / ``--user`` / ``--identity`` /
``--kex-algorithms`` from ``CAR_AGENT_*`` environment variables. That is
correct for the CLIs. It is wrong for the rulers: a developer who exports
them -- precisely the developer working against ``target=cloud`` -- would
otherwise watch unit tests change colour, while CI, which never sets them,
stays green. Measured on 2026-08-18: exporting the three connection
variables turns four ``scripts/tests`` cases red without touching any code.

Same family as the ``dev-stack.local`` leak (RC14): a ruler must not read the
operator's deployment choice. The list below is deliberately hand-written --
``test_cloud_connection_defaults_stay_hidden_from_unit_tests`` compares it
against the names actually read by ``scripts/*.py``, so adding a fifth
variable in one place and not the other is a red.
"""
from __future__ import annotations

import pytest

OPERATOR_CLOUD_VARIABLES = frozenset(
    {
        "CAR_AGENT_DEPLOY_HOST",
        "CAR_AGENT_DEPLOY_USER",
        "CAR_AGENT_SSH_IDENTITY",
        "CAR_AGENT_SSH_KEX_ALGORITHMS",
    }
)


@pytest.fixture(autouse=True)
def _hide_operator_cloud_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in sorted(OPERATOR_CLOUD_VARIABLES):
        monkeypatch.delenv(name, raising=False)
