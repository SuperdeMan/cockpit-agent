"""QA 轮存量脏数据盘点与清洗（阶段 0.4）——**默认 dry-run，写库要显式 --apply**。

背景：2026-08-15 探索式 QA 轮的根因分析（卡 Q5/Q11）在真栈库里查到四族脏数据。
它们不是「测试造的垃圾」——**每一族都对应一个仍然敞着的写入口**，所以清洗只是
把读数复位，**不修入口就会再脏**（§40 那次 `place.company` 复位已经证明过一次）。

四族（逐族独立开关，判据全部确定性、零 LLM）：

  ① `--relation-self-loops`  自环关系边 `X --family--> X`
     实测 4 条（老婆/女儿/孩子/爸妈各一）。零信息量，且会在关系检索里占位。
  ② `--relation-inverted`    主宾颠倒 / 把地点当人
     判据：`works_at`/`lives_at`/`place_of` 的 **object 必须是地点、subject 必须是人**
     ——用「object 是否命中人称词表」与「subject 是否命中地点后缀」双向判，
     两边同时命中反向特征才算颠倒（**宁可漏，不误杀**）。
     ⚠ **本判据的召回面比首版注释写的窄，2026-08-16 实测留痕**：库里三条形态相似的
     脏边只抓得到一条——`深圳国家工程实验大楼A栋 --works_at--> 用户` 命中（两端都反）；
     而 `公司 --lives_at--> 深圳国家工程实验大楼A栋`（「公司」不在人称词表）与
     `深圳 --place_of--> 出发地`（「出发地」不在人称词表）**判不出、也不该判**。
     首版注释把三条都写成「实测」，读起来像三条都会被清掉——**漏是设计内的，
     但把设计内的漏写成待清项就是文档漂移**。要扩召回得先扩词表，那是另一件事。
  ③ `--relation-conflicts`   同 (subject, rel) 多个互不相同的现行 object
     实测：`女儿 --place_of-->` 同时有「深圳市南山实验小学」「南山实验小学」，
     另有 `孩子 --place_of-->` 同时有「南山外国语学校」「深圳南山实验小学」。
     `superseded_by` 列存在但 **全表 0 条写过** ⇒ supersede 从未生效，
     每轮召回哪条看运气。
     处置**只标 supersede 不删行**，且**保留信息最全的那条**（最长 object 优先，
     不是最新优先——E5 的 dry-run 教过一次：新条目常常信息更少）。
     ⚠ **③ 是四族里唯一自带「必须人裁」的一族，所以它的 `--apply` 还要再点一次名**：
     必须同时给 `--conflict-subject <subject>`（可重复）逐组授权，**不接受整族一次清**。
     理由是同一族内不同组的性质可以完全相反——2026-08-16 实测两组：
     「女儿」那组两个 object 是**同一所学校**（差个城市前缀，清掉才对），
     「孩子」那组是**两所不同的学校**（可能是真实变化，清掉就是丢事实）。
     把「哪几组该清」写成参数，而不是靠跑脚本的人记得只清一组。
  ④ `--reminders-invalid`    `fire_at <= 0` 却仍是 pending 的提醒
     实测 **3 条**：「妈妈住杭州」「停车位在B2」「现在这段对话里最便宜的瑞幸咖啡价格」
     ——前两条**根本不是提醒，是用户陈述的事实**被 `reminder.create` 吃掉了。
     **这三条闭合了报告 I-056 的因果链**：时间解析失败 → `fire_at=0` 仍落库 →
     永远 pending → 按 `fire_at` 升序**永远排在「进行中的任务」最前面** →
     每次查任务都先看到它们。报告把这三条读成「其他会话的提醒泄漏」，
     实际是**三条永远不会触发的伪提醒**。

  ⑤ `--reminders-expired`   `fired` 之后沉在库里超过 N 天（默认 7）的提醒
     状态机 `pending→fired` 到头，**fired 永远算 ACTIVE**：永远参与 `find_by_title`、
     永远占着序数参照系。2026-08-26 长会话跑完，u1 底下沉了 90 条这样的条目，
     直接后果是「取消第一条」取消掉了一条八月初的探针垃圾（真栈 T59）。
     形态是泓舟 2026-08-27 拍板的那一种：**复用 `cancelled` + `extra.reason=
     "expired_sweep"`**，零 schema/枚举变更（status 枚举扩散到全部查询面的影响
     大于收益，`extra` 里的 reason 足够分开「用户取消」与「系统沉积清扫」）。
     ⚠ 那 90 条已于 2026-08-27 由单事务点名 SQL 清完（`extra.batch=
     "20260827-hongzhou-approved-90"`）。**这一族是把那次一次性动作机制化**——
     同一个 `extra.reason` 约定，不发明第二个标记；下一次沉积不必再手写 SQL。

⚠ **顺序纪律（泓舟 2026-08-15 拍板）**：清洗本身已授权，但 `--apply` **排在
Q5/Q11 的写入闸落地之后**。在那之前本脚本只跑 dry-run 出盘点。

⚠ **本脚本不碰的**：不删任何行（③ 只软失效，①②④ 用 `--apply` 时才删/停用，
且逐族独立授权）；不改 text/valid_from/weight；不猜未观测到的形态；
画像地点污染（`place.company` 被写成酒店）**刻意不在此处清**——它是
`memory_item` 里的一条普通语义记忆，判据是「这条事实对不对」，
只能人裁，不能靠模式匹配（同 E5「谓词归一是让消费面够得着」与
「把多条事实压成一条」是两件事）。

跑法（宿主没有 asyncpg；memory 容器有。**按 compose service 名寻址，别写死容器名**
——容器名的 project 段派生自启动目录，`scripts/tests/test_e2e_container_names.py` 守着这条）：

    C="docker compose -f compose.yaml"
    $C cp scripts/qa_data_hygiene.py memory:/tmp/
    $C exec -T memory python /tmp/qa_data_hygiene.py                        # dry-run 全族
    $C exec -T memory python /tmp/qa_data_hygiene.py --relation-self-loops --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# 人称词表与地点后缀：判据要窄，只认**明确**的两端特征，中间地带一律不动。
_PERSON_WORDS = ("用户", "我", "本人", "老婆", "妻子", "老公", "丈夫", "爸爸", "妈妈",
                 "父亲", "母亲", "爸妈", "父母", "孩子", "女儿", "儿子", "宝宝",
                 "爷爷", "奶奶", "外公", "外婆", "同事", "朋友", "小王", "司机")
_PLACE_SUFFIX = ("路", "街", "道", "号", "楼", "栋", "大厦", "广场", "中心", "园",
                 "小学", "中学", "大学", "学校", "医院", "公司", "店", "站", "机场",
                 "市", "区", "县", "镇", "村", "省", "酒店", "餐厅", "商场", "城")
# 这些谓词的语义方向是固定的：subject=人，object=地点。
_DIRECTED_RELS = ("works_at", "lives_at", "place_of")
# 这些谓词是**单值**的：一个人通常一个公司/一个住处/一个就读地点。
# `family`/`owns`/`prefers_brand` **刻意不在此列**——它们天然一对多
#（「爸妈」既指向爸爸也指向妈妈），把它们当冲突去重会丢掉真实的人。
_SINGLE_VALUE_RELS = ("works_at", "lives_at", "place_of")


def _looks_person(value: str) -> bool:
    return any(w in value for w in _PERSON_WORDS)


def _looks_place(value: str) -> bool:
    return value.endswith(_PLACE_SUFFIX) or any(s in value for s in _PLACE_SUFFIX)


def _inverted(subject: str, obj: str) -> bool:
    """只在**两端都命中反向特征**时判颠倒——单边命中一律放过（宁可漏不误杀）。"""
    return _looks_place(subject) and _looks_person(obj) and not _looks_person(subject)


async def _connect():
    import asyncpg
    dsn = os.getenv("POSTGRES_DSN") or os.getenv("PG_DSN") or ""
    if not dsn:
        print("缺 POSTGRES_DSN（应由 compose 注入）", file=sys.stderr)
        raise SystemExit(2)
    return await asyncpg.connect(dsn)


async def scan_self_loops(conn):
    """自环关系边。⚠ **`family` 排除在外——那不是噪声，是「无名的人」的表示法。**

    2026-08-16 读消费方时发现的：`store.resolve_person_place` 靠 family 边的
    **object** 反查人实体，没名字的人（「老婆」）就以称谓自身作实体名，
    于是 `family(老婆→老婆)` 是**必需的**一跳。卡 §3-Q5 把它记成「零信息」，
    本脚本首版据此准备删掉库里那 4 条——**那会当场打断「老婆在哪上班」这类解析**
    （`test_resolve_person_place_via_works_at` 是它的既有断言）。

    > **判据**：清洗脚本删的是**数据**，而数据是不是垃圾要问**消费方**，
    > 不能只看它长得像不像垃圾。这是 dry-run 第三次劝退一条判据
    > （前两次：单值谓词冲突、`fire_at<=0`）。
    """
    return await conn.fetch(
        "SELECT id, user_id, subject, rel, object FROM memory_relation "
        "WHERE subject = object AND rel <> 'family' AND superseded_by IS NULL "
        "ORDER BY user_id, subject")


async def scan_inverted(conn):
    rows = await conn.fetch(
        "SELECT id, user_id, subject, rel, object FROM memory_relation "
        "WHERE rel = ANY($1) AND superseded_by IS NULL", list(_DIRECTED_RELS))
    return [r for r in rows if _inverted(str(r["subject"]), str(r["object"]))]


async def scan_conflicts(conn):
    """同 (user, occupant, subject, rel) 有 >1 个互不相同的现行 object。

    ⚠ **只对单值语义的谓词生效**（`_SINGLE_VALUE_RELS`）。首版没有这道过滤，
    dry-run 当场把 `爸妈 --family--> 爸爸` 和 `爸妈 --family--> 妈妈` 判成冲突，
    并准备把「妈妈」标成失效——**直接丢掉一个真实的人**。`family`/`owns`/
    `prefers_brand` 天然一对多，「同一个 subject 有多个 object」是正常状态不是冲突。
    这是 E5 那条判据的第二例：**dry-run 会自己劝退你**——去重会吞掉本来就该并存的条目。

    ⚠ 本函数**只按字面 subject 分组**，所以同一个人被写成两个称谓时它抓不到：
    实测 `妈妈 --lives_at--> 苏州` 与 `用户的妈妈 --lives_at--> 杭州` 是同一个人的
    两个城市，两条分属两组、各自「无冲突」。**实体归一不在本脚本职责内**
    （Q5 的另一半），别为了让它抓到而把分组键改成模糊匹配——那会把
    「爸爸」「爸妈」这类真的不同实体并成一组。
    """
    rows = await conn.fetch(
        "SELECT id, user_id, occupant_id, subject, rel, object, created_at "
        "FROM memory_relation WHERE superseded_by IS NULL AND rel = ANY($1) "
        "ORDER BY user_id, subject, rel, created_at", list(_SINGLE_VALUE_RELS))
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault(
            (r["user_id"], r["occupant_id"], r["subject"], r["rel"]), []).append(r)
    out = []
    for key, items in groups.items():
        if len({str(i["object"]) for i in items}) < 2:
            continue
        # 保留**信息最全**的那条：object 最长优先，同长按最新。
        # ⚠ 不是「最新优先」——E5 的 dry-run 实测过，新条目常常信息更少
        #（「用户的宠物名叫 Cookie」会取代「…喜欢睡在窗台旁」）。
        keep = max(items, key=lambda i: (len(str(i["object"])), i["created_at"]))
        out.append((key, keep, [i for i in items if i["id"] != keep["id"]]))
    return out


async def scan_invalid_reminders(conn):
    return await conn.fetch(
        "SELECT id, user_id, title, fire_at, status FROM reminder_item "
        "WHERE fire_at <= 0 AND status = 'pending' ORDER BY user_id")


async def scan_expired_reminders(conn, days: int):
    """`fired` 之后沉了超过 days 天、且**还没被扫过**的条目。

    时刻取 `fired_at`，它是 0（老数据/未记录）时退回 `fire_at`——两者都判不出
    就不算沉积，**认不出就不动它**（同 B3 那条「认不出就用默认值」的反面）。
    已经带 `expired_sweep` 标记的不再重复召回：恒绿的清扫表比没有更糟。
    """
    cutoff = int(time.time()) - days * 86400
    return await conn.fetch(
        "SELECT id, user_id, title, fire_at, fired_at, status FROM reminder_item "
        "WHERE status = 'fired' "
        "AND COALESCE(NULLIF(fired_at, 0), fire_at) > 0 "
        "AND COALESCE(NULLIF(fired_at, 0), fire_at) < $1 "
        "AND COALESCE(extra->>'reason', '') <> 'expired_sweep' "
        "ORDER BY user_id, COALESCE(NULLIF(fired_at, 0), fire_at)", cutoff)


async def main_async(args) -> int:
    conn = await _connect()
    try:
        total = 0

        if args.all or args.relation_self_loops:
            rows = await scan_self_loops(conn)
            print(f"\n① 自环关系边：{len(rows)} 条")
            for r in rows:
                print(f"   {r['user_id']}  {r['subject']} --{r['rel']}--> {r['object']}")
            total += len(rows)
            if rows and args.apply and args.relation_self_loops:
                await conn.execute("DELETE FROM memory_relation WHERE id = ANY($1)",
                                   [r["id"] for r in rows])
                print(f"   → 已删除 {len(rows)} 条")

        if args.all or args.relation_inverted:
            rows = await scan_inverted(conn)
            print(f"\n② 主宾颠倒 / 把地点当人：{len(rows)} 条")
            for r in rows:
                print(f"   {r['user_id']}  {r['subject']} --{r['rel']}--> {r['object']}")
            total += len(rows)
            if rows and args.apply and args.relation_inverted:
                await conn.execute("DELETE FROM memory_relation WHERE id = ANY($1)",
                                   [r["id"] for r in rows])
                print(f"   → 已删除 {len(rows)} 条")

        if args.all or args.relation_conflicts:
            groups = await scan_conflicts(conn)
            picked = args.conflict_subject or []
            n = sum(len(losers) for _k, _keep, losers in groups)
            print(f"\n③ 同 (subject, rel) 冲突值：{len(groups)} 组 / 可 supersede {n} 条")
            selected = []
            for (user, _occ, subject, rel), keep, losers in groups:
                mark = "  ← 本次点名" if subject in picked else ""
                print(f"   {user}  {subject} --{rel}-->{mark}")
                print(f"       保留：{keep['object']}")
                for loser in losers:
                    print(f"       失效：{loser['object']}")
                if subject in picked:
                    selected.append((keep, losers))
            if groups:
                print("   ⚠ ③ **必须人裁再 --apply**：脚本只会「留最长的那条」，"
                      "但两个不同的 object 可能是**真实变化**（转学/换公司）而不是错误；"
                      "且「孩子」与「女儿」很可能是同一个人分成了两个 subject"
                      "——**实体归一不在本脚本职责内**（Q5 的另一半）。")
                print("   ⚠ 因此 ③ 的 --apply 必须再点名 --conflict-subject <subject>，"
                      "**不接受整族一次清**：同一族内不同组的性质可以完全相反。")
            total += n
            if args.apply and args.relation_conflicts:
                if not picked:
                    print("   ✗ 未给 --conflict-subject，③ 不写库（见上一条）。",
                          file=sys.stderr)
                    return 2
                unknown = [s for s in picked
                           if s not in {k[2] for k, _keep, _l in groups}]
                if unknown:
                    print(f"   ✗ 点名的 subject 不在冲突组里：{unknown}——"
                          "**认不出就拒绝，不静默跳过**（B3 那条「认不出就用默认值」）。",
                          file=sys.stderr)
                    return 2
                done = 0
                for keep, losers in selected:
                    await conn.execute(
                        "UPDATE memory_relation SET superseded_by=$1 WHERE id = ANY($2)",
                        str(keep["id"]), [x["id"] for x in losers])
                    done += len(losers)
                print(f"   → 已标记 supersede {done} 条（不删行），"
                      f"点名 {len(selected)}/{len(groups)} 组")

        if args.all or args.reminders_invalid:
            rows = await scan_invalid_reminders(conn)
            print(f"\n④ fire_at<=0 却仍 pending 的提醒：{len(rows)} 条")
            for r in rows:
                print(f"   {r['user_id']}  fire_at={r['fire_at']}  {r['title'][:48]}")
            total += len(rows)
            if rows and args.apply and args.reminders_invalid:
                await conn.execute(
                    "UPDATE reminder_item SET status='cancelled' WHERE id = ANY($1)",
                    [r["id"] for r in rows])
                print(f"   → 已置 cancelled {len(rows)} 条")

        if args.all or args.reminders_expired:
            rows = await scan_expired_reminders(conn, args.expired_days)
            print(f"\n⑤ fired 后沉积超过 {args.expired_days} 天的提醒："
                  f"{len(rows)} 条")
            for r in rows:
                when = r["fired_at"] or r["fire_at"]
                print(f"   {r['user_id']}  fired_at={when}  {r['title'][:48]}")
            total += len(rows)
            if rows and args.apply and args.reminders_expired:
                batch = f"expired-{time.strftime('%Y%m%d')}"
                # 复用 `cancelled` + `extra.reason`（泓舟 2026-08-27 拍板的形态）：
                # 零 schema 变更，且 reason 分得开「用户取消」与「系统沉积清扫」。
                # **不物理删除**——审计要能回答「这条是谁清的、哪一批清的」。
                await conn.execute(
                    "UPDATE reminder_item SET status='cancelled', "
                    "extra = COALESCE(extra, '{}'::jsonb) "
                    "  || jsonb_build_object('reason', 'expired_sweep', "
                    "                        'batch', $2::text) "
                    "WHERE id = ANY($1)",
                    [r["id"] for r in rows], batch)
                print(f"   → 已置 cancelled + reason=expired_sweep {len(rows)} 条"
                      f"（batch={batch}）")

        print(f"\n合计命中 {total} 条。", end=" ")
        if args.apply:
            print("已按所选族写库。")
        else:
            print("**dry-run，未写库**。")
            print("⚠ 顺序纪律：清洗已获授权，但 --apply 排在 Q5/Q11 写入闸落地之后"
                  "——只清不修会再脏（§40 已证明一次）。")
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--relation-self-loops", action="store_true")
    ap.add_argument("--relation-inverted", action="store_true")
    ap.add_argument("--relation-conflicts", action="store_true")
    ap.add_argument("--conflict-subject", action="append", metavar="SUBJECT",
                    help="③ 逐组授权：只对点名 subject 的冲突组写库（可重复）。"
                         "③ 的 --apply 必须带它——同一族内不同组性质可以相反")
    ap.add_argument("--reminders-invalid", action="store_true")
    ap.add_argument("--reminders-expired", action="store_true")
    ap.add_argument("--expired-days", type=int, default=7, metavar="N",
                    help="⑤ 判「沉积」的天数阈值（默认 7）")
    ap.add_argument("--apply", action="store_true",
                    help="真正写库（只作用于同时点名的族；不点名族只盘点）")
    args = ap.parse_args()
    args.all = not any([args.relation_self_loops, args.relation_inverted,
                        args.relation_conflicts, args.reminders_invalid,
                        args.reminders_expired])
    if args.apply and args.all:
        print("--apply 必须与具体族一起用（逐族独立授权），不接受一次清全部",
              file=sys.stderr)
        return 2
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
