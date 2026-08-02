"""E2E/脚本层禁止写死 compose 容器名（守卫）。

容器名 `<project>-<service>-<n>` 的 project 段派生自启动目录名：本地是
`car-agent`，CI checkout 是 `cockpit-agent`——写死任何一个都会在另一边必然
失败（nightly run #33 的根因，e2e_research_async 因此在 CI 上恒红）。栈内
服务一律经 `test/support/e2e.py` 的 `compose_argv`/`compose_exec_argv`/
`postgres_psql_argv` 按 service 名寻址。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD_NAME = Path(__file__).name

# project 段只钉已知会出现的两个派生名；新增第三个派生名之前先想想为什么会有它。
CONTAINER_LITERAL = re.compile(r"(?:car|cockpit)-agent-[a-z0-9-]+-\d+")


def _sources() -> list[Path]:
    files: list[Path] = []
    for root in ("test", "scripts"):
        files.extend((REPO_ROOT / root).rglob("*.py"))
    return [
        path
        for path in files
        if "__pycache__" not in path.parts and path.name != GUARD_NAME
    ]


def test_no_hardcoded_compose_container_names():
    offenders: dict[str, list[str]] = {}
    for path in _sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = sorted(set(CONTAINER_LITERAL.findall(text)))
        if hits:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = hits
    assert not offenders, (
        "compose 容器名不可写死（项目名派生自启动目录，换个 checkout 目录名就会失败）；"
        f"改用 support.e2e 的 compose_argv/compose_exec_argv 按 service 名寻址：{offenders}"
    )
