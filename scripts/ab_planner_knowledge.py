"""规划知识 A/B：同一份输入、两份规划知识，在生产 cloud-planner 容器里**只规划不执行**（2026-10-17 第十三轮，后续批 §20.3）。

为什么要它：规划软知识（技能指南、能力描述）按项目规矩先 A/B 证伪再上线，可部署两版做真栈对照太重（部署工具不许回滚旧 release，
措辞 A/B 只能做成新提交）。这里在线上容器里用真实目录、真实 llm-gateway 跑 `PlanBuilder.build`，两臂只差那一处知识，
不派发任何步、不建挂起、不读写用户记忆（`assemble(mem_on=False)`）。会花模型调用：每句 × 每遍 × 两臂各一次规划（含重试）。

    python scripts/ab_planner_knowledge.py --spec spec.yaml --out .artifacts/ab/m10.jsonl
    python scripts/ab_planner_knowledge.py --summarize .artifacts/ab/m10.jsonl

规格（YAML）：
    name: m10                      # 进 session id，便于在 collector 里认出
    n: 5                           # 每句每臂几遍
    provider: minimax              # 钉的规划模型（与跑数器同口径）
    model: MiniMax-M3
    texts: [停车时有人刮车能录下来吗, 我有哪些提醒]
    arm_b:                         # 两种之一
      guide: {name: weather-outing, file: path/to/改过的指南.yaml}
      # description: {agent_id: manual-rag, intent: manual.query, text: 新描述}

判定规则要在跑之前写进设计文档（先定规则再看数）。SSH 用部署同一套 `SshConfig`（`CAR_AGENT_DEPLOY_HOST` / `_USER` / `_SSH_IDENTITY`）；
Windows 上从用户级环境变量刷新（本会话进程环境可能是旧值）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PAYLOAD = Path(__file__).with_name("ab_planner_knowledge_payload.py")
SERVICE = "cloud-planner"
_SSH_KEYS = ("CAR_AGENT_DEPLOY_HOST", "CAR_AGENT_DEPLOY_USER", "CAR_AGENT_SSH_IDENTITY", "CAR_AGENT_SSH_KEX_ALGORITHMS")

#: 云主机上跑的那段：按 compose 服务标签找到唯一的 cloud-planner 容器，把载荷与配置送进去，逐行转出它的输出
_HOST = r'''
import base64, subprocess, sys
payload = base64.b64decode("__PAYLOAD__")
ids = subprocess.check_output(["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=__SERVICE__"],
                              timeout=20).decode().split()
if len(ids) != 1:
    print('{"error": "containers:%d"}' % len(ids)); sys.exit(2)
proc = subprocess.Popen(["docker", "exec", "-i", ids[0], "python", "-", "__CONFIG__"], stdin=subprocess.PIPE,
                        stdout=sys.stdout, stderr=subprocess.DEVNULL)
proc.communicate(payload)
sys.exit(proc.returncode)
'''


def build_config(spec: dict, base: Path = ROOT) -> dict:
    """规格 → 容器载荷的配置。只认一种 B 臂变体；指南文件按相对仓库根解析。"""
    texts = [str(t) for t in (spec.get("texts") or []) if str(t).strip()]
    n = int(spec.get("n") or 0)
    if not texts or n < 1 or not str(spec.get("name") or "").strip():
        raise ValueError("spec needs name, texts and n >= 1")
    arm_b = spec.get("arm_b") or {}
    if len([k for k in ("guide", "description") if arm_b.get(k)]) != 1:
        raise ValueError("arm_b takes exactly one of guide / description")
    config = {"name": str(spec["name"]), "texts": texts, "n": n,
              "provider": str(spec.get("provider") or "minimax"), "model": str(spec.get("model") or "MiniMax-M3")}
    if arm_b.get("guide"):
        guide = arm_b["guide"]
        path = Path(guide["file"])
        config["guide_name"] = str(guide["name"])
        config["guide_yaml_b"] = (path if path.is_absolute() else base / path).read_text(encoding="utf-8")
    else:
        desc = arm_b["description"]
        config["description_b"] = {"agent_id": str(desc["agent_id"]), "intent": str(desc["intent"]),
                                   "description": str(desc["text"])}
    return config


def host_program(payload: str, config: dict) -> str:
    encoded = base64.b64encode(json.dumps(config, ensure_ascii=False).encode()).decode()
    return (_HOST.replace("__PAYLOAD__", base64.b64encode(payload.encode()).decode())
            .replace("__SERVICE__", SERVICE).replace("__CONFIG__", encoded))


def summarize(rows: list[dict]) -> list[str]:
    """按句、按臂统计规划落点（意图序列）；另报错误数与 B 臂注入核对（指南摘要 / 描述补丁）。"""
    by: dict[tuple, Counter] = defaultdict(Counter)
    texts: list[str] = []
    for r in rows:
        if r.get("text") not in texts:
            texts.append(r.get("text"))
        by[(r.get("text"), r.get("arm"))]["error:" + r["error"] if r.get("error") else ",".join(r.get("intents") or []) or "(空)"] += 1
    out = []
    for text in texts:
        out.append(f"{text}\n  A: {dict(by[(text, 'A')])}\n  B: {dict(by[(text, 'B')])}")
    for arm in ("A", "B"):
        rs = [r for r in rows if r.get("arm") == arm]
        out.append(f"{arm}: rows {len(rs)}, errors {sum(1 for r in rs if r.get('error'))}, "
                   f"guide_sha {dict(Counter(r.get('guide_sha') for r in rs))}, patched {sum(1 for r in rs if r.get('patched'))}")
    return out


def _ssh():
    from scripts.cloud_release_lib import SshConfig
    if os.name == "nt":                       # 用户级变量（本会话进程环境可能是旧值）
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as env:
            for key in _SSH_KEYS:
                try:
                    os.environ[key] = str(winreg.QueryValueEx(env, key)[0])
                except OSError:
                    pass
    return SshConfig(os.environ["CAR_AGENT_DEPLOY_HOST"], os.environ.get("CAR_AGENT_DEPLOY_USER", "ubuntu"),
                     Path(os.environ["CAR_AGENT_SSH_IDENTITY"]), os.environ.get("CAR_AGENT_SSH_KEX_ALGORITHMS"))


def run(spec_path: Path, out: Path) -> int:
    config = build_config(yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {})
    if out.exists():
        raise ValueError("output already exists; use a new file")
    out.parent.mkdir(parents=True, exist_ok=True)
    program = host_program(PAYLOAD.read_text(encoding="utf-8"), config)
    with out.open("w", encoding="utf-8") as sink:
        proc = subprocess.Popen(_ssh().ssh_argv("sudo python3 -"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        proc.stdin.write(program.encode())
        proc.stdin.close()
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("{"):
                sink.write(line + "\n")
                sink.flush()
        return proc.wait()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--spec", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--summarize", type=Path)
    args = p.parse_args()
    if args.summarize:
        rows = [json.loads(line) for line in args.summarize.read_text(encoding="utf-8").splitlines() if line.strip()]
        print("\n".join(summarize(rows)))
        return 0
    if not args.spec or not args.out:
        p.error("--spec and --out are required (or --summarize)")
    return run(args.spec, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
