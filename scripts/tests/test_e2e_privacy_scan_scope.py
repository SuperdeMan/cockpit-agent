"""隐私清单扫描不得走进「同一个仓库的另一份 checkout」（守卫）。

2026-08-03 实证：本机 `.claude/worktrees/intent-adversarial/` 下留着一份完整 checkout
（Claude Code 的 worktree 就建在那儿），`_static_privacy_candidates` 把 `runtime/privacy_registry.py`
读了两遍，抛出 `duplicate privacy candidate entries: [...]` —— **报错文案听起来像隐私登记表
自己出了严重问题，真实原因只是磁盘上有第二个 checkout**。连带 `scripts/tests/` 与
`test/test_remaining_e2e_protocol.py` 共 **33 条**契约测试凭空变红，并被记进文档当成
「既有 e2e 运行器的环境路径问题」——又一例「失败被记成了别的东西」。

排除表原本只有 `.worktrees`（带点），而 worktree 实际落在 `.claude/worktrees/`。
**判据：排除名单要按东西实际落在哪写，不是按它「应该」落在哪写。**

⚠ 这条守卫先反验过：把 `_PRIVACY_EXCLUDED_DIRS` 恢复成修复前的集合，本文件三条断言
全部变红（`ManifestError: duplicate privacy candidate entries`）。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "scripts" / "e2e_contract.py"

# 与 test_e2e_manifest.py 的 `_candidate_source` 同形，刻意不 import 那个 1700 行的文件。
_SOURCE = (
    "PERSONAL_DATA_TARGETS = (\n"
    "    {'id': 'memory_item', 'storage_variants': ('memory_item',)},\n"
    ")\n"
)


def _contract():
    assert CONTRACT_PATH.is_file(), "scripts/e2e_contract.py must define the manifest contract"
    return importlib.import_module("scripts.e2e_contract")


def _write(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SOURCE, encoding="utf-8")


@pytest.mark.parametrize(
    "nested",
    [
        # 真实布局：Claude Code 的 EnterWorktree 建在这里
        ".claude/worktrees/intent-adversarial/runtime/privacy_registry.py",
        # 历史布局：排除表原本只认得这一种
        ".worktrees/some-branch/runtime/privacy_registry.py",
        # 不带点的 worktrees/ 也要挡住——同一件事换个目录名不该换结论
        "worktrees/some-branch/runtime/privacy_registry.py",
    ],
)
def test_nested_repo_checkout_is_not_scanned_twice(tmp_path: Path, nested: str) -> None:
    contract = _contract()
    _write(tmp_path, "runtime/privacy_registry.py")
    _write(tmp_path, nested)

    got = contract._static_privacy_candidates(tmp_path, "PERSONAL_DATA_TARGETS")

    assert [candidate.id for candidate in got] == ["memory_item"], (
        f"{nested} 被当成了第二份隐私清单——扫描走进了同一个仓库的另一份 checkout"
    )


@pytest.mark.parametrize("marker_is_dir", [True, False])
def test_any_directory_carrying_dot_git_is_treated_as_another_checkout(
    tmp_path: Path,
    marker_is_dir: bool,
) -> None:
    """第二道闸：不靠目录名，靠「这个子目录自带 `.git`」——那是 checkout 的定义。

    名字清单必然滞后于布局（本次就是：写好了 `.worktrees`，真实是 `.claude/worktrees/`）。
    `marker_is_dir=True` 模拟嵌套 clone（`.git/` 目录），`False` 模拟 worktree（`.git` 文件）。
    """
    contract = _contract()
    _write(tmp_path, "runtime/privacy_registry.py")
    _write(tmp_path, "vendor/mirror/runtime/privacy_registry.py")
    marker = tmp_path / "vendor" / "mirror" / ".git"
    if marker_is_dir:
        marker.mkdir()
    else:
        marker.write_text("gitdir: ../../.git/worktrees/mirror\n", encoding="utf-8")

    got = contract._static_privacy_candidates(tmp_path, "PERSONAL_DATA_TARGETS")

    assert [candidate.id for candidate in got] == ["memory_item"], (
        "vendor/mirror 自带 .git，是另一份 checkout，不该被当成第二份隐私清单"
    )
