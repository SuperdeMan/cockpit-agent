# 支付基础设施真实化：双渠道收单 + 商户 MCP 支付闭环

> **状态**：批 1 实施中（2026-08-11 泓舟拍板：三批全做，四类凭证——支付宝沙箱/微信商户号/
> 瑞幸 token/麦当劳 token——均会提供）。
> **交付对象**：后续实施者与评审者
> **关联**：`payment-gateway/`、`proto/cockpit/payment/v1/`、`agents/parking_payment/`、
> `agents/mcp_bridge/`、`agents/_sdk/`、`hmi/`、`docs/conventions.md` §9.17、
> `docs/architecture/detailed/ws6-real-capabilities-and-agent-collaboration.md` §2
> **时机**：批 1（网关核心）→ 批 2（parking 闭环 + HMI）→ 批 3（桥 streamable_http +
> 瑞幸/麦当劳）。每批独立提交、过全量门禁、push 前经泓舟授权。

---

## 0. 一段话给接手者

payment-gateway 在本批之前是一个「已建成但从未接线」的孤儿服务：proto 两段式契约在
（Authorize/Capture/Cancel/GetStatus），但 `server.py` 漏回传 `confirm_token`（Capture
结构性不可达）、store 纯内存（Redis 客户端写了没接）、全仓库零调用方零测试零渠道 SDK；
真实跑的「停车缴费」是 parking-payment Agent 内部 mock 回执（`rcpt_mock_`）。本批把它
接成真的：**自有收单**（支付宝当面付 / 微信 Native 扫码，轮询查单）与**商家收银登记**
（瑞幸/麦当劳官方 MCP 下单返回支付链接，网关只登记会话）两种形态统一到一个状态机；
parking-payment 切走 mock；mcp-bridge 补 streamable_http 传输后接入两家官方 MCP。
安全底座（scope 校验/中央确认闸/require_confirm 只认 manifest/副作用永不重试/声纹不进
支付）全部是现成且有契约测试的，本批站在其上，不新造确认机制。

## 1. 外部事实（2026-08-11 核实）

| 对象 | 事实 | 对方案的约束 |
|---|---|---|
| 麦当劳官方 MCP | `https://mcp.mcd.cn`，**Streamable HTTP** + `Authorization: Bearer`（平台控制台申请 token），28 工具，**创建订单返回 `payH5Url`**（`https://m.mcd.cn/mcp/scanToPay?orderId=…`，商家收银台）；限流 600 req/min；仅中国大陆 | 桥必须支持 streamable_http；支付闭环=展示商家链接二维码；是否有取消工具接入日 `tools/list` 核实 |
| 瑞幸 AI 开放平台 | open.lkcoffee.com（2026-06 上线），MCP/CLI/Skill 三形态；token 绑瑞幸账号会话（约 1 个月有效） | 工具清单与支付链接字段名拿到 token 后现场核实；token 过期=能力诚实缺席（不自动刷新） |
| 支付宝当面付 | `alipay.trade.precreate` 生成收款二维码 + query/close/refund；**有沙箱**（网关可配 openapi-sandbox）；RSA2 签名 | 真实联调验收点=沙箱钱包扫码全流程 |
| 微信支付 APIv3 | Native 下单出 `code_url` + 查单/关单/退款；商户私钥 SHA256-RSA 签名、应答验签（公钥模式 / 平台证书）；**无公开沙箱**（v2 `sandboxnew` 已废） | 代码按 v3 真实实现+签名单测锁死；真实联调待商户号配置；**不拿支付宝沙箱通过盖微信的章** |

## 2. 核心裁决（10 条）

### 2.1 两种支付形态统一到一个支付订单状态机

- **自有收单**（`ALIPAY_QR`/`WECHAT_QR`；停车费等我方为收款方的场景）：网关直连渠道
  precreate 出二维码，轮询查单推进状态。
- **商家收银**（`MERCHANT_HOSTED`；瑞幸/麦当劳）：商户 MCP 下单返回支付链接，网关只
  **登记会话**（审计+展示+超时收口），**不自动轮询终态**——商户是订单状态的真相源
  （M-D 既有裁决），用户问订单走商户查单工具。我方对商户订单也没有查单凭证：支付
  发生在商户收银台，不在我们的商户号里。

### 2.2 Capture 语义 = 「确认后亮码」，不加 PAID 终态

Authorize 本地建单**不碰渠道** → 用户确认 → Capture 调渠道 precreate 拿二维码、订单落
`pending_pay` → 轮询 worker 确认渠道收款后推进 `captured`。两段式的安全价值（确认前
渠道零动作）逐字保留；Capture 是 RPC（动作），CAPTURED 是状态（结果），两者本就不必
同刻发生——RPC 契约零破坏。**不加 PAID**：全仓对 CAPTURED 的既有语义就是「支付完成」，
第二个「已支付」态=第二真相源。状态机：

```
authorized(1) ──Capture(渠道 precreate)──► pending_pay(5) ──worker 查单 PAID──► captured(2) [终]
authorized    ──Cancel / 确认超时────────► cancelled(3) [终]
pending_pay   ──二维码过期(worker close)──► expired(6) [终]
pending_pay   ──Cancel(渠道 close)───────► cancelled(3) [终]
captured      ──Refund──► refunding(7) ──渠道退款成功──► refunded(8) [终]
渠道 precreate 失败 ──► failed(4) [终]
merchant_hosted：Authorize 直接落 pending_pay；只有过期收口（不查渠道），无 captured 推进
```

GetStatus 对不存在的单 `abort(NOT_FOUND)`（原实现硬编码回 FAILED=4——调用方为零，无兼容包袱）。

### 2.3 confirm_token 幂等重取，不走挂起 payload

engine 刻意不持久化 step.meta（防重放设计），挂起步结果也不进恢复种子——「第一趟产生、
第二趟要用」的值在编排里**没有官方通道**，硬开有三重泄露面（obs 内容采集 / HMI /
session Redis）。正解：Agent 第二趟 confirmed 分支**用同幂等键重调 Authorize**，幂等
命中返回同单同 token，立即 Capture。token 生命周期=单次 `handle()` 栈内，不落任何持久
面（测试断言 speech/ui_card/data 无 token）。两层安全各司其职：**「用户确认过」由编排
层保证**（confirmed 只能被 `engine._restore` 注入 + 中央兜底闸），**confirm_token 防的
是绕过 Authorize 直接 Capture / 拿 A 单 token 打 B 单**。token 单次有效（Capture 成功
即作废），幂等命中不轮换。

### 2.4 轮询 worker：网关进程内 asyncio task + Redis zset

`payment:poll` zset（member=payment_id，score=下次轮询时刻），3s→5s→8s 步进；重启全量
装载续轮——**停机不丢钱**（渠道侧照收），最坏回执迟到，上限=二维码有效期
（`PAYMENT_QR_EXPIRE_S` 默认 300s）。**不建独立 poller 服务**：查单要签名、签名必须在
凭证域内，独立服务=渠道凭证第二注入点。单实例假设显式注释（多副本需 per-payment
SETNX 租约，v1 不做）。

### 2.5 merchant_hosted 单次 Authorize 登记（不走两段）

两段式价值是「确认前渠道零动作」，而商家场景的用户确认已由 MCP 写工具的
require_confirm 闸完成、商户下单已发生——Capture 在此无事可做。桥侧一个
`Authorize(channel=MERCHANT_HOSTED, external_pay_url, external_order_ref)`，网关直接
建单落 `pending_pay`、回 status。本地终局=过期收口（默认 30 分钟 → `expired`，语义
「登记会话结束」而非「用户没付」）。话术钉死「订单状态以商家为准」，且「说『查一下
订单』我帮你问商家」只有该商户的查单工具真进了准入清单才许说（§9.9 先有能力再有话术）。

### 2.6 补偿形态显式化：`compensate_policy ∈ {tool, abandon_unpaid}`

麦当劳流是「创建**未支付**订单 → 用户扫码才付钱」，写操作本身不产生钱的义务，天然补偿
=不支付+商户自动过期——与 demo-coffee「下单即扣款、补偿=退款取消」是两种形态，硬套
`compensate_tool` 只会逼出造假声明。admission 增 `compensate_policy`：`tool`（默认，
必须给 `compensate_tool` **且校验其 ∈ 本 server 白名单**——现状只查非空不查存在性，
历史教训在准入器里没修干净，一并补）；`abandon_unpaid`（强制 confirm_prompt 说明
「下单后需扫码支付，不支付将自动取消」）。这是准入规则的诚实化，不是放宽。

### 2.7 REQUIRE_REAL_EXEMPT 拆分 + PAYMENT_REAL_SCENES 防「假数据收真钱」

`parking` 豁免今天捆绑两件事：停车数据 mock + 支付即模拟。接真支付后前者依然为真。
处理：payment 成为**独立决议域**（决议行 `provider[payment]=…`，不进默认豁免）；
`parking` 豁免保留、理由改「停车数据源（ETCP）未接真」——同步**五处**（.env.example /
compose / conventions ×2 / **`agents/_sdk/provenance.py` 代码默认值**，漏最后一处则
前四处全部无效）。最危险组合是 `PAYMENT_VENDOR=真渠道 + PARKING_VENDOR=mock`：拿假
费用收真钱。闸：**`PAYMENT_REAL_SCENES` 场景白名单**（逗号分隔，默认空），scene 不在
白名单的 Authorize 一律路由 mock provider（fail-closed）——运维必须逐场景显式声明
「金额来源已真实化」才允许走真渠道。

### 2.8 幂等三层链，参数取快照

`idempotency_key`（Agent 请求指纹 `sha256(user_id|scene|订单要素)[:16]`，**刻意不含
金额**）→ `payment_id`（`pay_`+12hex）→ **`out_trade_no ≡ payment_id`**。渠道参数从
订单快照取、不从请求重算：微信对「同单号不同参数」直接报错；更重要的是金额漂移——
第一趟报 5 元用户确认、第二趟费用变 6 元时，幂等命中返回 5 元快照单，**用户确认的
金额=扣的金额**（幂等键含金额则漂移导致 miss、悄悄建一张用户没确认过的新单，这正是
键不含金额的原因）。Capture 对 pending_pay 重入直接回缓存二维码。退款
`out_refund_no = payment_id + "_r1"`（v1 仅整单退一次）。

### 2.9 微信 v1：公钥模式优先 + 平台证书懒加载；无回调纯轮询

优先「微信支付公钥」静态配置（2024 起新商户默认，零轮换）；平台证书模式作兼容：懒加载
`GET /v3/certificates`（APIv3 key AES-256-GCM 解密），内存缓存 12h，**应答验签遇未知
序列号即时重拉一次**（覆盖轮换窗口），不做后台定时任务。**v1 无支付回调（纯主动查单）**：
车机无公网入站，入站验签面为零——这是轮询模式在车机场景的安全红利，不是妥协。

### 2.10 终态经统一主动引擎推送；provider_mode 与 pay_url_hosts

HMI 与 payment-gateway 之间没有任何现成通道，不为轮询新开一条。支付终态由 worker 经
`runtime/proactive.py::publish_proactive` 推送（`priority=user_contract`——用户明确
期待的回执正是该档语义；`dedup_key=payment|{payment_id}`），HMI 走既有 `agent.proactive`
渲染。payment_qr 卡只渲二维码+本地倒计时（到 `expires_at` 置灰）。proto 回传
`provider_mode`（"real"/"mock"）：mock 渠道出的二维码卡必须按 §9.3 打 `_prov` 角标——
出一张真二维码样式的卡不标注=盖真章违规。商户回传的 `payH5Url` 是外部 URL：servers.yaml
per-server 声明 **`pay_url_hosts`** 域名白名单，不合白名单拒登记、卡片不出二维码只出
话术（防被篡改响应诱导扫码钓鱼）。

## 3. proto 变更（只加不改）

`proto/cockpit/payment/v1/payment.proto`：

- `enum Channel { CHANNEL_UNSPECIFIED=0; ALIPAY_QR=1; WECHAT_QR=2; MERCHANT_HOSTED=3 }`
- `AuthorizeRequest` +`Channel channel=9` +`string external_pay_url=10` +`string external_order_ref=11`
- `AuthorizeResponse` +`GetStatusResponse.Status status=5` +`string provider_mode=6`
- `CaptureRequest` 不变；`CaptureResponse` +`string qr_content=4` +`string pay_url=5`
  +`int64 expires_at_ms=6` +`string trade_no=7` +`string provider_mode=8`
- `GetStatusResponse.Status` +`PENDING_PAY=5; EXPIRED=6; REFUNDING=7; REFUNDED=8`；
  message +`string trade_no=5` +`Channel channel=6` +`int64 expires_at_ms=7`
- 新增 `rpc Refund (RefundRequest) returns (RefundResponse)`：
  `RefundRequest{payment_id, amount_cents(0=全额), reason}` → `RefundResponse{ok, refund_id, error}`

## 4. 三批清单（文件级）

### 批 1：网关核心 + 双渠道 provider（本文档 + ws6 §2 + conventions §9.17 先行）

- `payment-gateway/providers/{base,mock,alipay,wechat}.py`：ABC（create_qr/query/close/
  refund）+ 工厂 `resolve_payment_providers()`（PAYMENT_VENDOR，含严格栈决议行，**网关内
  自实现、不 import agents 包**——`agents/_sdk/__init__` 会连带拖入 BaseAgent/gRPC 全家）；
  mock（`PAYMENT_MOCK_AUTOPAY_S` 默认 8s 模拟支付完成，0=立即，-1=永不）；alipay 当面付
  四接口 RSA2 自实现（httpx+cryptography，`ALIPAY_GATEWAY` 可指沙箱）；wechat Native v3
  四接口（签名/验签按 2.9）。
- `payment-gateway/store.py` 重写：Redis 真读写（hash `payment:order:{id}` /
  `payment:idem:{key}` / zset `payment:poll`）+ 内存兜底（Redis 不可达诚实降级 + 启动
  warning）；金额>0、CNY、≤`PAYMENT_MAX_AMOUNT_FEN` fail-closed；`mark_*` 状态迁移族 +
  `due_polls/schedule_poll` + `redact_owner` Redis 版；顶部 `PERSONAL_DATA_TARGETS` 同步。
- `payment-gateway/server.py` 重写：**修 confirm_token 回传 bug**；继承
  `payment_pb2_grpc.PaymentGatewayServicer`；PAYMENT_REAL_SCENES 选 provider；
  MERCHANT_HOSTED 单段分支（pay_url 域名白名单 `PAYMENT_EXTERNAL_PAY_HOSTS` 由桥侧
  servers.yaml 声明值透传？——**否**：网关侧配置 env 白名单，桥侧声明只决定提取；两层
  各自持有自己的白名单，防单点绕过）；GetStatus NOT_FOUND；audit + obs span。
- `payment-gateway/worker.py`：PollWorker（查单推进 captured / 过期 close→expired /
  proactive 推送 / zset 续轮）。
- `payment-gateway/main.py` 启动 worker；`Dockerfile` +`COPY security`；requirements
  +httpx+cryptography；`security/audit.py` +payment_captured/payment_refunded。
- 隐私四处+redact：store 头部 / `runtime/privacy_registry.py`（backend→redis、
  storage_variants）/ `test/e2e_manifest.yaml` / `payment_redact_owner` 实现。
- compose：payment-gateway env 全组 + healthcheck + 加固四件套（read_only/
  no-new-privileges/mem_limit/cpus）+ http-proxy + egress 白名单（支付宝/微信域名）；
  `.env.example` 支付渠道段。
- 测试：`payment-gateway/tests/` test_store / test_server / test_sign_alipay /
  test_sign_wechat / test_worker（全离线零外呼；confirm_token 回归钉死原 bug）。

### 批 2：parking 闭环 + HMI 支付卡 + 豁免拆分

- `agents/_sdk/payment_client.py`（parking 与桥共用，「Agent 只经网关支付」机制化）。
- parking `_pay` 重写（幂等重取时序，2.3/2.8）；provider 删 `pay()`（支付不再是停车
  provider 职责，还 TODO 债）；tests 重写含 token 不泄露断言。
- HMI：`payment_qr` 新卡（qrcode.react）+ 存量欠账 `payment_receipt`/`parking_fee`
  （agent 早在发、HMI 一直渲染 null）。
- 豁免拆分五处（2.7）；compose `PAYMENT_GATEWAY_ADDR` + depends_on。
- `test/e2e_payment.py` + L3 journey；支付宝沙箱 opt-in 联调（凭证在才跑，CI skip）。

### 批 3：桥 streamable_http + 瑞幸/麦当劳

- conventions §9.9 先行：HTTP/SSE 解封（边界=仅官方商户远程端点+全量人工准入）；新字段
  表 transport/url/headers(${VAR})/pay_url_locator/pay_url_hosts/compensate_policy；
  远程 server 版本锁定=version 留空+schema_sha 工具级锁（远程平台版本随平台升级，逐字
  锁常态拒载）；商户会话 token（进桥）≠ 支付渠道凭证（只进网关）。
- `mcp_client.py` +HttpMcpClient（POST JSON-RPC + SSE 分帧 + Mcp-Session-Id 存续 +
  lazy re-init + 日志永不打 headers）；admission 扩展（${VAR} 缺失=该 server 拒载，
  不静默空 token 出站吃 401）；`_call_write` 按 `pay_url_locator` 声明式提取支付链接 →
  `PaymentClient.authorize(MERCHANT_HOSTED)` 登记 → payment_qr 卡（登记失败不阻断出卡
  ——下单是既成事实）。
- servers.yaml +mcdonalds/+luckin（intent 用 `mcd.*`/`luckin.*` 域，不混 `shop.*`——
  demo 不卷进真钱路径；**demo-coffee 一字不动**，`mcp-bridge#0` 退役需专项安全回归）。
- HMI +`mcp_order`/`mcp_result` 存量欠账卡；修 `test/e2e_mcp.py` 过期断言（
  shop.order_cancel 进白名单后 337-339 没同步）。
- 尺子侧全套：exemplars mcd/luckin、对抗语料每新 intent 正2/硬负2/对照1（跨商户混淆
  「在麦当劳点杯咖啡」、读写边界「汉堡多少钱≠下单」）、mode_routing、5 处 servers.yaml
  门禁 `--strict` 确认。
- token 到位后真机：tools/list 快照 + schema_sha 锁定 + 小额真实下单亮码。

## 5. 风险与缓解（摘要）

| 风险 | 缓解 |
|---|---|
| 假数据收真钱 | PAYMENT_REAL_SCENES 默认空 fail-closed（2.7）；e2e 断言空名单零真渠道外呼 |
| 金额漂移（确认 A 扣 B） | 幂等键不含金额 + 快照单一真相 + confirmed 不重查费（2.8） |
| token/密钥泄露 | confirm_token 不出栈（2.3）；渠道凭证只注入网关、商户 token 只注入桥；测试断言日志无 Authorization/私钥 |
| 重复扣款 | 三层幂等链 + Capture 重入回缓存码 + 副作用步永不重试（verify.py 既有硬约束） |
| worker 停机期间用户已付款 | zset 续轮（2.4）；最坏回执迟到≤码有效期；渠道侧钱不丢 |
| 微信无沙箱 | 签名单测锁死报文；发布说明诚实标注「微信路径未经真环境验收」 |
| 商户平台升级 | version 留空+schema_sha 工具级锁：变更→逐工具拒载+告警，能力诚实缺席不误执行 |
| payH5Url 钓鱼 | pay_url_hosts（桥）+ 网关侧域名白名单双层（4.批1） |
| 隐私门禁红 | 四处+redact 原子同步（批 1 单 commit 内） |

## 6. 实施记录

### 6.1 批 1：网关核心 + 双渠道 provider（2026-08-11）

**交付面**（全部落地）：
- 文档三件：本文档、ws6 §2 时序重写、conventions §9.17 + §6 env 支付渠道段 +
  §9.4 域名清单加 payment + 服务表更新。
- proto：Channel 枚举 / Authorize·Capture·GetStatus 扩字段 / Status +4 态 /
  Refund RPC，`scripts/gen-proto.ps1` 重新生成并验证字段齐全。
- `payment-gateway/providers/`：base（ABC + 决议工厂 + 严格栈同口径自实现）、
  mock（`PAYMENT_MOCK_AUTOPAY_S` 旋钮）、alipay（当面付四接口，RSA2 签名 +
  响应节点原文验签 `_extract_node`）、wechat（v3 四接口，公钥模式优先 + 平台证书
  懒加载 + 未知序列号即时重拉 + TOFU 拉取）。
- store 重写：Redis hash/idem/zset 三键形态 + 内存兜底（backend 启动定命，运行中
  不切换防状态分裂）；9 态迁移表 `_TRANSITIONS`；金额/币种/上限 fail-closed；
  confirm_token 亮码即作废；`redact_owner`/`count_for_owner`（隐私探针面）。
- server 重写：**修 confirm_token 不回传 bug**（F23，回归测试钉死）；继承生成基类；
  scope 执行层校验（metadata 缺失=PoC fail-open + `fail_open_scopes` 留痕，缺
  scope=硬拒 + `permission_denied`）；`PAYMENT_REAL_SCENES` 白名单；MERCHANT_HOSTED
  单段（域名白名单 + `pay_url_denied` 审计）；GetStatus NOT_FOUND；obs span 四节点。
- worker：查单推进/过期收口/退避重轮/终态单自清/`payment_captured` 审计/
  `user_contract` 档回执推送（`dedup_key=payment|{id}`，mock 单卡带 `_prov`）。
- audit +3 方法（payment_captured/payment_refunded/pay_url_denied）；main 启动
  worker + NATS；Dockerfile +`COPY security`；requirements +httpx+cryptography。
- 隐私四处同步（store 头部/privacy_registry backend→`redis_or_memory` +
  storage_variants/e2e_manifest 镜像/redact 实现）。
- compose：凭证注入位全组（只注入本服务）+ healthcheck + 加固四件套（read_only/
  no-new-privileges/mem_limit/cpus，对齐 third_party 沙箱）+ http-proxy/NO_PROXY +
  egress 白名单三域名；.env.example 支付段；「parking=支付设计即模拟」过时注释
  四处更新（.env.example/conventions×2/provenance.py）。
- 测试五件 96 条：test_store 30（幂等/状态机/序列化往返/轮询集/脱敏）、
  test_server 24（含 F23 回归/白名单闸/merchant 单段/审计断言）、
  test_sign_alipay 22 + test_sign_wechat 12（自造密钥对 + MockTransport 离线回放，
  含 AES-GCM 证书解密与未知序列号重拉）、test_worker 8。
  **导入形态**：tests 以 `payment_gateway` 别名包加载（不占 providers/store 裸名，
  与 llm-gateway 同名模块零冲突——「import server 裸名劫持」教训的机制化规避）；
  被测代码 try 平坦 / except 相对双形态。

**与方案的偏离（2 处）**：
1. `e2e_strict_stack.py` 批 1 不改：它是 WS 卡片 `_prov` 探针不是日志 grep，批 1
   没有支付卡可探——改了也测不到，属虚假交付。批 2 payment_qr 卡落地后加支付探针。
   严格栈的批 1 保障=网关 mock 决议在 on 档**拒绝启动**（比探针更硬）。
2. 豁免文案拆分从批 2 提前到批 1：网关真实化后「支付设计即模拟」已与事实不符，
   文档与事实一致优先（`REQUIRE_REAL_EXEMPT` 默认值本身不变，改的是理由文案与
   payment 独立决议域的登记）。

**验证读数**：payment-gateway 单测 **96/96**；scripts+runtime 守卫 **737 passed /
4 skipped**（含 privacy_registry↔e2e_manifest 比对——四处同步一致）；L0 门禁
strict **25/25 exit 0**；能力完整性门禁 **PASS exit 0**；端侧 smoke **13/13**；
决议行冒烟 `provider[payment]=mock` 格式对齐 §9.4。全量 pytest 基线见提交信息。

**附带修复（尺子可移植性，非案例集变更）**：首次全量跑出 3 failed，全部在
`test/test_eval_intent_adversarial_cli.py` 的子进程测试——定责对照（同代码、仅
去掉宿主 `PYTHONIOENCODING=utf-8`）后 166/166 全绿，坐实为**环境敏感**而非支付
改动：`subprocess.run(text=True)` 不带 encoding 靠「子进程输出编码==父 locale」
的巧合成立，宿主带 PYTHONIOENCODING 时子进程 UTF-8、reader GBK，reader 线程
UnicodeDecodeError 后 `proc.stdout=None`。修法两端钉死（`_SUBPROC_UTF8`：reader
`encoding="utf-8"` + 子进程 env 强制 UTF-8），**三条断言语义一字未动**——这是让
尺子在所有环境量同一个东西，与「为模型改案例集」不同族。修复后带/不带该 env
两臂各自实测全绿。同族先例：e2e_verify 的 GBK 账（history §27.6）。

### 6.2 批 2：parking 闭环 + HMI 支付卡（2026-08-11）

**交付面**：
- `agents/_sdk/payment_client.py`：PaymentClient（authorize/capture/status/cancel，
  mTLS 随 runtime 工厂，trace 经 metadata `x-trace-id`）；调用失败返回 None——
  Agent 按 R9 诚实降级。granted_scopes 不随 Agent meta 下发（编排层校验后不透传），
  网关侧走 PoC fail-open 留痕契约。
- parking `_pay` 重写为幂等重取时序（2.3/2.8）；**provider 接口删 `pay()`**（还
  TODO 债——残留 mock pay 接口=诱惑下一个实现者绕过网关，接口删除有回归钉
  `test_provider_has_no_pay_surface`）；`_query_fee` 保持原样。
- **批 1 契约的两处接线修订**（业务接线揭示）：① store 幂等查找**先于**参数校验
  （第二趟传占位金额必须命中快照单），且 cancelled/expired/failed 命中时建新单
  remap 幂等键——幂等防双付、不防用户对已关订单重新尝试（captured 命中仍返回原单）；
  ② AuthorizeResponse +`amount_cents`（幂等重取方没有金额可显示）。
- **二维码渲染裁决**：网关侧生成 SVG data URI（纯 python `qrcode` 无 pillow，
  CaptureResponse +`qr_svg`），HMI **零新依赖** `<img>` 直渲——不给前端构建链加
  npm 依赖，且付款码属支付凭证展示面、与渠道逻辑同域。生成失败诚实回空，HMI
  回落显示 pay_url 文本。
- HMI 三卡：`payment_qr`（本地倒计时到 expires_at 置灰+过期蒙层；**不轮询网关**
  ——HMI 与网关无通道，回执由主动引擎推）+ 存量欠账清偿 `payment_receipt` /
  `parking_fee`（agent 早在发、HMI 一直渲染 null）。图标库是 Figma 封闭清单无
  对勾图标，回执卡用文本符号。
- compose：x-python-env +`PAYMENT_GATEWAY_ADDR`；parking depends_on
  payment-gateway(healthy) + NO_PROXY 加 payment-gateway（proxy 与 gRPC 并存的
  nearby 先例）。parking 镜像闭包核验：gen/python 全量 COPY 含 payment proto ✓。
- `test/e2e_payment.py` + manifest 注册（milestone 车道——「交停车费」落域依赖真
  LLM）：纯 WS 黑盒三场景，场景③「再缴费→已支付过」一句话同时证明 worker 推进
  captured 与幂等防双付（也绕开 winnat 挡 50071 宿主直连的老坑）。
- L3 journey `A7-1`（target_a）：need_confirm 态 + payment_qr 卡状态化断言
  （`cards_not` 不在 runner 算子集，不硬造——「确认前零动作」由 need_confirm 态
  与网关契约测试合钉）。
- `scripts/alipay_sandbox_probe.py`：沙箱人工联调探针（precreate 1 分钱→人扫码→
  查单 PAID→退款；**拒绝对非沙箱网关跑**；无凭证 SKIP exit 0）。人工环节不进
  e2e_manifest 任何车道。
- 豁免文案拆分五处已在批 1 提前完成（§6.1 偏离 2）。

**与方案的偏离（1 处）**：e2e_strict_stack 不加支付探针——它的 PROBES 是「外源
数据卡 `_prov` 一致性」语义，支付卡是凭证展示面不是外源数据；严格栈下支付域的
自证由启动闸（mock 决议拒启）承担，比探针更硬。

**验证读数**：网关+parking 单测与 e2e_manifest 守卫 **164 passed**；HMI
`node --test` **225/225** + `vite build` 通过；**真栈演练**（重建 payment-gateway
与 parking-payment-agent 两镜像后）：网关容器 healthy、决议行
`provider[payment]=mock`、NATS 连接、PollWorker 启动全对；
`run_e2e --lane milestone --id e2e_payment` **PASS 3/3**——①确认话术=网关快照
金额 ②payment_qr 卡（mockpay 码+SVG+mock 角标）③等待后再缴费答「已经支付过」
（worker 推进 captured 与幂等防双付的黑盒合证）。沙箱探针 SKIP 路径冒烟 exit 0。
全量 pytest 见提交信息。

### 6.3 批 3：桥 streamable_http + 商户接入机制（2026-08-11）

**交付面**：
- conventions §9.9 先行改写：transport 解封（仅官方商户远程端点）、远程版本锁定
  =version 留空+schema_sha 工具级、compensate_policy 两态、支付链接闭环行、
  「HTTP/SSE 不做」从不做清单移出并留痕。
- `mcp_client.py`：+`HttpMcpClient`（与 Stdio 鸭子同形；POST JSON-RPC + SSE 分帧
  + Mcp-Session-Id 存续 + **404 重新握手重试一次**；`parse_tool_result` 抽公共）。
  **与 stdio 的关键语义差**：HTTP 无子进程持久状态——alive 恒 True、healthy 握手
  成功后保持，单次网络失败按次抛错下次自愈，不像 stdio「崩了整批死到重启」。
  安全：异常文本只带类型、日志永不打 headers（token 泄露面）。
- `admission.py`：ServerSpec +transport/url/headers/pay_url_hosts/env_error；
  ToolSpec +compensate_policy/pay_url_locator；`${VAR}` 展开**缺 env=具名拒载**
  （不静默空 token 出站吃 401）；admit +compensate_tool **存在性校验**（批 3 补死
  demo-coffee「声明存在≠能用」在准入器里的残留）+ abandon_unpaid 强制 confirm_prompt。
- `agent.py`：bootstrap 客户端工厂分派 + env_error 整台拒载；`_call_write` 成功
  分支按 `pay_url_locator` 声明式提取（`_dig` 点路径，桥核心零领域词）→
  `_register_merchant_payment`（白名单第一层拦截不出链接防钓鱼 / MERCHANT_HOSTED
  登记 / **登记失败不阻断出卡** / demo 三重标注保持）。
- 网关配套两处：store 允许 merchant_hosted 单 amount=0（商户可能不回金额——
  登记 0 是「金额未知」的诚实表达不造数）；AuthorizeResponse +`qr_svg`
  （merchant 登记即亮不走 Capture，SVG 就地回传）。
- `servers.yaml`：mcdonalds/luckin 以**完整注释模板**入库（含五步激活 checklist）。
  刻意不激活：工具真实名与 schema 要 token 到位后 tools/list 现场核实，而未验证
  条目会被 5 处离线门禁当真实能力面消费——「清单里有而真机没验过」=虚假声明。
  **demo-coffee 一字未动**（`mcp-bridge#0` 退役需专项安全回归）。

> **2026-08-12 安全裁决更新**：上文“登记失败不阻断出卡”只记录 8 月 11 日批次当时的
> 实施口径，现已被后续对抗审查否决。商户订单创建成功与支付入口是否可安全展示是两件事：
> 前者仍如实返回；后者必须同时通过桥/网关两层域名白名单并完成网关登记，否则清洗全部
> URI，只提示去官方 App 支付。不得把未经网关登记的原始 `pay_url` 当降级卡片返回。
- 配置面：.env.example +两 token 段（申请入口/有效期/缺席语义）；compose
  mcp-bridge +token env+HTTP(S)_PROXY/NO_PROXY+depends http-proxy；egress
  +mcp.mcd.cn+open.lkcoffee.com。
- HMI +`mcp_order`/`mcp_result` 卡（存量欠账清偿——「演示商户」三重冗余的第二重
  第一次有了前端渲染出口）。
- **修过期尺子** `test/e2e_mcp.py`：「cancel 不在能力面」断言在 shop.order_cancel
  进白名单（2026-08-01）后恒红了 10 天没人发现（e2e_mcp 只在 milestone 车道跑）
  ——改为四工具齐全断言 + admin 仍禁。
- 测试三件 25 条：test_admission_http 8（解析/展开/缺 env/两态/存在性）、
  test_http_client 8（json+SSE/会话头/404 重握手/**token 不泄露**/单次失败自愈）、
  test_payurl_register 9（_dig/登记参数/白名单拦截/失败不阻断/demo 标注/金额未知）。

**与方案的偏离（2 处）**：
1. 瑞幸/麦当劳条目**注释模板而非激活条目**（理由见上；「配置即接入」的全部机制
   已交付并被 25 条测试锁死，激活是 token 到位后的纯配置动作——checklist 在
   yaml 注释里，零代码）。尺子侧（mcd/luckin 范例+对抗语料）随激活同批做，不为
   未激活的能力面造语料。
2. e2e_mcp 不扩 http 假商户：生产 servers.yaml 不该有测试 server；http 协议面由
   MockTransport 单测全覆盖，真机验证留给 token。

**验证读数**：桥测试 **60 passed**（新 25 + 存量 35 零破坏）；HMI **225/225** +
build 通过；servers.yaml yaml 合法性验证（激活面仍仅 demo-coffee）；真栈重建
mcp-bridge 后桥日志「准入 4 个工具」照旧、`run_e2e --lane milestone --id e2e_mcp`
**PASS 12/12**（含修正后的四工具断言）。批 2+3 定稿工作区最终全量
**4994 passed / 14 skipped / 0 failed**；L0 strict 2/2、能力完整性 PASS、
smoke 13/13 全绿。

**验证时序说明（诚实记录）**：批 2 的独立全量在批 3 开工后被判**混合快照作废**
（pytest 启动后被改动的未加载文件会读到新代码），故批 2 凭分面证据（守卫 168 +
网关/parking 测试 + e2e_payment 真栈 3/3）收口、最终全量以批 2+3 定稿工作区一次
实测。同理两批共享文件多（proto/store/server/compose/HMI/design），合并为一个
commit 分段描述——硬拆会造出单独 checkout 不可运行的半截历史。

### 6.4 批 3b：凭证到位，商户真机激活 + 沙箱真实联调（2026-08-11）

泓舟填入四凭证后当日激活。**「现场核实改变方案」的完整记录**：

**真机 tools/list 核实**（`HttpMcpClient` 直连，快照与全量 inputSchema 存会话产物）：
- 麦当劳（mcp.mcd.cn，serverInfo=mcd-mcp 1.0.0）：**29 个工具**（比报道多 1），
  **无取消类工具**——`create-order` 补偿形态=abandon_unpaid 的预判坐实。但 schema
  显示它是**强多步流程 API**：`create-order` 的 `items` 是结构化数组，全链硬依赖
  `query-nearby-stores` 返回的 storeCode/beCode，连 `query-meals` 查菜单都 required
  storeCode。桥的「扁平 slots→LLM→arg_map」单步机制接不住——**下单归二期**，
  阻塞点=planner 结构化参数与步间引用（编排能力面，不是配置问题）。
- 瑞幸（gwmcp.lkcoffee.com/order/user/mcp，luckyordermcpmaster 1.0.0）：8 工具，
  **有 `cancelOrder`**——补偿两态被两家真实商户各占其一地验证。`queryShopList`
  required 精确经纬度 × third_party 桥禁 `location.precise`（ws8 硬上限）——
  **结构性不可激活**；门店发现本就归 nearby（高德）。

> **2026-08-12 二期实施入口：** 后续审计裁定不改全局 slots/proto，而在桥内新增确定性
> 商户 workflow + Redis checkout snapshot，Planner 只传标量和 opaque token；公开 POI
> 坐标由 Executor 的可信 slot-ref 来源证明。完整方案、写安全、交互与验收见
> [`2026-08-12-merchant-mcp-full-flow.md`](2026-08-12-merchant-mcp-full-flow.md)。

**v1 激活面收敛为独立价值明确的三件**（全只读零资金风险）：`mcd.menu`
（list-nutrition-foods，营养/餐品知识——nearby 答不了「巨无霸多少大卡」）、
`mcd.order_status`（query-order）、`luckin.order_status`（queryOrderDetailInfo，
查瑞幸 App 下的单）。门店类 intent 刻意不激活——不为激活数量保留与既有能力打架
的 intent。schema_sha 全部按真机指纹锁死；version 留空（远程平台版本不锁）。

**新机制两个 + 既有缺陷一处**（激活压力测试出来的）：
- `const_args`（ToolSpec）：真实商户工具的 required 枚举选择器（beType 等）在
  清单里声明死，LLM 槽位不填场景参数；组装序 const→槽位→系统键。
- `_resolve_order_ref` 的 args 层键名此前用规划期名 `order_id` 回填——demo 商户
  参数恰好同名没暴露，麦当劳的 `orderId` 会踩；改走 arg_map 翻译。
- 能力完整性门禁自身缺 `stdout.reconfigure`——GBK 控制台下 `✓` 编不出**门禁自己
  崩溃**（PowerShell 带 PYTHONIOENCODING 绿 / Bash 崩，批 1 环境敏感一族）。

**尺子侧全套**（门禁逐级抓出的具名缺口，全部照单补齐）：exemplars mcd/luckin
两域（域错配契约挡掉边界条→移对抗语料锁）；对抗语料 18 条净增（正/硬负/对照 +
**cohort leakage 四句改写**——unseen_transfer 的句子与范例逐字相同被契约抓出 +
**boundary 台账兑现**：2 条新裁定双向各 2 例 + relation invariant 3 条）；
boundaries.yaml 两条裁定（品牌词是判据）；suites.yaml 上界 540→560（第三次适用
「上限跟着裁定数走」先例）；mode_routing 5 条。

**真栈验证**：根 compose 重建后三 server 准入全成（麦当劳 2 工具 + 27 具名忽略 /
瑞幸 1 + 7 忽略 / demo 4；schema_sha 真机校验过）；容器内直连问答——mcd.menu
返回麦当劳真实营养数据（status 0）、luckin.order_status 触达瑞幸真实后端（无单
号被如实拒参，链路通）。⚠ 中途踩「compose 根入口」老坑一脚（`-f deploy/...` 读
不到根 .env 插值，token 没进容器——拒载路径反而先被真栈验证了一遍）。

**支付宝沙箱真实联调**（`scripts/alipay_sandbox_probe.py`）：
- **抓到真 bug**：支付宝网关响应是 **GBK**，中文（sub_msg「交易不存在」）出现时
  UTF-8 字节验签必炸——全 ASCII 响应两种编码字节相同，precreate 成功响应与全部
  MockTransport 单测都侥幸绿。修=按 `resp.encoding` 还原签名字节 + GBK 中文回归
  钉（`test_gbk_response_with_chinese_verifies`）。**单测结构性盖不住、非真实
  渠道不可见**——这就是「真实接入」验收的价值本体。
- 已在真实沙箱验证：precreate 出真码（`https://qr.alipay.com/...` + 终端 ASCII
  码图）、query 语义（TRADE_NOT_EXIST→WAITING）、超时 close→True。
- **待补最后一段**：扫码→PAID→refund。当日多轮尝试的最终定性（2026-08-11 晚）：
  泓舟已完成扫码（真码可被沙箱钱包识别），但**沙箱钱包内支付动作本身失败**——
  支付宝沙箱服务当日不可用级故障（查单通道同时段 48+ 连续 `20000 系统异常`）。
  我方四接口中 precreate（出真码）/query 语义/close/GBK 验签修复均已真栈验证；
  PAID 读取与退款段待支付宝沙箱恢复后重跑 `python -u scripts/alipay_sandbox_probe.py`
  （探针已带：precreate/query 抖动退避、浏览器自动弹大码 HTML——qr.alipay.com
  链接在浏览器会重定向不展示码、终端 ASCII 会截断，均为当日实测教训）。

**遗留记账**：商户返回的英文 API 说明文本直接进桥话术（mcd.menu 实测会朗读一段
API 文档）——需要「超长外文 text 截断+卡片承载/LLM 重述」层，记 AGENTS.md 余项。

**批 3b 验证读数**：定稿全量 **4996 passed / 14 skipped / 0 failed**（+2=GBK
回归钉 + const_args 测试；三处清单哨兵有意更新：catalog 135→138 / chars_full
11081 / 边界台账 21→23）；L0 strict **81/81 + 25/25**（553 唯一输入，上界
540→560 第三次先例适用）；范例/技能/能力门禁全绿（能力门禁自身的 GBK 崩溃已修）；
e2e_mcp 最终形态（能力面 7 intent）**PASS 12/12**；三 server 真栈准入 + 双商户
真实问答冒烟通过。

### 6.5 批 3c：商户端到端体验修复（2026-08-11，泓舟指示「端到端验证并让它合理」）

真栈 WS 五句取证（落域→桥→真实商户→话术+卡片全链）抓出四处不合理，修后复验对照：

| 案例 | 修复前（实测） | 修复后（实测） |
|---|---|---|
| 「巨无霸多少大卡」 | 念 **6638 字**英文 API 文档；卡 13KB 原始数据 | **「巨无霸一份是513大卡。」**（12 字）；卡 1.2KB |
| 「麦当劳订单到哪了」（无单号） | 拿 demo 单号打必败请求 + 念 2836 字文档 | 追问「要操作哪一单？说个订单号」 |
| 「瑞幸单子怎么样了」（无单号） | 错误 JSON 逐字念给用户 | 同上追问 |
| 「在麦当劳点个巨无霸套餐」 | 被 demo 咖啡店抢单「准备下单：巨无霸套餐」 | 落 mcd.menu，诚实说「套餐组合做不了」+单品 513 大卡+建设性追问 |
| 「附近有麦当劳吗」 | ✓ nearby（守住） | ✓ 依旧 |

**四项修复**（全声明式/桥核心零领域词）：
1. **`speech_mode: summarize`**（ToolSpec 新字段）：真实商户返回的是「给 LLM 读
   的 API 文档+原始数据」，raw 逐字进话术是实测事故。summarize=text/data 截 3000
   字交 LLM 用中文一两句**直接回答用户的问题**（SDK LLMClient，temperature 0.3）；
   LLM 不可用回落「截断+详情见屏幕」（诚实降级不念文档）。三个真实商户工具已标。
2. **读路径 NEED_SLOT 追问**：declared 槽位与账本回填都空 → 追问而非打必败请求
   （写路径早有，读路径漏了）。账本回填的引用（order_id **或幂等键**）算「有引用」
   ——不打断 M-D 按幂等键核对超时单的既有路径（改坏过一次被 61 条桥测试当场抓回）。
3. **`mcp_result` 卡瘦身**：text 不进卡（speech 已承载，此前双份塞卡）；data 字段
   值逐个截 800 字、总预算 4KB（13KB→1.2KB）。
4. **落域双修**：demo `shop.order` 描述判别化（「仅限演示饮品；麦当劳/瑞幸等真实
   品牌下单未接入不适用」——端到端实锤 planner 把品牌下单话术塞给 demo 的真因是
   描述太像通用点餐）+ `mcp-bridge#0` guard 增品牌让路词（瑞幸/麦当劳/星巴克/库迪/
   肯德基——治「点一杯瑞幸生椰拿铁」带「杯」命中 pattern 被 demo 截胡的那族；
   guard 只增不减，专项回归=eval_route_hints **78/78** + eval_fast_intent **57/57**
   + L0 门禁全绿，就是 AGENTS.md 记的「动 mcp-bridge#0 须专项安全回归」的兑现）。

**顺带抓修的正确性 bug**：账本查单/补偿回填**跨商户污染**——`mcp_order` 账不分
商户，麦当劳查单被回填 demo 咖啡的单号（「订单号不存在」把错配伪装成「用户没单」）。
修=result_ref 记 `server` 归属 + 回填只认同商户（存量无 server 字段的旧账按
demo-coffee 归属——此前只有 demo 在写账）。

**语料期望实测校准**：「在麦当劳点个巨无霸套餐」的 forbidden 移出 `mcd.menu`——
落营养能力并诚实说明是合理出路（当天 authored 未经实测的期望按实测修正，非为模型
让步；demo 抢单与查单错位仍 forbidden）。

**已知小瑕疵**（记录不修）：追问话术「或者先说『查一下我的订单』」对真实商户场景
半句冗余（那是 demo 账本的入口）——`_SLOT_PROMPTS` 按槽位名通用，桥零领域词原则
下不为单商户定制话术；nearby 话术的记忆注入重复（「用户喜欢川菜」×2）是存量
nearby 问题非本批引入。
