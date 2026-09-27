# 2026-09-27：构建历史查询触发 Docker daemon 崩溃

## 1. 事实与影响

CA2-05 候选构建完成 24/26 个镜像后，HMI 构建等待时间偏长。执行者为定位构建进度，
运行了只读命令 `docker buildx history ls --format=json`。该查询返回 EOF，Docker 29.1.3 的
`ListenBuildHistory → filterHistoryEvents → slices.SortFunc` 随即发生空指针 panic（history.go:1074）。
这是本轮执行者的诊断操作触发的基础设施故障，不能归为项目新代码发布成功或一般构建失败。

2026-09-27 13:18:26（Asia/Shanghai），systemd 自动重启 daemon；日志记录 exit-code 2、restart counter 1。
未见内核 OOM 记录，容器 OOMKilled 均为 false。car-agent 的 30 个容器及当时运行的 7 个 drone-agent
容器停止；候选没有激活，原发布指针未改变，五个 car-agent 外部端点一度均返回 502。

相同函数/行号的上游缺陷见 [moby/moby #52257](https://github.com/moby/moby/issues/52257)。
本地堆栈是此次归因依据；上游报告的其它版本不作为本机升级已修复的证据。

## 2. 恢复与验证

- 按既有发布恢复 car-agent 原 30 个容器；先数据服务和代理，再业务服务。未重建数据卷、未改变配置。
- 7 个 drone-agent 容器按原 ID/镜像取得用户单独授权后恢复；其余原本已停止的容器未启动。
  后续读回 7/7 running，desk、console、SITL 的现有 healthcheck 均 healthy。
- car-agent status 恢复 5/5 healthy、零 warning、发布/运行 SHA 一致。
  首次完整 verify 未通过；远端结构复核通过后重跑，恢复版 verify 为 `verified`。
  精确发布身份与最新证据统一见 [QA 交接 §2](2026-08-30-qa-closeout-handoff.md)。
- 续建临时脚本曾因只读全局变量与原发布函数的局部变量重名而停止收尾；当时 26 个镜像已完成但尚未切换。
  修正脚本作用域、再次验证原归档/源码/26 个镜像后，仅补齐原有清单与激活阶段，没有重建或覆盖既有镜像。
- 服务恢复不等于在途任务与瞬态车态无变化；缺少故障前内存状态快照，不作无损恢复声明。
- 本轮没有升级 Docker、主动重启系统服务、修改 .env、网络策略或数据库 schema。

## 3. 接续约束

1. 当前共享主机禁止构建历史 API，包括 `docker buildx history` / `ListenBuildHistory`；不能认为查询必然无进程影响。
2. 构建进度读取本次 image-inventory.tsv 和本次构建输出；不得重复触发该崩溃路径来“验证已复现”。
3. 中断构建保留源码、归档、已建镜像与证据。续建须核对原归档 hash、逐文件源码和既有镜像 ID，
   持有 release 锁、重新通过容量检查；只建缺项，随后仍走原备份/激活/验证/失败回退函数。
4. 当前规避只是不再调用问题 API。Docker/BuildKit 的版本修复另做受控运维评审，不能在本轮擅自升级共享宿主。

## 4. 本地证据

`.artifacts/capability-v2/` 中保留：`33c2a731-daemon-panic-stack.json`、`33c2a731-outage-diagnosis.json`、
`33c2a731-old-release-restored.json`、`collateral-stopped-containers.json`、`collateral-restoration-followup.json`、
`02363932-restored-status.json`、`02363932-restored-verify-retry.log`。
恢复 verify 为 `.artifacts/dev-stack-verifications/20260927T053340Z-0236393.json`，MiniMax-M3。
这些是 ignored 的本机证据，不随 clone 分发；不得据此推断任何后续 release 已通过。
