from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts" / "run_go_tests.ps1"


def test_wrapper_uses_read_only_bookworm_copy_tidy_and_default_package():
    assert WRAPPER.is_file(), "Go test wrapper is missing"
    source = WRAPPER.read_text(encoding="utf-8")
    assert "golang:1.24-bookworm" in source
    assert ":/src:ro" in source
    assert "Copy-Item" not in source
    assert "cp -a /src/. /work/" in source
    assert "cp -a /src /work" not in source
    assert "-w /work" in source
    assert "cd /work && go mod tidy" in source
    assert "go mod tidy" in source
    assert "go test" in source
    # 命令串必须是 `go test "<引号>$@<引号>"`，且引号形态按 **PowerShell 的传参方式**
    # 分支——不是按操作系统。Legacy（Windows PowerShell 5.1）要写 `\"` 才能让 docker
    # 收到 `"`；Standard（pwsh 7 非 Windows 默认）逐字传参，必须写真引号，否则反斜杠
    # 会原样进容器、`go test` 收到带引号的包名。此前只有 `\"` 一种写法，
    # **于是这条 wrapper 在 Linux 上一直是坏的**（CI argv 比对红的根因）。
    assert "go test $quote`$@$quote" in source
    assert "PSNativeCommandArgumentPassing" in source
    legacy_quote = "'" + "\\" + '"' + "'"          # 源码里的 '\"'
    standard_quote = "'" + '"' + "'"               # 源码里的 '"'
    assert legacy_quote in source, "Legacy 传参分支不见了"
    assert standard_quote in source, "Standard 传参分支不见了"
    assert "$packageText" not in source
    assert "'./...'" in source or '"./..."' in source


def test_wrapper_hashes_go_mod_and_sum_before_and_after():
    assert WRAPPER.is_file(), "Go test wrapper is missing"
    source = WRAPPER.read_text(encoding="utf-8")
    assert source.count("Get-FileHash") >= 2
    assert "go.mod" in source
    assert "go.sum" in source
    assert "changed" in source.lower()


def _powershell() -> str:
    r"""解析当前平台的 PowerShell 可执行文件。

    此前硬编码 `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`
    ——那在 Ubuntu 上直接 `KeyError: 'SystemRoot'`（CI 装的是跨平台的 `pwsh`）。
    **一条只能在一个平台跑的测试，写死那个平台的路径就等于在别处必红。**
    解析不到就 skip：本仓库既有惯例（同 `test_e2e_wrappers_ci._powershell`），
    诚实跳过好过假装通过，也好过让整组红掉。
    """
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT")
        if system_root:
            candidate = (Path(system_root) / "System32"
                         / "WindowsPowerShell" / "v1.0" / "powershell.exe")
            if candidate.is_file():
                return str(candidate)
        found = shutil.which("powershell.exe") or shutil.which("pwsh")
    else:
        found = shutil.which("pwsh") or shutil.which("powershell")
    if not found:
        pytest.skip("PowerShell runtime unavailable on this platform")
    return found


def test_wrapper_fails_nonzero_when_docker_is_unavailable():
    assert WRAPPER.is_file(), "Go test wrapper is missing"
    powershell = _powershell()
    env = dict(os.environ)
    env["PATH"] = ""
    completed = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(WRAPPER),
            "./gateway/edge",
        ],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    assert completed.returncode != 0


def _run_with_fake_docker(tmp_path, packages):
    capture = tmp_path / "docker-argv.json"
    fake = tmp_path / "fake_docker.py"
    fake.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['FAKE_DOCKER_CAPTURE']).write_text("
        "json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    # 假 docker 要按平台造：此前只写 `docker.cmd`（Windows 批处理），
    # **Linux 上它根本不会被当成可执行的 docker**，于是 wrapper 打到了真 docker
    # ——CI 上表现为 `go: downloading ...` 与 pwsh 超时。不是断言错了，
    # 是这条用例在那儿根本没被隔离起来跑。
    if os.name == "nt":
        shim = tmp_path / "docker.cmd"
        shim.write_text(
            '@"' + sys.executable + '" "' + str(fake) + '" %*\r\n',
            encoding="utf-8",
        )
    else:
        shim = tmp_path / "docker"
        shim.write_text(
            "#!/bin/sh\nexec \"" + sys.executable + "\" \"" + str(fake) + "\" \"$@\"\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)
    powershell = _powershell()
    env = dict(os.environ)
    env["PATH"] = str(tmp_path) + os.pathsep + env.get("PATH", "")
    env["FAKE_DOCKER_CAPTURE"] = str(capture)
    completed = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(WRAPPER),
            *packages,
        ],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    argv = (
        json.loads(capture.read_text(encoding="utf-8"))
        if capture.is_file()
        else None
    )
    return completed, argv


@pytest.mark.parametrize(
    "packages",
    [
        ["-exec", "/bin/true"],
        ["./gateway/edge;true"],
        ["./gateway/edge other"],
        ["../gateway/edge"],
        ["/gateway/edge"],
        ["C:/gateway/edge"],
    ],
)
def test_wrapper_rejects_non_repository_package_patterns_before_docker(
    tmp_path,
    packages,
):
    completed, argv = _run_with_fake_docker(tmp_path, packages)
    assert completed.returncode != 0
    assert argv is None


def test_wrapper_passes_legal_multiple_packages_as_separate_shell_positionals(
    tmp_path,
):
    completed, argv = _run_with_fake_docker(
        tmp_path,
        [".", "./gateway/edge", "./gateway/cloud/..."],
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8",
        errors="replace",
    )
    assert argv is not None
    shell_index = argv.index("sh")
    assert argv[shell_index:] == [
        "sh",
        "-c",
        'cp -a /src/. /work/ && cd /work && go mod tidy && go test "$@"',
        "sh",
        ".",
        "./gateway/edge",
        "./gateway/cloud/...",
    ]


def test_wrapper_default_package_is_separate_dot_slash_ellipsis(tmp_path):
    completed, argv = _run_with_fake_docker(tmp_path, [])
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8",
        errors="replace",
    )
    assert argv[-1] == "./..."
