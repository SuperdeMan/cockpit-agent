"""M1b 自进化流水线单测（RFC §5-3）：mine 信号判定 / triage 解析安全面 / propose 白名单。
evolve.py 是脚本非包——按文件路径独名加载（同 llm-gateway tests 惯例，零 sys.modules 污染）。"""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "evolve_under_test",
    os.path.join(os.path.dirname(__file__), "..", "evolve.py"))
ev = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ev)


def test_synthetic_prefix_filter():
    assert ev.is_synthetic("e2e-jrn-b3-4-123")
    assert ev.is_synthetic("eval-mode-1")
    assert not ev.is_synthetic("demo-i9c92i")     # 真机演示会话是真实数据，保留
    assert not ev.is_synthetic("")


def test_fallback_speech_patterns():
    assert ev.match_fallback("抱歉，处理失败。")
    assert ev.match_fallback("抱歉，我没听清您想让我做什么，可以换个说法吗。")
    assert not ev.match_fallback("好的，空调已打开")
    assert not ev.match_fallback("")


def test_restatement_similar_within_window():
    turns = [
        {"trace_id": "t1", "session_id": "s", "ts": 1000, "user_text": "打开主驾座椅加热"},
        {"trace_id": "t2", "session_id": "s", "ts": 5000, "user_text": "打开主驾驶座椅加热"},
    ]
    assert ev.find_restatements(turns) == {"t1"}


def test_restatement_ignores_unrelated_and_out_of_window():
    turns = [
        {"trace_id": "t1", "session_id": "s", "ts": 1000, "user_text": "打开空调"},
        {"trace_id": "t2", "session_id": "s", "ts": 5000, "user_text": "今天天气怎么样"},
        {"trace_id": "t3", "session_id": "s2", "ts": 1000, "user_text": "导航去公司"},
        {"trace_id": "t4", "session_id": "s2", "ts": 1000 + 61_000, "user_text": "导航去公司啊"},
    ]
    assert ev.find_restatements(turns) == set()   # 不相似 / 超 60s 窗都不算


def test_restatement_identical_not_counted():
    turns = [  # 完全同句（如按钮重发）不算重述
        {"trace_id": "t1", "session_id": "s", "ts": 1000, "user_text": "确认"},
        {"trace_id": "t2", "session_id": "s", "ts": 2000, "user_text": "确认"},
    ]
    assert ev.find_restatements(turns) == set()


def test_triage_parse_closed_set_and_truncation():
    batch = [{"user_text": "a"}, {"user_text": "b"}, {"user_text": "c"}]
    raw = ('前置噪声 [{"id":1,"cause":"route_error","note":"' + "长" * 200 +
           '","confidence":0.9},'
           '{"id":2,"cause":"hack_the_system","note":"越界类目","confidence":2.5}] 尾噪')
    out = ev.parse_triage_reply(raw, batch)
    assert len(out) == 3
    assert out[0]["cause"] == "route_error"
    assert len(out[0]["cause_note"]) <= 120          # 治理②：自由文本硬截断
    assert out[1]["cause"] == "unknown"              # 封闭集外 → unknown，不发明类目
    assert out[1]["confidence"] == 1.0               # 越界 confidence 夹紧
    assert out[2]["cause"] == "unknown"              # id 缺位 → unknown 兜底


def test_triage_parse_garbage_reply():
    out = ev.parse_triage_reply("完全不是 JSON", [{"user_text": "a"}])
    assert len(out) == 1 and out[0]["cause"] == "unknown"


def test_proposal_forbidden_whitelist():
    """治理③：修改面白名单——涉 VAL/权限/确认/payment/policy 的建议拒绝产结构化草案。"""
    assert ev.forbidden_hit("route_hints for require_confirm capability")
    assert ev.forbidden_hit("修改 payment 流程")
    assert ev.forbidden_hit("skills/policies/freshness.yaml")
    assert not ev.forbidden_hit("pattern: (?:导航|路线)  intent: navigation.navigate_to")


def test_kw_pattern_extracts_repeated_words():
    p = ev._kw_pattern(["打开座椅加热", "帮我把座椅加热开一下", "座椅加热打开"])
    assert "座椅加热" in p or "座椅" in p
    assert ev._kw_pattern(["唯一一句"]) == "TODO"     # 无重复词 → 留 TODO 不硬造
