"""槽值保真：**槽值比原话少了什么**（QA 卡 Q12）。

## 它要挡的是什么

planner 产出的槽值是**自由文本的转述**，转述会丢限定词，而下游拿到的是一个
**看起来完全合法**的值——没有任何东西能发现它比原话少了信息：

| 原话 | 槽值（真栈实测两种） | 下游看到的 |
|---|---|---|
| 明天下午四点提醒我开会，**三点半**再提醒我一次 | `time_text="三点半"` | 一个合法时刻 → 18:20 说这句就落到**次日 03:30** |
| 同上 | `time_text="明天三点半"`（**日留下了、段位丢了**） | 同样落到次日 03:30，而话术说「下午 15:30」 |

这是既有判据「**planner 改写 query 是不可信指代通道，指代解析只信 raw**」（sports 批）
的又一次复发。推论也一样：**槽值校验不能只校「有没有」，要校「比原话少了什么」。**

## 判据：只回填「原话里明摆着、而槽值自己丢了」的那部分

三道闸，任一不成立就**原样返回**（诚实不动，绝不猜）：

1. **形态闸**——槽值整值必须是 `[日词][段位词]时刻` 的形态，**且至少缺一维**。
   ⚠ **首版要求「整值是裸时刻」，被真栈当场证否**：模型产的是 `明天三点半`，
   两维缺一维，首版原样放行、落成次日 03:30——**把要修的症状在修完之后又复现了一次**。
   判据因此是**逐维**的：缺哪一维补哪一维（CLAUDE.md §6「防御要防到真正会被拿去用的
   那个值」的时间版——我防的是整值形状，该防的是那一维在不在）。
2. **定位闸**——值里那个**时刻**必须在原话里**恰好出现一次**。出现零次说明 planner
   改写过（`三点半`→`15:30`），够不着就不回填；出现多次说明指代不唯一。
3. **来源闸**——限定词只能来自两处，优先级由近及远：
   - **紧邻**：值的出现位置前面直接贴着日词/段位词（planner 把它剪掉了）；
   - **继承**：整句里更靠前的一个**带限定词的时间短语**（`明天下午四点`）。
     省略继承是中文的正常语法——后一个时刻默认沿用前一个已确立的日与时段。

⚠ **「早上跑完步，提醒我三点半吃药」不回填**：`早上` 后面没有时刻，它修饰的是
「跑完步」不是某个时间短语。判据因此写成「必须继承自一个**带时刻的**短语」，
而不是「原话里有没有出现过段位词」——后者会把状语当限定词，
同 E2 那条「**话里提到了人**不等于**这条记忆是关于那个人的**」。

⚠ **认不出的限定词一律让路**：值前面贴着 `周三` / `15号` 这类本模块不解析的日期词时
直接返回，不做继承——**宁可不回填，也不要回填错**。回填错等于系统替用户改了他说的话。

## 只有「时间」这一维

卡 §3-Q12 列了四类关键槽（时间/地点/角色/规格），本模块只实现时间。另外三类
2026-08-16 逐条取证后**不属于本机制**，各自归位（证据写在卡的实施记录里）：

- **地点/城市**（I-012）：归目的地接地卡，且已在 person-pickup 卡上；
- **角色**（I-029「从 X 出发」）：**能力缺席**（Q8），不是回查层的事——planner 无处
  可放才就近把出发地塞进 destination，回填一个不存在的槽没有意义。
  ⚠ 这条的事实陈述 2026-08-28 更新过：`origin` 槽 2026-08-20 已加在
  `navigate_to`/`estimate` 上、同日 C8 补到 `reroute`（QA P1-08）。**修法归位没变**
  ——出发地修在能力契约层，本模块维持只管时间维的裁定；这里改的只是那句
  「manifest 根本没有 origin 槽」，它从 2026-08-20 起就不再成立（重判 3）；
- **规格**（I-025②「少冰」）：2026-08-21 真栈取证后**整条重判**。planner 其实
  **没有丢词**（3/3 都把「少冰/去冰/燕麦奶」填进了正确的槽），所以这一维根本不是
  「回查原话」能修的——它丢在两个别的地方：① 契约里**没有那个槽**（planner 产
  `size: 大杯`，而 `luckin.order` 当时没有 `size` 槽）；② 槽有、但**值域声明是猜的**
  （`ice` 查的「冰量」组在瑞幸根本不存在，冰档位是「温度」组的取值）。
  修法因此落在 B6 §4 的 `input_schema`（`servers.yaml` 唯一声明 + 真机台账门禁），
  不是本模块的第二维。本模块在这一维上只留一条通用闸：`undeclared_slots`（见下）。

**先落一个没有真消费方的通用回填框架，就是 B4 那条「不加第二份表达同一件事的声明」
的孪生形态。** 所以本模块只长它现在真的用得上的那一维。

## 另一半：`undeclared_slots`——**契约**比原话少了什么

回填管的是「槽值比原话少了什么」。2026-08-21 真栈翻出它的孪生形态：**槽值一个字
没丢，丢的是契约**——planner 产出 `size: 大杯`，而那个能力当时根本没有 `size` 槽，
于是这个值一路走到下发、被下游当未知键忽略，**全程没有任何一处会报错**。
用户说了大杯，系统下了标准杯，零信号。

判据只用能力自己的声明（`declared_slots`，来自 manifest / servers.yaml），
**零领域词**。`declared` 为空时一律不判——那说明这个能力没声明槽位，我们无从判断
「多」还是「少」，不能拿沉默当证据。
"""
from __future__ import annotations

import re

from runtime.cntime import CLOCK_SRC, DAY_ALT, SEG_ALT

#: 槽值本身的形状：`[日词][段位词]时刻`，三段都可缺，但必须整值匹配。
#: ⚠ **不能只认「裸时刻」**（2026-08-16 真栈当场证否）：planner 实测产出的是
#: `明天三点半`——**日留下了、段位丢了**，于是「只认裸时刻」的首版放它过去，
#: 落成次日 03:30，把 I-008 原样复现了一次。判据因此改成**逐维**：
#: 值缺哪一维就补哪一维。
_VALUE_RE = re.compile(rf"^\s*({DAY_ALT})?\s*({SEG_ALT})?\s*({CLOCK_SRC})\s*$")
#: 带限定词的时间短语（日词/段位词至少有一个 + 时刻，且三者相邻）。
_PHRASE_RE = re.compile(rf"(?:({DAY_ALT})\s*)?(?:({SEG_ALT})\s*)?{CLOCK_SRC}")
#: 值的出现位置**紧邻**的限定词（贴在它前面的那一段）。
_HEAD_RE = re.compile(rf"(?:({DAY_ALT})\s*)?(?:({SEG_ALT})\s*)?$")
#: 本模块**不解析**的日期限定词。贴在值前面时一律让路，不做继承。
_FOREIGN_QUALIFIER_RE = re.compile(
    r"(?:(?:下*)(?:个)?(?:周|星期|礼拜)[一二三四五六日天]|\d+\s*[号日])\s*$")


def restore_time_qualifiers(raw: str, value: str) -> tuple[str, str]:
    """时刻槽值 → 逐维补回原话里管着它、而槽值自己丢了的日词/段位词。

    返回 `(值, 理由)`；没有改动时理由为空串。理由是给观测/日志读的
    （`time_qualifier:+下午`），**不进话术**。
    """
    text = raw or ""
    m = _VALUE_RE.match(value or "")
    if not m:
        return value, ""
    day_v, seg_v, clock = m.group(1), m.group(2), m.group(3)
    if day_v and seg_v:
        return value, ""                       # 两维都在，没丢东西
    if text.count(clock) != 1:
        return value, ""                       # 定位不到 / 不唯一：诚实不动
    prefix = text[:text.index(clock)]
    if _FOREIGN_QUALIFIER_RE.search(prefix):
        return value, ""                       # 贴着一个本模块不解析的日期词 → 让路
    head = _HEAD_RE.search(prefix)
    day_r, seg_r = (head.group(1), head.group(2)) if head else (None, None)
    if not day_r and not seg_r:
        # 继承：整句里更靠前的那个「带限定词的时间短语」（同一句里的省略）
        phrases = [p for p in _PHRASE_RE.finditer(prefix)
                   if p.group(1) or p.group(2)]
        if not phrases:
            return value, ""
        day_r, seg_r = phrases[-1].group(1), phrases[-1].group(2)
    day, seg = day_v or day_r, seg_v or seg_r
    added = [w for w, had in ((day, day_v), (seg, seg_v)) if w and not had]
    if not added:
        return value, ""
    return f"{day or ''}{seg or ''}{clock}", "time_qualifier:+" + "".join(added)


def restore_dropped_qualifiers(raw: str, slots: dict,
                               skip=()) -> dict[str, tuple[str, str]]:
    """对一组槽位做原话回查，返回**发生改动的**槽位 → (新值, 理由)。

    `skip`：不许动的槽名（服务端权威来源解析出来的值——那些不是 planner 的转述，
    回填它们等于拿用户原话覆盖 provenance）。
    """
    changed: dict[str, tuple[str, str]] = {}
    for name, value in (slots or {}).items():
        if name in skip or not isinstance(value, str):
            continue
        new_value, reason = restore_time_qualifiers(raw, value)
        if reason:
            changed[name] = (new_value, reason)
    return changed


def undeclared_slots(slots: dict, declared) -> list[str]:
    """→ 有值、却**不在能力契约里**的槽名（排序）。契约为空时一律返回空。

    调用方**只观测不改值**：删掉它不会让用户拿回那个规格（能力确实没有这一维），
    硬塞给下游反而是拿模型编的键去撞商户接口。它回答的是另一个问题——
    **「模型往契约外面塞过东西吗」**，那是补能力的线索，不是本轮能修的错。
    """
    names = {str(name) for name in (declared or []) if str(name).strip()}
    if not names:
        return []
    return sorted(name for name, value in (slots or {}).items()
                  if name not in names and str(value or "").strip())
