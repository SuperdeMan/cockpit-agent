"""查单引用的**会话范围判定**——确定性、零 LLM、本域唯一实现（Q10）。

## 为什么需要它

QA 轮用户在干净 session 里问「我刚才那笔订单是什么」，`_resolve_order_ref` 只按
`user_id` 取账本最近一单，于是把**三天前**那笔历史订单当成「刚才的」答了出来。
报告据此写下「确认前创建了真实订单」这个 P0——阶段 0.1 用三重证据推翻：
QA 轮全程零 `create-order`。

> **元判据**：「查到了一个真实副作用」不等于「这次操作产生了它」。查单一旦不绑
> 会话，就会把**历史**副作用搬进当前上下文，与「刚刚发生」无法区分。

同一个文件里，写路径（`_backfill_write_slots`，补偿类）**早就**有「优先本 session」
的逻辑，读路径连 `session_id` 都没传进来。**这是查询条件问题，不是存储问题**
——`task_ledger` 一直有 `session_id` 列。

## 三档，都从**原话**判

| 档 | 触发 | 行为 |
|---|---|---|
| `SESSION` | 本会话指代（「刚才」「这次」「这单」…） | **只认本 session 的单**；没有就诚实说没有，**不出站** |
| `HISTORY` | 历史限定（日期、「之前」「上次」…） | 照旧按 user 取最近 |
| `NEUTRAL` | 都没有（「查一下我的订单」） | 优先本 session；回落历史**但话术标注时间** |

**两档同时命中判 `HISTORY`**：「刚才我说的那笔 8 月 12 号的订单」里，日期是更具体的
限定，而「刚才」修饰的是「我说」不是「订单」。**具体限定优先于指代**。

## 误判代价不对称，所以 SESSION 词表可以窄但必须准

- 判成 SESSION、用户其实要历史单 ⇒ 系统说「这次对话里没下过单，给我订单号」
  ——**用户还有出路**。
- 判成 NEUTRAL、用户其实要本会话那单 ⇒ 系统把历史单端上来，**用户以为那是刚才的**
  ——这正是 I-021 那个错误 P0 定性的成因，且用户没有任何线索能发现。

所以本模块**只放明确的会话指代词**：模糊的「上一单」「那笔」一律留在 NEUTRAL
（NEUTRAL 已经优先本会话，够用了）。

⚠ 显式订单号走 `_explicit_numeric_order_id`，**在本判据之前生效**——严格模式若
把报了单号的查询也挡掉，用户就再也查不了任何历史订单了。那条对照断言在
`test_explicit_order_number_still_wins_over_session_scoping`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SESSION = "session"
HISTORY = "history"
NEUTRAL = "neutral"

#: 本会话指代。**刻意窄**——见模块 docstring 的代价不对称一段。
#: 「上一单/上一笔/那笔」不在此列：它们既可能指本会话也可能指历史，
#: 而 NEUTRAL 档本来就优先本会话，把它们收进来只会增加误判面。
_SESSION_RE = re.compile(
    r"刚才|刚刚|方才|刚下|刚点|刚买|刚订|刚下单|这次|本次|这单|这笔|这个订单")

#: 历史限定。日期形态用正则，其余是明确的时间副词。
#: 「之前」在别处（如指代解析）有歧义，但在**查单**语境里「之前那单」就是历史。
_HISTORY_RE = re.compile(
    r"之前|以前|历史|上次|上回|前几天|昨天|前天|上周|上个?月|"
    r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]|\d{1,2}\s*[日号]那")


def reference_scope(raw_text: str) -> str:
    """原话 → 三档范围之一。**纯函数**：同一输入永远同一输出，不问模型、不查库。"""
    text = str(raw_text or "")
    if not text:
        return NEUTRAL
    # 顺序即优先级：具体限定（日期/历史副词）压过指代词。
    if _HISTORY_RE.search(text):
        return HISTORY
    if _SESSION_RE.search(text):
        return SESSION
    return NEUTRAL


#: 指代型占位符——planner 把用户原话原样塞进 `order_id` 槽时长这样。
#: 比 `_SESSION_RE` 宽：这里判的是「这个**槽值**是不是一句指代」，
#: 不是「这句话指不指本会话」，所以「最近/上次/我的订单」也算。
_DEICTIC_SLOT = ("刚才", "刚刚", "最近", "上一", "上次", "上个", "那单",
                 "那笔", "这单", "这笔", "我的订单", "我的瑞幸订单", "订单")


def is_deictic_placeholder(value) -> bool:
    """这个 `order_id` 槽值是不是 planner 抄来的一句指代，而不是真订单号。

    **真栈实证（2026-08-16，Q10 批）**：「帮我取消刚才那笔订单」三次取样里有一次，
    planner 把 `order_id` 填成了字面量 **「刚才那笔订单」**。后果有两层：

    1. 槽位非空 ⇒ 账本回填**根本不被调用** ⇒ 会话范围守卫整个被绕过；
    2. 那个字符串会被拿去调商户 API——**「准备取消订单 刚才那笔订单 并退款，确认吗？」**
       已经念给用户听了。

    > 判据：既有的「**planner 改写 query 是不可信指代通道**」（sports 批）在
    > **槽值**上的形态。模型输出是不可信输入，防到真正会被拿去调 API 的那个值。

    ⚠ 瑞幸 workflow 里早就有一份逐字同样的判据（`_explicit_order_id`），
    通用写路径没有——**同一件事的第四处**。现在那一份改为调用这里。
    """
    compact = re.sub(r"\s+", "", str(value or ""))
    if not compact:
        return False
    # 带数字的一律放过：真订单号必然有数字，而「取消订单123456」这种混写
    # 里那串数字才是真值，交给下游的数字提取，不在这里判死。
    if re.search(r"[0-9]", compact):
        return False
    return any(marker in compact for marker in _DEICTIC_SLOT)


def allows_history_fallback(scope: str) -> bool:
    """本会话找不到订单时，允不允许退而用历史那一单。

    **本仓有三处独立的「从账本找订单引用」实现**，逐字同构地写着
    「优先本 session、否则用 fallback」：

    | 实现 | 路径 | 差异 |
    |---|---|---|
    | `agent._resolve_order_ref` | 读（查单） | 按 `result_ref.server` 分商户 |
    | `agent._backfill_write_slots` | 写（通用取消/补偿） | 过滤 outcome/status |
    | `luckin._owned_order` | 写（瑞幸取消） | 按 `result_ref.merchant`、tombstone 语义、可取消状态白名单 |

    三份的**过滤条件**确有正当差异（不同商户的状态机、真机打磨过的墓碑逻辑），
    所以本批**不强行把三个循环并成一个**——那是另一件事，风险也不在一个量级。
    但「SESSION 档不许回落历史」这条**规则**只该有一个定义处，就是这里。

    > 判据取自 §4.3「同一件事有三份各自正确的实现，就迟早会有第四份是错的」的
    > 一个**中间形态**：这三份不是各自正确、是**各自都有同一个洞**（都回落历史）。
    > 共享规则先把洞堵上；要不要收敛循环本身，留给下一批带着真机证据决定。
    """
    return scope != SESSION


@dataclass
class OrderRef:
    """一次查单引用解析的结果。

    `found=False` 且 `scope=SESSION` 是**唯一需要诚实弃权**的组合——
    其余情况要么有引用可查，要么退回既有的「报个订单号」追问。
    """
    found: bool = False
    from_session: bool = False
    created_at: float = 0.0
    scope: str = NEUTRAL

    @property
    def needs_honest_declination(self) -> bool:
        """本会话问「刚才那单」但本会话根本没有单 ⇒ 不出站、直说没有。"""
        return self.scope == SESSION and not self.found

    @property
    def should_label_as_history(self) -> bool:
        """查到的不是本会话那单 ⇒ 话术必须标注它是什么时候的。

        ⚠ `HISTORY` 档**也要标注**：用户说「之前那单」并不代表他知道是哪一天。
        标注的成本是一句话，不标注的成本是让历史与「刚才」不可区分。
        """
        return self.found and not self.from_session and self.created_at > 0
