"""除雾/除霜能力面的回归钉子（2026-08-10 补齐）。

出处：M5 P3 收尾（`docs/design/2026-07-28-intent-accuracy-data-flywheel.md` §P3 收尾 ①）
留下的那张卡——「`VEHICLE_INTENTS` 里根本没有除雾意图，两臂都只能在错误答案之间抖」。

补之前实测到的是**两种错法、一个根因**（根因＝除雾在系统里不是可寻址的能力）：

  ① 裸「打开前除雾 / 关闭强力前除雾 / 开除雾 / 除霜」→ 规则返回 None 整句上云，
     而云侧能力面也没有除雾意图 → planner 只能在别的工具里挑。P3a 影子的实录是
     `关闭强力前除雾` → `accompany_home.close`，并被 VAL 照单执行。
  ② 「空调开除雾 / 把空调调到除雾」→ 走进空调分支，`classify()` 把无 value 的
     `aircon.set` 一律映射成 `hvac.on`，**mode 在映射里被丢掉** ⇒ 端侧静默执行成
     「只开空调」。②比①更糟：①至少还上云求助，②是端侧替用户按错了按钮还回「开了」。

所以本文件的断言分三层，缺一层都守不住：
  - **识别层**：规则认得出，且前/后挡分得开；
  - **能力层**：能力面里真有这条 intent、描述判别（否则修了端侧云侧还是瞎猜）；
  - **执行层**：VAL 真的改了对应状态位（否则「认出来了」只是话术）。

外加两条**反方向**断言（让路），因为这是一条新增的宽匹配面：
「帮我查一下车的说明书里怎么除雾」问的是方法，「新建一个下雨模式，关窗加除雾」是场景
管理句——两条都不许端侧当场执行。同 `test_fast_intent_adversarial` 的纪律：收窄/放宽面
只写一边守不住。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from fast_intent import classify, classify_structured, is_local
from val import VAL


def _name(text: str) -> str | None:
    result = classify(text)
    return result["name"] if result else None


# ═══════════════════════════════════════════════════════════════════════════
# 1. 识别层
# ═══════════════════════════════════════════════════════════════════════════

class TestRecognition:

    @pytest.mark.parametrize("text,expect", [
        # 原始 badcase 原句
        ("关闭强力前除雾", "front_defogger.close"),
        # 语料 `空调模式/功能控制` 下除雾族的实际说法
        ("打开前除雾", "front_defogger.open"),
        ("请帮我把前除雾打开", "front_defogger.open"),
        ("开后除雾", "rear_defogger.open"),
        # 裸词：真车「一键除雾」＝前挡，产品判断写在 commands.yaml
        ("开除雾", "front_defogger.open"),
        ("把除雾关了", "front_defogger.close"),
        # 除霜与除雾同一组能力（真车同一个按钮），差异只在词表层
        ("除霜", "front_defogger.open"),
        ("打开除霜", "front_defogger.open"),
        ("开启后除霜", "rear_defogger.open"),
        # 后挡的各种量词形态
        ("后挡除雾打开", "rear_defogger.open"),
        ("把后风挡除雾关掉", "rear_defogger.close"),
        ("后玻璃除雾开一下", "rear_defogger.open"),
    ])
    def test_recognized(self, text, expect):
        assert _name(text) == expect

    def test_all_are_local(self):
        """认出来还得能本地执行——`LOCAL_INTENTS` 漏登记会让它白白上云。

        这正是 `hvac.inc/dec` 踩过的坑：规范名改了、白名单没跟上，
        端侧认不出自己产出的名字（`fast_intent` LOCAL_INTENTS 开头注释）。
        """
        for text in ("打开前除雾", "关闭强力前除雾", "开后除雾", "除霜"):
            name = _name(text)
            assert name and is_local(name), f"{text} → {name} 不在 LOCAL_INTENTS"


class TestAircraftBranchNoLongerSwallowsMode:
    """②号错法的钉子：带「空调」二字时不许再退化成 `hvac.on`。

    这一条比看起来重要——它是**静默错误执行**：VAL 返回成功、话术说「开了」，
    而风挡上的雾一点没少。除雾分支必须早于空调分支，否则 `"空调" in t` 先抢走。
    """

    @pytest.mark.parametrize("text", ["空调开除雾", "把空调调到除雾", "空调除霜"])
    def test_not_degraded_to_hvac_on(self, text):
        assert _name(text) == "front_defogger.open"

    def test_close_is_not_hijacked_by_hvac_close(self):
        """`关闭强力前除雾` 若走进空调分支，第一句 `if "关" in t` 会判成关空调。"""
        assert _name("关闭强力前除雾") == "front_defogger.close"
        assert _name("把前除雾关掉") == "front_defogger.close"


class TestYieldsToNonCommands:
    """反方向：新增的是一条宽匹配面，让路面必须一起钉住。"""

    def test_manual_query_is_not_executed(self):
        """`帮我查一下车的说明书里怎么除雾` 问的是方法（mode_routing 语料在册）。"""
        assert _name("帮我查一下车的说明书里怎么除雾") != "front_defogger.open"
        assert classify_structured("除雾怎么开") is None
        assert classify_structured("为什么除雾没反应") is None

    def test_scene_management_still_goes_to_cloud(self):
        """`新建一个下雨模式，关窗加除雾` 里的除雾是**场景内容**不是当下指令。

        让路由更上游的 `_is_scene_management` 负责，本条只守它没被新分支抢跑。
        """
        assert classify_structured("新建一个下雨模式，关窗加除雾") is None


class TestAirconStillWorks:
    """空调本身不许被新分支蹭掉——除雾分支插在它正前方，回归面就在这里。"""

    @pytest.mark.parametrize("text,expect", [
        ("打开空调", "hvac.on"),
        ("关空调", "hvac.off"),
        ("空调26度", "hvac.set"),
        ("空调风速调大", "aircon.wind_speed.inc"),
        ("空调开内循环", "hvac.on"),
    ])
    def test_aircon_unchanged(self, text, expect):
        assert _name(text) == expect


# ═══════════════════════════════════════════════════════════════════════════
# 2. 能力层
# ═══════════════════════════════════════════════════════════════════════════

class TestCapabilitySurface:
    """端侧认得出 ≠ 云侧选得中。原始 badcase 死在这一层。"""

    def test_intents_are_in_the_vehicle_whitelist(self):
        from edge_agents_mod.vehicle import VEHICLE_INTENTS
        for name in ("front_defogger.open", "front_defogger.close",
                     "rear_defogger.open", "rear_defogger.close"):
            assert name in VEHICLE_INTENTS

    def test_objects_are_declared_in_the_val_knowledge_base(self):
        """`edge_call._to_structured` 只放行知识库声明过的对象，漏声明＝云侧下发解不出。"""
        objects = (VAL().commands or {}).get("objects") or {}
        assert "front_defogger" in objects
        assert "rear_defogger" in objects

    def test_cloud_dispatch_decodes(self):
        from edge_call import decode_intent
        objects = set((VAL().commands or {}).get("objects") or {})
        decoded = decode_intent("rear_defogger.close", objects)
        assert decoded and decoded["data"] == {"object": "rear_defogger",
                                               "operate": "close"}


# ═══════════════════════════════════════════════════════════════════════════
# 3. 执行层
# ═══════════════════════════════════════════════════════════════════════════

class TestExecution:

    @pytest.mark.parametrize("text,key,expect", [
        ("打开前除雾", "front_defogger", True),
        ("关闭强力前除雾", "front_defogger", False),
        ("开后除雾", "rear_defogger", True),
        ("把后风挡除雾关掉", "rear_defogger", False),
    ])
    def test_val_flips_the_right_state_bit(self, text, key, expect):
        """前后挡是两个物理开关——开前挡不许顺手把后挡也点亮。"""
        val = VAL()
        other = "rear_defogger" if key == "front_defogger" else "front_defogger"
        val.state[key] = not expect          # 先置反，避免与初值同值时读不出变化
        before_other = val.state[other]
        ok, _ = val.execute(classify_structured(text))
        assert ok
        assert val.state[key] is expect
        assert val.state[other] is before_other

    def test_speech_is_specific_not_generic(self):
        """话术落到 `generic_success`（「好的」）就说明 responses.yaml 漏了条目。"""
        val = VAL()
        _, msg = val.execute(classify_structured("打开前除雾"),
                             answer_length="detailed")
        assert "前挡除雾" in msg
