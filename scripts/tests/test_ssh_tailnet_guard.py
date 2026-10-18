"""公网 SSH 闸（deploy/host/ssh_tailnet_guard.py，2026-10-18）：22 端口只收回环 / tailnet 进来的新连接，Tailscale 不健康就撤闸。"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "deploy" / "host"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load("ssh_tailnet_guard", HOST / "ssh_tailnet_guard.py")

STATUS_OK = {"BackendState": "Running", "Self": {"TailscaleIPs": ["100.69.183.124", "fd7a:115c:a1e0::6801:b7ca"]}}


class _Host:
    """假主机：`tailscale status --json` 的回包与 nft 表在不在；记下每条命令。"""

    def __init__(self, status=STATUS_OK, status_code: int = 0, table: bool = False, nft_fails: bool = False) -> None:
        self.status = status
        self.status_code = status_code
        self.table = table
        self.nft_fails = nft_fails
        self.calls: list[list[str]] = []
        self.applied: list[str] = []

    def __call__(self, argv, data=None):
        self.calls.append(list(argv))
        if list(argv) == ["tailscale", "status", "--json"]:
            out = self.status if isinstance(self.status, str) else json.dumps(self.status)
            return self.status_code, out
        if list(argv) == ["nft", "list", "table", "inet", "ssh_tailnet_guard"]:
            return (0, "table inet ssh_tailnet_guard {}\n") if self.table else (1, "")
        if list(argv) == ["nft", "-f", "-"]:
            if self.nft_fails:
                return 1, ""
            self.applied.append(data.decode())
            self.table = True
            return 0, ""
        if list(argv) == ["nft", "delete", "table", "inet", "ssh_tailnet_guard"]:
            if self.nft_fails:
                return 1, ""
            self.table = False
            return 0, ""
        raise AssertionError(argv)

    def writes(self) -> list[list[str]]:
        return [call for call in self.calls if call[:2] == ["nft", "-f"] or call[:2] == ["nft", "delete"]]


@pytest.fixture
def iface(tmp_path: Path) -> Path:
    path = tmp_path / "tailscale0"
    path.mkdir()
    return path


def test_healthy_tailnet_puts_the_guard_up_once(iface: Path):
    host = _Host(table=False)
    first = guard.check(host, iface)
    assert (first["action"], first["guard"], first["tailnet_healthy"]) == ("applied", True, True)
    assert host.applied == [guard.RULESET]
    second = guard.check(host, iface)
    assert (second["action"], second["guard"]) == ("unchanged", True)
    assert len(host.applied) == 1


@pytest.mark.parametrize(("status", "status_code", "reason"), [
    (STATUS_OK, 1, "tailscale status failed (1)"),
    ("not json", 0, "tailscale status unparsable"),
    ({"BackendState": "NeedsLogin", "Self": {"TailscaleIPs": ["100.69.183.124"]}}, 0, "backend NeedsLogin"),
    ({"BackendState": "Stopped"}, 0, "backend Stopped"),
    ([], 0, "backend None"),
    ({"BackendState": "Running", "Self": {"TailscaleIPs": ["fd7a:115c:a1e0::6801:b7ca"]}}, 0, "no tailnet IPv4"),
    ({"BackendState": "Running"}, 0, "no tailnet IPv4"),
])
def test_unhealthy_tailnet_takes_the_guard_down(iface: Path, status, status_code, reason):
    """fail-open：Tailscale 接不住 SSH 时公网 22 必须回到原样，否则 Tailscale 一坏就只剩控制台 VNC。"""
    host = _Host(status=status, status_code=status_code, table=True)
    record = guard.check(host, iface)
    assert (record["action"], record["guard"], record["tailnet_healthy"], record["reason"]) == (
        "removed", False, False, reason)
    assert host.table is False
    # 闸本来就不在：什么都不写
    idle = _Host(status=status, status_code=status_code, table=False)
    assert guard.check(idle, iface)["action"] == "unchanged"
    assert idle.writes() == []


def test_missing_tailnet_interface_is_unhealthy_without_asking_tailscale(tmp_path: Path):
    host = _Host(table=True)
    record = guard.check(host, tmp_path / "tailscale0")
    assert (record["action"], record["reason"]) == ("removed", "no tailscale0 interface")
    assert ["tailscale", "status", "--json"] not in host.calls


def test_dry_run_reports_without_touching_nft(iface: Path):
    host = _Host(table=False)
    record = guard.check(host, iface, dry_run=True)
    assert (record["action"], record["dry_run"]) == ("applied", True)
    assert host.writes() == [] and host.table is False


def test_nft_failure_fails_the_run(iface: Path, capsys):
    host = _Host(table=False, nft_fails=True)
    assert guard.main(["check"], run=host, iface=iface) == 1
    assert json.loads(capsys.readouterr().out) == {"schema_version": 1, "error": "nft apply failed (1)"}


def test_quiet_only_when_healthy_and_unchanged(iface: Path, tmp_path: Path, capsys):
    assert guard.main(["check"], run=_Host(table=True), iface=iface) == 0
    assert capsys.readouterr().out == ""
    assert guard.main(["check"], run=_Host(table=False), iface=tmp_path / "absent") == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "no tailscale0 interface"
    assert guard.main(["check"], run=_Host(table=False), iface=iface) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "applied"


def test_operator_remove_and_status(iface: Path, capsys):
    host = _Host(table=True)
    assert guard.main(["status"], run=host, iface=iface) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1, "tailnet_healthy": True, "reason": "running", "guard": True, "action": "none"}
    assert guard.main(["remove"], run=host, iface=iface) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "removed_by_operator"
    assert host.table is False
    assert guard.main(["remove"], run=host, iface=iface) == 0   # 已经不在：不报错、不再删
    assert host.writes() == [["nft", "delete", "table", "inet", "ssh_tailnet_guard"]]


def test_ruleset_drops_only_new_ssh_connections_from_outside_the_tailnet():
    lines = guard.RULESET.splitlines()
    # 先 add 再 delete 再建：同一事务里幂等替换，不影响别的表
    assert lines[:2] == ["table inet ssh_tailnet_guard", "delete table inet ssh_tailnet_guard"]
    assert "\t\ttype filter hook input priority -10; policy accept;" in lines
    rules = [line.strip() for line in lines if "drop" in line or "accept" in line.split(";")[-1]]
    assert rules == ['tcp dport 22 ct state new iifname != { "lo", "tailscale0" } counter drop']
    assert guard.RULESET.count("drop") == 1 and "flush" not in guard.RULESET


def test_guard_touches_nothing_but_its_own_table():
    source = (HOST / "ssh_tailnet_guard.py").read_text(encoding="utf-8")
    commands = set(re.findall(r'\["(\w+)", "([\w-]+)"', source))
    assert commands == {("tailscale", "status"), ("nft", "list"), ("nft", "-f"), ("nft", "delete")}
    code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())   # 注释里提到回滚命令不算
    for forbidden in ("iptables", "ufw", "systemctl", "flush ruleset", "sshd_config", "shutil", "os.remove"):
        assert forbidden not in code, forbidden


def test_units_and_readme_describe_the_same_installation():
    service = (HOST / "ssh-tailnet-guard.service").read_text(encoding="utf-8")
    timer = (HOST / "ssh-tailnet-guard.timer").read_text(encoding="utf-8")
    readme = (HOST / "README.md").read_text(encoding="utf-8")

    assert "Type=oneshot\n" in service and "After=tailscaled.service\n" in service
    assert "ExecStart=/usr/bin/python3 -I /usr/local/sbin/ssh-tailnet-guard check\n" in service
    for line in ("OnBootSec=1min\n", "OnUnitActiveSec=1min\n", "Unit=ssh-tailnet-guard.service\n", "WantedBy=timers.target\n"):
        assert line in timer
    rows = dict(re.findall(r"^\| `deploy/host/([^`]+)` \| `([^`]+)` \|", readme, re.M))
    assert {name: rows.get(name) for name in ("ssh_tailnet_guard.py", "ssh-tailnet-guard.service", "ssh-tailnet-guard.timer")} == {
        "ssh_tailnet_guard.py": "/usr/local/sbin/ssh-tailnet-guard",
        "ssh-tailnet-guard.service": "/etc/systemd/system/ssh-tailnet-guard.service",
        "ssh-tailnet-guard.timer": "/etc/systemd/system/ssh-tailnet-guard.timer",
    }
    # 安装先排自动回滚、验收通过才开 timer
    install = readme[readme.index("### 安装（系统配置，需授权）"):]
    assert install.index("ssh-tailnet-guard-rollback") < install.index("systemctl start ssh-tailnet-guard.service")
    assert install.index("systemctl stop ssh-tailnet-guard-rollback.timer") < install.index(
        "systemctl enable --now ssh-tailnet-guard.timer")
