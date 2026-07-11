"""跨 Agent 会话状态键的**权威登记**（与 `docs/conventions.md`「跨 Agent 状态键」同步）。

Agent 无状态化：一次会话的临时状态落 profile KV（经 `Context.save_shared_state` /
`load_shared_state`），供跨轮或跨 Agent 复用。key 常量集中于此，杜绝字面量散落——改 key /
换存储时改一处即可，不再静默断链（审计 A5）。

| key              | owner（写）        | reader（读）              | schema（value）                        | TTL |
|------------------|--------------------|---------------------------|----------------------------------------|-----|
| `news_active`    | info（news 域）    | deep-research（深挖第N条）  | `{items:[{title,source}]}`             | 会话/被覆盖 |
| `research_active`| deep-research      | deep-research（多轮聚焦）   | `{question,summary,sections[],freshness}` | 会话/被覆盖 |
| `trip_active`    | trip-planner       | trip-planner（有状态改天）  | `Trip.to_dict()`                        | 会话/被覆盖 |
| `reminders_active`| reminder（list/create/complete/cancel 后刷新）| reminder（「第N条」序号解析） | `{items:[{id,title}]}` | 会话/被覆盖 |
| `reminder_pending`| reminder（缺时刻 NEED_SLOT 追问时写） | reminder（下一轮 create 合并标题） | `{title}` | 一轮追问/消费即清 |
| `remindable_active`| 产"未来事件"的域 opt-in（现 info sports；trip/charging 即插）| reminder（缺时间路径推导） | `{source,label,ts,items:[{title,fire_at}]}`（items 序=卡片渲染序） | 会话/被覆盖 |

注：底层 profile KV 无独立 TTL（随画像存储；被同 key 下次写覆盖）。新增跨 Agent 状态键**先在此
登记 + 更新 conventions.md**，再在 owner/reader 用常量引用，不要在业务码写裸字符串。
"""
from __future__ import annotations

# info（news 域）写当前新闻列表 → deep-research「详细讲讲第N条」桥接读
NEWS_ACTIVE = "news_active"
# deep-research 写当前活动调研 → 自身多轮「展开第N点」聚焦读
RESEARCH_ACTIVE = "research_active"
# trip-planner 写当前活动行程 → 自身「改某天」有状态读
TRIP_ACTIVE = "trip_active"
# reminder 写当前提醒列表（list/create/complete/cancel 后刷新）→ 自身「第N条」序号解析读
REMINDERS_ACTIVE = "reminders_active"
# reminder create 缺时刻追问时写 {title} → 下一轮 create 合并标题；消费即清
REMINDER_PENDING = "reminder_pending"
# 跨域提醒 P1c：产"未来将发生之事"的域按标准 schema opt-in 写入（写入顺序=卡片渲染顺序），
# reminder 缺时间路径统一消费（「第一场提醒我观看」→ 开赛时刻-提前量）。
# 见 docs/design/2026-07-11-reminder-cross-domain.md。
REMINDABLE_ACTIVE = "remindable_active"

__all__ = ["NEWS_ACTIVE", "RESEARCH_ACTIVE", "TRIP_ACTIVE",
           "REMINDERS_ACTIVE", "REMINDER_PENDING", "REMINDABLE_ACTIVE"]
