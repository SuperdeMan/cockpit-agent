"""跨 Agent 会话状态键的**权威登记**（与 `docs/conventions.md`「跨 Agent 状态键」同步）。

Agent 无状态化：一次会话的临时状态落 profile KV（经 `Context.save_shared_state` /
`load_shared_state`），供跨轮或跨 Agent 复用。key 常量集中于此，杜绝字面量散落——改 key /
换存储时改一处即可，不再静默断链（审计 A5）。

| key              | owner（写）        | reader（读）              | schema（value）                        | TTL |
|------------------|--------------------|---------------------------|----------------------------------------|-----|
| `news_active`    | info（news 域）    | deep-research（深挖第N条）  | `{items:[{title,source}]}`             | 会话/被覆盖 |
| `research_active`| deep-research      | deep-research（多轮聚焦）   | `{question,summary,sections[],freshness}` | 会话/被覆盖 |
| `trip_active`    | trip-planner       | trip-planner（有状态改天）  | `Trip.to_dict()`                        | 会话/被覆盖 |

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

__all__ = ["NEWS_ACTIVE", "RESEARCH_ACTIVE", "TRIP_ACTIVE"]
