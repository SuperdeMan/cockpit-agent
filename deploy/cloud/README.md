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

### 受控运行时模型

- `runtime-models.json` 是服务器端与客户端运行时模型的统一哈希清单；远端构建在任何镜像构建前逐文件校验。
- Edge NLU 与 CAM++ 声纹位于 `/opt/car-agent/shared/models/{nlu,voiceprint}`，分别进入 edge-orchestrator 与 llm-gateway。
- Silero VAD 与 sherpa-onnx KWS 四件套位于 `/opt/car-agent/shared/models/hmi/public/**`，只在云端 HMI 专用构建中按精确文件名复制进镜像；不得用目录通配把训练包、测试音频或其他忽略文件带入镜像。
- HMI 镜像只是向浏览器提供这些静态文件。VAD/KWS 推理仍在电脑或手机浏览器本地执行，唤醒前音频不因模型交付方式改变而上传云端。
- 模型二进制继续由 `.gitignore` 排除，不进入提交。新增、替换文件或改变任何 SHA-256 都属于基础设施变更，必须重新审查并更新 `release-infrastructure.json` 后才能发布。

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
    ├── vite.hmi.cloud.config.mjs
    ├── bin/backup.sh
    ├── backups/
    └── evidence/
```

每个 release 根 `.env` 都是指向 `/opt/car-agent/shared/.env` 的符号链接。共享配置权限必须为 `0600`，不得在 release 中复制第二份。
`vite.hmi.cloud.config.mjs` 只为固定镜像内的 Vite 5 注入当前 Tailnet Host 白名单，必须以只读方式挂载；不得把公网域名或通配 Host 写入该文件。

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

## 固定代码发布工作流

在仓库根目录使用同一个入口。`plan` 会读取 Git 和远端状态，但不写服务器；`deploy` 不带 `--apply` 也只是 dry-run：

```powershell
python scripts/cloud_release.py plan --sha HEAD
python scripts/cloud_release.py deploy --sha HEAD
python scripts/cloud_release.py deploy --sha HEAD --apply
python scripts/cloud_release.py verify
python scripts/cloud_release.py rollback --to 4c1f479 --apply
```

连接信息只通过命令行参数或以下环境变量提供，不写入仓库和发布 manifest：

| 环境变量 | 用途 |
|---|---|
| `CAR_AGENT_DEPLOY_HOST` | SSH 主机名或地址 |
| `CAR_AGENT_DEPLOY_USER` | SSH 用户名，默认 `ubuntu` |
| `CAR_AGENT_SSH_IDENTITY` | 本机 SSH 私钥路径 |
| `CAR_AGENT_SSH_KEX_ALGORITHMS` | 服务器明确要求时使用的 KEX 算法 |

首次运行预期返回 `bootstrap_required`：服务器还没有满足新工作流所需的共享 scripts/models。`plan` 只列出源、目标路径、权限和模型 SHA-256，不生成复制命令；脚本来源必须是受审目标提交，服务端模型来源必须是当前已验证 release，HMI 客户端模型来源必须是哈希匹配的已批准本地资产。首次 bootstrap 必须单独批准并完成以下共享底座：runtime project 名、五个脚本、九个运行时文件，以及 `/opt/car-agent/shared/release-infrastructure.json`。

`release-infrastructure.json` 是唯一的基础设施批准锚，记录受审提交的 `deploy/cloud/**` 聚合摘要、逐文件摘要及安装位置。普通 deploy 只能读取，不能创建或更新它。普通代码变化一旦命中 `deploy/cloud/**`、Compose、数据库 schema、`.env.example`、CI/CD 或密钥材料，流程立即停止并要求重新审查。

发布事务遵守以下边界：

- 构建 26 个镜像时，`current` 和现有 30 个容器保持不变；完成全部镜像与备份后才切换。
- 上传中断、构建失败和验收失败的目录都保留为诊断/清理候选，不自动清理。
- merge、git push、首次真实 `deploy --apply` 和每次 `rollback --apply` 分别取得授权。
- 普通发布不修改 `.env`、Tailscale Serve、安全组、systemd、数据库 schema 或数据。

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

回滚只切换应用 release 和容器镜像，不自动回滚 PostgreSQL、Redis 或 Collector 数据。先用不带 `--apply` 的命令检查目标，再经单独授权执行：

```powershell
python scripts/cloud_release.py rollback --to 4c1f479
python scripts/cloud_release.py rollback --to 4c1f479 --apply
```

入口会在同一事务锁内校验目标目录、全部 SHA 镜像和备份状态，切换后执行完整验收；失败时恢复原 release。首次部署没有第二个 release，不做虚构的回滚演练。涉及不兼容 schema 时另立数据库迁移/回滚方案并重新审批。
