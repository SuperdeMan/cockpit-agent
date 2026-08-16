"""共享时刻/时间窗解析单测（E1）：时刻消歧、事件时刻、用餐窗反推、**业务时区**。

⚠ 断言一律用 `runtime.clock`（业务时区 UTC+8）构造期望值，**不用 `time.mktime` /
`time.localtime`**——后者按宿主本地时解释，而宿主恰好就是 UTC+8，于是
「容器 TZ=UTC 导致整体偏 8 小时」这类缺陷在本机**永远不红**（真栈实测
「预计 05:17 到达，比您要求的 17:00 早约 703 分钟」，两个 provider 逐字一致）。
另配一条**源码级守卫**：时钟族里不许再出现裸 `time.localtime` / `time.mktime`。
"""
import re
from pathlib import Path

from runtime.clock import BUSINESS_TZ, epoch_at, hhmm, local_dt
from agents._sdk.timewindow import (
    DINING_BUFFER_MIN, DINING_DWELL_MIN, clock_minutes, dining_window,
    fmt_clock, parse_clock_time, parse_event_time)

# 2026-08-14 周五 14:00（业务时区），与 navigation 既有用例同一基准
_NOW = epoch_at(2026, 8, 14, 14, 0)


def _wall(ts):
    d = local_dt(ts)
    return (d.year, d.month, d.day, d.hour, d.minute)


def test_clock_time_disambiguates_bare_hours():
    assert _wall(parse_clock_time("5点", now_ts=_NOW)) == (2026, 8, 14, 17, 0)
    late = epoch_at(2026, 8, 14, 20, 0)
    assert _wall(parse_clock_time("5点", now_ts=late)) == (2026, 8, 15, 5, 0)
    assert _wall(parse_clock_time("下午5点半", now_ts=_NOW)) == (2026, 8, 14, 17, 30)
    assert _wall(parse_clock_time("17:00", now_ts=_NOW)) == (2026, 8, 14, 17, 0)
    assert _wall(parse_clock_time("23点", now_ts=_NOW)) == (2026, 8, 14, 23, 0)
    assert parse_clock_time("尽快", now_ts=_NOW) is None


def test_clock_is_anchored_to_business_timezone_not_host():
    """核心回归：解析与渲染都按 UTC+8，**与宿主/容器 TZ 无关**。

    容器是 UTC：旧实现把「晚上7点」算成 19:00 UTC（=次日 03:00 北京），
    把 ETA 播成 05:17。这条断言用固定 epoch 钉死北京墙钟，UTC 主机上也成立。
    """
    ts = parse_clock_time("晚上7点", now_ts=_NOW)
    assert local_dt(ts).utcoffset() == BUSINESS_TZ.utcoffset(None)
    assert _wall(ts) == (2026, 8, 14, 19, 0)
    assert fmt_clock(ts) == "19:00" == hhmm(ts)
    assert clock_minutes(ts) == 19 * 60


# 会按「几点/哪天」做判断或渲染的生产模块。新增同类消费点时加进来——
# 这张表就是「哪些地方的墙钟是业务语义」的清单。
_WALL_CLOCK_MODULES = (
    "agents/_sdk/timewindow.py",
    "agents/navigation/src/agent.py",
    "agents/nearby/src/agent.py",
    "agents/nearby/src/providers/base.py",
    "orchestrator/cloud/context.py",
    "orchestrator/cloud/planning.py",
    "agents/road_safety/src/agent.py",
    "agents/trip_planner/src/pipeline.py",
    "agents/info/src/providers/stock_tushare.py",
    "proactive/governor.py",
    "memory/routine.py",
    "memory/server.py",
    "memory/extract.py",
    "agents/scene_orchestrator/src/triggers.py",
    # Q10（2026-08-16）：查单话术要说「这是您 8 月 15 日下的那笔」——**日期说错
    # 一天，用户就核对不上**，而这一族在容器里正好偏 8 小时、跨日边界必错。
    "agents/mcp_bridge/src/agent.py",
)
# ⚠ **`merchant/mcdonalds.py` 刻意不在上表**（2026-08-16 加过一次又撤回，留痕）：
# 它的 `_shanghai_timezone()` 是**麦当劳中国的营业时区**，不是车机业务墙钟——
# PoC 里同值 UTC+8，语义不同（多时区时业务墙钟跟车走、商户营业时区不跟）。
# 把它收敛成 `BUSINESS_TZ` 会让 `str(tzinfo)` 从 "Asia/Shanghai" 变 "UTC+08:00"，
# `test_default_clock_is_explicit_shanghai_time…` 当场红——**那条既有断言守的正是
# 这个区分，它按住了这次误收敛**。
# > 判据：「看起来是第二份定义」和「真的是同一件事」是两回事。判同不同要问**语义**。


def _code_only(path: Path) -> str:
    """把注释与字符串**按原位置抹成空格**，其余字符原样保留。

    两处都是反向验证抓出来的（「注入缺陷会红」和「对照仍绿」两头都要做）：
    1. 首版根本没剥注释 → **误伤了记录这条规则的注释本身**（`scene/triggers.py`
       里那句「直接 time.localtime() 会让…错 8 小时」）；
    2. 改成 tokenize 后用**空格拼接** token → `time.localtime(` 变成
       `time . localtime (`，正则再也匹配不上——**注入缺陷时守卫纹丝不动**。
       一个恒绿的断言比没有断言更糟，它让人以为这块被守着。
    """
    import io
    import tokenize
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    starts = []
    off = 0
    for ln in lines:                       # 行号(1-based) → 该行在全文的起始偏移
        starts.append(off)
        off += len(ln)
    buf = list(text)
    with open(path, "rb") as f:
        for tok in tokenize.tokenize(io.BytesIO(f.read()).readline):
            if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                continue
            (r1, c1), (r2, c2) = tok.start, tok.end
            a = starts[r1 - 1] + c1
            b = starts[r2 - 1] + c2
            for i in range(a, min(b, len(buf))):
                if buf[i] != "\n":
                    buf[i] = " "
    return "".join(buf)


def test_wall_clock_modules_have_no_naked_local_time():
    """源码级守卫（本机就能红，不用等 UTC 环境）。

    判据同 B6「反例最好从被测系统自己派生」：这一族出过**四次**事
    （scene / memory 两处写对并留了注释，navigation+timewindow、road_safety、
    proactive 三处仍写错），每次都是「某处又写了一个裸 localtime」。
    把「哪些地方的墙钟是业务语义」变成一张表 + 一条断言，别靠下次记得。
    """
    root = Path(__file__).resolve().parents[3]
    naked = re.compile(r"\btime\.(?:localtime|mktime)\s*\(|\bdatetime\.now\(\s*\)")
    offenders = []
    for rel in _WALL_CLOCK_MODULES:
        if naked.search(_code_only(root / rel)):
            offenders.append(rel)
    assert not offenders, (
        f"这些文件里还有裸 time.localtime/mktime/datetime.now()：{offenders}。"
        "容器 TZ=UTC，墙钟一律走 runtime.clock（业务时区 UTC+8）。")


def test_business_timezone_has_exactly_one_definition():
    """「不加第二份表达同一件事的声明」的机械版。

    这条缺陷的成因就是 UTC+8 被各写各的：7 处生产代码各有一份、**第 8 处写成了裸
    localtime**。定义收敛到 `runtime/clock.py` 之后，用断言把它钉住。
    （测试与 scripts 不在管辖内——它们跑在 UTC+8 宿主上，且各自显式 pin。）
    """
    root = Path(__file__).resolve().parents[3]
    # ⚠ **正则收紧过一次，留痕**（2026-08-16，Q10 批）：首版尾部写死 `\)\s*\)`，
    # 要求 `timedelta(hours=8)` 后**紧跟**收尾括号。于是 mcdonalds.py 里的
    # `timezone(timedelta(hours=8), "Asia/Shanghai")` **多一个参数就绕过去了**，
    # 一份完整的第二定义在守卫眼皮底下活着。现在只认到 `timedelta(hours=8)` 为止，
    # 后面跟什么都算。
    # > 这条守卫本身就是「恒绿的断言比没有更糟」的标本——它当时**扫不到任何真实
    # > 违规**，读起来却像这块被守着。写完扫描类断言要问的不只是「它会不会红」，
    # > 还有「它够不够得着现实里那些写法」。
    literal = re.compile(r"timezone\s*\(\s*timedelta\s*\(\s*hours\s*=\s*8\s*\)")
    offenders = [rel for rel in _WALL_CLOCK_MODULES
                 if literal.search(_code_only(root / rel))]
    assert not offenders, (
        f"这些文件重新定义了业务时区：{offenders}。"
        "唯一定义在 runtime/clock.py::BUSINESS_TZ，import 它。")


def test_event_time_needs_both_a_clock_and_an_event_word():
    for raw, word in (("晚上7点的电影，先找个地方吃饭", "电影"),
                      ("7点半那场话剧", "话剧"),
                      ("电影是晚上七点的", "电影"),
                      ("下午3点的高铁，先吃点东西", "高铁")):
        got = parse_event_time(raw, now_ts=_NOW)
        assert got is not None and got[1] == word, raw


def test_arrive_by_phrasing_is_not_read_as_an_event():
    """「5点前到」是到达时限（navigation 的 arrive_by），不是事件时刻——
    两条链不许抢同一句，否则同一个数字会被两处各解释一遍。"""
    for raw in ("五点前到学校", "17:00 我必须到公司", "帮我6点前赶到"):
        assert parse_event_time(raw, now_ts=_NOW) is None, raw


def test_event_word_too_far_from_the_clock_is_not_paired():
    """时刻与事件词隔了大半句 → 不成对（避免把无关的两个词凑成约束）。"""
    raw = "7点提醒我出门买菜顺便看看有没有便宜的西红柿然后晚上再说电影的事"
    assert parse_event_time(raw, now_ts=_NOW) is None


def test_dining_window_is_derived_backwards_from_the_event():
    ev = parse_event_time("晚上7点的电影，先吃个饭", now_ts=_NOW)
    w = dining_window(ev[0], now_ts=_NOW)
    assert fmt_clock(w["event_ts"]) == "19:00"
    assert fmt_clock(w["leave_ts"]) == "18:30"        # 事件 − 路上预留
    assert fmt_clock(w["seat_ts"]) == "17:30"         # 离席 − 用餐时长
    assert w["dwell_min"] == DINING_DWELL_MIN and w["buffer_min"] == DINING_BUFFER_MIN
    assert w["tight"] is False


def test_dining_window_flags_tight_instead_of_squeezing():
    """来不及就是来不及——不压缩窗口凑出一个能满足的数（编数据比说不行更糟）。"""
    late = epoch_at(2026, 8, 14, 18, 0)
    ev = parse_event_time("晚上7点的电影，先吃个饭", now_ts=late)
    assert dining_window(ev[0], now_ts=late)["tight"] is True


def test_window_params_are_injectable():
    w = dining_window(_NOW + 7200, dwell_min=90, buffer_min=45, now_ts=_NOW)
    assert (w["event_ts"] - w["leave_ts"]) == 45 * 60
    assert (w["leave_ts"] - w["seat_ts"]) == 90 * 60


def test_clock_minutes_matches_business_wall_clock():
    ts = epoch_at(2026, 8, 14, 17, 30)
    assert clock_minutes(ts) == 17 * 60 + 30
    assert fmt_clock(ts) == "17:30"
