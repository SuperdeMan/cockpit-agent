# 切云验证通路的九条根因（2026-08-18）

> 迁移 apply 成功、云端 30 个容器起齐、`dev_stack status` 报 ok 之后，
> `dev_stack verify` 仍然一次都没绿过。本页把它收敛成九条确定性根因，
> 全部修完并各带一条反向验证过的回归。上一批（迁移 apply 的 RC1/RC2）见
> [`2026-08-18-redis-migration-identity-root-causes.md`](2026-08-18-redis-migration-identity-root-causes.md)。
> RC15/RC16 是**接手方**在核对交接页最后两条验收项时补上的（§6c）。

## 0. 这批的形状

`dev_stack verify` 只有两步：`cloud_release verify` + `run_e2e --target cloud --id
e2e_remote_safe`。第二步倒在 **`e2e_remote_safe` 这一个用例**上，而这个用例是**云端验证
唯一会跑的东西**。逐层剥下来，它在四个不同的地方各卡一次；切完之后本地单测又露出第七条；
最后去核「HMI/Dashboard 能不能联调云端」，那条路又是两条——

| # | 位置 | 形态 |
|---|---|---|
| RC8 | `dev_stack_lib._endpoint_status` | 阈值照本地时延写，云端往返进不去 |
| RC9 | `cloud_remote_lock.acquire` | 只读 stdout 不读 stderr ⇒ 管道写满死锁 |
| RC10 | `run_e2e._child_environment` | 用例要的环境变量被运行器**故意剥掉** |
| RC11 | 本地 `.env` + `dev_stack verify` | 云端 `AUTH_REQUIRED=true`，本地没有那个 token；缺了也不早报 |
| RC12 | `test/e2e_remote_safe.py` | 等一个边缘 WS **从不发送**的握手帧 |
| RC13 | `test/e2e_remote_safe.py` | `meta` 值类型不合网关契约 ⇒ 整条消息被**静默丢弃** |
| RC14 | `scripts/tests` 三个套件 | 单测跟着仓库部署档变色——切云当天 **17 条转红** |
| RC15 | `dev_stack_lib.frontend_command` | argv[0] 是裸名 `npm`，Windows 上 **一定** `FileNotFoundError` |
| RC16 | `dev_stack.py` 前端车道 | dev server 的输出被 pipe 吃掉；Ctrl-C 打不到它，留下孤儿 Vite |

RC10/RC12/RC13 三条都在同一个文件里，而且都是**这条路径从来没成功过**的证据：
`e2e_remote_safe` 自打写下来就没有真正跑通过一次。

> **判据①：一条从来没跑绿过的用例，不会只有一个 bug。**
> 修完第一处就重跑，会得到第二处；每一处单独看都像「刚刚坏掉」。
> 判断依据不是次数而是**形态**——四处全是「不可能成功」而不是「偶尔失败」。

## 1. RC8 阈值照错场景写

`_endpoint_status` 用 `timeout_s=3.0` 探五个端点。云端经 Tailscale 的真实往返是
**2.3–3.1 秒**，于是 `status` 稳定报 `degraded`。改成 `ENDPOINT_TIMEOUT_S = 10.0`
后 5/5 healthy。

> 与 RC1 同族：**阈值必须按这条路径真实会遇到的输入/场景定**。
> 3 秒是照 localhost 写的，而这个函数在 cloud 档下面对的从来不是 localhost。

## 2. RC9 只读一头的管道会死锁

`RemoteCloudLock.acquire()` 把 `stdout.readline` 丢进线程池等 20 秒，
**stderr 一个字节都不读**。这台云主机每次 ssh 都打一整屏微信扫码登录横幅到 stderr；
Windows 匿名管道缓冲只有 4 KiB，写满后远端阻塞，`READY` 那行**永远发不出来**。

取证：并发抽干 stderr 的同一条 ssh，`READY` **1.5 秒**就到。
修法：起线程抽干 stderr，只留最后 8 KiB 供报错用（错误路径原本读的就是它）。

回归 `test_remote_lock_ack_survives_a_login_banner_larger_than_the_stderr_pipe`
必须起**真子进程**——内存流复现不了「管道写满」。改前 25 秒超时红、改后 1 秒绿。

> **判据②：环境相关的死锁，测试替身天然复现不出来。**
> 这类回归的成本就是要拉一个真进程；用 `BytesIO` 写的替身会永远绿。

## 3. RC10 运行器故意剥掉的名字，用例却在要

`_child_environment` 对**每一个**用例子进程无条件剔除 `AUDIO_API_URL`
（运行器认可的名字是 `VITE_AUDIO_API_URL` 与 `E2E_AUDIO_API_ORIGIN`），
而 `e2e_remote_safe.py` 第 21 行 `os.environ["AUDIO_API_URL"]` —— import 期就 KeyError。

两头各有一条测试，**互相矛盾却都绿**：

- `test_remote_safe_probe_uses_only_runner_endpoints_and_isolated_identity`
  断言探针源码里**出现** `os.environ["AUDIO_API_URL"]`；
- `test_run_e2e.py` 断言子进程环境里**没有** `AUDIO_API_URL`。

> **判据③：两条只验形式的断言可以同时绿，并且互相矛盾。**
> 「源码里有这个名字」和「运行时拿得到这个名字」是两件事。
> 新回归 `test_remote_safe_probe_reads_only_names_the_runner_hands_the_child`
> 改成**内容判据**：把探针里所有 `os.environ["X"]` 抽出来，
> 与 `endpoint_environment ∪ .env.example ∪ _child_environment` 实际交付的集合比差集。

## 4. RC11 云端开了鉴权，本地没有那把钥匙

云端 `.env`：`AUTH_REQUIRED=true` + 64 字符 `VITE_WS_TOKEN`（compose 里是
`${VITE_WS_TOKEN:?}` 硬要求）。本地 `.env` **根本没声明这个键**，于是探针连不上边缘 WS。

这是配置缺口不是代码缺口，但**「缺了要多久才知道」是代码问题**：
`dev_stack verify` 会先跑一遍云端 release verify、再拉起 E2E，三分钟后才以
`child_failed` 告终。`frontend_command` 早就为 cloud HMI 立过同一条规矩
（`VITE_WS_TOKEN is required for cloud HMI`）——verify 只是没跟上。

修法：cloud 档的 verify 前置读 `.env`，缺则 `configuration_rejected` 直接退出（rc=2）。

> **判据④：同一个远端栈，HMI 要的凭据 E2E 也要。**
> 一个入口 fail closed、另一个入口一路跑到子进程里才炸，等于这条规矩只落实了一半。

## 5. RC12 等一个不存在的握手

探针连上边缘 WS 后 `if ack.get("type") != "hello_ack": raise`。
`hello_ack` 是 **cloud-gateway 的 gRPC 通道**帧（`gateway/cloud/main_test.go`），
边缘 WS 从来不发。未签名客户端连上后，网关发的是
`{"type":"vehicle_state"}`（`gateway/edge/main.go:235`），此后不问就不答。

鉴权失败本来就体现在 upgrade 被拒（`websockets.connect` 抛异常），
这个握手判定既不成立也不必要，删掉即可；后续帧 `wait_final` 本来就会跳过。

回归 `test_remote_safe_probe_waits_only_for_frames_the_edge_gateway_emits`：
用 AST 抽出探针**比较过 `type` 的每一个字面量**，与 `gateway/edge/main.go` 里
`"type": "..."` 能发出的集合比差集。改前红在 `['hello_ack']`。

## 6. RC13 类型不合的 meta，会被静默吃掉

网关 `wsRequest.Meta` 是 `map[string]string`。探针传
`"memory_enabled": False`（JSON `false`，布尔）⇒
`json.Unmarshal(msg, &req) != nil` ⇒ **`continue`**。

于是现象是：连接正常、`vehicle_state` 正常收到、请求发出去 **60 秒一帧不回、
边缘网关和编排器日志一行不写**——读起来跟「云端栈死了」一模一样。

而 `test_remote_safe_probe_uses_only_runner_endpoints_and_isolated_identity`
里有一行 `assert '"memory_enabled": False' in source`：**把错误的形状钉住了**。

> **判据⑤：静默丢弃是最贵的失败模式，因为它长得像别的故障。**
> 排查方向会被带到「云端是不是挂了」上去，而真相在**客户端的一个字面量**里。
> 新回归 `test_remote_safe_probe_payload_matches_the_gateway_wire_contract`
> 从 Go 结构体的 json tag 反推每个字段的线上类型，再用 AST 核对探针 payload
> 里的常量类型——**契约从被消费方派生，不写死在断言里**。

## 6b. RC14 单测不该读操作者的部署选择

切到 `target=cloud` 之后，`scripts/tests` 里 **17 条单测转红**，而被测代码一行没动。
根因：`runner.main()` 从仓库根的 `dev-stack.local` 解析 target，这些用例传的是真实
`REPO_ROOT`（它们需要真 manifest），于是 `--parallel-isolation` / `--profile` /
`--full` / `e2e_auth` 这些**本地专属模式在 cloud 档下被合法拒绝**，preflight rc=2。

`CLAUDE.md` 明说 `target=cloud` 期间本地仍要能跑单测——所以这是缺陷不是限制。
修法：7 个字面量 argv 的调用点显式 `--target local`，共享助手 `_invoke` 缺省注入。

> **判据：单测的读数不许依赖操作者当前的部署档。**
> 这一条与 RC10 同源——尺子被环境串了味。区别是 RC10 串的是环境变量，
> 这条串的是**一个可以被随时切换的仓库状态文件**，所以它会「某天忽然全红」。
> 守卫 `test_runner_unit_tests_never_inherit_the_repository_deployment_target`
> 扫这三个套件里所有字面量 argv 的 `runner.main(` 调用，缺 `--target` 即红。

## 6c. RC15/RC16 前端联调那条路同样一次没跑过

交接页的验收项写着「本机 HMI/Dashboard 能联调云端」，而文档给的入口
`python scripts/dev_stack.py hmi` 在 Windows 上**必然失败**：

- **RC15**：`frontend_command` 返回 `argv[0] = "npm"`。`subprocess.Popen` 不套
  `PATHEXT`，Windows 上能执行的是 `npm.cmd`，裸名直接
  `FileNotFoundError` ⇒ `ReleaseError` ⇒ CLI 打一句 `{"status": "failed"}` 收场，
  **没有任何诊断**。实测：`Popen(["npm","--version"])` → `[WinError 2]`；
  `Popen(["npm.cmd","--version"])` → rc 0。
- **RC16**：`SubprocessRunner` 对每个子进程都 `stdout=PIPE` + `communicate()`。
  dev server 要活到操作者停它为止，于是**整段生命周期一个字都不吐**——
  看不到 `Local: http://127.0.0.1:5173/`，看不到报错。叠加
  `CREATE_NEW_PROCESS_GROUP`，Ctrl-C 打不到子进程：python 退了、Vite 还占着 5173
  （仓库既往那条「宿主遗留 vite 占 5173」就是它）。

两条守卫此前都存在，且都只验字面量：`test_cloud_hmi_uses_local_vite_and_remote_endpoints`
与 `test_cli_hmi_runs_only_vite_and_redacts_token` 各断言一次
`argv == ("npm", "run", "dev", "--", "--host", "127.0.0.1")`——**用的是 FakeCliRunner，
从来没有真起过进程**。

> **判据⑥（RC10/RC12/RC13 的第四次同形）：断言字面量等于没断言。**
> 尺子换成内容判据：把 `argv[0]` 交给**CLI 真正会用的那个 runner** 去启动，
> 起不来就是红。这条测试不依赖机器上装没装 npm——PATH 指向临时目录里的桩启动器，
> 桩的名字按本平台的命名法给（`npm.cmd` / `npm`）。

修法：
- `resolve_frontend_launcher()` 用 `shutil.which` 解析，解析不到抛
  `DevStackError("npm was not found on PATH")`（与 `cloud_release_lib` 解析 `buf` 同款）；
  `frontend_command` 返回的 argv **已经是一个存在的文件**。
- runner 增加 `attached=True`：不接管 stdout/stderr、不开新进程组，
  `wait()` 而不是 `communicate()`。前端车道用它，其余路径一字未动。
- CLI 在**启动之前**先 emit 一条 `status: starting`（带脱敏后的端点），
  因为子进程一旦接管控制台，后面那条要等它退出才打得出来。

四条回归全部反向验证过：撤掉解析 ⇒ `ReleaseError: could not run npm: FileNotFoundError`
+ `DID NOT RAISE DevStackError`；撤掉 attached ⇒ `assert 'stub-launcher-ok' in ''`
+ `KeyError: 'attached'`。

链路的其余部分是好的（RC15 一挡就挡在第一步，此前无从得知）：用解析后的 argv 起 Vite，
HMI 5173 / Dashboard 5174 均 200，且 `/src/App.tsx` 与
`/src/components/CommandBar.tsx` 的转译结果里**逐字含 tailnet FQDN**
——注入的云端端点确实进了浏览器拿到的那份 bundle，不只是进了进程环境。

## 7. 收口状态

- `dev_stack verify` = `status: verified`，证据
  `.artifacts/dev-stack-verifications/20260818T081414Z-34d72d7.json`：
  `release_sha=34d72d7…`、`provider=minimax`、`model=MiniMax-M3`、
  `lock_kind=e2e`、`case_ids=[e2e_remote_safe]`。
- `dev_stack status` = ok，5/5 端点 healthy。
- 九条根因各带一条回归，全部做过反向验证（改回旧码必红，且红在该红的那条断言上）。
- `scripts/tests` 全量在 **`target=cloud` 档下**跑绿——切云不再改变本地单测的读数。
- 云端只读复核（2026-08-18 17:0x）：容器 **30 total / 30 running / 0 bad**、
  `current` 指向 `34d72d7…`、`car-agent-backup.timer` 下次 08-19 00:00:04 CST、
  Tailnet Serve 五个入口（443/8443/8444/8445/8446）齐。
- `dev_stack verify` 由接手方**独立重跑一次**仍 `verified`
  （`.artifacts/dev-stack-verifications/20260818T091100Z-34d72d7.json`，
  lock `e2e-0fdbc272…`）——同一份证据两个人各取一次，才叫可复现。
