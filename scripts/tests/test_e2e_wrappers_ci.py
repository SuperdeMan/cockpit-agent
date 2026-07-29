from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PS_WRAPPER = ROOT / "scripts" / "run_e2e.ps1"
SH_WRAPPER = ROOT / "scripts" / "run_e2e.sh"
MAKEFILE = ROOT / "Makefile"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
NIGHTLY_WORKFLOW = ROOT / ".github" / "workflows" / "nightly-e2e.yml"


def _last_json(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    for line in reversed(completed.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    pytest.fail(
        "wrapper did not emit a final JSON object\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )


def _run(command: list[str]) -> tuple[int, dict[str, object]]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
    )
    return completed.returncode, _last_json(completed)


def _powershell() -> str:
    if os.name == "nt":
        executable = shutil.which("powershell.exe")
    else:
        executable = shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell runtime unavailable locally; Linux CI installs and gates it")
    return executable


def test_wrappers_are_semantics_free_argument_and_exit_code_forwarders():
    ps_source = PS_WRAPPER.read_text(encoding="utf-8")
    sh_source = SH_WRAPPER.read_text(encoding="utf-8")

    assert "scripts/run_e2e.py" in ps_source
    assert "@args" in ps_source
    assert "$LASTEXITCODE" in ps_source
    assert re.search(r"\bexit\b", ps_source, re.IGNORECASE)
    assert "$steps" not in ps_source
    assert "Invoke-WebRequest" not in ps_source
    assert "test/e2e_" not in ps_source

    assert "scripts/run_e2e.py" in sh_source
    assert '"$@"' in sh_source
    assert re.search(r"\bexec\s+python(?:3)?\b", sh_source)
    assert "run_step" not in sh_source
    assert "declare -a" not in sh_source
    assert "COLLECTOR_HEALTHZ" not in sh_source
    assert "test/e2e_" not in sh_source


@pytest.mark.parametrize(
    ("args", "expected_rc"),
    [
        (["--dry-run", "--group", "default"], 0),
        (["--dry-run", "--id", "__missing_wrapper_contract__"], 2),
    ],
)
def test_python_and_powershell_preserve_final_json_selection_argv_and_rc(
    args: list[str],
    expected_rc: int,
):
    python_rc, python_json = _run([sys.executable, "scripts/run_e2e.py", *args])
    ps_rc, ps_json = _run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(PS_WRAPPER),
            *args,
        ]
    )

    assert python_rc == ps_rc == expected_rc
    assert python_json == ps_json
    assert python_json["selection"] == ps_json["selection"]


def test_make_e2e_targets_only_delegate_to_the_manifest_runner():
    source = MAKEFILE.read_text(encoding="utf-8")

    assert "python scripts/run_e2e.py --full" in source
    assert "python scripts/run_e2e.py --check --stale-policy warn" in source
    assert (
        "python scripts/run_e2e.py --lane ci --full --stale-policy warn"
        in source
    )
    assert (
        "python scripts/run_e2e.py --lane nightly --full --stale-policy warn"
        in source
    )
    assert (
        "python scripts/run_e2e.py --milestone $(MILESTONE) --lane milestone "
        "--full --canonical --provider $(PROVIDER) --model $(MODEL) "
        "--stale-policy error"
        in source.replace("\\\n", "")
    )
    assert "test/e2e_" not in source
    assert "run_step" not in source


def test_pr_ci_has_fixed_structure_and_execution_gates_plus_wrapper_parity():
    source = CI_WORKFLOW.read_text(encoding="utf-8")

    assert (
        "run: python scripts/run_e2e.py --check --stale-policy warn" in source
    )
    assert (
        "run: python scripts/run_e2e.py --lane ci --full --stale-policy warn"
        in source
    )
    assert "python scripts/run_e2e.py --dry-run --group default" in source
    assert "pwsh -NoProfile -NonInteractive -File scripts/run_e2e.ps1" in source
    assert "bash scripts/run_e2e.sh --dry-run --group default" in source
    assert "apt-get install -y powershell" in source
    assert "wrapper-parity" in source
    assert "compare_wrapper_outputs.py" not in source
    assert "docker compose" not in _workflow_job(source, "e2e-contract")


def test_nightly_uses_one_runner_selection_and_uploads_its_json_summary():
    source = NIGHTLY_WORKFLOW.read_text(encoding="utf-8")

    command = (
        "python scripts/run_e2e.py --lane nightly --full "
        "--stale-policy warn"
    )
    assert source.count(command) == 1
    assert "run_e2e()" not in source
    assert "run_e2e test/e2e_" not in source
    assert "nightly-e2e-summary.json" in source
    assert "actions/upload-artifact@v4" in source
    assert "docker compose -f compose.yaml" in source
    assert "--canonical" not in source


def _workflow_job(source: str, job_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        source,
    )
    assert match is not None, f"workflow job missing: {job_name}"
    return match.group(0)
