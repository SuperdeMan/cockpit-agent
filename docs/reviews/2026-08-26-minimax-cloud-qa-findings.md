# 2026-08-26 MiniMax-only 云端长会话 QA 复验问题记录

> 状态：**只记录，不修复**。泓舟在长会话完成前明确要求本轮不再改实现；本文件是后续会话的修复入口。
> 被测 release：`c7c211bedb4ff504dfceaf09e652c7875bdaebb8`；目标 `cloud`；LLM 仅
> `minimax:MiniMax-M3`；TTS 仅 MiniMax `speech-2.8-turbo / female-tianmei`。

## 1. 结论

- 发布身份：start/end/status 均为 `c7c211b`，5/5 端点 healthy；统一 `verify` 通过，
  `e2e_remote_safe`、provider/model、`lock_kind=e2e` 均完整。
- 长会话：5 persona、315 轮，探针**自动计分 282 PASS / 33 FAIL**；另有下文手工漏检，
  **不代表 282 轮业务全部通过**。0 persona 中止；388 次 LLM 调用全部 pinned MiniMax-M3，
  fallback=0。
- TTS：5 persona 均拿到 MiniMax PCM，全部 `playable=true`、零 TTS failure。
- HMI C14：**1/1 PASS**；5/5 persona 真播放，PCM `1,271,154` bytes，barge-in
  `cancelSent=true / localStops=3`；start/end release 均为 `c7c211b`、5/5 healthy。
- 终态：open operation IDs 全部为 0；merchant draft cleanup 全部归零；但 vehicle persona
  **无法由 collector 回读恢复终态**，云端被测车态恢复终值未被证明。

| Persona | 探针自动计分 | 自动判定失败轮 |
|---|---:|---|
| vehicle | 55/59 | 34–37 |
| family | 58/69 | 17、22、25、29–31、47、50、61–63 |
| merchant | 61/66 | 19、20、44–46 |
| adversarial | 57/62 | 29、33–35、48 |
| information | 51/59 | 3–5、18、41、48、54、55 |

## 2. 去重问题卡

### P0-01 红色机油灯被车控/天气或旧黄灯上下文覆盖

- family T28 `2b74de140b65410fb5e1e962a382dd57`、adversarial T32
  `f1846fa4e5ca4ca382524d26881e327d`：输入“红色机油灯亮了怎么办”，实际执行
  `warning_light.close`，回复“**双闪关了**”；探针未判红，是验收漏检。
- information T24/T25 `4a9f7d8ab5f745eead9b292e84c76b1e` / `511866e625da4427b6f98ee6be591268`：
  红色机油灯及“慢一点开”被答成深圳雨天驾驶建议；探针未判红。
- family T29/T30、adversarial T33/T34：后续继续消费更早的“胎压黄灯”，没有保持红色机油灯的
  停车/熄火/救援上下文。
- 影响：安全域落错、确定性车控误执行、后续风险等级被降低。

### P0-02 vehicle cleanup 未证明恢复，云端被测车态终值未知

- 业务后状态含 `hvac_on=true / front_defogger=true / rear_defogger=true / window=open /
  steering_wheel_heating=true / media=paused`。
- 探针发送了 6 条恢复命令，但 `after_cleanup={}`，失败为“collector 无法回读车辆恢复终态”，
  `verified=false`。
- 本轮按“只记录”要求没有追加写操作或再次恢复；后续会话必须先只读确认云端被测车态再决定处置。

### P1-03 person-pickup / 复合接送在长上下文中失焦

- family T47 `da80bb226dc640fb86a95f50720b0870`：“接爸妈去吃饭”直接变 nearby 川菜列表，未询问
  父母地点。
- family T50 `620e3e4a57d348539f940b4da4b94604`：接孩子 + 麦当劳 + 到校时限，缺 `navigate`。
- adversarial T48 `b876c30eb2c642889bf967bb3af6677c`：“接孩子后去万象城”误入杭州万象城 trip plan，
  并产生不应出现的待确认。
- information T54 `fb8bc46bef6342239d0192d70e84a73c`：用户明确“找不到地点就问我、不要猜城市”，
  仍执行 `navigate` 并加入咖啡途经点。
- family T53 `6e62243dbf1f4a6589536313a56ae17c`：“接孩子后去万象城”退化为普通商场列表；
  family T55 `e66b37f716034040af766bac0dbc4481`：“先去接我妈”仍被旧“万象城”焦点吞掉。

### P1-04 merchant SP1 候选重列不稳定，无法恢复到商品确认

- merchant T19/T20 `cc9aec5dd57f4fb6be35ca01f745deba` / `55d5173e6cee4a2b94c81c49d30c332c`：
  “重新列出刚才可以选择的项目”分别返回不同城市/不同门店列表；缺真实第 1 个按钮，第二次仍
  `need_confirm=false`，未恢复 `merchant_order_preview` 与“不另外加糖”。

### P1-05 merchant CD6 旧餐单焦点吞掉后续新域三轮

- merchant T44–T46 `fee9f1c7b39f4f4f9ecb7f5d8a84a8d6` /
  `cd37b7c1634d4be7abca52e57bda9de0` / `b4d5bf29c950499c884e00a3dad2d5fb`：
  新的“看看麦当劳菜单”被解释为在旧菜单里搜“全部”，随后“附近的川菜馆”和跨组价格比较也继续
  被旧麦当劳菜单消费；两轮候选前提均未成立。

### P1-06 天气城市相邻性错误，深圳追问漂移到上海

- information T1–T3 是深圳天气/空气；T4 `528084d35d6d4f9a9a1117bc3eb180f1`、T5
  `68b921e9a83245a39972624f8a654e6e` 突然回答上海预警/生活指数，且话术出现空标点。
- 同组三张外源卡还缺 `_prov`，见 P1-12。

### P1-07 reminder 多条取消续接错域/错对象，疑似留下探针提醒

- family T17 `9e98b9d78e384645803d864dab578031`：取消第一条后再次取消同标题，落到
  `chitchat.talk`，只追问是否取消第二条。
- family T59 `b26d1e2b4bbf4b3c9108dde98751d71f`：声称取消了无关标题“刚才那个提醒现在几点”；
  探针未判红。
- T18→T60 的 active 列表由 5 条增至 6 条，两个批次各自剩余的 16:00 项存在残留可能；
  本轮未做删除，后续先按真实 reminder ID 只读核对。
- information T37 `7af739d4c62c4166a003dbb3a7108663`：问“刚才实际改了哪一条、时间是什么”，
  只返回整份 reminder 列表，没有回答执行账本事实。

### P1-08 显式导航起点未进入路线

- family T22 `5e05ba0a1c1c45f1ae190a342e531aa6`：“从深圳欢乐海岸出发去世界之窗”，卡片仍为
  `origin="当前位置"`，回复也是“当前位置 → 世界之窗”。

### P1-09 行程否定约束改坏天数并进入待确认

- information T18 `ecc830ee0d2a4f3ebcbd57a1c1ed4d80`：“不要把珠海排到广州前面”对原 3 天方案执行
  `trip.modify`，扩成 4 天并进入待确认；后续精简仍沿用 4 天。
- information T17 `efd0da8b219d4dbb936ece255e0ff18b`：询问第二天安排，却回答“下一站大梅沙”并称
  “后面还有 9 站”，没有按天展开。

### P1-10 股票来源追问丢标的、报错 provider/time

- information T41 `18c62e5c16e541768a72a8bdb5decd96`：期望 `info.stock`，实际
  `chitchat.talk`；真实 provider 为 Tushare、行情日为 `20260826`，回复却称“东方财富实时行情、
  19:23 前后”，且无 provenance 卡。
- information T44 `84c5a437707f48e39ab66e8191c482fb`：“只总结刚才行情”反问股票代码；探针未判红。
- information T43 `62392be5825f440795de4da46a7447b1`：虚构“把上证指数当成沪深300”的自我纠错，
  与前一轮真实卡内容不一致。

### P1-11 charging plan 被当成普通导航到字面目的地

- information T48 `55f9508756e24d8db3211c13d6832bd8`：“规划去广州路上的补能，但先不要启动导航”
  实际为 `chitchat.talk`/普通 route，目的地字面值“广州路补能”，并询问是否发起导航；未进入
  `charging.plan`。

### P1-12 provenance 契约累计 20 个失败行

- 5 个 manual 卡使用 `mode=mock`（vehicle T34、family T25/T61、adversarial T29、
  information T55）。
- 11 个 road-safety 卡使用 `mode=deterministic`，被“外部数据卡只允许 real/cached/degraded”
  审计判非法。
- 4 个 info 外源卡缺真实性章（空气质量、预警、生活指数、股票来源追问）。
- 需要后续先裁决契约归属：road-safety 是内部确定性卡还是外部卡；不能简单把审计放宽后算绿。

### P1-13 五轮执行/建议总结丢域

- information T55 `5266a3eca2d44cff8e1c8d0673716504`：要求总结五轮执行/建议，误入 manual mock，
  回复“手册里没有查到”。

### P2-14 口味偏好与推荐结果相反（探针漏检）

- information T28 明确“不吃辣、不排队”；T29 `e5e016ac00af4f5bb1329a42c825fe2c` 仍优先推荐
  川菜、美蛙肥肠鱼、酸菜鱼，并称“按您的口味优先川菜”。
- T30 `7d3d3071b4e04a31ae4dd2cca03aaf75` 未解释推荐原因；T31
  `e2eefe5b3d0349a8b71fca0e10355206` 未列出本轮明确偏好；T32
  `70dd8ba929174a079f2803e1a5c6ecf3` 错误声称“本轮没说口味”，与 T28 明示偏好矛盾。

### P1-15 到校时限把“5 点”解释成凌晨 5 点（探针漏检）

- family T8 `7540ef8e232a4d75acdfe7daa6306c0b`：把“5 点到学校”解释成凌晨 5 点，输出“比要求
  早 593 分钟”；随后仍给出晚上 19:06 到达，时限判断自相矛盾。

### P1-16 恢复轮仍被旧业务焦点占用（探针漏检）

- vehicle T51 `d388a3f9b6364f5ba9de3edda57e7480`：问“现在还有待确认的操作吗”，只回复“嗯”。
- information T56 `12e7677bae57446cacc52e008d36d01f`：同一恢复问题回答学校地址，旧 pickup/navigation
  焦点没有退出。

### P2-17 序数取消失败未被验收捕获

- family T34 `2b43db23257846278a086b440cf7848e`：“第二个先取消，其他继续”仅回复“抱歉，处理失败”，
  没有给出可恢复的澄清或完成部分操作。

> C14 artifact 的原始 JSON 为正常 UTF-8；曾在 PowerShell `Get-Content` 输出中看到的 mojibake
> 是终端显示问题，不计为产品 badcase。

## 3. 33 个失败行对账

| Persona | Turn / Case | 主要失败 |
|---|---|---|
| vehicle | 34 SF3 | manual mock provenance |
| vehicle | 35–36 SF3 | deterministic provenance 非法 |
| vehicle | 37 SF4 | deterministic provenance 非法 |
| family | 17 SL1 | reminder.cancel 落到 chitchat |
| family | 22 SL4 | 显式 origin 未生效 |
| family | 25 SF1 | manual mock provenance |
| family | 29–30 SF3 | 旧黄灯上下文 + 安全话术不足 + deterministic provenance |
| family | 31 SF4 | deterministic provenance |
| family | 47 PU3 | 未走 person-location clarification |
| family | 50 PU5 | 缺 navigate |
| family | 61 SF3 | manual mock provenance |
| family | 62–63 SF3 | deterministic provenance |
| merchant | 19–20 SP1 | 缺真实按钮；未恢复商品预览/确认 |
| merchant | 44–46 CD6 | 旧餐单焦点吞掉菜单、nearby 与跨组比较 |
| adversarial | 29 SF1 | manual mock provenance |
| adversarial | 33–34 SF3 | 旧黄灯上下文 + 安全话术不足 + deterministic provenance |
| adversarial | 35 SF4 | deterministic provenance |
| adversarial | 48 PU7 | pickup 误入 trip.plan/待确认 |
| information | 3–5 INF-WEATHER | 外源 provenance 缺失（并伴随城市漂移） |
| information | 18 INF-TRIP | 不该执行 trip.modify/待确认，3 天变 4 天 |
| information | 41 INF-STOCK | 错 intent/provider/time，缺 provenance |
| information | 48 INF-CHARGING | charging.plan 落域失败 |
| information | 54 INF-COMPOUND | 明确禁止猜测后仍 navigate |
| information | 55 INF-COMPOUND | 总结误入 manual mock |

## 4. 证据与接手边界

- 长会话原始 artifact：`.artifacts/dev-stack-verifications/qa-minimax-long-sessions.json`
- C14 artifact：`.artifacts/dev-stack-verifications/hmi-cdp-c14.json`
- release verify：`.artifacts/dev-stack-verifications/20260826T103954Z-c7c211b.json`
- 本地全量基线（release 前一代码 SHA `5e764aa`）：`7225 passed / 32 skipped / 0 failed`，
  `TREE_STABLE=True`；部署 SHA `c7c211b` 只比它多一条文档措辞修正，未冒充重跑全量。
- 云端当前状态：release `c7c211b`，5/5 healthy；C14 后再次 status 仍一致。
- 本轮没有修复上述业务/审计问题，没有删除 reminder、release、backup、snapshot 或 artifact。
- 后续会话先从 P0-01/P0-02 开始；任何云端被测车态/提醒处置先只读确认，再另取写授权。
