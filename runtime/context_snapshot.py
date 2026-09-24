"""跨模式重建时注入的「最近完整问答」——**唯一渲染处**（评审四轮 R4-05，2026-09-24）。

修前 S2S 的 `build_context_summary` 取 `GetSession(last_n=4)`——4 **条**消息，约 2 对问答（planner 缺省是 4 **对**），每条再硬截
`text[:120]`：一条 150 字的用户消息末尾是「不要启动导航」，重建后的摘要里这句被截掉；S2S 会话每 20 轮主动重建、断线重连都走这份摘要，
模型手里的上下文就少了一条禁止事项。

判据：

- 按**完整问答对**取（与 planner 同一缺省：4 对；memory 按 exchange 整对返回）；
- 单条过长时**压缩但不剪禁止事项**：保留首尾分句与每一个带否定 / 限定 / 未完成标记的分句，其余以「…」省略；分不开的整条保留——
  宁可少压缩，也不把「不要启动导航」剪掉、不把「还没开始导航」压成「开始导航」；
- 助手那一条带上这一轮**真实执行过的动作名**（memory 的 `actions`，执行事实，不是话术）——「做了什么」不靠模型从话术里猜。

落在 `runtime/`：llm-gateway 镜像 `COPY runtime`，今后别的重建入口（HMI 三段式 ↔ S2S 切换）读同一份，不再各写一套截断。
"""
from __future__ import annotations

import re

#: 否定 / 限定 / 未完成的标记：带它们的分句一律保留。零领域词；多保留几句的代价只是摘要长一点。
KEEP_MARKERS = ("不", "没", "未", "别", "勿", "莫", "非", "无", "只", "仅", "暂", "还没", "尚未")
#: 单条消息超过这么多字才压缩。
MESSAGE_LIMIT = 160
DEFAULT_EXCHANGES = 4
_CLAUSE_RE = re.compile(r"[^，,。！!？?；;\n]+[，,。！!？?；;\n]*|[，,。！!？?；;\n]+")


def clip_message(text: str | None, limit: int = MESSAGE_LIMIT) -> str:
    """≤ `limit` 原样；否则保留首尾分句与每一个带 `KEEP_MARKERS` 的分句，其余以「…」代替。分句本身从不截断。"""
    t = str(text or "").strip()
    if len(t) <= limit:
        return t
    clauses = [c for c in _CLAUSE_RE.findall(t) if c]
    if len(clauses) <= 2:
        return t                    # 没有可省的中间分句（常见于无标点的转写）：整条保留，不切句子
    keep = {0, len(clauses) - 1} | {i for i, c in enumerate(clauses)
                                    if any(marker in c for marker in KEEP_MARKERS)}
    out: list[str] = []
    in_gap = False
    for index, clause in enumerate(clauses):
        if index in keep:
            out.append(clause)
            in_gap = False
        elif not in_gap:
            out.append("…")
            in_gap = True
    return "".join(out)


def _field(turn, name: str, default=None):
    if isinstance(turn, dict):
        return turn.get(name, default)
    return getattr(turn, name, default)


def _exchanges(turns) -> list[list]:
    """按 exchange 分组（同一 exchange_id 的连续消息一组）；没有 exchange_id 的旧记录：每条用户消息开一组。"""
    groups: list[list] = []
    current = None
    for turn in turns or []:
        exchange = str(_field(turn, "exchange_id", "") or "").strip()
        role = str(_field(turn, "role", "") or "")
        new_group = (not groups
                     or (exchange and exchange != current)
                     or (not exchange and role == "user"))
        if new_group:
            groups.append([])
        groups[-1].append(turn)
        current = exchange or current
    return groups


def render_exchanges(turns, *, exchanges: int = DEFAULT_EXCHANGES,
                     limit: int = MESSAGE_LIMIT) -> list[str]:
    """最近 `exchanges` 对完整问答 → `用户：… / 你：…（已执行：a、b）` 行。空文本的消息不出行。"""
    lines: list[str] = []
    for group in _exchanges(turns)[-max(1, int(exchanges)):]:
        for turn in group:
            text = clip_message(_field(turn, "text", ""), limit)
            if not text:
                continue
            if str(_field(turn, "role", "") or "") == "user":
                lines.append(f"用户：{text}")
                continue
            actions = [str(a).strip() for a in (_field(turn, "actions", None) or []) if str(a).strip()]
            lines.append(f"你：{text}" + (f"（已执行：{'、'.join(actions)}）" if actions else ""))
    return lines
