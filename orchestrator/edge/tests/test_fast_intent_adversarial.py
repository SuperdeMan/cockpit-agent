"""端侧对抗测试首轮发现的三组缺陷的回归钉子。

出处：`docs/design/2026-08-02-intent-routing-adversarial-findings.md` §1（L0 五条红灯）。
三组缺陷共用一个形态：**端侧认的是「对象 + 动作词」**——于是提问被执行、独立请求被当成
上一句的补语、名字拼错的合法意图落不进 LOCAL_INTENTS。每组都同时钉住「要修的方向」与
「不许顺手改坏的反方向」，因为这三条规则都是**收窄/放宽**面，只写一边守不住。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import (classify, classify_structured, cloud_domain_of,
                         is_local, is_sequence_connector, split_and_classify_any)


def _name(text: str) -> str | None:
    result = classify(text)
    return result["name"] if result else None


# ═══════════════════════════════════════════════════════════════════════════
# 1. 提问不是指令（findings §1.1，high risk）
# ═══════════════════════════════════════════════════════════════════════════

class TestQuestionIsNotACommand:
    """「这车的天窗最大能开多大」曾真的把天窗打开了——用户根本没有下指令。"""

    def test_capability_question_does_not_execute(self):
        assert classify_structured("这车的天窗最大能开多大") is None
        assert classify_structured("车窗最多能开多大") is None

    def test_hypothetical_question_does_not_execute(self):
        assert classify_structured("要是下雨了车窗会自动关吗") is None
        assert classify_structured("如果没电了空调还能开吗") is None

    def test_question_tail_does_not_execute(self):
        assert classify_structured("空调开着吗") is None
        assert classify_structured("天窗是不是开着的") is None

    # ── 反方向：这些仍然必须是端侧秒回 ──────────────────────────────────
    def test_polite_request_is_still_a_command(self):
        """带「帮我/麻烦/请」的疑问句是礼貌祈使，不是提问。"""
        assert _name("能帮我把车窗关上吗") == "window.close"
        assert _name("麻烦帮我打开空调") in ("hvac.on", "aircon.open")

    def test_plain_commands_unaffected(self):
        assert _name("打开天窗") == "sunroof.open"
        assert _name("关上车窗") == "window.close"
        assert _name("把遮阳帘关上") == "sunshade.close"

    def test_query_intents_survive_the_veto(self):
        """否决面只盖**写操作**——查询类带疑问词是常态，一刀切会把好用的秒回砍掉。"""
        assert _name("胎压是多少") == "tire_pressure.query"
        assert _name("电量还有多少") == "battery.query"
        assert _name("今天体感温度怎么样") == "info.weather"

    def test_interrogative_plus_operation_verb_is_still_a_command(self):
        """与 `_is_env_temp_query` 同一条判据：疑问词 + 操作动词仍算指令。"""
        assert (_name("温度如何调高") or "").startswith(("hvac", "aircon"))


# ═══════════════════════════════════════════════════════════════════════════
# 2. 独立请求 ≠ 上一句的补语（findings §1.2）
# ═══════════════════════════════════════════════════════════════════════════

class TestCloudDomainIsRecognised:
    """端侧「认得但不归自己管」——认得出，就说明它自成一句。"""

    def test_reminder_is_a_recognised_cloud_domain(self):
        assert cloud_domain_of("提醒我八点开会") == "reminder"
        assert cloud_domain_of("别忘了给我买咖啡") == "reminder"

    def test_scene_and_memory_are_recognised(self):
        assert cloud_domain_of("开启露营模式") == "scene"
        assert cloud_domain_of("记住我女儿叫小满") == "memory"

    def test_unclassifiable_fragment_is_not_a_domain(self):
        """「周杰伦的」是补语不是请求——端侧不该假装认得它。"""
        assert cloud_domain_of("周杰伦的") is None
        assert cloud_domain_of("走最快的那条路") is None

    def test_sequence_connector_marks_a_new_act(self):
        assert is_sequence_connector("，再")
        assert is_sequence_connector("，然后")
        assert is_sequence_connector("并且")
        assert not is_sequence_connector("，")      # 裸逗号后面跟的多半是补语
        assert not is_sequence_connector("")


class TestMixedSplitCarriesGroupingSignals:
    def test_reminder_fragment_is_marked(self):
        parts = split_and_classify_any("音量调小一点，提醒我八点开会")
        assert parts is not None and len(parts) == 2
        assert parts[0]["_needs_cloud"] is False
        assert is_local(classify("音量调小一点")["name"])
        assert parts[1]["_needs_cloud"] is True
        assert parts[1]["_cloud_domain"] == "reminder"

    def test_sequencing_fragment_carries_its_connector(self):
        parts = split_and_classify_any("打开座椅加热，再找个充电站")
        assert parts is not None and len(parts) == 2
        assert parts[1]["_needs_cloud"] is True
        assert is_sequence_connector(parts[1]["_sep"])

    def test_trailing_qualifier_carries_a_bare_comma(self):
        """反方向：补语必须**留在**裸逗号档，否则会把它从主意图上撕下来。"""
        parts = split_and_classify_any("帮我播一首歌，周杰伦的")
        assert parts is not None and len(parts) == 2
        assert parts[1]["_needs_cloud"] is True
        assert parts[1]["_cloud_domain"] == ""
        assert not is_sequence_connector(parts[1]["_sep"])


# ═══════════════════════════════════════════════════════════════════════════
# 3. 后视镜落不进 LOCAL_INTENTS（findings §1.3）
# ═══════════════════════════════════════════════════════════════════════════

class TestMirrorFoldIsLocal:
    """名字取自 mode 而不是 operate：VAL 认 operate=set + mode=fold，
    LOCAL_INTENTS 登记的是 rear_view_mirror.fold——旧实现拼出 `.set` 于是整句上云。"""

    def test_fold_is_local(self):
        assert _name("把后视镜收起来") == "rear_view_mirror.fold"
        assert is_local("rear_view_mirror.fold")

    def test_unfold_is_local(self):
        assert _name("把后视镜展开") == "rear_view_mirror.unfold"
        assert is_local("rear_view_mirror.unfold")

    def test_structured_still_speaks_val_dialect(self):
        """改的是名字映射，不是分类器产出——VAL 那侧的 operate/mode 口径不许被动。"""
        result = classify_structured("把后视镜收起来")
        assert result["data"]["operate"] == "set"
        assert result["data"]["mode"] == "fold"


# ═══════════════════════════════════════════════════════════════════════════
# 4. 规格问句不是状态查询（2026-08-26 QA，卡 C2-C / N3）
# ═══════════════════════════════════════════════════════════════════════════

class TestSpecQuestionIsNotAStateQuery:
    """「胎压应该补到多少」问的是**推荐值**，端侧手里只有当前读数。

    实录两种错法、一个根因（端侧按对象词一把抓）：
      · adv T30「胎压应该补到多少」→ 端侧 `tire_pressure.query` 秒回「暂不支持哦」；
      · info T23 混合路径下端侧先答「暂不支持该控制指令」、云端再答一遍，
        用户听到的是**两段拼接**。

    与本文件第 1 组「提问不是指令」是同一形态在**查询侧**的复发：那一组守的是
    「问句不许被执行成写操作」，这一组守的是「规格问句不许被端侧当成读操作抢答」。
    收窄面照例两头钉：真状态查询必须仍然走端侧秒回。
    """

    def test_spec_asks_go_to_cloud(self):
        for text in ("胎压应该补到多少", "标准胎压是多少", "胎压多少正常",
                     "胎压建议打到多少", "不知道具体车型时，标准胎压应该是多少"):
            assert classify_structured(text) is None, f"{text!r} 不该被端侧接管"

    def test_state_queries_still_answered_locally(self):
        for text in ("胎压是多少", "看下胎压", "帮我查一下胎压", "胎压正常吗",
                     "轮胎气压查一下"):
            result = classify_structured(text)
            assert result is not None, f"{text!r} 认不出来了"
            assert result["data"]["object"] == "tire_pressure_monitoring"
            assert is_local(_name(text) or ""), f"{text!r} 不该上云"
