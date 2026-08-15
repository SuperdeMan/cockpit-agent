"""业务时区墙钟——**全仓唯一实现**（车机 PoC 固定 UTC+8）。

为什么需要它：**容器不设 TZ（=UTC），而宿主机是 UTC+8**。于是裸
`time.localtime()` / `datetime.now()` 在容器里把所有「几点」判定整体偏 8 小时，
而**在宿主上跑单测永远不红**——`memory/server.py` 在 G7 真栈补验时就写下过这句
（「9月15日」的 offer 显示成「9月14日16:00」，还会经按钮传导成真错提醒），
可这一族随后仍在三处独立复发：

- `navigation._fmt_clock` / `_sdk/timewindow`（G1 时限与用餐窗）：真栈实测
  「预计 **05:17** 到达，比您要求的 17:00 早约 **703 分钟**」——两个 provider 逐字一致，
  正是「与模型无关的系统缺陷」的签名；
- `road_safety._is_night`（夜间节流窗口按 22:00–06:00 判）；
- `proactive.Governor`（**免打扰时段**——偏 8 小时等于在错误的时间打扰用户）。

判据沉淀：**同一件事有三份各自正确的实现，就迟早会有第四份是错的。**
所以本模块是唯一入口，既有的三处正确实现（scene / memory / nearby）一并迁来。

用法：需要「北京几点」就用这里的函数，不要再碰 `time.localtime`。
epoch 秒本身与时区无关——只有**转成墙钟**和**从墙钟构造 epoch** 这两步需要时区。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

# 车机业务时区。PoC 固定 UTC+8（与 reminder 的 Asia/Shanghai 默认一致）；
# 真要做多时区是产品决策，不是把这里改成 localtime。
BUSINESS_TZ = timezone(timedelta(hours=8))


def local_dt(ts: float | None = None) -> datetime:
    """epoch → 业务时区的 aware datetime（缺省=现在）。"""
    return datetime.fromtimestamp(time.time() if ts is None else float(ts), BUSINESS_TZ)


def local_struct(ts: float | None = None) -> time.struct_time:
    """epoch → 业务时区的 struct_time（给还在按 `tm_hour` 取值的老代码）。"""
    return local_dt(ts).timetuple()


def hour_of(ts: float | None = None) -> int:
    return local_dt(ts).hour


def minutes_of(ts: float | None = None) -> int:
    """当天的「时:分」折算分钟（营业时段这类判定要的就是它）。"""
    d = local_dt(ts)
    return d.hour * 60 + d.minute


def hhmm(ts: float | None = None) -> str:
    d = local_dt(ts)
    return f"{d.hour:02d}:{d.minute:02d}"


def epoch_at(year: int, month: int, day: int, hour: int = 0, minute: int = 0,
             second: int = 0) -> int:
    """业务时区的墙钟 → epoch 秒（替代 `time.mktime`，后者按容器本地时区解释）。

    day 允许越界（`day+1` 落到次月），与 `time.mktime` 的宽松语义一致——
    调用方（如「过点滚到明天同刻」）依赖这个行为。
    """
    base = datetime(year, month, 1, hour, minute, second, tzinfo=BUSINESS_TZ)
    return int((base + timedelta(days=day - 1)).timestamp())
