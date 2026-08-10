# B2 意图对抗门禁进 CI + 主干治理

> **状态**：已批准待实施（源自外部评审采纳批次 B2，裁决见
> [`../reviews/2026-08-10-external-review-adoption.md`](../reviews/2026-08-10-external-review-adoption.md)）
> **交付对象**：后续实施者；分支保护档位需泓舟拍板（§3.2 给出两档利弊与操作命令）
> **关联**：`.github/workflows/ci.yml`、`test/eval_intent_adversarial.py`、`Makefile`、`scripts/`
> **与 B1 无依赖，可并行。**

---

## 0. 一段话给接手者

2026-08-10 刚发生过一次真实事故：除雾能力补齐时漏了对抗覆盖，`--strict` 实际 exit 2，人工
`cmd | tail; echo $?` 把它读成 exit 0 报了假绿，直到下一提交才补上（history §23.1、
findings §26.4）。而**最能防住这次遗漏的门禁（L0 `--strict`）目前根本不在 CI 里**——
`ci.yml` 无任何 `eval_intent_adversarial` 调用（`:213-245` 只有 fast_intent 观测步 +
Skill/Exemplar 门禁）。本批做三件事：① L0 strict 两条 suite 进 CI 作阻断步骤；② 本地/CI
共用的 Python 封装脚本根治管道吞码；③ main 分支保护 + CODEOWNERS（分档待拍板）。
L0 全离线零网络零费用（真实 Edge servicer + 真实检索，无 LLM），完全符合「live LLM gate
不阻断 PR」的既有边界——阻断的只是确定性检查。

## 1. 现状与证据

- `.github/workflows/ci.yml:213-232`：`eval_fast_intent`/`eval_route_hints`/
  `eval_registry_resolve` 带 `continue-on-error: true`（设计如此：「跌破基线告警不阻塞」
  哲学，`:182-185` 注释成文，**本批不动它**——会随语料漂移的意图基线与确定性门禁是两类）。
- `ci.yml:238-245`：Skill / Exemplar contract gate 已是 blocking——本批新步骤与它们同类
  （确定性、零 LLM、失败必须红），并列摆放。
- `test/eval_intent_adversarial.py`：L0 discovery 当前 76/76（569 条语料）、gate L0 strict
  25/25，本地 exit 0（AGENTS.md §4.0）。`--strict` 语义：coverage gap 从**展示**升级为
  **阻断**（§4.3 纪律「门禁在两种模式下严厉程度不同」——CI 必须跑 strict 档）。
- 管道吞码是记录在案的复发坑：`${PIPESTATUS[0]}` 首记于 R3 批次，2026-08-10 又踩
  （findings §26.4）。机制化根治 = 由 Python 读退出码，任何证据链上不再出现 shell 管道。
- GitHub 侧：`main` 无分支保护，force-push / 删除 / 直推红提交均无拦截（评审经 API 查证）。

## 2. 方案 A：门禁封装脚本 + CI 阻断步骤

### 2.1 `scripts/check_intent_gate.py`（本地与 CI 共用的唯一入口）

薄封装，职责刻意收窄：

```text
1. subprocess 依次执行（不经 shell、不接管道）：
   python test/eval_intent_adversarial.py --suite discovery --layer l0 --strict
   python test/eval_intent_adversarial.py --suite gate      --layer l0 --strict
2. 逐条捕获 returncode，打印每条的 suite/exit/关键摘要行
3. 任一非零 → 总 exit 1；全零 → exit 0
4. --json <path> 可选：落一份 {suite: {exit, summary}} 供 CI artifact
```

不做的事（防过度设计）：不解析报告内容、不做 GitHub Annotation（strict 失败信息本身已
可读）、不校验文档快照同步（另一个关注点，见 §5 备注）。

### 2.2 Makefile 目标 + PowerShell 等价

```make
gate-intent-l0:
	python scripts/check_intent_gate.py
```

Windows 侧 `scripts/` 已有 ps1 惯例，`check_intent_gate.py` 本身跨平台，无需另写 ps1，
README 验证表加一行即可。**今后人工报 L0 门禁读数，只允许引用该入口的输出**——杜绝
「每个人自己拼命令 + 自己读退出码」的事故面。

### 2.3 ci.yml 增量（`intent-eval-baseline` job 内，Exemplar 门禁之后）

```yaml
      # B2：意图对抗 L0 门禁（阻断）。全离线零 LLM——strict 档把 coverage gap 升级为红灯；
      # 由 Python 封装读退出码（2026-08-10 管道吞码事故的机制化根治），不接任何 shell 管道。
      - name: Intent adversarial L0 gate (blocking)
        run: python scripts/check_intent_gate.py --json docs/reviews/eval/_ci-run-intent_gate.json
```

放在该 job 的理由：依赖完全重合（requirements + buf codegen 已就位），秒到分钟级耗时，
产物顺搭既有 `_ci-run-*` artifact 上传（`ci.yml:247-253`，前缀已 gitignore）。

### 2.4 验收

1. CI 上该步骤绿（正常提交）；
2. **红灯验证**：临时 worktree 里删一条对抗覆盖（或新增 active intent 不补覆盖），推分支
   触发 CI，该步骤必红；还原后绿。红灯验证不做等于没接（除雾事故的教训就是「绿灯从没
   被证明会变红」）；
3. 本地 `make gate-intent-l0`（或直接跑脚本）与 CI 读数一致；
4. `docs/guides/intent-adversarial-testing.md` 运行手册补「唯一门禁入口」条目，
   AGENTS.md §4.0 门禁行改为引用该入口。

## 3. 方案 B：主干治理

### 3.1 CODEOWNERS（先落文件，立即可做）

`.github/CODEOWNERS`，内容即评审清单（它同时是「安全敏感文件清单」的机器可读化）：

```text
# 执行安全内核（B1 对象）——动这些文件必须过独立 review
/orchestrator/edge/val.py            @SuperdeMan
/orchestrator/edge/server.py         @SuperdeMan
/orchestrator/edge/edge_call.py      @SuperdeMan
/orchestrator/cloud/loop.py          @SuperdeMan
/orchestrator/cloud/planning.py      @SuperdeMan
/security/                           @SuperdeMan
/registry/                           @SuperdeMan
/proto/                              @SuperdeMan
# 评测尺子与正式 baseline——动它们要先过「案例集是尺子」纪律（AGENTS.md §4.3）
/test/eval_intent_adversarial.py     @SuperdeMan
/docs/reviews/eval/baseline_*        @SuperdeMan
```

单人仓库下它暂不产生强制力（require PR + codeowner review 开启后才生效），当前价值是
声明边界：AI 协作者据此知道哪些文件的改动必须在提交说明里显式陈述理由。

### 3.2 分支保护：两档，泓舟拍板

**GitHub 机制现实（先说清，避免买错）**：required status checks 只在 **PR merge** 时强制，
对直接 push 不生效。所以「CI 红不许进 main」这个目标**只有重档能实现**；轻档防的是
历史重写与误删，不防红提交直推。

| | 轻档（建议立即开） | 重档（改变工作流，拍板后开） |
|---|---|---|
| 内容 | 禁 force-push、禁删除 `main` | 轻档 + require PR + required checks（`python-tests`/`go-build-test`/`frontend`/`intent-eval-baseline`）+ conversation resolution |
| 成本 | 零——现有「直推 main」工作流不变 | 每批改动走 PR（AI 协作者可 `gh pr create` + auto-merge，泓舟按批合并或授权自动合并） |
| 防住什么 | `git push -f` 事故、误删分支 | 以上 + CI 红灯进不了 main + CODEOWNERS 生效 |
| 防不住什么 | 红提交直推、绕过 review | 本地未跑测试的等待时间转移到 CI |

轻档操作（gh CLI，泓舟或授权后执行）：

```bash
gh api -X PUT repos/SuperdeMan/cockpit-agent/branches/main/protection \
  -f 'required_status_checks=null' -f 'enforce_admins=false' \
  -f 'required_pull_request_reviews=null' -f 'restrictions=null' \
  -F 'allow_force_pushes[enabled]=false' -F 'allow_deletions[enabled]=false'
```

重档在轻档基础上把 `required_status_checks` 换成 ci.yml 四个 job 名并开
`required_pull_request_reviews`。**推荐路径**：轻档现在开；重档等 B1/B2 代码批次全部合入、
CI 稳定绿两周后再评估——当前高频批次节奏下 require PR 的摩擦真实存在，而最大的两个风险
（评测假绿、force-push）分别已被 §2 和轻档覆盖。

### 3.3 验收

1. 轻档：`git push --force` 到 main 被拒（用一次性测试分支验证同配置，不真对 main 演练）；
2. CODEOWNERS 文件合入且 GitHub 界面识别（Insights → Code owners 无语法错误）；
3. 若开重档：一个红 CI 的 PR 无法 merge 的截图/记录留档 history。

## 4. 实施步骤

| 步骤 | 内容 | 依赖 |
|---|---|---|
| 1 | `scripts/check_intent_gate.py` + Makefile 目标 + 本地跑通 | 无 |
| 2 | ci.yml 加阻断步骤，推分支看绿 | 步骤 1 |
| 3 | 红灯验证（§2.4.2） | 步骤 2 |
| 4 | CODEOWNERS 合入 | 无 |
| 5 | 轻档分支保护开启 | 泓舟授权（红线：CI/CD 与仓库配置变更） |
| 6 | 运行手册/AGENTS.md 门禁入口指针更新 | 步骤 1-3 |

> ⚠ 步骤 2/5 触及 CLAUDE.md 自主边界红线（CI/CD 配置）。本方案文档已把变更内容完整列出，
> 实施者动手前仍需泓舟对「按 B2 方案执行」一次性确认，不得默认既往授权覆盖。

## 5. 备注：评审建议中本批不做的

- **不动** `eval_fast_intent` 组的 `continue-on-error`——那是拍过板的「观测不阻塞」设计
  （ci.yml:182-185 注释），且其分数随语料/规则漂移，不满足「确定性检查才阻断」的准入。
- **不做**「文档快照数字与实测同步」的自动校验——有价值但属另一关注点（文档洁癖自动化），
  规模不明，不搭车；若后续做，入口应是独立脚本而非塞进 gate 封装。
- **不做** live LLM gate 进 CI——维持 §4.2 既有后置条款（凭证/预算/方差三前提）。
