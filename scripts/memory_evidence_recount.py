"""记忆证据数存量重算（CA2-15 遗留）——**默认 dry-run，写库要显式 --apply**。

背景：证据数原来是抽取窗口里的轮次 id 个数（一写入就是 8～12），条目一落库强度就到上限——
2026-10-03 线上车主 40 条显式偏好里 36 条被标成「常用」、5 条推断全被标成「明确说过」，
而 41 个维度里 35 个只在一个会话里出现过。新写入已改为按场合（会话）计（`weighting.next_evidence`），
本脚本把存量现行条目改成同一口径：

  场合数 = 同一维度里与它说同一件事的条目（现行 + 已被取代）来自几个不同的会话
           （单值维度：同一来源类别的条目；多值维度：`revision.equivalent` 判为同一个值的条目）。

被取代的条目也算，与写入时「取代就继承证据」（`MemoryStore._inherit_evidence`）同一口径；旧的 reinforce
不记会话，所以这是下限，之后再被观测到一次就 +1。

只处理新口径上线之前写入的条目（`--created-before` 取 memory 容器启动时刻）与取代了它们的条目：上线后写入的
已经按新口径计数，重算只会把它算低。上线到重算之间被加强过的旧条目会少算至多一次（加强覆盖了来源会话）。

只改 evidence_count 与 weight 两列（weight 按 `weighting.compute_weight` 的未衰减口径重算，召回时再按
valid_from 衰减），只动 weight>0 的现行 semantic 条目；不改 text/valid_from/superseded_by，不删行。
只打印计数，不打印记忆原文。判据全部来自 memory 服务自己的模块（revision/weighting），本脚本不留第二份声明。

跑法（memory 容器里有 asyncpg 与最新的 revision/weighting；按 compose service 名寻址，别写死容器名）：

    C="docker compose -f compose.yaml"
    T=$(date -d "$(docker inspect -f '{{.State.StartedAt}}' $($C ps -q memory))" +%s)
    $C exec -T memory python - --created-before $T < scripts/memory_evidence_recount.py            # dry-run
    $C exec -T memory python - --created-before $T --apply < scripts/memory_evidence_recount.py    # 写库
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import os
import sys


def _load_rules():
    here = globals().get("__file__")
    for path in ("/app/memory",
                 os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(here))), "memory") if here else ""):
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)
    import revision  # noqa: E402
    import weighting  # noqa: E402
    return revision, weighting


_ITEMS = """
    SELECT id, user_id, occupant_id, COALESCE(subject,'') AS subject, predicate, text, provenance,
           COALESCE(source_session,'') AS source_session, COALESCE(superseded_by,'') AS superseded_by,
           evidence_count, weight, half_life_days, created_at
    FROM memory_item
    WHERE kind='semantic' AND predicate <> ''
"""
_FAMILY = """
    SELECT user_id, occupant_id, subject, rel, object FROM memory_relation
    WHERE superseded_by IS NULL AND rel='family'
"""


def in_scope(rows: list[dict], created_before: int) -> list[dict]:
    """要重算的现行带权条目：上线前写入的，加上取代了上线前条目的（它们继承了旧口径的计数）。"""
    old = {r["id"] for r in rows if r["created_at"] < created_before}
    heirs = {r["superseded_by"] for r in rows if r["id"] in old and r["superseded_by"]}
    return [r for r in rows if not r["superseded_by"] and float(r["weight"] or 0) > 0
            and (r["id"] in old or r["id"] in heirs)]


def plan(rows: list[dict], family: list[dict], revision, weighting,
         created_before: int) -> list[tuple[dict, int, float]]:
    """每个要重算的条目的 (条目, 新证据数, 新 weight)；只返回会变的。`rows` 含已被取代的条目。"""
    edges: dict[tuple, list[dict]] = collections.defaultdict(list)
    for edge in family:
        edges[(edge["user_id"], edge.get("occupant_id") or "primary")].append(edge)
    aliases = {key: revision.aliases_from_family(found) for key, found in edges.items()}
    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    for row in rows:
        owner = (row["user_id"], row.get("occupant_id") or "primary")
        groups[(row["user_id"],) + revision.dimension(row, aliases.get(owner))].append(row)
    changes = []
    for row in in_scope(rows, created_before):
        owner = (row["user_id"], row.get("occupant_id") or "primary")
        members = groups[(row["user_id"],) + revision.dimension(row, aliases.get(owner))]
        if revision.is_multi_valued(row["predicate"]):
            same = [m for m in members if revision.equivalent(m["text"], row["text"])]
        else:
            same = [m for m in members if revision.explicit(m) == revision.explicit(row)]
        occasions = len({m["source_session"] or m["id"] for m in same})
        weight = weighting.compute_weight(provenance=row["provenance"], evidence_count=occasions,
                                          age_seconds=0.0, half_life_days=row["half_life_days"])
        if occasions != row["evidence_count"] or abs(weight - float(row["weight"])) > 1e-4:
            changes.append((row, occasions, weight))
    return changes


def _bucket(n: int) -> str:
    return "1" if n <= 1 else ("2-4" if n <= 4 else ("5-12" if n <= 12 else "13+"))


def report(rows: list[dict], changes: list[tuple[dict, int, float]], created_before: int) -> dict:
    current = [r for r in rows if not r["superseded_by"] and float(r["weight"] or 0) > 0]
    scope = in_scope(rows, created_before)
    after = {row["id"]: (count, weight) for row, count, weight in changes}
    out = {"current_weighted": len(current), "in_scope": len(scope), "users": len({r["user_id"] for r in current}),
           "to_change": len(changes)}
    for label, pick in (("before", lambda r: (r["evidence_count"], float(r["weight"]))),
                        ("after", lambda r: after.get(r["id"], (r["evidence_count"], float(r["weight"]))))):
        out[f"evidence_{label}"] = dict(collections.Counter(
            f"{r['provenance']}:{_bucket(pick(r)[0])}" for r in scope))
        out[f"weight_{label}"] = dict(collections.Counter(
            f"{r['provenance']}:{round(pick(r)[1], 1)}" for r in scope))
    return out


async def run(dsn: str, *, apply: bool, user: str, created_before: int) -> int:
    import asyncpg
    import json

    revision, weighting = _load_rules()
    conn = await asyncpg.connect(dsn)
    try:
        rows = [dict(r) for r in await conn.fetch(_ITEMS)]
        family = [dict(r) for r in await conn.fetch(_FAMILY)]
        if user:
            rows = [r for r in rows if r["user_id"] == user]
            family = [r for r in family if r["user_id"] == user]
        changes = plan(rows, family, revision, weighting, created_before)
        print(json.dumps(report(rows, changes, created_before), ensure_ascii=True, sort_keys=True))
        if not apply:
            print("[dry-run] nothing written; add --apply to write evidence_count and weight")
            return 0
        written = 0
        async with conn.transaction():
            for row, count, weight in changes:
                # 只在条目没被并发改过时写（同时发生的 reinforce 已经按新口径 +1，不能覆盖）
                tag = await conn.execute(
                    "UPDATE memory_item SET evidence_count=$2, weight=$3 "
                    "WHERE id=$1 AND superseded_by IS NULL AND evidence_count=$4",
                    row["id"], count, weight, row["evidence_count"])
                written += str(tag).endswith(" 1")
        print(f"[apply] updated {written} of {len(changes)} rows")
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=os.getenv("POSTGRES_DSN", ""), help="默认取 POSTGRES_DSN 环境变量")
    ap.add_argument("--user", default="", help="只处理某个 user_id（默认全部）")
    ap.add_argument("--created-before", type=int, required=True,
                    help="新口径上线时刻（unix 秒，取 memory 容器启动时刻）；之后写入的条目不重算")
    ap.add_argument("--apply", action="store_true", help="写库（evidence_count 与 weight）")
    args = ap.parse_args()
    if not args.dsn:
        print("需要 --dsn 或 POSTGRES_DSN", file=sys.stderr)
        return 2
    return asyncio.run(run(args.dsn, apply=args.apply, user=args.user, created_before=args.created_before))


if __name__ == "__main__":
    raise SystemExit(main())
