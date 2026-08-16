"""端侧「结构化意图 → 意图名」只能有一个出口（QA 卡 Q13）。

**背景**：端侧有两处把结构化意图翻成意图名——单句路径 `classify()` 与分段路径
`_to_legacy_name()`——它们不一致。阶段 0.2 探针实测 **15/38 处不同，其中 7 处
`is_local` 判定相反**：同一句话在单句形态与复合句形态下走**不同的路**。
后果三类（卡 §3-Q13）：

1. 媒体域的端侧快路径在**最常见的单句形态下根本没生效**（`classify("暂停音乐")`
   产 `music.pause`，不在 `LOCAL_INTENTS` → 整句上云）。架构文档写的是「端侧快系统
   处理高频/确定/安全敏感指令（车控、**媒体**）」——媒体这半边一直在绕云。
2. 「下一首」方向相反：单句本地、复合句上云。
3. `_to_legacy_name` 对双闪/前后除雾返回 `None`，复合句里必然整组上云。

**这条判据代码里已经写过一次，当时只修了一处**（`fast_intent.py` aircon 分支，
2026-08-04）：「发现根因时要问『同一形态还有几处』——『改遍了』和『发现了』是两件事」。
那次没问，今天的答案是 15 处。那条注释是它自己的反例。

## 这里为什么是三条断言，以及哪条会长期有效

- `test_only_one_intent_naming_implementation`：**源码级，长期有效**。收敛之后
  「两个出口一致」这种断言会变成**恒绿**（只剩一个实现，比什么都过）——正是 §4.3
  「恒绿的断言比没有更糟」。真正防再分叉的是这一条。
- `test_intent_name_golden`：**行为锁**。它钉的不是「两个出口相等」，是**这 38 条
  文本各自应该叫什么名字、能不能本地执行**。改任何一个名字都会红。
- `test_touched_objects_have_no_dead_alias`：收敛让 `LOCAL_INTENTS` 里一批
  「两个名字都收着」的条目变成死条目。**只删本批证明得了的那些**——
  「清理死条目之前先证明它真的死了」（2026-08-04 那条老账）。
"""
from __future__ import annotations

import inspect
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from fast_intent import LOCAL_INTENTS, classify, classify_structured, is_local
import fast_intent


# ── 金标：文本 → (意图名, 能否端侧执行) ──────────────────────────────────
# 权威来源：`commands.yaml` 各对象的 `edge_intents`（B4「端侧意图名单的唯一声明处」）。
#   · on/off 还是 open/close —— 按 edge_intents 声明（ambient_light/headlight/seat/wiper
#     声明的都是 `.on/.off`，所以 `classify()` 原来产的 `.open` 是错的那个）；
#   · 媒体子类统一落 `media.*` —— `LOCAL_INTENTS` 只登记 media.*，这也是架构文档的承诺；
#   · 除雾按 `front_defogger.open/close` —— edge_intents 声明的就是 open/close。
_GOLDEN = [
    # 媒体：收敛前单句形态全部上云（`music.*` 不在 LOCAL_INTENTS）
    ("音乐别停", "media.pause", True),      # ⚠ 极性错误是 Q7 的事，这里只锁名字与路由
    ("停止音乐", "media.pause", True),
    ("暂停音乐", "media.pause", True),
    ("音乐停", "media.pause", True),
    ("播放音乐", "media.play", True),
    # 方向必须保住：结构化意图里 `下一首`/`上一首` 逐字相同（都是 operate=switch），
    # 方向只在原话里。收敛后由唯一实现从 `_raw_text` 取回——不是靠某个调用方记得传形参。
    ("下一首", "media.next", True),
    ("上一首", "media.prev", True),
    # 空调族（收敛前两个出口就一致，作对照——证明「没修过头」）
    ("打开空调", "hvac.on", True),
    ("关闭空调", "hvac.off", True),
    ("空调调到26度", "hvac.set", True),
    ("空调温度高一点", "hvac.inc", True),
    ("风速大一点", "aircon.wind_speed.inc", True),
    # 开合类对照
    ("打开车窗", "window.open", True),
    ("关闭车窗", "window.close", True),
    ("打开天窗", "sunroof.open", True),
    ("关闭天窗", "sunroof.close", True),
    ("打开遮阳帘", "sunshade.open", True),
    ("关闭遮阳帘", "sunshade.close", True),
    ("把后视镜折叠", "rear_view_mirror.fold", True),
    ("把后视镜展开", "rear_view_mirror.unfold", True),
    ("打开后备箱", "trunk.open", True),
    ("锁车门", "door_lock.close", True),
    ("解锁车门", "door_lock.open", True),
    ("打开充电口", "charging_port.open", True),
    ("打开方向盘加热", "steering_wheel.heating.open", True),
    ("音量大一点", "volume.inc", True),
    ("音量小一点", "volume.dec", True),
    # on/off 族：edge_intents 声明的是 on/off，收敛前 `classify()` 产的 `.open` 是错的
    ("打开氛围灯", "ambient_light.on", True),
    ("氛围灯调成红色", "ambient_light.set", True),
    ("打开大灯", "headlight.on", True),
    ("座椅加热打开", "seat.heating.on", True),
    ("座椅通风打开", "seat.ventilation.on", True),
    ("打开雨刷", "wiper.on", True),
    # 除雾：收敛前 `_to_legacy_name` 缺这两个对象 → 复合句里整组上云
    ("打开前除雾", "front_defogger.open", True),
    ("打开后除雾", "rear_defogger.open", True),
    # 行车记录仪：收敛**当场照出**的一处——`_to_legacy_name` 原来产 `dashcam.on`，
    # 而 edge_intents 与 LOCAL_INTENTS 都是 `open/close` ⇒ 分段路径从来路由不了它，
    # 单句路径靠 `classify()` 的兜底恰好拼对、把它盖住了。
    # ⚠ 抓到这处的是 **L0 语料 `ei.local.dashcam`**（它测 ingress 路由），
    # 本文件当时全绿——**名字对不对与走哪条路，是两层断言**。补进金标。
    ("打开行车记录仪", "dashcam.open", True),
    ("关闭行车记录仪", "dashcam.close", True),
    # 双闪：`commands.yaml` 没给它 edge_intents ⇒ **本来就不是端侧能力**。
    # 收敛只要求两个出口给同一个答案，不要求把它变成本地——补 `hazard_light`
    # 能力面是 Q8（阶段 4），在那之前它诚实地上云。
    ("打开双闪", "warning_light.open", False),
    # 静音：两个出口都认不出。**这是能力缺席（Q8）不是命名分歧**，
    # 锁在这里是为了让 Q8 补 `media.mute` 时这一行必须跟着改。
    ("静音", None, False),
    ("取消静音", None, False),
]


@pytest.mark.parametrize("text,want_name,want_local", _GOLDEN,
                         ids=[g[0] for g in _GOLDEN])
def test_intent_name_golden(text, want_name, want_local):
    got = (classify(text) or {}).get("name")
    assert got == want_name, f"{text!r} → {got!r}，金标 {want_name!r}"
    assert is_local(got or "") is want_local, (
        f"{text!r} → {got!r} 的 is_local={is_local(got or '')}，金标 {want_local}")


@pytest.mark.parametrize("text,want_name,_local", _GOLDEN,
                         ids=[g[0] for g in _GOLDEN])
def test_segment_path_agrees_with_single_sentence_path(text, want_name, _local):
    """分段路径（复合句）必须给出同一个名字。

    收敛之后这条与上一条走的是同一个实现，因此**结构上必然通过**——它留在这里的
    作用是「再分叉」的探针（配合下面那条源码级断言），不是独立证据。
    这话写在这里，免得下一个人把它当成两条独立的保护。
    """
    structured = classify_structured(text)
    got = fast_intent._to_legacy_name(structured) if structured else None
    assert got == want_name, f"{text!r} 分段路径 → {got!r}，金标 {want_name!r}"


# ── 源码级：只允许存在一处「对象 → 名字」的分支链 ──────────────────────────

_OBJ_BRANCH = re.compile(r"\bobj\s*(?:==|in)\s")


def test_only_one_intent_naming_implementation():
    """整个 `fast_intent` 里，只能有**一个**函数在做「对象 → 意图名」的分派。

    判据是「函数体里有多少处按 `obj` 分支」——≥5 处即认定它在维护一张对象表。
    收敛前 `classify()` 与 `_to_legacy_name()` 各有一张（60+ 与 40+ 处），
    于是同一个对象在两处各拼一个名字，而 `LOCAL_INTENTS` 把两个名字都收着，
    分歧就此隐形三个月。

    ⚠ 这条断言本身要能红：把 `classify()` 的分支链复制回来，它立刻红。
    """
    src = inspect.getsource(fast_intent)
    offenders = []
    for name, fn in vars(fast_intent).items():
        if not callable(fn) or not hasattr(fn, "__code__"):
            continue
        if fn.__code__.co_filename != fast_intent.__file__:
            continue
        try:
            body = inspect.getsource(fn)
        except OSError:            # pragma: no cover - 源码不可读时不误判
            continue
        hits = len(_OBJ_BRANCH.findall(body))
        if hits >= 5:
            offenders.append((name, hits))
    assert len(offenders) == 1, (
        f"「对象 → 意图名」的分派必须只有一处，实测 {len(offenders)} 处："
        f"{sorted(offenders)}。同一件事有两份实现，迟早有一份是错的。")
    assert src.count("def _to_legacy_name") == 1


# ── 收敛的连带：本批证明得了的死条目 ────────────────────────────────────

# 这四族的 `edge_intents` 声明的是 `.on/.off`，收敛后唯一实现只会产 `.on/.off`，
# `.open/.close` 因此**不可达**。`LOCAL_INTENTS` 只服务端侧路由（`is_local`），
# 云侧场景动作走 `action_to_structured` 对 `commands.yaml`，不看这张表——
# 所以删掉这些条目不会影响 `scene_orchestrator` 产的 `ambient_light.open`。
_DEAD_AFTER_CONVERGENCE = [
    "ambient_light.open", "ambient_light.close",
    "headlight.open", "headlight.close",
    "wiper.open", "wiper.close",
    "seat.heating.open", "seat.heating.close",
    "seat.ventilation.open", "seat.ventilation.close",
    "seat.massage.open", "seat.massage.close",
    "seat.lumbar_support.open", "seat.lumbar_support.close",
    "fragrance.open", "fragrance.close",
]


@pytest.mark.parametrize("dead", _DEAD_AFTER_CONVERGENCE)
def test_touched_objects_have_no_dead_alias(dead):
    """收敛后不可达的别名必须从 `LOCAL_INTENTS` 删掉。

    留着它们不是无害的：**「两个名字都收着」正是上一次分歧能藏三个月的原因**
    （2026-08-04 那条注释写得很清楚，只是当时没把它变成断言）。

    ⚠ 只删本批**证明得了**的这些。`bluetooth/wifi/equalizer/voice_assistant/
    hotspot/auto_hold/epb/surround_view/low_beam/call_log/dashboard` 也各有一对别名，
    但它们属于 §4.2 那 14 条端侧能力欠账（`commands.yaml` 里压根没有 `edge_intents`），
    定名字要连能力面一起定——那是 Q8/阶段 4，不在本卡范围。**不在这里顺手删。**
    """
    assert dead not in LOCAL_INTENTS
