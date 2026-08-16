# Cloud Release Verification Hotfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复云发布验证器把字面量 `\\t` 当分隔符传给 `curl` 的缺陷，并保证验证函数内部的 `die` 只终止验证子进程，使激活流程可以执行既有回滚逻辑。

**Architecture:** 保留现有验证与回滚职责边界。HTTPS 端点表改由 Bash `printf` 生成真实制表符；激活、显式回滚和回滚后复验均在子 shell 中调用 `verify_release`，把失败转换为可由父事务捕获的非零状态。

**Tech Stack:** Bash, Python 3.12, pytest, Git worktree, Tencent Cloud release scripts.

---

## Task 1: 锁定两个生产故障

**Files:**

- Modify: `scripts/tests/test_cloud_deploy_assets.py`

- [x] 新增行为测试：以 fake `curl` 运行 `verify_https_endpoints`，断言五个 URL 独立且合法。
- [x] 新增反例测试：`verify_release` 调用 `die` 时，激活流程仍恢复旧 `current` 并记录终态。
- [x] 先运行两条测试并确认在现有实现上失败。

## Task 2: 最小修复

**Files:**

- Modify: `deploy/cloud/verify-release.sh`
- Modify: `deploy/cloud/activate-release.sh`

- [x] 用 `printf` + process substitution 输出真实 tab 分隔的五条 HTTPS 端点。
- [x] 在三个事务捕获点以子 shell 调用 `verify_release`。
- [x] 运行新增测试和完整 `scripts/tests/test_cloud_deploy_assets.py`。

## Task 3: 集成、发布与清理

**Files:**

- Modify: `docs/superpowers/plans/2026-08-16-cloud-release-verification-hotfix.md`

- [ ] 白名单提交本计划、测试和两份脚本；合并到 `main` 并推送。
- [ ] 按受控工作流把最新 `main` 发布到云端，检查 30/30 容器、HTTPS/WSS、PostgreSQL、Redis、备份 timer 和验证证据。
- [ ] 验证完成后删除已批准的本地/远端镜像导入包、无用工作树及已合并分支。
- [ ] 复核本地 `main`、`origin/main`、云端 `current` 与清理结果。
