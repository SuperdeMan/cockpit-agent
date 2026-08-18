# Redis 迁移 apply 失败：两条根因与修法（2026-08-18）

> 起点是 [`2026-08-17-cloud-data-migration-handoff.md`](../reviews/2026-08-17-cloud-data-migration-handoff.md) §3
> 那句「现有证据只能说明 apply 路径处理本地 Redis 包失败，不能据此宣称是 RDB 损坏、AOF 转换、
> 权限、挂载或数据内容中的任何一个具体根因」。本文把它收敛成**两条确定性复现的根因**，
> 并记下每一条是怎么被钉死的。交接页的约束逐条遵守：输入包只读、不挂载云端活动 Redis 卷、
> 不停云端服务、只记类别/退出码/摘要。

## 0. 结论先行

| # | 根因 | 位置 | 现象 |
|---|---|---|---|
| RC1 | key-control 体积上限写死 1 MiB，而真实 manifest 是 **7.3 MB** | `deploy/cloud/store_identity_evidence.py::_load_key` | `redis-restore` rc=1，且**只留 rc**、不留原因 |
| RC2 | 逻辑指纹取 `sha1(DUMP(key))`，对 `hashtable` 编码的对象**按进程随机顺序序列化** | `store_identity_evidence.py::REDIS_PAGE_LUA` | 修完 RC1 后**紧接着**倒在 `assert_redis_container_matches_manifest` |

**两条都不是数据坏了，也不是「有人还在写」**——本地写入方在快照前 1 小时 52 分钟就全停了
（容器 `FinishedAt` 逐个核过：13:45:30–33 UTC，快照 15:37:43 UTC）。

## 1. 怎么定位到 RC1 的（时间戳是唯一可靠的向导）

云端 `journal.json` 只给了 `failed_step=redis-restore, rc=1`。但把三处时间戳并排看就唯一了：

| 时刻 (UTC) | 事件 | 来源 |
|---|---|---|
| 15:39:38 | journal 记 `REPLACING / redis / started` | `journal.json` |
| 15:39:40.797 | prepare 写完 `redis-replace.json`（`phase=prepared`） | rollback 桶文件 mtime |
| 15:39:41.209 | loader 容器入网 | `journalctl -u docker`（`sbJoin ep=…redis-loader`） |
| 15:39:41.665 | loader 的 task 被删 | 同上（`TaskDelete`） |
| 15:39:41 | journal 记 `APPLY_FAILED` | `journal.json` |

**判据一**：`restore_redis_rdb` 里等 loader 的 `PING` 循环是 `seq 1 60` + `sleep 1`。
失败发生在 loader 起来后 **0.45 秒**，⇒ **那个循环根本没跑满**，
所以失败不在「loader 装不进数据」这一族（版本错配、RDB 损坏都属于这一族，全被排除）。
loader 是被 `docker rm -f` 主动删的——那只出现在 PING 之后的失败分支里。

**判据二**：`assert_redis_container_matches_manifest` 失败时**不会**删除它写到一半的
`.redis-verify.<run>.json.partial`（`os.unlink` 只在成功路径上）。批次目录里**没有**这个文件
⇒ 失败发生在写证据**之前**，即 `store_identity_evidence.py` 自己启动那一步。

顺着这两条读源码，`_load_key` 的 `st_size > 1024 * 1024` 就是唯一能在这个时间窗里
无声退出 1 的东西。本机验证：同一条命令，把 `--key-control` 从 7.3 MB 的 manifest
换成 89 字节的等价文件，`rc=1` → `rc=0`。

**为什么回滚成功**：回滚这一步传的是 `${BACKUP_ROOT}/${stamp}.backup-manifest.json`
——云端自己那份备份的 manifest。云端 Redis 只有 33 KB 数据，manifest 远不到 1 MiB。
> **「apply 失败而 rollback 成功」的全部原因就是这个阈值，不是别的。**

判据沉淀：**一个只有一种真实输入的守卫，阈值必须按那个输入定。**
`--key-control` 的 4 个调用点**全部传 manifest**，1 MiB 是照着「小密钥文件」写的，
而这条路径从来没收到过小密钥文件。

## 2. RC2：`DUMP` 不是逻辑身份

修完 RC1 之后紧跟的比对仍会失败。取证过程本身是最值钱的部分：

1. 拿失败批次的 `redis.rdb` 在本机隔离卷里走完整条 apply 路径（prepare → loader →
   AOF 建立 → SAVE → `--complete`），**全通**；DBSIZE 3299 与 manifest 逐字一致
   ⇒ **包不坏、工具链不坏**。
2. 把扫描结果与 manifest 比：3299 条里 **3 条 `logical` 不一致**（`identity` 全对）。
3. 排除「有人还在写」：本地写入方 13:45 UTC 全停，快照 15:37。
4. 排除「副本改坏了」：**最初那次真 `snapshot` 命令产的批次（13:45:08）对它自己的 rdb
   也是同样 3 条**。
5. **决定性一步**：把同一个 `redis.rdb` 用两个独立容器各载一次，两次扫描互比
   ——**同样那 3 条互不相同**，加上 manifest 那次，**三次三个值**。

⇒ 指纹不是数据的函数，是**进程的函数**。

命中的对象精确落在一族上：

| key | 类型 | 编码 | 规模 |
|---|---|---|---|
| `payment:*` ×2 | hash | **hashtable** | 24 字段（有值 >64B ⇒ 越过 `hash-max-listpack-value`） |
| `user_sessions:*` | set | **hashtable** | 1340 成员 |

其余 3296 个（`sess:` 列表 listpack / 字符串 / 小集合）**一条不差**。

机制：`DUMP` 对 dict 型编码按**内部桶序**序列化，而桶序取决于 Redis 每个进程随机的
hash seed；listpack / quicklist / intset 是顺序结构，序列化确定。合成实验另证一条相关效应：
把集合涨到 600 成员再删到 2，内存里停在 `hashtable`，其 `DUMP` 与原生小集合不同，
**而存盘重载后归一成 listpack、指纹随之改变**。

> **这不是边缘情况，是会随数据增长必然发生的**：每人会话数越过 128、支付单变多，
> 就会有更多对象落进 hashtable 编码，迁移身份断言从此不可能通过。

## 3. 修法

### RC1
`MAX_KEY_CONTROL_BYTES = 64 MiB`，按本工具自己的行数上限（`MAX_ITEMS` / `MAX_COLLECTOR_ITEMS`）
反推最坏 ~32 MB 再留一倍余量。**symlink 守卫保留**——那条才是真正的安全性质。

### RC2
逻辑材料改成**编码无关的规范化形式**再 sha1：

| 类型 | 规范化 |
|---|---|
| string | 值 |
| list | 元素**按原序**（顺序有语义） |
| set | 成员**排序后** |
| hash | 字段/值对**按字段排序后** |
| zset | 成员/分数**按成员排序后** |
| 其他 | **fail closed**（`error("unsupported Redis value type: …")`） |

逐元素带长度前缀 `<len>:<bytes>`（二进制安全、无拼接歧义）。值读取切回 RESP2 拿扁平数组，
返回前切回 RESP3——**对调用方的 `map={…}` 契约不变**。

⚠ **manifest 里 digest 的语义变了**：旧批次与新证据不可比，必须重新生成 final 批次。
（本来也要重生成——旧批次带的就是不稳定指纹。）

同一条影响还波及**修复前产出的云端周期备份**（`backup.sh` 由 systemd timer 定期跑，
其 backup-manifest 里是旧式指纹）：用**修复后**的代码去回滚一份**修复前**的备份，
身份比对会不通过。方向是安全的（fail closed，不会把错数据当对的放行），但要知道这件事——
真要回滚老备份，得用与那份备份同代的工具，或先接受「只校验 key 集合不校验值」的降级并留痕。

性能：规范化要逐 key 读全值，实测 3299 个 key（3062 list / 205 string / 25 set / 7 hash）
**3.7 秒**、约 13 页，而 `_redis_page` 每页超时 30 秒——没有引入新的超时风险。

## 4. 回归（三条，反向验证都做了）

| 用例 | 守什么 | 对修前代码 |
|---|---|---|
| `test_identity_key_control_bound_is_sized_for_real_migration_manifests` | >1 MiB 的 manifest 必须被接受；把上限调小又必须被拒（守卫没被删，只是改对了尺寸） | 红 |
| `test_redis_logical_material_is_canonical_not_dump_order` | 源码级：不许再出现 `DUMP`、集合必须先排序、未知类型 fail closed、长度前缀在 | 红 |
| `test_redis_logical_digest_is_insertion_order_and_rdb_round_trip_stable` | 行为级（真 Redis）：①正序/逆序写入指纹相同 ②RDB 往返 + 换进程指纹逐字不变 ③改一个成员必须只有那一行变 | 红（倒在①，正是 DUMP 缺陷） |

另有一条**既有**断言按新语义改判而非回退防御：
`test_redis_identity_evidence_scans_pages_without_keys_or_full_table_lua` 原本把
「扫描中途消失的 key 不进证据」写死成 `if ttl ~= -2 and dump then`——把不变量与
「用 DUMP 取值」绑成一件事。现在钉三件缺一不可的事（ttl 哨兵 / 类型哨兵 / 取值拿不到就跳过），
并验过它对修前代码仍红（不是恒绿）。

## 5. 还没做的（要授权）

1. `deploy/cloud/**` 变了 ⇒ **下次真实迁移前必须先做受控基础设施安装 + 摘要更新**
   （`release-infrastructure.json` 的聚合摘要会对不上）。
2. **重新生成 final 批次**（不复用旧 ID），且需要本地停写授权。
3. `apply --apply` 与其后的切换按交接页 §4 第 7/8 步逐关取授权。
