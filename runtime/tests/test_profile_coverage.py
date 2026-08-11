"""B3 覆盖面结构断言：没有服务能绕过部署形态闸。

## 为什么要有这一份

实施 B3 时把 ``DEPLOY_PROFILE`` 加进了 compose 的 ``x-python-env`` anchor，理所当然地
认为「所有 Python 服务都拿到了」。**容器演练当场证否**：`registry` / `edge-orchestrator`
/ `proactive` 三个服务压根没有 ``<<: *python-env``，它们自己列 env——于是
`DEPLOY_PROFILE=prod docker compose run registry` 照常起来了，闸形同虚设。

这就是本项目记过的那条判据的又一例：**「能力从哪里声明」和「能力写在哪个文件」是两件事**
（shop 域零范例事故同源）。修法不是「下次记得」，是把覆盖面变成断言——新加服务漏配即红。

同时对应 B1 那一课：**「可选断言」等于把最该红的一类回归托付给「写用例的人记得加一行」。**

## 两条断言

1. **配置面**：compose 里每个**自建镜像**的服务都必须带 ``DEPLOY_PROFILE``，
   前端（hmi/dashboard，不跑我们的 Python/Go 闸）走显式豁免且必须给理由。
2. **代码面**：每个服务镜像的启动入口都必须**够得着**闸——自己调
   ``enforce_deploy_profile()``，或经 ``runtime.grpcio.aio_server()``（Python 唯一出口），
   或经 Go 的 ``deployprofile.Enforce``。
"""
from __future__ import annotations

import os
import re

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COMPOSE = os.path.join(ROOT, "deploy", "docker-compose.yaml")

#: 显式豁免：**必须写理由**。豁免的判据是「这个镜像根本不跑我们的闸」，不是「配起来麻烦」。
EXEMPT_SERVICES = {
    "hmi": "前端 Vite 开发服务器，不跑任何 Python/Go 服务代码；隐私默认挡位由源码级断言守",
    "dashboard": "同 hmi，纯前端",
}

#: 代码面的闸标记。任一出现即认为这个入口够得着闸。
_GATE_MARKERS = ("enforce_deploy_profile(", "aio_server(", "deployprofile.Enforce(")


def _compose() -> dict:
    with open(COMPOSE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _built_services() -> dict[str, dict]:
    """自建镜像的服务（第三方镜像如 redis/nats/postgres 不跑我们的代码，不在范围内）。"""
    return {name: svc for name, svc in _compose()["services"].items() if "build" in svc}


def test_every_built_service_gets_the_profile_env():
    missing = sorted(
        name for name, svc in _built_services().items()
        if name not in EXEMPT_SERVICES
        and "DEPLOY_PROFILE" not in (svc.get("environment") or {}))
    assert not missing, (
        f"这些自建服务没有 DEPLOY_PROFILE：{missing}。"
        "⚠ 写进 x-python-env anchor **不等于**每个服务都有——不用该 anchor 的服务要单列。"
        "确实不跑闸的（纯前端）请进 EXEMPT_SERVICES 并写理由。")


def test_exemptions_carry_a_reason():
    for name, reason in EXEMPT_SERVICES.items():
        assert reason.strip(), f"{name} 的豁免没有理由——豁免判据是「不跑闸」不是「懒得配」"
    stale = sorted(set(EXEMPT_SERVICES) - set(_built_services()))
    assert not stale, f"豁免表里有已不存在的服务：{stale}（只进不出会腐烂）"


def _dockerfiles() -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", ".worktrees", "gen")]
        if "Dockerfile" in filenames:
            out.append(os.path.join(dirpath, "Dockerfile"))
    return sorted(out)


def _entry_source(dockerfile: str) -> str | None:
    """从 Dockerfile 的 CMD 解出 Python 启动文件路径；非 Python 入口返回 None。"""
    with open(dockerfile, "r", encoding="utf-8") as f:
        text = f.read()
    cmds = re.findall(r"^(?:CMD|ENTRYPOINT)\s+(.+)$", text, re.M)
    if not cmds:
        return None
    tokens = re.findall(r'"([^"]+)"', cmds[-1])
    if not tokens or tokens[0] != "python":
        return None
    if len(tokens) >= 3 and tokens[1] == "-m":
        return os.path.join(ROOT, tokens[2].replace(".", os.sep) + ".py")
    target = tokens[-1]
    if not target.endswith(".py"):
        return None
    # `python main.py` 的相对基准是 Dockerfile 的 WORKDIR；仓库里两种写法都有
    # （`python main.py` 与 `python agents/x/main.py`），依次尝试。
    for base in (os.path.dirname(dockerfile), ROOT):
        path = os.path.join(base, target)
        if os.path.exists(path):
            return path
    return None


def _reaches_gate(path: str, depth: int = 3, seen: set[str] | None = None) -> bool:
    """入口文件自身、或它（间接）import 的仓库模块里，是否出现闸标记。

    三层足够覆盖仓库里的两种形态：直接 ``aio_server()``（各服务 main），以及
    ``main.py → agents/_sdk/__init__.py → agents/_sdk/server.py``（Agent 走 SDK 的 serve）。
    再深就不是「入口顺手调一下」而是藏起来了，那本身就该被看见。``seen`` 防环。
    """
    seen = set() if seen is None else seen
    real = os.path.realpath(path)
    if real in seen:
        return False
    seen.add(real)
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    if any(m in src for m in _GATE_MARKERS):
        return True
    if depth <= 0:
        return False
    for base, mod in _imported_modules(src, os.path.dirname(path)):
        rel = mod.replace(".", os.sep)
        for candidate in (os.path.join(base, rel + ".py"),
                          os.path.join(base, rel, "__init__.py")):
            if os.path.exists(candidate) and _reaches_gate(candidate, depth - 1, seen):
                return True
    return False


def _imported_modules(src: str, pkg_dir: str) -> list[tuple[str, str]]:
    """解出 ``(基准目录, 模块路径)``。

    **相对 import 必须一起解**：``agents/_sdk/__init__.py`` 用的是 ``from .server import serve``，
    只认绝对 import 的扫描会在这里断链，于是 14 个 Agent 全被误判成「够不着闸」
    ——一个扫不全的结构断言比没有更糟，它会让人去改**本来是对的**代码。
    """
    out = []
    for dots, mod in re.findall(r"^\s*(?:from|import)\s+(\.*)([A-Za-z_][\w.]*)", src, re.M):
        if not dots:
            out.append((ROOT, mod))
            continue
        base = pkg_dir
        for _ in range(len(dots) - 1):
            base = os.path.dirname(base)
        out.append((base, mod))
    return out


_PY_ENTRIES = [(d, _entry_source(d)) for d in _dockerfiles()]
_PY_ENTRIES = [(d, s) for d, s in _PY_ENTRIES if s]


def test_found_the_python_entrypoints():
    """先证明扫描本身没扫空——一个恒空的清单会让下面那条断言恒绿。"""
    assert len(_PY_ENTRIES) >= 20, f"只找到 {len(_PY_ENTRIES)} 个 Python 入口，扫描逻辑可能失效"


@pytest.mark.parametrize("dockerfile,entry", _PY_ENTRIES,
                         ids=[os.path.relpath(d, ROOT).replace("\\", "/")
                              for d, _ in _PY_ENTRIES])
def test_python_entrypoint_reaches_the_gate(dockerfile, entry):
    assert _reaches_gate(entry), (
        f"{os.path.relpath(entry, ROOT)} 够不着部署形态闸：它既不调 enforce_deploy_profile()，"
        "也不经 runtime.grpcio.aio_server()。不建 gRPC server 的服务要在进程入口显式调一次。")


def _copies_runtime(dockerfile: str) -> bool:
    with open(dockerfile, "r", encoding="utf-8") as f:
        return bool(re.search(r"^\s*COPY\s+[^\s]*runtime[^\s]*\s", f.read(), re.M))


@pytest.mark.parametrize("dockerfile,entry", _PY_ENTRIES,
                         ids=[os.path.relpath(d, ROOT).replace("\\", "/")
                              for d, _ in _PY_ENTRIES])
def test_service_image_contains_the_runtime_package(dockerfile, entry):
    """够得着闸 ≠ 镜像里有那个包。

    **同一条判据在 Docker 层的复发**（2026-08-11 真栈演练抓到）：B3 给 collector 与
    proactive 的入口加了 `from runtime.profile import enforce_deploy_profile`，
    上面那条 `_reaches_gate` 断言照过——因为它读的是仓库里的源码。可这两个服务的
    Dockerfile **没有 `COPY runtime`**，镜像里根本没这个包，一重建就
    `ModuleNotFoundError` 起不来。既有容器跑的是加闸之前的镜像，于是这处断裂在
    **40 小时里毫无症状**。

    判据：**「代码里 import 得到」和「镜像里拷进去了」是两件事。**
    同族第一次是 shop 域零范例（门禁只读 manifest，而 mcp-bridge 能力由 servers.yaml
    启动期合成），第二次是 `DEPLOY_PROFILE` 写进 anchor 而三个服务不用那个 anchor。
    """
    if not _reaches_gate(entry):
        return                                  # 上一条断言会先红，这里不重复报
    assert _copies_runtime(dockerfile), (
        f"{os.path.relpath(dockerfile, ROOT)} 没有 `COPY runtime`，"
        f"但 {os.path.relpath(entry, ROOT)} 够得着部署形态闸——镜像里没有 runtime 包，"
        "容器一重建就 ModuleNotFoundError。")


def test_go_gateway_mains_call_the_gate():
    for main_go in ("gateway/edge/main.go", "gateway/cloud/main.go"):
        with open(os.path.join(ROOT, main_go), "r", encoding="utf-8") as f:
            src = f.read()
        assert "deployprofile.Enforce(" in src, f"{main_go} 没调 deployprofile.Enforce"


def test_python_gate_lives_in_the_single_server_factory():
    """闸必须在 aio_server() 里——这是 Python 侧「唯一出口」的那个出口。"""
    with open(os.path.join(ROOT, "runtime", "grpcio.py"), "r", encoding="utf-8") as f:
        src = f.read()
    factory = src[src.index("def aio_server("):]
    assert "enforce_deploy_profile()" in factory.split("\ndef ")[0]
