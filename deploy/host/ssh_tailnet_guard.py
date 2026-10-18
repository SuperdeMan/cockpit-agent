#!/usr/bin/env python3
"""公网 SSH 闸：22 端口只收回环与 Tailscale（`tailscale0`）进来的新连接；Tailscale 不健康时自动撤闸（fail-open）。

为什么在主机上做（2026-10-18，说明见同目录 README「公网 SSH 闸」）：
- 公网 22 端口一直被互联网扫描器敲：10-13 → 10-18 每天被 sshd 节流丢掉的未认证连接 75–585 次；
- 三周里所有成功登录都是同一把部署钥匙，10-09 之后全部来自 tailnet；car-agent 与 drone-agent 都按 Tailscale 名字连；
- 云上的安全组本机没有凭证改，于是在主机内核里丢掉不是从回环 / tailnet 来的 22 端口新连接。

fail-open：timer 每分钟跑一次 `check`。Tailscale 不健康（没有 tailscale0 网卡、`tailscale status` 失败、后端不是 Running、
本机没有 100.x 地址）就撤闸，公网 22 回到原样（只认钥匙）——Tailscale 坏了照样能用公网地址登进来修；恢复健康的下一轮重新上闸。
只认「新连接」：撤闸期间从公网登进来的会话，重新上闸后不会被掐断。
只管 22 端口、只动自己这一张 nft 表，不碰 Docker、Tailscale、主机安全（YJ-FIREWALL）各自的规则。
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

TABLE = "ssh_tailnet_guard"
FAMILY = "inet"
SSH_PORT = 22
#: 放行的入站网卡：回环与 Tailscale 的内核网卡。其余（公网网卡、docker 网桥…）来的 22 端口新连接一律丢弃。
ALLOWED_IFACES = ("lo", "tailscale0")
TAILNET_IFACE = Path("/sys/class/net/tailscale0")
TIMEOUT_S = 15

_IFACE_SET = ", ".join('"' + name + '"' for name in ALLOWED_IFACES)
#: 先 add、再 delete、再建：同一份事务里幂等地换成这份规则（表原来在不在都一样）。优先级 -10 早于 iptables-nft 的
#: filter 链（0）；inet 族同时管 IPv4 / IPv6。
RULESET = (
    f"table {FAMILY} {TABLE}\n"
    f"delete table {FAMILY} {TABLE}\n"
    f"table {FAMILY} {TABLE} {{\n"
    "\tchain input {\n"
    "\t\ttype filter hook input priority -10; policy accept;\n"
    f"\t\ttcp dport {SSH_PORT} ct state new iifname != {{ {_IFACE_SET} }} counter drop\n"
    "\t}\n"
    "}\n"
)


class GuardError(RuntimeError):
    pass


def _run(argv: list[str], data: bytes | None = None) -> tuple[int, str]:
    """跑一条命令 → (退出码, 标准输出)。命令不存在或超时记成 127，不抛。"""
    try:
        proc = subprocess.run(argv, input=data, capture_output=True, timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def tailnet_health(run=_run, iface: Path = TAILNET_IFACE) -> tuple[bool, str]:
    """Tailscale 此刻接不接得住 SSH：有 tailscale0 网卡、后端 Running、本机有 100.x 地址。"""
    if not iface.exists():
        return False, "no tailscale0 interface"
    code, out = run(["tailscale", "status", "--json"])
    if code != 0:
        return False, f"tailscale status failed ({code})"
    try:
        status = json.loads(out)
    except ValueError:
        return False, "tailscale status unparsable"
    if not isinstance(status, dict) or status.get("BackendState") != "Running":
        state = status.get("BackendState") if isinstance(status, dict) else None
        return False, f"backend {state}"
    me = status.get("Self")
    ips = me.get("TailscaleIPs") if isinstance(me, dict) else None
    if not any(isinstance(ip, str) and ip.startswith("100.") for ip in (ips or [])):
        return False, "no tailnet IPv4"
    return True, "running"


def guard_present(run=_run) -> bool:
    code, _ = run(["nft", "list", "table", FAMILY, TABLE])
    return code == 0


def apply_guard(run=_run) -> None:
    code, _ = run(["nft", "-f", "-"], RULESET.encode())
    if code != 0:
        raise GuardError(f"nft apply failed ({code})")


def remove_guard(run=_run) -> None:
    code, _ = run(["nft", "delete", "table", FAMILY, TABLE])
    if code != 0:
        raise GuardError(f"nft delete failed ({code})")


def check(run=_run, iface: Path = TAILNET_IFACE, dry_run: bool = False) -> dict:
    """一轮：健康就保证闸在，不健康就保证闸不在。返回这一轮的记录（`action` = applied / removed / unchanged）。"""
    healthy, reason = tailnet_health(run, iface)
    present = guard_present(run)
    action = "unchanged"
    if healthy and not present:
        action = "applied"
        if not dry_run:
            apply_guard(run)
    elif not healthy and present:
        action = "removed"
        if not dry_run:
            remove_guard(run)
    guard = present if action == "unchanged" else action == "applied"
    return {"schema_version": 1, "tailnet_healthy": healthy, "reason": reason, "guard": guard,
            "action": action, "dry_run": dry_run}


def main(argv: list[str] | None = None, run=_run, iface: Path = TAILNET_IFACE) -> int:
    parser = argparse.ArgumentParser(description="Public SSH guard: port 22 only over the tailnet; fail-open.")
    parser.add_argument("command", nargs="?", default="check", choices=("check", "status", "remove"))
    parser.add_argument("--dry-run", action="store_true", help="check: report what would change, change nothing")
    args = parser.parse_args(argv)
    try:
        if args.command == "remove":
            # 运维回滚用。timer 还开着时下一轮会重新上闸：先 `systemctl disable --now ssh-tailnet-guard.timer`。
            if guard_present(run):
                remove_guard(run)
            record = {"schema_version": 1, "action": "removed_by_operator", "guard": False}
        elif args.command == "status":
            healthy, reason = tailnet_health(run, iface)
            record = {"schema_version": 1, "tailnet_healthy": healthy, "reason": reason,
                      "guard": guard_present(run), "action": "none"}
        else:
            record = check(run, iface, dry_run=args.dry_run)
    except GuardError as exc:
        print(json.dumps({"schema_version": 1, "error": str(exc)}), flush=True)
        return 1
    # 每分钟一轮：没变化又健康时不写日志，免得 journald 里每分钟一行；变化、不健康、手动命令照写。
    if args.command != "check" or args.dry_run or record["action"] != "unchanged" or not record["tailnet_healthy"]:
        print(json.dumps(record), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
