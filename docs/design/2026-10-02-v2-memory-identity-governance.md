# CA2-15：偏好修订、任务约束、多人隔离与未知声音的隐私投影

> 状态：2026-10-02 **已决策**（§4）：本轮只做 S1；S1 拆成 S1a（端点按 token 解析主体，§5）与 S1b（删除总线与代际，§6）。
> **S1a 已部署 `e7766e98`**（§5.5）；S1b 待设计定稿（含 schema 变更，单独批准）。S2 / S3 未批准。
> 完成判据（实施方案 CA2-15）：显式修改胜过旧偏好；任务结束不复活旧约束；
> 未认证声音不读敏感个体记忆；forget 与在途治理同代际失效。依赖 [CA2-03 任务身份](2026-09-26-v2-task-identity.md)、
> [CA2-07 权限视图](2026-09-28-v2-permissioned-context-view.md)。排序见[路线图](../roadmap.md)。

## 1. 现状（2026-10-02 复核，代码位置以此为准）

| 判据 | 现状 | 缺口 |
|---|---|---|
| 未认证声音 | `pg_store.identify_speaker` / `voiceprint.decide` 只有 `accept` 给出具体乘员，其余（没模板、低于阈值、歧义、模型过期、网关异常）一律回 `primary`；HMI 只把 `occupant_id` 写进 meta，判定结果丢了；Android 写死 `primary`；云端 `build_context` 直接信 meta | 云端分不清「认出是车主」和「没认出、按车主处理」；没认出的人拿到车主的全部记忆 |
| 敏感级别 | 记忆有 normal / sensitive / highly_sensitive；只有**非定向**召回排除 highly_sensitive（`pg_store.py:468`），定向读（地点、关系、按 scope/前缀）不排除；`sensitive` 从不过滤；`identity.name` 存为 normal | 「敏感个体记忆」没有一处执行面 |
| 偏好修订 | 抽取异步、每 4 轮或命中「记住」类正则才跑；同谓词别名组 + subject 才 supersede，极性不进键；读取按 score×weight 排序，推断 8 次（0.70）压过一次明说（0.60）；只有 `no_spicy` / `no_queue` 做到「本轮 > 会话 > 记忆」 | 显式修改没有优先权；历史上已有 22° 与 26° 并存、记住的川菜压过本轮「不要太辣」等记录 |
| 任务约束 | `session_constraints` 只覆盖 `no_spicy` / `no_queue`；按会话保存（焦点 TTL 7200 s，每次保存续期），与任务无关（W09 的有意设计）；已完成任务的 `active_task` 仍可被继承 30 min | 任务结束后约束与槽值仍可能回到新任务 |
| 跨端 | `session_id` 来自客户端帧、与 token / 设备无绑定；挂起按 (user, session) 寻址；主动推送去掉 occupant 后发给该用户所有设备 | 同一账户内的设备可凭 session_id 续接；乘员维度不跟随 |
| forget 与在途 | 只有全量 forget 走删除总线（先删 Memory 再发总线）；云端撤销本进程的请求视图并写会话栅栏；Memory 没有栅栏、没有代际；后台巩固、Agent 写记忆、车端 AppendTurn、S2S 回流都可能在删除之后写回 | 删除与在途写入没有同代际失效 |

另：修前 llm-gateway 的记忆 / 声纹 HTTP 端点里只有 `/api/memory/forget` 用 Bearer token 解析主体，其余 9 个
（读会话 / 上下文 / 画像、删单条记忆、声纹列表 / 识别 / 注册 / 改名 / 删除）**不鉴权**，直接信任查询串里的
`user_id`；删乘员默认连同该乘员记忆一起清，删单条与删乘员也不通知删除总线。它们是 HMI 记忆管理页的正式功能，
属于已登记的「调试 HTTP 主体授权」边界里最具体也最危险的一处——S1a 已收口（§5）。

## 2. 分片

| 分片 | 内容 | 性质 |
|---|---|---|
| **S1 删除与代际** | ① 记忆 / 声纹的读写 HTTP 端点一律按 Bearer token 解析主体，`user_id` 只能等于 token 的主体（S1a）；② 单乘员删除与条目删除也走删除总线（栅栏 + 撤销在途）；③ owner 代际号：forget 时递增，后台巩固 / Agent 写记忆 / AppendTurn / 回流写入前比对，过期代际一律丢弃（②③ 为 S1b） | 确定性、安全向，不改对话行为 |
| **S2 声音证明与隐私投影** | 声纹识别结果由 llm-gateway 签发短期证明（主体、乘员、判定、有效期，HMAC），客户端只转交；云端验签后得到「已认出 / 没认出」；没认出时按 §4 Q1 的口径投影记忆（定向读与非定向召回同一判据）；Android 不再写死 `primary`；S2S `session.start` 改为按 token 绑定主体（§5.3） | 改变「没认出的人能读什么」，需要产品口径 |
| **S3 修订与约束** | 显式陈述（本轮或「记住」）胜过推断，同谓词按时间新者胜；极性进 supersede 键；任务结束（`active_task` 完成）时清掉该任务带来的会话约束与槽继承 | 改变规划与回答行为；推翻 W09「约束按会话保存」的设计，需 A/B 与历史量误伤 |

顺序 S1 → S2 → S3：S1 是纯安全修补、先上；S2 的产品口径已定（§4），另批实施；S3 涉及规划知识与抽取，按规划知识 A/B 的纪律单独立批。

## 3. 兼容与回滚

- S1a：不带 Bearer token 的调用一律被拒（决定「直接收紧」）。HMI 随云端同一次发布升级；**手机与 Dashboard 不调这些端点**
  （方案稿原写「手机需重新出包」，复核后不成立）。本地栈若 `VITE_WS_TOKEN` 不在 `AUTH_TOKENS` 的四段条目里，
  HMI 记忆面板会 401（与修前 `/api/memory/forget` 同口径）。回滚 = 回滚整个 release。
- S1b：代际号缺省为 0，旧记录兼容；含 DDL，按发布闸单独批准 schema 摘要。
- S2：没有证明的请求按「没认出」处理，所以客户端要先升级再收紧，否则所有人都会被当作未认出；可先观测后收紧。
- S3：行为变化，按固定语料与历史对话 A/B 后再上。

## 4. 决定（2026-10-02）

1. **没认出的声音能读什么**（S2 的口径）：**A**——只读 normal 级（舒适 / 车控类偏好），不读地点、关系、个人事实与称呼；
   账户从没录过声纹时仍按车主处理（单人用车不受影响）。
2. **本轮做哪几片**：**只做 S1**（删除与代际）。S2 / S3 不在本轮。
3. **端点鉴权怎么过渡**：**直接收紧**——端点立即要求 token，同一批修好客户端源码。

## 5. S1a：端点按 token 解析主体（已部署 `e7766e98`）

### 5.1 判据

`llm-gateway/http_server.py::_owner_query` 是这 9 个端点的唯一入口判据：

- 主体只来自 `Authorization: Bearer`：`AUTH_TOKENS` 四段条目（与车端网关同一解析口径）；`e2e.v1.` 前缀的签名测试身份
  只在 `E2E_IDENTITY_ENABLED=true` 且签名、有效期都通过时成立（与 S2S、车端网关同一开关与密钥）；
- 查询串里的 `user_id` 可以不带；带了就必须逐字节等于主体，否则 403 `owner_mismatch`；没有可用 token → 401 `unauthorized`；
- **拒绝发生在读请求体与调 Memory 之前**：注册 / 识别不读音频，删除不触达 Memory；识别的拒绝仍回 `occupant_id=primary`
  （识别面的既有契约：任何失败都回落到 primary）。

`/api/memory/forget` 的既有判据（body 里的 `user_id` 只能等于主体）不变，签名测试身份对它同样生效。

### 5.2 客户端

- HMI：`audio.ts::memoryAuth()` 给全部记忆 / 声纹请求带同一个 `VITE_WS_TOKEN`；免唤醒的说话人识别经
  `postIdentify(audioApi, userId, token)` 带上（`handsFreeController` 传入 `memoryAuthToken()`）。
  云端 HMI 的 token 主体是 `u1`，与 HMI 写死的 `USER_ID` 一致（只读核对，未改 `.env`）。
- E2E：`e2e_voiceprint` / `e2e_memory_graph` / `e2e_s2s` 改用运行器签发的测试身份 token 调这些端点。

### 5.3 本批发现、不在本批修的同类缺口

S2S 的 `session.start` 在生产里同样直接信客户端帧里的 `user_id`：会话用它读该会话近几轮对话做上下文，并把对话回流写进
该用户的记忆（只有开了 E2E 签名身份的测试命名空间才验签）。HMI 与 Android 都走这条路，收紧要重新出 Android 包，
所以并入 S2（S2 本来就要改 Android 的身份上送）。

### 5.4 验证

- `llm-gateway/tests/test_memory_endpoint_auth.py`：8 个端点 × {无 token 401 且不触达 Memory、他人 token 403、
  本人 token 带 / 不带 `user_id` 都按主体调用} + 识别拒绝回 primary + 注册先鉴权后读样本 + 签名测试身份三态 +
  源码里只剩判据函数自己读查询串 `user_id`；
- HMI `memoryAuth.test.mjs`：每个记忆 / 声纹请求都带 bearer、识别带传入的 token；
- 变异 6 条全部变红：跳过不一致检查、声纹删除改回信查询串、签名身份不看开关、缺 token 回落到查询串、
  HMI 一个请求漏 bearer、识别丢 bearer。

### 5.5 发布与真栈核对（`e7766e98`）

- 本地：全量 10614 / 35 / 11（371.83 s），四门禁与 smoke 13/13；llm-gateway 377、HMI 361 全过，`vite build` 通过，
  HMI 类型检查维持修前的 25 条（全是 `.mjs` 缺声明与 `Blob` 参数类型，与本批无关）。
- 发布：主工作树有别的会话未提交的文档改动，部署闸 `safety_rejected`；按开发指南从隔离 worktree 发布，不碰别人的改动。
  dry-run 零阻断、无 schema / CI 变更、基础设施摘要不变；status 5/5 零 warning，verify `20261002T125956Z-e7766e9.json`。
- 真栈只读核对（主机回环，只记状态码与条数，不取内容）：无 token 或伪造 token 读画像、地点、声纹列表 → 401；
  HMI 的 token → 200；同一 token 冒充别的 `user_id` → 403 `owner_mismatch`。线上 HMI 下发的 `audio.ts` 有 10 处请求带
  `memoryAuth()` 且 token 已注入，识别模块带 bearer，免唤醒控制器传入 token。证据 `.artifacts/ca2-15/e7766e98-endpoint-auth-probe.json`（本地，不入库）。
- 未运行：`e2e_voiceprint` / `e2e_memory_graph` / `e2e_s2s` 是 `remote_safe: false` 的本地栈用例，`target=cloud` 下不跑；
  固定语料走对话链路、本批未改，未重跑；浏览器里的记忆面板与真麦识别未人工点验。

## 6. S1b：删除总线与代际（下一步）

范围（§2 ②③）：单乘员删除与条目删除也走删除总线；Memory 侧给 owner 维护代际，forget 时推进，后台巩固 /
Agent 写记忆 / 车端 AppendTurn / S2S 回流在写入前比对，代际过期的写入丢弃。设计定稿与 schema 摘要另行提交批准。
