# 主机级容量维护（`deploy/host/`）

> 云主机由 car-agent 与同机 drone-agent 共用。本目录是主机级维护的唯一来源，由 car-agent 维护；改动前先与 drone-agent 取得共识。
> 设计见 [容量治理方案](../../docs/design/2026-09-28-cloud-host-capacity-governance.md) §4.4–4.5（P2；§4.4 于 2026-10-09 修订）。
> 本目录不在 car-agent 的基础设施批准锚内（锚只覆盖 `deploy/cloud/**`），也不随发布安装；安装与更新属于系统配置，需逐次授权。

## 安装清单

| 仓库文件 | 安装位置 | 属主与权限 |
|---|---|---|
| `deploy/host/host_capacity_gc.py` | `/usr/local/sbin/host-capacity-gc` | root 0755 |
| `deploy/host/host-capacity-gc.service` | `/etc/systemd/system/host-capacity-gc.service` | root 0644 |
| `deploy/host/host-capacity-gc.timer` | `/etc/systemd/system/host-capacity-gc.timer` | root 0644 |
| `deploy/host/journald-host-capacity.conf` | `/etc/systemd/journald.conf.d/60-host-capacity.conf` | root 0644 |

## 行为

- `host-capacity-gc.timer` 每小时触发一轮（随机延迟不超过 10 分钟），每轮做两件事：
  1. 删除 `/var/lib/apport/coredump` 下超过 7 天的 `core.*` 普通文件。不递归，其他条目只计数。
  2. 只在磁盘紧张时回收构建缓存。任何一轮真删了记录的 prune 都会让 BuildKit 解绑缓存键，两个项目的下一次构建因此大面积冷启动，
     所以空间充足时完全不调用 prune。三个值读自 car-agent 已批准策略 `/opt/car-agent/shared/retention-policy.json`，
     由 car-agent 的 `retention.py` 校验，它是该策略唯一的校验者。每轮依次判断：
     - 可用不低于 `capacity.target_free_gib`（45 GiB）：`not_needed`，并清除退避状态。可用取 `/var/lib/docker/buildkit` 所在文件系统的 `f_bavail`，与构建闸 `df --output=avail` 同口径。
     - 上一次回收后可用仍未达到目标线，且当前可用还不低于「上次回收后可用 − 5 GiB」：`deferred`（退避），写 warning，提示人工清理缓存以外的数据。
     - 要回收但任一项目持锁：`skipped`。
     - 否则执行 `docker buildx prune --builder default --force --all --min-free-space <字节> --reserved-space <字节>`，一次清到可用 ≥ `build_cache.prune_to_free_gib`（60 GiB），缓存至少留 `build_cache.reserved_space_gib`（10 GiB）；仍未达标就把回收后可用写进 `/var/lib/host-capacity-gc/state.json`。
     - BuildKit 的 `--min-free-space` 比较的是 `Bfree`（含 root 保留块），所以传入值 = 目标线 + 当轮实测的保留块字节。两个参数都按字节传，避开 `GB` 被按 1024 进制解析的坑。
- 锁：对 car-agent 的 `/opt/car-agent/shared/locks/release.lock` 和 drone-agent 的 `/home/ubuntu/drone-agent/stack.lock`
  各做一次非阻塞 flock 探测，拿到后立即释放；要回收时任一被占，本轮就跳过。prune 期间不持有任何锁：两个项目都用非阻塞方式取锁，
  持锁会让对方的发布或批次直接失败。锁文件缺失或不是普通文件时，本轮以失败退出，不做 prune。
- 证据：每轮向 journald 写一行 JSON（`schema_version` 2）。
  - 每轮都记 `available_before` 与 `buildx du` 的 Total；真 prune 时另记 `available_after`、`total_after` 与实际传入的 `min_free_space`。
  - 退避与回收不达标写在 `warnings` 里，不算失败。
  - 策略无效、锁文件异常、退避状态损坏、docker 或删除失败时非零退出，unit 进入 failed。
  - 不调用 `docker buildx history`（2026-09-27 曾让共享 daemon panic）。
- journald：`SystemMaxUse=1G`；生效只需重启 `systemd-journald`，不影响容器。

## 安装与更新（系统配置，需授权）

1. 与 drone-agent 约好窗口。
2. 从已提交的 SHA 取文件（git blob 保证 LF 行尾），上传后在主机上核对 sha256。新装时目标文件不得已存在；更新前先备份旧文件。
3. 以 root 执行：

```bash
install -o root -g root -m 0755 host_capacity_gc.py /usr/local/sbin/host-capacity-gc
install -o root -g root -m 0644 host-capacity-gc.service host-capacity-gc.timer /etc/systemd/system/
install -d -o root -g root -m 0755 /etc/systemd/journald.conf.d
install -o root -g root -m 0644 journald-host-capacity.conf /etc/systemd/journald.conf.d/60-host-capacity.conf
systemd-analyze verify /etc/systemd/system/host-capacity-gc.service /etc/systemd/system/host-capacity-gc.timer
systemctl daemon-reload
systemctl restart systemd-journald
/usr/bin/python3 -I /usr/local/sbin/host-capacity-gc --dry-run
systemctl enable --now host-capacity-gc.timer
systemctl start host-capacity-gc.service   # 手动触发首轮
```

4. 记录首轮前后的 `buildx du` Total、可用空间和 timer 下次触发时间，并告知 drone-agent。

**更新**只重装变了的文件（脚本与 service 用上面前两行 `install`；只改脚本时不必 `daemon-reload`），之后先跑一次 `--dry-run` 看判断结果。
脚本读的策略与 `retention.py` 随 car-agent 的基础设施锚安装，与本目录分开。两边改了同一份策略约定时（如 2026-10-09 的 P2 修订），
先批准锚、装好新策略与 `retention.py`，再紧接着装本目录的文件。中间这段时间两边新旧错配，旧脚本读不到新键，会以失败退出、不 prune。

## 查看

```bash
systemctl list-timers host-capacity-gc.timer
systemctl status host-capacity-gc.service
journalctl -u host-capacity-gc -o cat -n 5
journalctl --disk-usage
```

## 回滚（同样需授权）

```bash
systemctl disable --now host-capacity-gc.timer
rm /etc/systemd/system/host-capacity-gc.service /etc/systemd/system/host-capacity-gc.timer
rm /usr/local/sbin/host-capacity-gc /etc/systemd/journald.conf.d/60-host-capacity.conf
systemctl daemon-reload
systemctl restart systemd-journald
```
