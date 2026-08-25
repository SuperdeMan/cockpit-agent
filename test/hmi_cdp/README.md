# test/hmi_cdp — HMI 二次交互 CDP 验证层（L4）

设计：`docs/design/2026-07-14-journey-e2e-test-system.md` §4.2 / §5.3。

## 分工（为什么有了 L3 还要这层）

协议事实：HMI 卡内二次交互在协议层 = 合成一句文本发送（`Cards.tsx` 收口 `onAction(text)`），
所以**后端续接语义**在 `test/e2e_journeys.py` 用等价文本全量测。本层只测协议层模拟不到的
**HMI 自有语义**：

- 卡片/确认条/推送卡真的渲染出来了；
- 点击/输入后**发出的 WS 帧**文本与 meta 正确（`Network.webSocketFrameSent` 实拦）——
  重点是 `App.tsx send()` 的五层序号改写（intent_choice / waypoint_choice「导航去X途经Y」/
  dest_choice 回填候选名 / place_list「看X的详情」+`meta.nearby_poi_id` / poi_list「导航去X」）；
- 过程区门控（重域出四阶段、简单车控零过程）；右舞台车况联动。

## 运行

```powershell
node test/hmi_cdp/run_cases.mjs           # 常规 C 组（C14 必须显式运行）
node test/hmi_cdp/run_cases.mjs C1 C4     # 指定
# C14：先核档，再把 exact SHA 与统一 status 中的 cloud collector 绑定进驱动
python scripts/dev_stack.py target show
$env:CDP_EXPECTED_SHA=(git rev-parse HEAD).Trim()
$status=(python scripts/dev_stack.py status | ConvertFrom-Json)
$collector=($status.endpoint_results | Where-Object { $_.name -eq 'collector' }).url
$env:CDP_COLLECTOR=($collector -replace '/healthz/?$','')
node test/hmi_cdp/run_cases.mjs C14       # MiniMax 业务 TTS 播放 + barge-in（真云栈）
$env:CDP_MERCHANT_PROMPT='已包含门店、商品和规格的完整下单语句'; node test/hmi_cdp/run_cases.mjs C7
$env:CDP_MERCHANT_CANCEL_PROMPT='取消刚才的瑞幸订单'; node test/hmi_cdp/run_cases.mjs C7 C8
$env:CDP_MERCHANT_QUERY_PROMPT='查询已有商户订单'; $env:CDP_MERCHANT_EXPECTED_STATUS='商户返回的精确终态'; node test/hmi_cdp/run_cases.mjs C9
$env:CDP_LATITUDE='31.2304'; $env:CDP_LONGITUDE='121.4737'  # 审计门店公开坐标
```

前置：`make up` 全栈在跑（真实 key，语义类用例走真 LLM/真 provider）。C7/C8/C9 账号型商户
用例必须打开 `AUTH_REQUIRED=true`，由 `AUTH_TOKENS` 中**已认证主用户**条目授予
`merchant.read,merchant.write`，并令 HMI 的 `VITE_WS_TOKEN` 与该随机 token 一致；匿名默认权限、
声纹身份或客户端自报 meta 均不能替代授权。真实 token 只临时注入运行环境，不写进仓库。
宿主 Node ≥22
（零依赖：全局 WebSocket/fetch）；宿主装有 Edge 或 Chrome（默认按常见安装路径探测，
`CDP_BROWSER` 环境变量可指定）；**宿主 5173 未被本地 vite 占用**（历史坑——占了会连到
错误的 HMI）。截图证据写 `shots/`（gitignore，本地留档）。

## 用例清单

| id | 验证点 | 关键帧断言 |
|---|---|---|
| C1 | 危险确认条：渲染→点确认→执行 | 帧 `is_confirmation:true`；collector trunk=open |
| C2a | place_list 裸序号（`ordinalSelectIn`） | 帧=「看{名}的详情」+ `meta.nearby_poi_id` |
| C2b | dest_choice「第一个」回填 | 帧=候选名本身（**非**「导航去…」改写） |
| C3 | scene_list 卡按钮 + 取消链路 | 帧=「开启露营模式」；确认条点取消 |
| C4 | 主动推送渲染 + 到点卡按钮 | 「提醒到点」卡出现；帧=「完成提醒：X」 |
| C5 | 过程区门控 | 重域出「理解需求…」；简单车控零过程 |
| C6 | 右舞台车况联动 | debug 压电量 55 → 舞台渲染 55 |
| C7 | 真实商户预览/确认/订单卡（显式 env 才运行） | 卡动作 `is_confirmation=false`；全局确认 `is_confirmation=true` |
| C8 | 与 C7 同会话查询并二次确认取消瑞幸订单 | 取消确认 `is_confirmation=true`；页面出现“已取消”终态 |
| C9 | 对已有订单做只读终态复验，不创建/取消订单 | 必须显式设置 `CDP_MERCHANT_EXPECTED_STATUS`，最终话术逐字包含该状态；任意回复或通用搜索不得判绿 |
| C11 | **QA Q1-B/C** 确认帧带 `operation_id`；两条挂起并存、先来那条仍可确认 | 同屏确认条 ≥2；点第一条的确认，出帧 `operation_id` 必须等于**它自己**那条的 id；另一条不受影响 |
| C12 | **QA Q3** 抢发并发归属 | 三轮各带不同 `request_id`；入帧回带 id；每轮**要么 final 要么 cancelled**，零气泡卡在「正在思考…」 |
| C13 | **QA Q4** 位置前置闸收窄 | 车控/取消句/显式地点/多意图四组**必须真的发出去**；对照组「附近有什么好吃的」仍征询定位（闸是收窄不是拆除） |
| C14 | **MiniMax-only QA** 五类真实业务回复的 HMI TTS 与打断 | 每类回复均由 collector 证明 LLM=`minimax:MiniMax-M3`；浏览器实抓 TTS start/text/finish、MiniMax provider/voice、PCM 二进制非静音与有效时长、AudioBuffer 播放收口；长天气播报被下一轮文本打断时必须发 cancel 且停止本地音源 |

> C11/C12/C13 是阶段 2 的出口条件。**它们只能在这条车道验**：Q3 的归属与 Q4 的位置闸
> 都活在客户端 JS 里，`test/e2e_*.py` 的 WS 探针是从闸**后面**进来的，跑多少轮都是假绿。

## 运营位

不进 nightly（浏览器层脆、依赖真 key）；进 release 前手动清单与 `make e2e` 后的人工抽验。
定位约定：按**可见文本**选按钮/断言（文本即契约），刻意不给产品代码加 testid。

`C7` 会在真实商户创建一笔**未支付订单**，所以默认无条件 `SKIP`。
只有在当前真栈已锁定可营业门店/商品/规格后，才临时设置
`CDP_MERCHANT_PROMPT` 定向运行；不把该句或外部凭据固化进仓库。用例不付款，
断言卡动作帧 `is_confirmation=false` 与全局确认帧 `is_confirmation=true` 的分工。

`C9` 是不产生副作用的收口用例，可分别对两家已经终态化的订单运行。旧版曾只断言“收到
回复”，导致官方响应明确终态但 HMI 说“没查到/待回传”仍会判绿；旧截图只算缺陷证据。
现在 query prompt 与 expected status 都必须显式提供，且脚本只接受最终话术命中精确状态。
2026-08-12 最终验收分别精确命中瑞幸“已取消”和麦当劳“订单已取消”，两次查询帧均为
`is_confirmation=false`。记录不包含 prompt、订单号、地址或支付链接。

`C14` 只允许对已经发布且版本可核对的 cloud 真栈运行；HMI 设置必须锁定
LLM `minimax:MiniMax-M3`、TTS `minimax`，不得接受 fallback。它不以“页面出现回复”判绿：
五类业务回复需逐轮对账 collector intent/LLM 调用，音频需同时存在协议帧、非静音 PCM、
浏览器 AudioBuffer 起播与自然结束；barge-in 还需同一 TTS WebSocket 的 cancel 帧和本地
buffer source stop 证据。截图只辅助复核，不替代上述机器断言。
驱动会在浏览器启动前和全部 TTS/barge-in 完成后各调用一次统一 `dev_stack status`，两端均
要求 `target=cloud`、exact `CDP_EXPECTED_SHA`、5/5 healthy，并要求实际取证用的
`CDP_COLLECTOR` 与 status 中的 cloud collector 相同；任一不满足即 fail closed。脱敏证据写到
`.artifacts/dev-stack-verifications/hmi-cdp-c14.json`（gitignore），保留 start/end release SHA、
MiniMax 锁、五类 PCM/播放及打断读数，不保存 token。
