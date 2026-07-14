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

```bash
node test/hmi_cdp/run_cases.mjs           # 全部 C 组用例
node test/hmi_cdp/run_cases.mjs C1 C4     # 指定
```

前置：`make up` 全栈在跑（真实 key，语义类用例走真 LLM/真 provider）；宿主 Node ≥22
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

## 运营位

不进 nightly（浏览器层脆、依赖真 key）；进 release 前手动清单与 `make e2e` 后的人工抽验。
定位约定：按**可见文本**选按钮/断言（文本即契约），刻意不给产品代码加 testid。
