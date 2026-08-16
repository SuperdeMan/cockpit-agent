"""中文时间词唯一声明（Q12 收敛）的行为锁 + 源码级守卫。

收敛之后「两份实现是否一致」这种断言会变成**恒绿**——只剩一份表，比什么都过
（§4.3「恒绿的断言比没有更糟」，Q13 那次记过同一件事）。所以这里放的是两类**长期
有效**的东西：

1. **源码级守卫**：段位词/日词表不许再出现第二份。防的是**再分叉**。
2. **行为锁**：收敛当场修正的两处（早晨/晚上12点）写成断言——它们此前两份实现
   给两个答案，是这次收敛的全部收益。
"""
from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

from runtime.cntime import (CLOCK_SRC, CN_NUM_SRC, DAY_WORDS, SEG_ALT,
                            SEG_DEFAULT_HOUR, SEGMENTS, cn_int, segment_of,
                            to_24h)

_ROOT = Path(__file__).resolve().parents[2]

#: 会碰「中文时段词」的模块。新增消费方要加进来——同 `_WALL_CLOCK_MODULES`
#: 那张表的形态：**把「哪些地方在解析中文时间」变成一张表 + 一条断言**。
_CN_TIME_MODULES = (
    "agents/reminder/src/timeparse.py",
    "agents/_sdk/timewindow.py",
    "runtime/slot_fidelity.py",
)


def _code_without_comments(path: Path) -> str:
    """只抹注释、**保留字符串字面量**——这一族的第二份声明就长在字符串里。

    （`test_timewindow.py` 那条守卫要抹掉字符串，因为它找的是 `time.localtime(`
    这种**代码**；这里找的是词表，抹了字符串就等于什么都不扫，
    那正是「恒绿的断言」。两条守卫扫的东西不同，剥法也必须不同。）
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    starts, off = [], 0
    for ln in lines:
        starts.append(off)
        off += len(ln)
    buf = list(text)
    for tok in tokenize.tokenize(io.BytesIO(text.encode("utf-8")).readline):
        if tok.type != tokenize.COMMENT:
            continue
        (r1, c1), (r2, c2) = tok.start, tok.end
        for i in range(starts[r1 - 1] + c1, min(starts[r2 - 1] + c2, len(buf))):
            if buf[i] != "\n":
                buf[i] = " "
    return "".join(buf)


def test_segment_words_have_exactly_one_declaration():
    """段位词表不许有第二份（判据从被测系统自己的表派生，不手抄）。

    阈值取「同一个文件里出现 ≥3 个不同段位词」：一两个是话术/注释里的自然提及，
    三个以上就是在重建那张表了。
    """
    words = [w for w, _ in SEGMENTS]
    offenders = {}
    for rel in _CN_TIME_MODULES:
        code = _code_without_comments(_ROOT / rel)
        hit = [w for w in words if w in code]
        if len(hit) >= 3:
            offenders[rel] = hit
    assert not offenders, (
        f"这些文件里又出现了一张段位词表：{offenders}。"
        "唯一声明在 runtime/cntime.py::SEGMENTS，import 它——"
        "两份表此前的差价是「早晨八点」在一边是 08:00、在另一边是 20:00。")


def test_consumers_hold_the_shared_tables_not_copies():
    """已知消费方必须**持有**共享表本身，不是各自抄一份等值的。

    ⚠ **日词表刻意不用上面那种扫描判据，留痕**：首版照抄段位词那条，当场被
    `timeparse._display()` 打红——它把「今天/明天/后天」当**话术**输出，
    词表词与展示词是同一批字。**词出现在文件里 ≠ 那里有第二张表**，
    presence 扫描分不开这两件事（同 §4.3「尺子写错必须改」）。
    所以日词这一维改用身份断言：能抓「已知消费方又自己写了一份」，
    抓不到「新模块自带一份」——后者仍由段位词那条扫描兜住，两条各管一半。
    """
    from agents._sdk import timewindow as tw
    from agents.info.src.handlers import weather as wx
    from agents.reminder.src import timeparse as tp
    assert tp._DAY_WORDS is DAY_WORDS
    assert tp._SEGS is SEGMENTS
    assert tp._SEG_DEFAULT_HOUR is SEG_DEFAULT_HOUR
    assert SEG_ALT in tw._CLOCK_RE.pattern      # timewindow 的段位前缀由共享表拼出
    assert not hasattr(wx, "_DAY_WORDS"), \
        "weather.py 又自带了一张日词表——那是 I-041「tomorrow 被当成 current」的根因"


def test_day_offset_covers_english_and_separates_unknown_from_today():
    """英文日词进同一张表；**「认不出」必须与「说的是今天」分得开**。

    I-041 的整条链就是这两件事被合成一个 `return 0`：
    `_requested_day_offset` 扫不到任何日词 → 返回 0 → 按今天实况作答，
    而用户说的是 tomorrow。
    """
    from runtime.cntime import day_offset_of
    assert day_offset_of("Shenzhen weather tomorrow, do not navigate.") == 1
    assert day_offset_of("Tomorrow's weather in Shenzhen") == 1      # 句首大写
    assert day_offset_of("the day after tomorrow") == 2              # 长词在前
    assert day_offset_of("tonight") == 0
    assert day_offset_of("明天下午会下雨吗") == 1
    assert day_offset_of("大后天呢") == 3
    assert day_offset_of("现在几度") is None                          # 认不出 ≠ 今天


def test_scan_guard_actually_catches_an_injected_second_table():
    """**注入验红**：守卫必须够得着现实里的写法，不能只在脑子里那个标准形态下红。

    §4.3 记过两次：一次是恒绿（tokenize 后空格拼接，注入缺陷纹丝不动），
    一次是够不着（正则尾部写死 `\\)\\s*\\)`，多一个参数就绕过去）。
    这里直接把「第二份表」的几种真实写法喂给判据本身。
    """
    words = [w for w, _ in SEGMENTS]

    def _hits(code: str) -> int:
        return len([w for w in words if w in code])

    assert _hits('_SEGS = [("凌晨", "dawn"), ("早上", "am"), ("下午", "pm")]') >= 3
    assert _hits('re.compile(r"(上午|早上|凌晨|中午|下午)")') >= 3          # 正则形态
    assert _hits('SEG = {"下午": 12, "晚上": 12,\n       "中午": 0}') >= 3   # 跨行 dict
    # 对照：单个词的自然提及不该被判成第二份表
    assert _hits('speech = f"下午好，{name}"') < 3


def test_cn_int():
    assert cn_int("三") == 3
    assert cn_int("十") == 10
    assert cn_int("十二") == 12
    assert cn_int("二十三") == 23
    assert cn_int("15") == 15
    assert cn_int("两") == 2
    assert cn_int("尽快") is None      # 认不出返回 None，**不猜**
    assert cn_int("") is None


def test_segment_of_scans_the_whole_sentence():
    assert segment_of("明天下午四点提醒我") == "pm"
    assert segment_of("早晨八点") == "am"          # 收敛前 timewindow 认不出这个词
    assert segment_of("夜里两点") == "eve"
    assert segment_of("提醒我开会") == ""


def test_to_24h_is_the_single_correction_rule():
    assert to_24h(3, "pm") == (15, 0)
    assert to_24h(5, "eve") == (17, 0)
    assert to_24h(12, "eve") == (0, 1)             # 晚上12点 = 次日零点
    assert to_24h(1, "noon") == (13, 0)
    assert to_24h(12, "noon") == (12, 0)
    assert to_24h(8, "am") == (8, 0)
    assert to_24h(6, "dawn") == (6, 0)
    assert to_24h(17, "pm") == (17, 0)             # 已是 24h 写法：不重复加
    assert to_24h(24, "") == (0, 1)


def test_two_clock_lexicons_accept_the_same_strings():
    """`CLOCK_SRC`（扫描用，非捕获）与 timeparse 的捕获版必须认同一批字符串。

    形状留了两份是**有意的**（捕获需求不同），但两份就有漂的可能——
    所以用样本把「接受集合相同」钉成断言，而不是靠「记得同步改」。
    """
    from agents.reminder.src import timeparse as tp
    scan = re.compile(rf"^{CLOCK_SRC}$")
    yes = ["三点", "三点半", "3点", "15:30", "17：00", "十一点", "八点一刻",
           "四点三十分", "十二点三刻", "9点5分"]
    no = ["尽快", "明天", "下午", "点", "25:00"]
    for s in yes:
        assert scan.match(s), f"CLOCK_SRC 不认 {s}"
        assert tp._HHMM_RE.fullmatch(s) or tp._CLOCK_RE.fullmatch(s), \
            f"timeparse 的时刻词法不认 {s}"
    for s in no:
        assert not scan.match(s), f"CLOCK_SRC 不该认 {s}"
        assert not (tp._HHMM_RE.fullmatch(s) or tp._CLOCK_RE.fullmatch(s)), \
            f"timeparse 的时刻词法不该认 {s}"


def test_numeral_lexicon_is_shared():
    """中文数字字符集只有一份——两个模块的正则都从 `CN_NUM_SRC` 派生。"""
    from agents.reminder.src import timeparse as tp
    assert CN_NUM_SRC in tp._NUM
