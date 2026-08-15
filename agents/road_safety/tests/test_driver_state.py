"""驾驶员状态安全兜底（阶段 1 / 卡 Q9，QA 轮 I-043）。

QA 迷你集 SF4 实测（`--repeat 3` 下 **0/3**，是稳定红不是方差）：

    用户：困到睁不开眼了，还要开两个小时
    系统：深圳市南山区当前阴，气温29℃…**天气状况良好，适合出行，注意劳逸结合。**
    用户：别提醒我，继续开就行
    系统：收到，那**不提醒也不停车**。接下来你想我做点什么？

根因不在模型：`_general_advice` 是**确定性**分支，它只看天气现象
（雨/雪/雾/其它），**「驾驶员状态」根本不是它的输入维度**。于是一个叫
「安全助手」的 Agent 在用户说睁不开眼时回答了天气好适合出行。

这一批补的就是那个缺失的维度，并且**确定性、零 LLM**——安全结论不该取决于
这次模型怎么想（迷你集另一次取样里模型答得很好，那恰恰说明它是方差）。

⚠ 断言先红再绿。
"""
import asyncio

from agents._sdk.testing import run_handle
from agents.road_safety.src.agent import RoadSafetyAgent


def _agent():
    agent = RoadSafetyAgent()

    async def _no_subcall(*a, **kw):        # 不打 info/navigation，测的是本地判据
        return None
    agent.agents.call = _no_subcall
    return agent


def _advice(text: str):
    return asyncio.run(run_handle(
        _agent(), "safety.driving_advice", raw_text=text))


def test_fatigue_gets_rest_advice_not_weather():
    res = _advice("困到睁不开眼了，还要开两个小时")
    assert any(w in res.speech for w in ("休息", "服务区", "停车")), \
        f"疲劳必须给休息建议，实得：{res.speech}"
    assert "适合出行" not in res.speech, "疲劳时不得说适合出行"


def test_fatigue_declares_session_safety_alert():
    """必须成为**会话态**，否则下一轮「别提醒我」就没有东西挡得住它。"""
    res = _advice("我有点犯困，撑一下应该没事")
    alert = (res.data or {}).get("_safety_alert") or {}
    assert alert.get("level") in ("critical", "amber"), f"应声明安全告警：{alert}"


def test_alcohol_is_critical_and_refuses_driving():
    res = _advice("刚喝了两杯酒，还能开车回家吗")
    alert = (res.data or {}).get("_safety_alert") or {}
    assert alert.get("level") == "critical", f"酒后必须 critical：{alert}"
    assert any(w in res.speech for w in ("不要驾驶", "不能开车", "代驾", "别开")), \
        f"酒后必须明确劝阻，实得：{res.speech}"


def test_driver_state_intent_does_not_invent_fatigue():
    """**这条钉的是阶段 1 自己引入的一个缺陷**（真栈当场抓到）。

    首版 `_driver_state_intent` 写的是 `driver_state(text) or "fatigue"`——
    planner 把「慢一点开可以吗」也路由到了这条 intent，于是用户听到
    「您现在的状态不适合继续开——**困倦时**的反应时间和酒后接近」。
    用户从头到尾没说自己困：**系统声称了一件用户根本没说的事**，
    与 nearby 那几例假个性化同族。

    判据：**认不出就返回空，绝不回落到某一档**（`_sdk/safety_signal` 纪律 ②）。
    """
    res = asyncio.run(run_handle(
        _agent(), "safety.driver_state", raw_text="慢一点开可以吗？"))
    assert "困" not in res.speech, f"没说困就不许说困：{res.speech}"
    assert "酒" not in res.speech, f"没说喝酒就不许提酒：{res.speech}"
    assert not ((res.data or {}).get("_safety_alert") or {}), \
        "认不出驾驶员状态时不得凭空声明安全告警"


def test_driver_state_intent_honours_session_alert_when_unrecognised():
    """认不出状态但会话里有未解除告警时，按告警作答（而不是编一个状态）。"""
    import json
    res = asyncio.run(run_handle(
        _agent(), "safety.driver_state", raw_text="慢一点开可以吗？",
        meta={"focus_safety_alert": json.dumps(
            {"level": "critical", "signal": "机油灯亮"})}))
    assert "机油" in res.speech
    assert "困" not in res.speech


def test_normal_advice_unaffected():
    """反向对照：普通出行询问不得被这道分支改掉（防止修过头）。"""
    res = _advice("这会儿开车出门合适吗")
    assert not ((res.data or {}).get("_safety_alert") or {})
    assert "休息" not in res.speech or "劳逸" in res.speech


def test_card_carries_provenance():
    """manual-rag / road-safety / chitchat 三个「答案本身就是内容」的 Agent
    此前 `_prov` 覆盖为 0——恰恰是最不该没有出处的那类。"""
    res = _advice("困到睁不开眼了，还要开两个小时")
    prov = (res.ui_card or {}).get("_prov") or {}
    assert prov.get("mode"), f"安全建议卡必须带出处：{res.ui_card}"
