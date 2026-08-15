"""记忆谓词别名存量清洗（E5，EVA 余项⑤）——**默认 dry-run，写库要显式 --apply**。

背景：`memory/extract.py::_PRED_CANON` 只在**写入时**把 LLM 自由造的谓词归一，
存量行不动。真栈 u1 库因此长出一堆同义谓词（`beverage.coffee`/`coffee.brand`/
`consume.coffee` 说的是同一件事；`place.avoid`/`poi.dislike`/`restaurant.no_queue`
scope 是 profile.taste 但谓词不带 `taste.` 前缀）。代价已经兑现：nearby 口味召回
带 `predicate_prefix="taste."`，而 `pg_store._score` 里 scope 与谓词前缀是 **AND**
——那几行永远进不了消费面。

本脚本做两件事，各自独立开关：
  ① 归一（`--apply`）：predicate → `normalize_predicate(predicate)`。
  ② 去重（`--supersede-dups`）：归一后同 (user, occupant, subject, predicate) 的多条
     现行记忆，除最新一条外标 `superseded_by=最新`——这正是当初谓词若没漂移、
     `consolidate` 本来就会做的事。**不删除任何行**（supersede 是软失效）。

刻意不做的：不改 text/valid_from/weight（衰减基准刷新等于把陈年偏好洗成新的）、
不跨 subject 合并（「爸爸的偏好」与本人的是两条独立记忆）、不猜未登记的别名
（归一表是唯一权威，要加别名去改 `_PRED_CANON`）。

跑法（宿主没有 asyncpg；memory 容器有，且**必须是重建过的镜像**才带最新归一表）。
容器名派生自启动目录名（本地 `car-agent`、CI checkout `cockpit-agent`），**一律按
compose service 名寻址**，别写死容器名（`scripts/tests/test_e2e_container_names.py`
守着这条，本脚本首版就被它抓了一次）：

    C="docker compose -f compose.yaml"
    $C cp scripts/memory_predicate_cleanup.py memory:/tmp/
    $C exec -T memory python /tmp/memory_predicate_cleanup.py            # dry-run
    $C exec -T memory python /tmp/memory_predicate_cleanup.py --apply    # 写库
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys


def _load_canon():
    """归一表的唯一来源是 memory/extract.py——本脚本不留第二份声明。"""
    for p in ("/app/memory", os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "memory")):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    import extract  # noqa: E402  （路径备好后再导）
    return extract.normalize_predicate, extract._PRED_CANON


_SELECT = """
    SELECT id, user_id, occupant_id, COALESCE(subject,'') AS subject,
           predicate, scope, valid_from, left(text, 42) AS sample
    FROM memory_item
    WHERE superseded_by IS NULL AND predicate <> ''
    ORDER BY user_id, occupant_id, predicate, valid_from
"""


async def run(dsn: str, *, apply: bool, supersede_dups: bool, user: str) -> int:
    import asyncpg

    normalize, canon = _load_canon()
    conn = await asyncpg.connect(dsn)
    try:
        rows = [dict(r) for r in await conn.fetch(_SELECT)]
        if user:
            rows = [r for r in rows if r["user_id"] == user]
        print(f"归一表：{len(canon)} 个 canonical / "
              f"{sum(len(v) for v in canon.values())} 个别名")
        print(f"现行记忆行：{len(rows)}" + (f"（user={user}）" if user else ""))

        renames = [(r, normalize(r["predicate"])) for r in rows]
        renames = [(r, c) for r, c in renames if c != r["predicate"]]
        print(f"\n── ① 待归一：{len(renames)} 行 ──")
        for r, c in renames:
            print(f"  {r['user_id']}/{r['occupant_id']:<8} "
                  f"{r['predicate']:<22} → {c:<20} [{r['scope']}] {r['sample']}")

        # 归一**之后**的谓词视图，用来算去重面（dry-run 也要按归一后的样子算，
        # 否则读数会低估——这正是本次清洗的目的）。
        after = {r["id"]: c for r, c in renames}
        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            key = (r["user_id"], r["occupant_id"], r["subject"],
                   after.get(r["id"], r["predicate"]))
            groups.setdefault(key, []).append(r)
        dups = {k: sorted(v, key=lambda x: x["valid_from"], reverse=True)
                for k, v in groups.items() if len(v) > 1}
        n_dup_rows = sum(len(v) - 1 for v in dups.values())
        print(f"\n── ② 归一后仍并存：{len(dups)} 组 / {n_dup_rows} 行会被 supersede ──")
        for (uid, occ, subj, pred), v in dups.items():
            print(f"  {uid}/{occ} {pred}{f' @{subj}' if subj else ''}：{len(v)} 条"
                  f"（留最新 {v[0]['id'][:8]} «{v[0]['sample']}»）")
            for old in v[1:]:
                print(f"      ↳ supersede {old['id'][:8]} «{old['sample']}»")

        if not apply and not supersede_dups:
            print("\n[dry-run] 没有写任何东西。写库加 --apply（归一）/ "
                  "--supersede-dups（去重），两者可分别授权。")
            return 0

        async with conn.transaction():
            if apply:
                for r, c in renames:
                    await conn.execute(
                        "UPDATE memory_item SET predicate=$2 WHERE id=$1", r["id"], c)
                print(f"\n[apply] 已归一 {len(renames)} 行。")
            if supersede_dups:
                import time
                now = int(time.time())
                n = 0
                for v in dups.values():
                    for old in v[1:]:
                        await conn.execute(
                            "UPDATE memory_item SET superseded_by=$2, valid_to=$3 "
                            "WHERE id=$1", old["id"], v[0]["id"], now)
                        n += 1
                print(f"[apply] 已 supersede {n} 行（不删除，可回溯）。")
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=os.getenv("POSTGRES_DSN", ""),
                    help="默认取 POSTGRES_DSN 环境变量")
    ap.add_argument("--user", default="", help="只处理某个 user_id（默认全部）")
    ap.add_argument("--apply", action="store_true", help="① 写库执行谓词归一")
    ap.add_argument("--supersede-dups", action="store_true",
                    help="② 归一后把同谓词的旧条目标为被取代（不删除）")
    args = ap.parse_args()
    if not args.dsn:
        print("需要 --dsn 或 POSTGRES_DSN", file=sys.stderr)
        return 2
    return asyncio.run(run(args.dsn, apply=args.apply,
                           supersede_dups=args.supersede_dups, user=args.user))


if __name__ == "__main__":
    raise SystemExit(main())
