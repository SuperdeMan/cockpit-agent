# 腾讯云私网 demo 部署证据（2026-08-16）

## 1. 范围与边界

- 应用版本固定为 `4c1f479513c8b13564803ba43555a470aacbf640`；未部署本地 dirty main。
- 运行入口为仓库根 `compose.yaml` 加 `deploy/cloud/compose.cloud.yaml`。
- 未修改本地根 `.env`；云端运行配置独立生成并以 `0600 root:root` 保存。
- 未修改腾讯云安全组，未启用 Tailscale Funnel，未执行最终支付、商户写操作、真实车控、数据删除或整机重启。
- 服务器地址、实际 Tailnet 名称、token、密码和 provider 凭证不写入本证据。

## 2. 部署与隔离

| 检查项 | 结果 | 证据摘要 |
|---|---|---|
| 应用容器 | 通过 | 30/30 running，0 restarting/exited/dead |
| immutable image | 通过 | 26 个自建镜像均固定为 release SHA；服务器使用 `--no-build --pull never` |
| 持久卷 | 通过 | PostgreSQL、Redis、Collector 共 3 个稳定命名卷 |
| PostgreSQL 初始化 | 通过 | `public` schema 共 5 张表，容器健康 |
| Redis | 通过 | `PONG`，容器健康 |
| 宿主机业务监听 | 通过 | 仅 `127.0.0.1:5173/5174/8090/8092/50059` |
| 内部基础设施端口 | 通过 | PostgreSQL、Redis、NATS 均未发布到宿主机 |
| Tailnet HTTPS/WSS | 等待控制面授权 | 节点在线；Tailnet 尚未启用 Serve/HTTPS，未落地任何 Serve/Funnel 配置 |

## 3. 应用链路与鉴权

- HMI、Dashboard、Edge health、LLM provider API、Collector health 的 loopback HTTP 探针均返回 200。
- Edge WebSocket 无 token 与无效 token 均以 HTTP 401 拒绝。
- 合法 token 下，闲聊、天气、附近、导航、搜索、新闻、股票和体育请求均返回 `final`，且有非空话术。
- Collector WebSocket 首次连接与主动断开后的重连均收到 `snapshot`。
- HMI/Dashboard 由 Vite 运行时读取的 Tailnet API 基址已在实际返回模块中核对，未回落 localhost 默认值。
- 云端规划器已实测消费 `PERMISSIONS_FAIL_OPEN=false`；修复前该键只在 `.env` 中存在但未注入容器。
- 本轮没有发起商户请求、支付请求或车控请求。导航探针只验证路线规划，不执行真实车辆动作。

## 4. Provider 真实调用

| Provider | 运行时结论 | 证据摘要 |
|---|---|---|
| 高德 | real / 通过 | geocode、POI text/around trace 均为 HTTP 200 |
| 和风天气 | real / 通过 | 城市、实况、指数、空气质量、预警、预报均为 HTTP 200 |
| Exa | real / 通过 | `provider.exa.web_search` HTTP 200 |
| AnySearch | real / 通过 | 独立只读检索返回 2 条有效结果 |
| SerpAPI | real / 通过 | `SerpApiNewsProvider` 独立只读新闻查询返回 2 条有效结果 |
| Tushare | real / 通过 | daily 与 daily_history 均为 HTTP 200 |
| API-Football | real / 通过 | fixtures HTTP 200 |
| DeepSeek | 通过 | 独立 probe 200；业务 trace 实际使用 `deepseek-v4-flash` |
| MiniMax | 通过 | 独立 probe 200，模型 `MiniMax-M3` |
| 通义千问 | 通过 | 独立 probe 200，模型 `qwen3.7-max` |
| MiMo | 失败 | 独立 probe 502，分类为鉴权失败；业务请求按配置回退到 DeepSeek |

网络成功、业务解析和运行时 real/mock 分开判定；表内“通过”均有解析结果或运行时 trace，不以单纯 TCP/HTTP 可达代替。

## 5. 备份

- `car-agent-backup.timer` 已启用且处于 active，已排定下一次执行。
- 首次手动备份最终 `Result=success`。
- PostgreSQL custom dump、Redis RDB、Collector SQLite SQL gzip 三类备份均已生成，权限为 `0600 root:root`。
- PostgreSQL 备份通过 `pg_restore --list`，Collector 备份通过 `gzip -t`，Redis 文件头为 `REDIS`。
- 备份任务只生成 `cleanup-candidates.txt`，不自动删除数据。
- 首次失败留下一个 0 字节 `.partial` 文件；因本次授权明确禁止删除，保持原位并不纳入有效备份。

## 6. 实施中抓修与本地验证

实施中按失败证据修复三项：

1. legacy 三段式 `AUTH_TOKENS` 自动升级为带 scopes 的四段式，并轮换 demo token；
2. Compose `ports: !reset` 改为 `!override`，确保合并后五个入口只绑定 loopback；
3. 备份任务显式解析 active release 的 Compose project，并将 `PERMISSIONS_FAIL_OPEN` 注入 cloud-planner。

本地 fresh 验证：`31 passed, 1 skipped`；`backup.sh` Bash 语法通过；`git diff --check` 通过。修复提交为 `55bada2`，仅存在于本地部署分支，未 push、未合并 main。

## 7. 尚需人在环验证

1. 在 Tailscale 管理台启用 Serve/HTTPS 后，配置 443/8443–8446 的五个 Tailnet-only 反向代理并验证有效证书与 WSS；禁止 Funnel。
2. Android 安装并登录 Tailscale 后，验证 HMI/Dashboard、麦克风权限、ASR/TTS、前后台与网络切换重连，以及关闭 Tailscale 后不可达。
3. 腾讯云安全组仅做控制台只读复核；本次没有修改授权，服务器侧已确认无公网业务监听。
4. MiMo 凭证需单独轮换或修正后重跑 probe；在此之前保留 DeepSeek fallback。
