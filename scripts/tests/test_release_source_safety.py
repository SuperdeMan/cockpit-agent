"""发布安全闸的**提前门禁**：仓库源码此刻就要能过 `validate_text_payload`。

为什么需要它（2026-09-09 实测代价）：`cloud_release_lib` 的凭证/私钥扫描只在
`dev_stack deploy` 那一刻跑，而 deploy 是发布日才做的动作。AR05 批里
`gateway/edge/main.go` 写了一行

    token = strings.TrimSpace(strings.TrimPrefix(h, "Bearer "))

——它不是凭证，是从请求头解析 token 的代码，但逐字命中
`CREDENTIAL_ASSIGNMENT_RE`（行首 `TOKEN\\s*=` + 16 字符以上的值）。于是整批代码写完、
测完、推完之后，deploy 才在 `safety_rejected` 上 fail closed，而错误输出**刻意不带路径**
（不泄露疑似凭证的内容），只能反过来逐文件比对才找得到。

闸是 fail-closed 的规格，不该为了让代码跑起来去放宽它；该做的是让这条判据在 CI 就说话。
本测试与 deploy 走**同一个函数**，不复制判据——复制一份就会在下一次改闸时漂移。
"""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cloud_release_lib import ReleaseError, validate_text_payload  # noqa: E402

#: 会进 release 源码包的文本类型。二进制与生成物不在此列（`gen/` 本就 gitignore）。
_TEXT_SUFFIXES = {
    ".py", ".go", ".ts", ".tsx", ".js", ".mjs", ".jsx",
    ".yaml", ".yml", ".json", ".proto", ".sh", ".ps1", ".md", ".toml", ".cfg",
    ".sql", ".env.example", ".gradle", ".kt", ".java", ".html", ".css",
}


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True,
    ).stdout.decode("utf-8")
    files = []
    for name in out.split("\0"):
        if not name:
            continue
        path = ROOT / name
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        if not path.is_file():
            continue
        files.append(path)
    return files


def test_every_tracked_source_file_passes_the_release_safety_scan():
    """本仓库当前 HEAD 的源码必须能通过发布期的凭证/私钥扫描。

    红了的正确处置是**改代码**（换个不像凭证赋值的写法），不是放宽扫描——
    那条扫描存在的全部意义就是它比人可靠。
    """
    offenders: list[str] = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue     # 非 UTF-8 文本按二进制处理，发布期的成员名判据另有守卫
        try:
            validate_text_payload(
                text, source_path=path.relative_to(ROOT).as_posix())
        except ReleaseError as exc:
            offenders.append(f"{path.relative_to(ROOT).as_posix()}: {exc}")
    assert offenders == [], (
        "这些文件会让 `dev_stack deploy` 在 safety_rejected 上 fail closed；"
        "deploy 的错误输出不带路径（防泄露），所以必须在这里就说清是谁：\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_actually_rejects_a_credential_shaped_line():
    """反向守卫：上面那条不能因为扫描退化成恒真而静默变绿。"""
    with pytest.raises(ReleaseError):
        validate_text_payload(
            'token = strings.TrimSpace(strings.TrimPrefix(h, "Bearer "))\n',
            source_path="gateway/edge/main.go",
        )


def test_strict_placeholders_are_still_allowed():
    """占位符仍要放行——否则 `.env.example` 之类会被一起判红。"""
    validate_text_payload("TOKEN=placeholder\n", source_path="deploy/x.env.example")
