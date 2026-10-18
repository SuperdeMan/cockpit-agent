# 主机级维护（`deploy/host/`）

> 云主机由 car-agent 与同机 drone-agent 共用。本目录是主机级维护的唯一来源，由 car-agent 维护；改动前先与 drone-agent 取得共识。
> 两个组件：容量维护（`host-capacity-gc`，设计见 [容量治理方案](../../docs/design/2026-09-28-cloud-host-capacity-governance.md)
> §4.4–4.5，P2；§4.4 于 2026-10-09 修订、2026-10-13 二次修订）与[公网 SSH 闸](#公网-ssh-闸ssh-tailnet-guard)（`ssh-tailnet-guard`，2026-10-18）。
> 本目录不在 car-agent 的基础设施批准锚内（锚只覆盖 `deploy/cloud/**`），也不随发布安装；安装与更新属于系统配置，需逐次授权。

## 容量维护（`host-capacity-gc`）

### 安装清单

| 仓库文件 | 安装位置 | 属主与权限 |
|---|---|---|
| `deploy/host/host_capacity_gc.py` | `/usr/local/sbin/host-capacity-gc` | root 0755 |
| `deploy/host/host-capacity-gc.service` | `/etc/systemd/system/host-capacity-gc.service` | root 0644 |
| `deploy/host/host-capacity-gc.timer` | `/etc/systemd/system/host-capacity-gc.timer` | root 0644 |
| `deploy/host/journald-host-capacity.conf` | `/etc/systemd/journald.conf.d/60-host-capacity.conf` | root 0644 |

### 行为

- `host-capacity-gc.timer` 每小时触发一轮（随机延迟不超过 10 分钟），每轮做两件事：
  1. 删除 `/var/lib/apport/coredump` 下超过 7 天的 `core.*` 普通文件。不递归，其他条目只计数。
     注：apport 自带 tmpfiles 规则（`d /var/lib/apport/coredump 0755 root root 3d`），systemd-tmpfiles-clean 通常 3 天就先删掉了；
     它删完会恢复目录 mtime，要看目录 ctime 才知道删除时刻（2026-10-13 实测）。
  2. 只在磁盘紧张时回收构建缓存。任何一轮真删了记录的 prune 都会让 BuildKit 解绑缓存键，两个项目的下一次构建因此大面积冷启动，
     所以空间充足时完全不调用 prune。两个值读自 car-agent 已批准策略 `/opt/car-agent/shared/retention-policy.json`，
     由 car-agent 的 `retention.py` 校验，它是该策略唯一的校验者。每轮依次判断：
     - 可用不低于 `capacity.target_free_gib`（45 GiB）：`not_needed`，并清除退避状态。可用取 `/var/lib/docker/buildkit` 所在文件系统的 `f_bavail`，与构建闸 `df --output=avail` 同口径。
     - 上一次回收后可用仍未达到 `build_cache.prune_to_free_gib`（60 GiB），且当前可用还不低于「上次回收后可用 − 5 GiB」：`deferred`（退避），写 warning，提示人工清理缓存以外的数据。
     - 要回收但任一项目持锁：`skipped`。
     - 否则执行 `docker buildx prune --builder default --force --all`，一次清空全部可回收的缓存；回收后仍低于 `prune_to_free_gib` 就把回收后可用写进 `/var/lib/host-capacity-gc/state.json`。
     - 不按 `--min-free-space` 部分回收、也不留保底（2026-10-13 撤销 10 GiB 保底）：删了任何记录，留下的记录同样会被解绑、再也命中不了，部分回收与全清的代价相同，腾出的空间却少得多。
- 锁：对 car-agent 的 `/opt/car-agent/shared/locks/release.lock` 和 drone-agent 的 `/home/ubuntu/drone-agent/stack.lock`
  各做一次非阻塞 flock 探测，拿到后立即释放；要回收时任一被占，本轮就跳过。prune 期间不持有任何锁：两个项目都用非阻塞方式取锁，
  持锁会让对方的发布或批次直接失败。锁文件缺失或不是普通文件时，本轮以失败退出，不做 prune。
- 证据：每轮向 journald 写一行 JSON（`schema_version` 3）。
  - 每轮都记 `available_before` 与 `buildx du` 的 Total；真 prune 时另记 `reclaimed`、`available_after` 与 `total_after`。
  - 本机执行 `python scripts/dev_stack.py capacity` 时，输出的 `host_gc` 字段会只读带出最近一轮的状态、warning 与退避状态。
  - 退避与回收不达标写在 `warnings` 里，不算失败。
  - 策略无效、锁文件异常、退避状态损坏、docker 或删除失败时非零退出，unit 进入 failed。
  - 不调用 `docker buildx history`（2026-09-27 曾让共享 daemon panic）。
- journald：`SystemMaxUse=1G`；生效只需重启 `systemd-journald`，不影响容器。

### 安装与更新（系统配置，需授权）

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

### 查看

```bash
systemctl list-timers host-capacity-gc.timer
systemctl status host-capacity-gc.service
journalctl -u host-capacity-gc -o cat -n 5
journalctl --disk-usage
```

### 回滚（同样需授权）

```bash
systemctl disable --now host-capacity-gc.timer
rm /etc/systemd/system/host-capacity-gc.service /etc/systemd/system/host-capacity-gc.timer
rm /usr/local/sbin/host-capacity-gc /etc/systemd/journald.conf.d/60-host-capacity.conf
systemctl daemon-reload
systemctl restart systemd-journald
```

## 公网 SSH 闸（`ssh-tailnet-guard`）

2026-10-18 经用户批准（「1245都做」中的第 1 项「关掉云主机的公网 SSH」）。

### 为什么

- 公网 22 端口一直被互联网扫描器敲。2026-10-12 drone-agent 经用户批准加了 `/etc/ssh/sshd_config.d/60-drone-agent-startups.conf`
  （`PerSourceMaxStartups 5`、`MaxStartups 40:30:120`）之后，部署连接不再被节流误丢（10-12 → 10-18 被丢的都是扫描器），
  但 10-13 → 10-18 每天仍有 75–585 次未认证连接被 sshd 丢掉，攻击面一直开着。`LoginGraceTime 420` 是为微信扫码登录
  （`pam_qrlogin`）留的，未认证连接最长占 7 分钟名额。
- 三周（journald 覆盖 09-25 起）里所有成功登录都是同一把部署钥匙（`ubuntu` 账户）；10-09 之后全部来自 tailnet。
  car-agent 用 `CAR_AGENT_DEPLOY_HOST`（Tailscale 名字）连，drone-agent 没设 `DRONE_AGENT_DEPLOY_HOST` 时复用同一组变量。
  扫码登录这段时间零次。
- 主机是 CVM，云上的安全组本机没有凭证改；在主机内核里丢包效果相同，还能做到 fail-open（见下）。

### 安装清单

| 仓库文件 | 安装位置 | 属主与权限 |
|---|---|---|
| `deploy/host/ssh_tailnet_guard.py` | `/usr/local/sbin/ssh-tailnet-guard` | root 0755 |
| `deploy/host/ssh-tailnet-guard.service` | `/etc/systemd/system/ssh-tailnet-guard.service` | root 0644 |
| `deploy/host/ssh-tailnet-guard.timer` | `/etc/systemd/system/ssh-tailnet-guard.timer` | root 0644 |

### 行为

- 一张自己的 nft 表 `inet ssh_tailnet_guard`：input 钩子、优先级 -10（早于 iptables-nft 的 filter 链），`policy accept`，
  只有一条规则：TCP 22 的**新连接**不是从 `lo` / `tailscale0` 进来的就丢弃（不回 RST）。不碰 Docker、Tailscale（`ts-input`）、
  主机安全（`YJ-FIREWALL-INPUT`）各自的规则，也不启用 `nftables.service`（它的配置会 `flush ruleset`）。
- `ssh-tailnet-guard.timer` 每分钟跑一次 `check`：Tailscale 健康（有 `tailscale0`、`tailscale status --json` 的 `BackendState`
  是 `Running`、本机有 100.x 地址）就保证表在；不健康就删表，**公网 22 回到装闸前的样子**（只认钥匙）——Tailscale 坏了
  （包括节点钥匙到期，当前 2027-02-11）仍能把本机的 `CAR_AGENT_DEPLOY_HOST` 临时指回公网地址登进来修。恢复健康的下一轮重新上闸。
- 只认新连接：撤闸期间从公网登进来的会话，重新上闸后不会被掐断。nft 规则不跨重启，开机后第一轮（`OnBootSec=1min`）前公网 22 是开着的。
- 日志：没变化又健康的轮次不写；上闸 / 撤闸 / 不健康 / 手动命令各写一行 JSON（`schema_version` 1）到 journald。
- 代价：从 tailnet 之外再也连不上 22——腾讯云控制台的网页 SSH（OrcaTerm）与扫码登录也一样；控制台的 VNC 登录不受影响。
  安全组里的 22 规则刻意保留：关掉它就没有 fail-open 了。

### 安装（系统配置，需授权）

1. 告知 drone-agent（它也经 tailnet 连，不受影响；它自己的 sshd drop-in 保持不动）。
2. 从已提交的 SHA 取文件（git blob 保证 LF 行尾），上传后在主机上核对 sha256；目标文件不得已存在。
3. 以 root 执行。先排一个 5 分钟后自动撤闸的回滚，验收通过再取消、再开 timer：

```bash
install -o root -g root -m 0755 ssh_tailnet_guard.py /usr/local/sbin/ssh-tailnet-guard
install -o root -g root -m 0644 ssh-tailnet-guard.service ssh-tailnet-guard.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/ssh-tailnet-guard.service /etc/systemd/system/ssh-tailnet-guard.timer
systemctl daemon-reload
/usr/bin/python3 -I /usr/local/sbin/ssh-tailnet-guard check --dry-run      # 期望 action=applied
systemd-run --on-active=300 --unit=ssh-tailnet-guard-rollback /usr/bin/python3 -I /usr/local/sbin/ssh-tailnet-guard remove
systemctl start ssh-tailnet-guard.service                                    # 上闸
```

4. 验收（另开连接，不复用安装那条）：经 tailnet 新开一条 SSH 必须成功；从 tailnet 之外连公网地址的 22 端口必须超时
   （PowerShell `Test-NetConnection <公网地址> -Port 22`，装闸前先跑一次确认它原本是通的）；`nft list table inet ssh_tailnet_guard`
   的计数在涨。
5. 通过后：`systemctl stop ssh-tailnet-guard-rollback.timer`，`systemctl enable --now ssh-tailnet-guard.timer`。
   不通过就什么都不用做，5 分钟后回滚自动撤闸。

### 查看

```bash
/usr/bin/python3 -I /usr/local/sbin/ssh-tailnet-guard status
nft list table inet ssh_tailnet_guard
systemctl list-timers ssh-tailnet-guard.timer
journalctl -u ssh-tailnet-guard -o cat -n 5
```

### 回滚（同样需授权）

```bash
systemctl disable --now ssh-tailnet-guard.timer
/usr/bin/python3 -I /usr/local/sbin/ssh-tailnet-guard remove
rm /etc/systemd/system/ssh-tailnet-guard.service /etc/systemd/system/ssh-tailnet-guard.timer /usr/local/sbin/ssh-tailnet-guard
systemctl daemon-reload
```
