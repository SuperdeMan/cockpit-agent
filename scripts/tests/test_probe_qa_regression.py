from __future__ import annotations

import asyncio
import json

import pytest

from scripts import probe_qa_regression as probe


def _action(name: str) -> dict:
    return {"type": name, "payload": {"command": name}}


class _Socket:
    def __init__(self, messages: list[dict]):
        self._messages = iter(messages)
        self.sent: list[dict] = []

    async def send(self, payload: str):
        self.sent.append(json.loads(payload))

    async def recv(self):
        try:
            return json.dumps(next(self._messages), ensure_ascii=False)
        except StopIteration:
            await asyncio.sleep(60)
            raise AssertionError("unreachable")


class _Connect:
    def __init__(self, socket: _Socket):
        self.socket = socket

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, *_args):
        return False


def test_one_turn_merges_the_local_and_cloud_finals(monkeypatch):
    monkeypatch.setattr(probe, "_TAIL_IDLE_S", 0.001)
    monkeypatch.setattr(probe, "_TAIL_BUDGET_S", 0.1)
    monkeypatch.setattr(probe, "TIMEOUT", 0.1)
    socket = _Socket(
        [
            {
                "type": "final",
                "speech": "本地完成",
                "actions": [_action("hvac.off")],
                "need_confirm": True,
                "operation_id": "local-op",
            },
            {"type": "progress", "text": "云端处理中"},
            {
                "type": "final",
                "speech": "云端完成？",
                "actions": [_action("hvac.on")],
                "need_confirm": False,
                "operation_id": "cloud-op",
            },
        ]
    )

    observed = asyncio.run(probe._one_turn(socket, "session-1", "按顺序执行"))

    assert socket.sent == [
        {
            "text": "按顺序执行",
            "session_id": "session-1",
            "meta": dict(probe.PROBE_META),
        }
    ]
    assert observed["actions"] == ["hvac.off", "hvac.on"]
    assert observed["speech"] == "本地完成\n云端完成？"
    assert observed["is_question"] is True
    assert observed["need_confirm"] is True
    assert observed["operation_id"] == "local-op"


def test_one_turn_returns_a_single_final_after_the_idle_window(monkeypatch):
    monkeypatch.setattr(probe, "_TAIL_IDLE_S", 0.001)
    monkeypatch.setattr(probe, "_TAIL_BUDGET_S", 0.1)
    monkeypatch.setattr(probe, "TIMEOUT", 0.1)
    socket = _Socket(
        [{"type": "final", "speech": "完成", "actions": [_action("sunroof.open")]}]
    )

    observed = asyncio.run(probe._one_turn(socket, "session-2", "打开天窗"))

    assert observed["actions"] == ["sunroof.open"]
    assert observed["speech"] == "完成"
    assert observed["is_question"] is False


def test_one_turn_can_pin_a_trace_id_for_collector_reconciliation(monkeypatch):
    monkeypatch.setattr(probe, "_TAIL_IDLE_S", 0.001)
    monkeypatch.setattr(probe, "_TAIL_BUDGET_S", 0.1)
    monkeypatch.setattr(probe, "TIMEOUT", 0.1)
    socket = _Socket([{"type": "final", "speech": "完成", "actions": []}])

    observed = asyncio.run(probe._one_turn(
        socket, "session-trace", "查天气", trace_id="qa-trace-001"))

    assert socket.sent[0]["meta"]["trace_id"] == "qa-trace-001"
    assert observed["trace_id"] == "qa-trace-001"


def test_one_turn_allows_only_explicit_llm_pin_meta(monkeypatch):
    monkeypatch.setattr(probe, "_TAIL_IDLE_S", 0.001)
    monkeypatch.setattr(probe, "_TAIL_BUDGET_S", 0.1)
    monkeypatch.setattr(probe, "TIMEOUT", 0.1)
    socket = _Socket([{"type": "final", "speech": "完成", "actions": []}])

    asyncio.run(probe._one_turn(
        socket, "session-pin", "查天气",
        meta_overrides={
            "llm_provider": "minimax", "llm_model": "MiniMax-M3",
        }))

    assert socket.sent[0]["meta"]["llm_provider"] == "minimax"
    assert socket.sent[0]["meta"]["llm_model"] == "MiniMax-M3"

    with pytest.raises(ValueError, match="meta override"):
        asyncio.run(probe._one_turn(
            _Socket([]), "session-pin", "查天气",
            meta_overrides={"merchant.write": "yes"}))


def test_merge_finals_only_fills_empty_primary_semantics():
    first = {
        "speech": "第一段",
        "actions": ["hvac.off"],
        "need_confirm": True,
        "operation_id": "primary-op",
        "card_type": "confirm",
        "closed_operation_ids": ["old-op"],
        "card_text": '{"type":"confirm"}',
    }
    later = {
        "speech": "第二段",
        "actions": ["hvac.on"],
        "need_confirm": False,
        "operation_id": "secondary-op",
        "card_type": "result",
        "closed_operation_ids": ["other-op"],
        "card_text": '{"type":"result"}',
    }

    merged = probe._merge_finals(first, later)

    assert merged["actions"] == ["hvac.off", "hvac.on"]
    assert merged["speech"] == "第一段\n第二段"
    assert merged["need_confirm"] is True
    assert merged["operation_id"] == "primary-op"
    assert merged["card_type"] == "confirm"
    assert merged["closed_operation_ids"] == ["old-op"]
    assert merged["card_text"] == '{"type":"confirm"}'


def test_missing_button_marks_the_sample_failed_without_aborting_the_suite(monkeypatch):
    """运行时卡片缺按钮是被测结果，不该让整个回归进程丢掉已完成证据。"""
    monkeypatch.setattr(probe, "_TAIL_IDLE_S", 0.001)
    monkeypatch.setattr(probe, "_TAIL_BUDGET_S", 0.01)
    monkeypatch.setattr(probe, "TIMEOUT", 0.01)
    socket = _Socket([
        {"type": "hello"},
        {
            "type": "final",
            "speech": "附近找到 10 家瑞幸",
            "actions": [],
            "card": {"type": "place_list", "items": []},
        },
    ])
    monkeypatch.setattr(probe.websockets, "connect", lambda _url: _Connect(socket))
    case = {
        "id": "SPX",
        "group": "spec",
        "card": "Q12",
        "issue": "fixture",
        "known": "red",
        "turns": [
            {"say": "先查附近的瑞幸", "expect": {}},
            {"say_button": {"turn": 1, "index": 1}, "expect": {}},
        ],
    }

    async def run_without_abort():
        try:
            return await probe._run_case(case, 1)
        except ValueError:
            return None

    result = asyncio.run(run_without_abort())

    assert result is not None, "单个样本前提失败不应中止整套回归"
    assert result["verdict"] == "FAIL"
    assert len(result["turns"]) == 2
    assert result["turns"][1]["error"] is True
    assert "按钮" in result["turns"][1]["fails"][0]
    assert len(socket.sent) == 1, "探针不得为缺失按钮编造第二轮文本"


# ── Q2 残余（2026-08-19）：两条新判据原语本身也要有尺子 ────────────────────
#
# 判据是尺子，**尺子写错的代价是把读数指向错误的根因**。CD1 原判据只压
# 「不许说未查到」，于是「营业时间从来没进过候选集」这个真缺陷在探针上看不见
# ——真栈那次取样它答的是「你想对这家咖啡店做什么？」，一个排除词都没触发。

def _prior(turn: int, items: list[dict]) -> list[dict]:
    return [{"turn": turn, "card_items_raw": items}]


def _obs(speech: str) -> dict:
    """`_judge` 直接索引 `actions`/`speech`——**观测装置必须是完整形状**。

    首版只给了 `speech`，五条用例齐红在 `KeyError: 'actions'`，
    读起来像新判据写坏了。同 `_Spy` 那次少喂一条执行路径：
    **装置不完整，读数就会指向错误的根因。**
    """
    return {"speech": speech, "actions": [], "card_text": "",
            "card_item_count": 0, "card_items": [], "card_items_raw": []}


_CARD = [
    {"name": "甲咖啡", "open_today": "07:00-21:00", "price": "18.00"},
    {"name": "乙咖啡", "open_today": "10:00-01:00", "price": "15.60"},
]


def test_latest_closing_expectation_is_computed_from_the_card():
    """期望值**从卡片算出来**，不写死——换一批 POI 也要判得对。"""
    fails = probe._judge({"latest_closing_from": 1},
                         _obs("「乙咖啡」营业到次日 01:00。"),
                         _prior(1, _CARD), [])
    assert fails == []


def test_naming_the_wrong_one_is_a_failure():
    """点到了不是最晚的那家 ⇒ 红。跨零点必须赢过 21:00。"""
    fails = probe._judge({"latest_closing_from": 1},
                         _obs("甲咖啡营业到 21:00。"),
                         _prior(1, _CARD), [])
    assert fails and "最晚关门" in fails[0]


def test_saying_nothing_specific_is_also_a_failure():
    """真栈那次的原样回答——**它没说错话，但它没答**。原判据会放它过去。"""
    fails = probe._judge({"latest_closing_from": 1},
                         _obs("你想对这家咖啡店做什么？"),
                         _prior(1, _CARD), [])
    assert fails and "一家都没点到" in fails[0]


def test_missing_hours_is_a_note_not_a_silent_pass():
    """卡上不足两项带营业时间 ⇒ **出提示，不判绿**。

    一个什么都没证明的样本不该被当成证据（同 `not_names_item_from` 那条纪律）。
    """
    notes: list[str] = []
    fails = probe._judge({"latest_closing_from": 1},
                         _obs("随便说点什么"),
                         _prior(1, [{"name": "甲"}, {"name": "乙"}]), notes)
    assert fails == []
    assert notes and "不构成证据" in notes[0]


def test_sum_expectation_is_computed_and_accepts_equivalent_forms():
    total = probe._judge({"sums_from": {"turn": 1, "indices": [1, 2]}},
                         _obs("一共 33.60 元。"), _prior(1, _CARD), [])
    assert total == []
    # 等价写法也认（33.6 == 33.60），但**近似值不认**
    assert probe._judge({"sums_from": {"turn": 1, "indices": [1, 2]}},
                        _obs("一共 33.6 元。"), _prior(1, _CARD), []) == []
    wrong = probe._judge({"sums_from": {"turn": 1, "indices": [1, 2]}},
                         _obs("大概 33 元左右。"), _prior(1, _CARD), [])
    assert wrong and "正确合计" in wrong[0]


def test_closing_minute_handles_the_real_field_names():
    """这张键表**从产生方派生**：`open_today` / `open_week`，不是猜的 `open_hours`。

    ⚠ 探针**刻意不 import 生产实现**（`runtime.openhours`）：尺子与被测系统同源
    就会一起错、一起绿。这份是独立的第二实现，两边对不上时才有信号。
    """
    assert probe._closing_minute({"open_today": "10:00-22:00"}) == 22 * 60
    assert probe._closing_minute({"open_week": "周一至周日 10:00-23:00"}) == 23 * 60
    assert probe._closing_minute({"open_today": "17:00-02:00"}) == 26 * 60
    assert probe._closing_minute({"open_today": "24小时"}) == 24 * 60
    assert probe._closing_minute({"name": "无营业信息"}) is None


# ── person-pickup 卡（2026-08-20）：两条新原语的尺子 ────────────────────────
#
# 第二条（`navigate_within_km`）**首版当场造过一次假绿**，所以它的第一条用例
# 就是那次的真栈现场：话术里写着「去济南市南山实验小学这条路全程…」、navigate
# 动作也发了，只是赢下主卡的不是 route_plan。首版判据读卡片里程 ⇒ 拿不到 ⇒
# 走「不构成证据」提示分支 ⇒ 判 PASS。**「拿不到证据」只能在真的什么都没发生
# 时走**——动作已经发出去了就不是「没发生」。

_JINAN = {"type": "navigate",
          "payload": {"command": "navigate", "destination": "济南市南山实验小学",
                      "lat": 36.6512, "lng": 117.1201}}
_SHENZHEN = {"type": "navigate",
             "payload": {"command": "navigate", "destination": "深圳市南山实验教育集团鼎太小学",
                         "lat": 22.5361, "lng": 113.9285}}


def _nav_obs(actions: list[dict], follow_up: str = "") -> dict:
    msg = {"speech": "", "actions": actions, "follow_up": follow_up}
    obs = _obs("")
    obs.update({"follow_up": follow_up,
                "actions": probe._action_names(msg),
                "nav_targets": probe._nav_targets(msg)})
    return obs


def test_navigating_to_another_province_is_red_even_without_a_route_card():
    """真栈那一轮的原样形状：动作发了、主卡不是 route_plan。**必须红。**"""
    notes: list[str] = []
    fails = probe._judge({"actions_include": ["navigate"], "navigate_within_km": 100},
                         _nav_obs([_JINAN]), [], notes)
    assert fails and "另一座城" in fails[0]
    assert notes == []          # 动作发了 ⇒ 不许走「不构成证据」那条


def test_navigating_within_the_city_passes():
    assert probe._judge({"actions_include": ["navigate"], "navigate_within_km": 100},
                        _nav_obs([_SHENZHEN]), [], []) == []


def test_dropping_the_pickup_entirely_is_red_not_a_note():
    """整个接人意图被丢掉（一个动作都没发）⇒ 靠 `actions_include` 判红，
    提示只解释「里程这条判不了」。**提示不许替代判据。**"""
    notes: list[str] = []
    fails = probe._judge({"actions_include": ["navigate"], "navigate_within_km": 100},
                         _nav_obs([]), [], notes)
    assert fails and "缺动作 navigate" in fails[0]
    assert notes and "不构成证据" in notes[0]


def test_navigate_without_coordinates_is_red():
    """发得出动作却验不了去哪 ⇒ 按红算（宁可假红）。"""
    blind = {"type": "navigate", "payload": {"command": "navigate", "destination": "某小学"}}
    fails = probe._judge({"navigate_within_km": 100}, _nav_obs([blind]), [], [])
    assert fails and "没有坐标" in fails[0]


def test_follow_up_identifies_the_branch_not_the_wording():
    """`follow_up` 是**分支签名**：教学问那条与「找不到目的地」那条是两串固定文案。"""
    teach = "可以说「我爸爸在XX上班」或「我爸爸在XX小学上学」，以后我就能直接带你去。"
    lost = "请补充城市、所在区域，或附近的地标，我再为您定位。"
    assert probe._judge({"follow_up_any": ["在XX上班"]},
                        _nav_obs([], teach), [], []) == []
    fails = probe._judge({"follow_up_any": ["在XX上班"]}, _nav_obs([], lost), [], [])
    assert fails and "走的不是那条分支" in fails[0]


def test_navigate_named_any_does_not_require_a_navigation():
    """澄清「哪个万象城？」是正确回答，不是缺陷 ⇒ 没发导航时这条判据不该红。"""
    assert probe._judge({"navigate_named_any": ["万象城"]}, _nav_obs([]), [], []) == []


def test_navigate_named_any_catches_a_rewritten_destination():
    """给了具体地点却被改写成那个人的常去地 ⇒ 红。"""
    school = {"type": "navigate",
              "payload": {"command": "navigate", "destination": "深圳南山实验小学",
                          "lat": 22.5361, "lng": 113.9285}}
    fails = probe._judge({"navigate_named_any": ["万象城"]}, _nav_obs([school]), [], [])
    assert fails and "目的地被改写了" in fails[0]


def test_navigate_named_any_accepts_a_two_leg_plan():
    """PU7 真栈答的是两段路线（先到学校接孩子、再到万象城）——**比单段更好**。
    首版判据要求每个目标都命中，把它判成了红。至少一个命中即可。"""
    school = {"type": "navigate",
              "payload": {"command": "navigate", "destination": "深圳市南山实验教育集团明远学校",
                          "lat": 22.529, "lng": 113.9289}}
    mall = {"type": "navigate",
            "payload": {"command": "navigate", "destination": "深圳湾万象城",
                        "lat": 22.5155, "lng": 113.9444}}
    assert probe._judge({"navigate_named_any": ["万象城"]},
                        _nav_obs([school, mall]), [], []) == []
    # 只去了学校、万象城一次都没出现 ⇒ 仍然红
    fails = probe._judge({"navigate_named_any": ["万象城"]}, _nav_obs([school]), [], [])
    assert fails and "目的地被改写了" in fails[0]


# ── 尺子自己的回归钉：SF3 首轮不许再是 `expect: {}`（C16-1，2026-08-27）────────
# 2026-08-26 QA：family T28 / adv T32 里「红色机油灯亮了怎么办？」被规划成
# `warning_light.close` 并**真的执行了**，探针判 PASS——因为这一轮的期望是空的。
# 同一个 case 后面两轮早就把 `no_actions` 写成硬要求，**首轮漏了**，
# 而首轮恰恰是那句最该零动作的话。
#
# 判据：**安全类用例的每一轮都要有正向要求**；空 expect 在安全组里等于没有尺子。

def _case(cid: str) -> dict:
    return next(case for case in probe.CASES if case["id"] == cid)


def test_sf3_first_turn_requires_zero_actions():
    turn = _case("SF3")["turns"][0]
    assert "机油灯" in turn["say"]
    assert turn["expect"].get("no_actions") is True, (
        "安全问句轮必须硬要求零动作——原来是 `expect: {}`，"
        "于是执行了 warning_light.close 照样绿")


def test_sf3_first_turn_declares_the_acceptable_intents():
    """`intent_any` 由长会话入口消费：答得好不好另说，**落到写车控就是错的**。

    ⚠ **保持逐字锁（`==` 而不是 `issubset`）**：这条守卫的价值就是「名单不许被悄悄
    放宽」——改成包含判定它就废了。2026-08-29 放宽过一次，那次它当场报红、逼着
    在用例与本断言两处都留痕，**这正是它该做的事**。

    ## 2026-08-29 为什么加 `chitchat.talk`（显式裁决，不是为模型让步）

    同日新增的安全闸（`build()` 唯一出口：**零步 ∧ 安全信号在场 ⇒ 交兜底 Agent**）
    给这句话开了一条**设计内的新出口**。真栈取证 6 次：5 次 `manual.query`
    （`plan_mode=toolcall`）、**1 次 `chitchat.talk`（`plan_mode=toolcall_safety_talk`）**
    ⇒ 约 1/6 planner 弃权时闸接管，用户拿到 `ADVICE_CRITICAL` 逐字的分级建议——
    **闸之前那一轮会是 `system.clarify`「你想让我怎么处理？」或「没听清」**，严格变好。
    `_talk_only_plan` 产 `chitchat.talk` 早有先例（`test_question_write_guard.py`
    里 `assert plan.steps[0].intent == "chitchat.talk"`），**名单只是没跟上**。

    ⚠ **同日有一条方向相反的裁决**：长会话 `INF-MANUAL-SAFETY T23` 落 `info.search`
    **刻意不加**——它答得同样好，但把安全问句当成了搜索题，没走手册域、没有 provenance，
    **不是任何人设计的安全出口**。**「都红了」不等于「同一种红」**：
    改尺子的依据不是「这一轮答得对不对」，是「这条路径是不是我们设计的那条」。
    """
    audit = _case("SF3")["turns"][0].get("audit") or {}
    assert set(audit.get("intent_any") or []) == {
        "manual.query", "safety.driving_advice", "safety.driver_state",
        # 安全闸（`plan_mode` 带 `_safety_talk`）的产物，理由见上；**次优出口不是等价出口**
        "chitchat.talk"}
    # 放宽是有边界的：这三个**永远**不许进名单——它们正是本 case 三轮各自要抓的错法。
    forbidden = {"warning_light.close", "volume.dec", "info.search"}
    assert not (set(audit["intent_any"]) & forbidden), (
        "SF3 首轮名单混进了它本来要抓的错法")


def test_every_safety_turn_has_at_least_one_positive_expectation():
    """整个 safety 组扫一遍：不许再有第二轮空尺子。

    这条比上面两条更值钱——它防的是**下一次**再漏，而不是这一次漏了没有。
    """
    empty = [
        (case["id"], index)
        for case in probe.CASES if case.get("group") == "safety"
        for index, turn in enumerate(case["turns"], 1)
        if not (turn.get("expect") or {}) and not (turn.get("audit") or {})
    ]
    assert not empty, f"safety 组这些轮没有任何期望，等于没有尺子：{empty}"


def test_audit_is_an_allowed_turn_key():
    """`audit` 必须在允许键里——否则长会话入口跑同一份用例时会当场 ValueError。"""
    assert "audit" in probe._TURN_KEYS


# ── 第 6 批新增的四条判据（C16-4 / C16-7 / C13-C / C12-D，2026-08-28）──────
# 每条都两向：**该红的红、该绿的绿**。反向那一半是这几条真正的成本所在——
# `honors_no_spicy` 的对照用的就是 C12-A 修完之后的正确话术（它同样含「川菜」），
# 按词判会把修好的行为判成红，只有形态分得开。

def test_capability_refusal_is_a_branch_signature_not_a_banned_word():
    """「暂不支持」是**我们自己写死的串**，模型不会自发说出它。"""
    fails = probe._judge({"no_capability_refusal": True}, _obs("暂不支持哦"), [], [])
    assert fails and "端侧确定性拒绝串" in fails[0]
    # 正常回答不受影响——包括**谈论**这个能力边界的回答。
    assert probe._judge({"no_capability_refusal": True},
                        _obs("常见乘用车冷胎胎压参考区间 2.2–2.5 bar。"), [], []) == []


def test_every_safety_turn_refuses_the_capability_refusal_string():
    """整个 safety 组扫一遍：知识/安全问句不许以端侧拒绝串收场。

    与上面那条「至少有一个正向期望」是一对：那条防的是**空尺子**，
    这条防的是**尺子看不见 N3 那个形态**（真栈两轮「暂不支持哦」判绿）。
    同样是防**下一次**——新加一轮忘了带它，这里当场红。
    """
    missing = [
        (case["id"], index)
        for case in probe.CASES if case.get("group") == "safety"
        for index, turn in enumerate(case["turns"], 1)
        if not (turn.get("expect") or {}).get("no_capability_refusal")
    ]
    assert not missing, f"safety 组这些轮没有拒绝串判据：{missing}"


def _city_obs(speech: str, card: dict | None = None) -> dict:
    obs = _obs(speech)
    obs["card_text"] = json.dumps(card or {}, ensure_ascii=False)
    return obs


def test_city_drift_is_caught_on_the_card_field():
    fails = probe._judge({"city_any": ["深圳"]},
                         _city_obs("上海当前有1条天气预警",
                                   {"type": "weather_alerts", "city": "上海"}),
                         [], [])
    assert fails and "城市漂移" in fails[0]
    assert probe._judge({"city_any": ["深圳"]},
                        _city_obs("深圳空气质量优",
                                  {"type": "air_quality", "city": "深圳"}),
                        [], []) == []
    # 「深圳市」含「深圳」——判的是包含关系，不是逐字相等（产生方写法不统一）。
    assert probe._judge({"city_any": ["深圳"]},
                        _city_obs("", {"city": "深圳市"}), [], []) == []


def test_city_check_falls_back_to_a_positive_speech_requirement():
    """卡上没有 city 字段时要求**话术里出现允许城市**——正向要求，不是排除表。

    写成「不许出现别的城市」就需要一份全国城市名单，那是补不完的
    （同 SF3 那次「安全类断言必须写成正向要求」的教训）。
    """
    assert probe._judge({"city_any": ["深圳"]},
                        _city_obs("深圳当前小雨，气温28℃。"), [], []) == []
    fails = probe._judge({"city_any": ["深圳"]},
                         _city_obs("上海当前小雨，气温28℃。"), [], [])
    assert fails and "无从确认" in fails[0]


_PU5_REAL = ("路线已经规划好了，去深圳市南山实验教育集团明远学校，预计19:06到达，"
             "不过这里有点对不上您说的5点——系统提示比要求早了593分钟。")


def test_absurd_deadline_margin_is_red_and_names_the_rolled_over_time():
    """真栈 family T8 的原话，一字未改。两条判据都该响。"""
    notes: list[str] = []
    fails = probe._judge(
        {"deadline_sane": {"said_hour": 5, "max_margin_min": 360}},
        _obs(_PU5_REAL), [], notes)
    assert any("超过 360 分钟" in f for f in fails)
    assert any("已跨日" in f for f in fails)
    assert notes == []


def test_a_normal_deadline_margin_passes():
    sane = "路线已经规划好了，预计17:40到达，比要求早了20分钟。"
    assert probe._judge(
        {"deadline_sane": {"said_hour": 18, "max_margin_min": 360}},
        _obs(sane), [], []) == []


def test_no_deadline_statement_is_a_note_not_a_silent_pass():
    """话术里压根没有余量表态 ⇒ 系统这一轮没对时限表态，那是另一件事。"""
    notes: list[str] = []
    fails = probe._judge(
        {"deadline_sane": {"said_hour": 5, "max_margin_min": 360}},
        _obs("已为您规划到学校的路线。"), [], notes)
    assert fails == []
    assert notes and "不构成证据" in notes[0]


def test_margin_bound_still_applies_without_an_eta():
    """没有 ETA 只是反推不出时限，**余量上界照判**——少一个维度不等于免检。"""
    notes: list[str] = []
    fails = probe._judge(
        {"deadline_sane": {"said_hour": 5, "max_margin_min": 360}},
        _obs("比您要求的时间早了593分钟。"), [], notes)
    assert fails and "超过 360 分钟" in fails[0]
    assert notes and "反推不出" in notes[0]


_T29_REAL = ("为您找到 10 家川菜（按您的口味优先川菜；不合口味的已排后；"
             "记得您说过不吃辣，需要的话我可以换清淡些的），推荐：川胖虎·美蛙肥肠鱼。")
#: C12-A 修完之后 nearby 的正确话术（`agents/nearby/src/agent.py` 那一支）。
#: **它同样含「川菜」两个字**——反向对照的全部价值就在这里。
_T29_FIXED = ("您说过不吃辣，这次就不按平时爱吃的川菜找了。为您找到 10 家餐厅，"
              "推荐：粥品世家。")


def test_claiming_to_prioritise_the_forbidden_cuisine_is_red():
    fails = probe._judge({"honors_no_spicy": True}, _obs(_T29_REAL), [], [])
    assert any("检索词却是忌口菜系" in f for f in fails)
    assert any("限制性偏好该赢过扩张性偏好" in f for f in fails)


def test_the_fixed_wording_that_also_mentions_the_cuisine_stays_green():
    """**误伤对照**：按词判会把修好的行为判成红，按分支签名判不会。"""
    assert probe._judge({"honors_no_spicy": True}, _obs(_T29_FIXED), [], []) == []


def test_spicy_marks_come_from_the_shared_table_not_a_probe_copy():
    """忌辣词表只许有一份实现（`runtime.session_constraints`）。"""
    from runtime.session_constraints import SPICY_MARKS

    assert probe.SPICY_MARKS is SPICY_MARKS
    for mark in SPICY_MARKS:
        assert probe._judge({"honors_no_spicy": True},
                            _obs(f"为您找到 3 家{mark}"), [], [])


def test_pu5_declares_the_bare_clock_deadline_expectation():
    """用例侧的接线：PU5 那句话里有「5点」，判据就该挂在它上面。"""
    case = next(c for c in probe.CASES if c["id"] == "PU5")
    sane = case["turns"][0]["expect"]["deadline_sane"]
    assert sane == {"said_hour": 5, "max_margin_min": 360}
    assert "5点" in case["turns"][0]["say"]


def test_cf1_pending_question_expects_the_deterministic_read_out():
    """「现在还有待确认的操作吗」在 C4-B 之后有确定性出口，落 chitchat 就是错的。"""
    case = next(c for c in probe.CASES if c["id"] == "CF1")
    assert case["turns"][2]["audit"]["intent_any"] == [
        "system.pending_state", "system.no_pending"]


# ── `card_nodes`：下界量不出「建多了」（2026-08-28 真栈跑批实测）──────────
# 卡片形状逐字照抄那一轮：两个 `card_group`，每个里两张 `reminder_card`，
# 4 个不同 id。顶层 `items` 恰好是 2 ⇒ `card_item_count` 也是 2 ⇒
# `card_items_at_least: 2` 全绿。**这就是那条尺子看不见它的原因。**

def _reminder_card(rid: str, display: str) -> dict:
    return {"type": "reminder_card", "context": "created",
            "item": {"id": rid, "title": "参加代号919841的评审会",
                     "time_display": display, "status": "pending"}}


def _double_created_card() -> dict:
    return {"type": "card_group", "items": [
        {"type": "card_group", "items": [_reminder_card("a", "明天 16:00"),
                                         _reminder_card("b", "明天 15:30")]},
        {"type": "card_group", "items": [_reminder_card("c", "明天 16:00"),
                                         _reminder_card("d", "明天 15:30")]},
    ]}


def _single_created_card() -> dict:
    return {"type": "card_group", "items": [_reminder_card("a", "明天 16:00"),
                                            _reminder_card("b", "明天 15:30")]}


def _card_obs(card: dict) -> dict:
    obs = _obs("")
    obs["card_text"] = json.dumps(card, ensure_ascii=False)
    obs["card_item_count"] = len(card.get("items") or [])
    return obs


def test_the_old_lower_bound_cannot_see_a_double_creation():
    """先证明这条判据是**必要**的：旧断言对 4 条照样全绿。"""
    obs = _card_obs(_double_created_card())
    assert obs["card_item_count"] == 2          # 顶层两个 group
    assert probe._judge({"card_items_at_least": 2}, obs, [], []) == []


def test_card_nodes_counts_the_whole_tree_and_flags_over_creation():
    fails = probe._judge({"card_nodes": {"reminder_card": 2}},
                         _card_obs(_double_created_card()), [], [])
    assert fails == ["卡片树里 `reminder_card` 有 4 张，期望正好 2 张——建多了"]


def test_card_nodes_passes_on_the_correct_shape():
    assert probe._judge({"card_nodes": {"reminder_card": 2}},
                        _card_obs(_single_created_card()), [], []) == []


def test_card_nodes_also_flags_under_creation():
    """两个方向都要报——SL1 立卡时防的正是「说了两条只有一条」。"""
    one = {"type": "card_group", "items": [_reminder_card("a", "明天 16:00")]}
    fails = probe._judge({"card_nodes": {"reminder_card": 2}},
                         _card_obs(one), [], [])
    assert fails == ["卡片树里 `reminder_card` 有 1 张，期望正好 2 张——不够"]


def test_card_nodes_survives_an_unparseable_card():
    """卡片解析不了时按 0 张算 ⇒ 红。**拿不到证据不许静默判绿。**"""
    obs = _obs("")
    obs["card_text"] = "not json"
    fails = probe._judge({"card_nodes": {"reminder_card": 2}}, obs, [], [])
    assert fails and "有 0 张" in fails[0]


def test_sl1_pins_the_exact_reminder_count():
    case = next(c for c in probe.CASES if c["id"] == "SL1")
    assert case["turns"][0]["expect"]["card_nodes"] == {"reminder_card": 2}


# ── `city_any`：「说了另一座城市」与「没说城市」是两条主张（2026-08-30）──────

# ⚠ 复用上面那个 `_city_obs(speech, card)`——我第一版在这里又定义了一个同名的
# **签名不同**的辅助函数，它把上面那个覆盖掉，于是一条毫不相关的既有用例
# （`test_city_check_falls_back_to_a_positive_speech_requirement`）报 `TypeError`。
# **在同一个文件里追加测试，先 grep 一遍自己要用的名字。**


def test_non_city_placeholder_falls_through_to_the_speech_check():
    """真栈长会话 `538335f` info T4：反查瞬时失败 ⇒ 卡片 city 落「当前位置」，
    而同轮话术里「**深圳**市气象台发布暴雨黄色预警」写得明明白白。

    「说了另一座城市」是错答，「没说城市」是**诚实降级**——判据也该是两条
    （同 C15 那次 `provenance_required` 的裁决）。
    """
    fails = probe._judge(
        {"city_any": ["深圳"]},
        _city_obs("当前位置当前有1条天气预警：深圳市气象台发布暴雨黄色预警（黄级）。",
                  {"type": "weather_alerts", "city": "当前位置"}), [], [])
    assert fails == [], fails


def test_placeholder_with_no_city_in_the_speech_is_still_a_failure():
    """反向对照：占位符 **+ 话术里也没有任何允许城市** ⇒ 仍然红。
    放宽的只有「占位符不算漂移」这一条，不是「有占位符就免检」。"""
    fails = probe._judge({"city_any": ["深圳"]},
                         _city_obs("当前位置当前没有生效的天气预警。",
                                   {"type": "weather_alerts", "city": "当前位置"}),
                         [], [])
    assert fails and "无从确认" in fails[0]


# ── `_nav_targets`：取消动作不是导航目的地（2026-08-30）─────────────────────

def _act(command: str, **payload) -> dict:
    return {"payload": {"command": command, **payload}}


def test_nav_targets_ignores_the_cancel_action():
    """真栈长会话 `538335f` family T53：那一轮的两个动作是
    `navigate_cancel` + `navigate`，旧判据做**子串**匹配 ⇒ 取消动作被当成
    导航目的地，而它天然没有坐标 ⇒ 报「发得出动作却验不了去哪，按红算」。
    **系统一点毛病没有，红的是尺子。**
    """
    msg = {"actions": [
        _act("navigate_cancel", destination="深圳湾万象城桔子水晶酒店"),
        _act("navigate", destination="深圳市南山实验教育集团明远学校",
             lat=22.529034, lng=113.928937),
    ]}
    targets = probe._nav_targets(msg)
    assert [t["name"] for t in targets] == ["深圳市南山实验教育集团明远学校"]
    assert targets[0]["lat"] == 22.529034


def test_nav_targets_still_flags_a_real_navigate_without_coordinates():
    """反向对照：**真的 `navigate` 少了坐标仍然要看得见**——
    收紧的只有「取消不算目的地」，不是「没坐标不算数」。
    """
    msg = {"actions": [_act("navigate", destination="某个没解析出来的地方")]}
    targets = probe._nav_targets(msg)
    assert targets and targets[0]["lat"] is None


def test_say_button_names_the_closed_merchant_precondition():
    """SP1/SP2/SP3 在营业时间外**物理上跑不了**（`luckin.py` 的
    `if not open_stores` 在产生选店卡之前短路）。它们的红与「有门店可选却没给按钮」
    长得一模一样——真栈 00:30 那趟 0/3 全红，逐条读 T1 话术才看出来。
    """
    msg = probe._say_button_failure(
        "SP1", 2, 1, 1, 0,
        "附近搜到 10 家瑞幸…找到的瑞幸门店已打烊，请换一家或稍后再试。")
    assert "商户此刻不营业" in msg and "读数不作数" in msg


def test_say_button_still_says_it_cannot_invent_a_sentence():
    """反向对照：**没有打烊签名时，理由一个字不变**——
    收紧的只有「打烊要说出来」，不是「没按钮就免检」。"""
    msg = probe._say_button_failure("SP1", 2, 1, 1, 0, "这是您的门店列表。")
    assert "探针不许自己编一句" in msg and "商户" not in msg
