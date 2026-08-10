"""Registry 静态 admission：token → agent_id 绑定（B3 §2.4）。

## 要关的是哪个洞

现状：``Registry.Register`` 接受**任意调用方**的 Manifest，同 ``agent_id`` 直接覆盖，没有
任何身份绑定——任何能连上 registry 的进程都可以把 ``navigation`` 换成自己的 endpoint，
此后所有导航请求都打到它。R3.1 只做了会话层（HMI↔网关、端↔云），服务网格内部没有准入。

本版是 PoC 级的静态版：一张 ``token → 允许申报的 agent_id 集合`` 表，配在 env 里。
证书身份绑定（每服务唯一 mTLS 身份 → SAN 即 agent_id）留给 prod 强制表 v2，随第三方
Agent 生态启动；本版先把「**任何人可覆盖任何 agent_id**」这个最大的洞关上。

## 默认关，且「关」的语义是逐字现状

``REGISTRY_ADMISSION_TOKENS`` 缺省为空 = admission 关闭，``Register`` 行为与今天逐字一致
（不带 token 也能注册）。这一点与 B3 的整体形态一致：**只加档，不改默认**。

## 格式

``<token>:<agent_id>[|<agent_id>...]``，多条用 ``,`` 分隔。例：

    tok-nav:navigation,tok-plan:charging-planner|scene-orchestrator

调用方把 token 放在 gRPC metadata 的 :data:`METADATA_KEY`（Agent SDK 读 ``AGENT_REGISTRY_TOKEN``）。

## 一条判据

**拒绝要留下可查的审计行，而审计行里不许有 token 本身。** 出问题时要回答的是「谁申报了
什么、允许的是什么」，不是「他的 token 是多少」——后者写进日志就成了新的泄漏面。
"""
from __future__ import annotations

import os
from typing import Iterable, Mapping

#: gRPC metadata 里放 token 的键（必须小写——gRPC 规范要求 metadata key 全小写）。
METADATA_KEY = "x-agent-token"

#: 服务端读的允许表。
TOKENS_ENV = "REGISTRY_ADMISSION_TOKENS"
#: 客户端读的自身 token。
AGENT_TOKEN_ENV = "AGENT_REGISTRY_TOKEN"


def parse_tokens(raw: str | None) -> dict[str, set[str]]:
    """``tok:a|b,tok2:c`` → ``{"tok": {"a","b"}, "tok2": {"c"}}``。

    畸形条目（无 ``:``、token 为空、允许集为空）直接丢弃——**不做补全**。
    一条配错的规则被静默补成「允许一切」远比它被丢掉危险。
    """
    table: dict[str, set[str]] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        token, _, agents = entry.partition(":")
        token = token.strip()
        allowed = {a.strip() for a in agents.split("|") if a.strip()}
        if not token or not allowed:
            continue
        table.setdefault(token, set()).update(allowed)
    return table


def enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return bool(parse_tokens(env.get(TOKENS_ENV)))


def token_from_metadata(metadata: Iterable[tuple[str, str]] | None) -> str:
    """从 gRPC ``invocation_metadata()`` 取 token（键大小写不敏感）。"""
    for key, value in (metadata or ()):
        if str(key).lower() == METADATA_KEY:
            return str(value).strip()
    return ""


def check(metadata: Iterable[tuple[str, str]] | None, agent_id: str,
          env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """判定一次 ``Register`` 是否准入。返回 ``(是否放行, 审计用的拒绝原因)``。

    关闭时恒放行且原因为空串。放行时原因同样为空串——**调用方判 bool，不要判原因非空**。
    """
    env = os.environ if env is None else env
    table = parse_tokens(env.get(TOKENS_ENV))
    if not table:
        return True, ""
    token = token_from_metadata(metadata)
    if not token:
        return False, f"missing {METADATA_KEY} (claimed={agent_id!r})"
    allowed = table.get(token)
    if allowed is None:
        # 不回显 token：审计行要回答「谁申报了什么」，token 本身写进日志就是新的泄漏面。
        return False, f"unknown admission token (claimed={agent_id!r})"
    if agent_id not in allowed:
        return False, (f"agent_id not allowed for this token "
                       f"(claimed={agent_id!r}, allowed={sorted(allowed)})")
    return True, ""


def client_metadata(env: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    """调用方注册时要带的 metadata；未配 token 时返回空列表（admission 关时无影响）。"""
    env = os.environ if env is None else env
    token = (env.get(AGENT_TOKEN_ENV) or "").strip()
    return [(METADATA_KEY, token)] if token else []
