# 云端私网部署资产约定

本目录只保存云端运行所需的 Docker Compose override、备份脚本和 systemd 单元。密钥、token、密码、DSN、服务器地址和实际 tailnet 名称不得写入本目录或提交到 Git。

## 运行入口

- 第一份 Compose 文件始终是仓库根 `compose.yaml`。
- 第二份文件才是本目录的 `compose.cloud.yaml`。
- 根 `.env` 是唯一运行时配置来源；服务器 release 根 `.env` 使用符号链接指向共享配置。
- 禁止把 `deploy/docker-compose.yaml` 作为第一份 Compose 文件启动。

## 暴露面与镜像

- override 清除基础 Compose 的全部宿主机端口，只恢复五个 `127.0.0.1` 入口。
- PostgreSQL、Redis、NATS、内部 gRPC、支付网关和出站代理不发布到宿主机。
- 26 个自建服务逐一使用 `car-agent-release/<实际服务名>:${RELEASE_SHA}`，设置 `pull_policy: never`，并清除原 `build` 定义。
- 服务器只允许 `--no-build --pull never` 启动，不能临时构建或补拉镜像。

## 数据与备份

- PostgreSQL、Redis 和 Collector 使用稳定命名卷。
- 禁止执行 `docker compose down -v`。
- 自动备份不得包含或复制 `.env`。
- 自动任务只创建备份并列出超过 7 天的清理候选，不得自动删除文件。
- 任何 release、worktree、镜像归档、备份或数据卷清理都必须先列出精确对象并取得批准。

## 服务器目录

```text
/opt/car-agent/
├── current -> releases/4c1f479
├── releases/4c1f479/
└── shared/
    ├── .env
    ├── compose.cloud.yaml
    ├── bin/backup.sh
    ├── backups/
    └── evidence/
```

每个 release 根 `.env` 都是指向 `/opt/car-agent/shared/.env` 的符号链接。共享配置权限必须为 `0600`，不得在 release 中复制第二份。

## 唯一运维命令形态

登录服务器后先进入当前 release：

```bash
cd /opt/car-agent/current
```

后续所有 Compose 命令都按下面的文件顺序和环境文件执行：

```bash
sudo docker compose \
  -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml \
  --env-file /opt/car-agent/shared/.env \
  config --quiet
```

常用只读检查：

```bash
sudo docker compose \
  -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml \
  --env-file /opt/car-agent/shared/.env ps

sudo docker compose \
  -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml \
  --env-file /opt/car-agent/shared/.env logs --tail=200 edge-gateway
```

应用配置或恢复单服务时使用 immutable image，不允许构建或拉取：

```bash
sudo docker compose \
  -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml \
  --env-file /opt/car-agent/shared/.env \
  up -d --no-build --pull never edge-gateway
```

不要运行 `docker compose down -v`。普通 `down` 也不是日常重启手段；优先用上面的 `up -d` 收敛目标状态。

## Tailscale 与调试入口

Android 必须打开 Tailscale 后才能访问 HMI/Dashboard；关闭 Tailscale 后不可达是正确的安全表现。Tailscale Serve 只反代五个宿主机 loopback 入口，不使用 Funnel：

| Tailnet HTTPS | 宿主机 upstream | 用途 |
|---|---|---|
| `443` | `127.0.0.1:5173` | HMI |
| `8443` | `127.0.0.1:8090` | Edge Gateway HTTP/WS |
| `8444` | `127.0.0.1:50059` | ASR/TTS/S2S |
| `8445` | `127.0.0.1:5174` | Dashboard |
| `8446` | `127.0.0.1:8092` | Collector HTTP/WS |

电脑长期运行其他 VPN 时优先使用 SSH 隧道，不要求安装 Tailscale：

```powershell
ssh -N `
  -L 15173:127.0.0.1:5173 `
  -L 18090:127.0.0.1:8090 `
  -L 15059:127.0.0.1:50059 `
  -L 15174:127.0.0.1:5174 `
  -L 18092:127.0.0.1:8092 `
  ubuntu@SERVER_IP
```

`SERVER_IP` 只在交互终端替换为实际值，不写入仓库。Dashboard 当前命令栏不会携带 HMI 的静态 WS token；`AUTH_REQUIRED=true` 时它只用于查看 Collector trace，不作为发指令入口。

## 备份与清理候选

手动运行并查看定时器：

```bash
sudo systemctl start car-agent-backup.service
sudo systemctl status car-agent-backup.service --no-pager
sudo systemctl list-timers car-agent-backup.timer --no-pager
```

备份位于 `/opt/car-agent/shared/backups/`。超过 7 天的文件只会被列到：

```text
/opt/car-agent/shared/backups/cleanup-candidates.txt
```

该文件不是删除清单的自动执行入口。需要释放空间时先展示候选、大小与时间，取得批准后再单独处理。

## 应用回滚

回滚只切换应用 release 和容器镜像，不自动回滚 PostgreSQL/Redis/Collector 数据。执行前确认上一 release 目录和全部 SHA 镜像确实存在，并确认 schema 向后兼容：

```bash
read -r -p 'Previous release SHA: ' PREVIOUS_SHA
test -d "/opt/car-agent/releases/${PREVIOUS_SHA}"
sudo ln -sfn "/opt/car-agent/releases/${PREVIOUS_SHA}" /opt/car-agent/current.next
sudo mv -Tf /opt/car-agent/current.next /opt/car-agent/current
cd /opt/car-agent/current
sudo docker compose \
  -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml \
  --env-file /opt/car-agent/shared/.env \
  config --quiet
sudo docker compose \
  -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml \
  --env-file /opt/car-agent/shared/.env \
  up -d --no-build --pull never
```

首次部署没有旧 release，不做虚构的回滚演练。涉及不兼容 schema 时另立数据库迁移/回滚方案并重新审批。
