"""M1b 自进化流水线单测（RFC §5-3）：mine 信号判定 / triage 解析安全面 / propose 白名单。
evolve.py 是脚本非包——按文件路径独名加载（同 llm-gateway tests 惯例，零 sys.modules 污染）。"""
import importlib.util
import os

import pytest

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


def test_exemplar_draft_replaces_regex_generator():
    """M5 P1：route_error/slot_error 的产物从正则骨架换成范例草案。
    `_kw_pattern`（bigram 拼 pattern）随之退役——**提案的产物形态**就是这一期要改的东西，
    留着它等于给规则工厂留了自动化入口。"""
    assert not hasattr(ev, "_kw_pattern")
    body = ev._exemplar_draft(["帮我看看附近有什么咖啡店", "附近有什么好吃的"], "2099-01-01")
    assert "exemplars:" in body and "source: trace" in body
    assert "帮我看看附近有什么咖啡店" in body
    assert "pattern" not in body and "guard" not in body      # 不含任何正则面字段


# ── P0 断点①修复（2026-07-28 数据飞轮 §2-①）：提案半环必须真闭合 ──────────────

def test_forbidden_word_boundary():
    """词边界：eval/validate 不再误伤 "val"；独立 VAL/文件名仍命中。"""
    assert not ev.forbidden_hit("原句已归 eval 语料候选")       # 历史自触发元凶
    assert not ev.forbidden_hit("validate the plan")
    assert not ev.forbidden_hit("granted_scopes 里没有")        # 下划线连接不算独立词
    assert ev.forbidden_hit("车控必须经 VAL 下发") == "val"
    assert ev.forbidden_hit("orchestrator/edge/val.py") == "val"
    assert ev.forbidden_hit("payment-gateway 补偿") == "payment"


def _args(date: str):
    class A:
        pass

    a = A()
    a.date = date
    return a


def test_propose_templates_do_not_self_trigger(tmp_path, monkeypatch):
    """回归锁：三类草案模板自带 "eval 语料候选"/"require_confirm" 样板文案，
    绝不能再触发治理③降级（上线四天 0 结构化提案的根因）。"""
    monkeypatch.setattr(ev, "_OUT_DIR", tmp_path)
    work = ev._work_dir("2099-01-01")
    rows = [
        {"cause": "route_error", "user_text": "帮我看看附近有什么咖啡店",
         "cause_note": "被视觉能力劫持"},
        {"cause": "route_error", "user_text": "附近有什么好吃的", "cause_note": ""},
        {"cause": "knowledge_gap", "user_text": "记住我喜欢乌龙茶",
         "cause_note": "未规划记忆写入工具"},
        {"cause": "slot_error", "user_text": "重新录一遍", "cause_note": "槽位误解析"},
        {"cause": "infra", "user_text": "今天天气怎么样", "cause_note": "网关超时"},
    ]
    ev._write_jsonl(work / "triaged.jsonl", rows)
    ev.cmd_propose(_args("2099-01-01"))
    proposals = ev._read_jsonl(work / "proposals.jsonl")
    kinds = {p["cause"]: p["kind"] for p in proposals}
    assert kinds["route_error"] == "exemplar"        # M5 P1：落域类提案产数据不产正则
    assert kinds["knowledge_gap"] == "guide"
    assert kinds["slot_error"] == "exemplar"
    assert kinds["infra"] == "report_only"           # 非可提案类仍纯报告
    assert (work / "proposals" / "exemplar-route_error.yaml").exists()
    assert (work / "proposals" / "guide-knowledge_gap.yaml").exists()
    assert (work / "proposals" / "exemplar-slot_error.yaml").exists()


def test_propose_dynamic_forbidden_still_bites(tmp_path, monkeypatch):
    """治理③仍有效：动态内容（归因 note）指向禁区面 → 降级纯报告并注明命中词。"""
    monkeypatch.setattr(ev, "_OUT_DIR", tmp_path)
    work = ev._work_dir("2099-01-02")
    rows = [{"cause": "route_error", "user_text": "帮我付停车费",
             "cause_note": "建议直接改 payment-gateway 的补偿流程"}]
    ev._write_jsonl(work / "triaged.jsonl", rows)
    ev.cmd_propose(_args("2099-01-02"))
    proposals = ev._read_jsonl(work / "proposals.jsonl")
    assert proposals[0]["kind"] == "report_only"
    assert "payment" in proposals[0]["note"]


def test_plan_mode_of_reads_sqlite_detail_shape():
    """P0 断点②：_plan_mode_of 消费 /api/turns/{id}（attrs 已是 dict），不再依赖内存环 repr。"""
    detail = {"spans": [
        {"node": "edge.fast", "attrs": {}},
        {"node": "cloud.planning",
         "attrs": {"plan_mode": "toolcall_degraded", "plan": "[{\"intent\": \"x\"}]"}},
    ]}
    mode, head = ev._plan_mode_of(detail)
    assert mode == "toolcall_degraded"
    assert head.startswith("[{")
    assert ev._plan_mode_of({"spans": [{"node": "a", "attrs": "junk"}]}) == ("", "")


# ── M5 P2 强模型影子分诊 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("prod,shadow,gold,expect", [
    # 有金标：三态可判
    ("nearby.search", "parking.pay", "parking.pay", "model_gap"),      # 换范例治不好
    ("a.x", "b.y", "c.z", "both_wrong"),                               # 信息/知识问题
    ("parking.pay", "parking.pay", "parking.pay", "both_right"),       # 落域没问题
    ("parking.pay", "nearby.search", "parking.pay", "prod_only_right"),# 强档反而更差
    # 无金标：**只报分歧，不假装能判对错**——这是这一步最容易骗自己的地方
    ("a.x", "a.x", "", "agrees"),
    ("a.x", "b.y", "", "diverges"),
    ("a.x,b.y", "b.y,a.x", "", "agrees"),                              # 集合比较不看顺序
    ("", "", "", "agrees"),
])
def test_shadow_verdict_table(prod, shadow, gold, expect):
    assert ev.shadow_verdict(prod, shadow, gold) == expect


def test_exemplar_draft_flags_model_gap():
    """model_gap 的案子投范例是治标——草案必须当面说清，否则人审会照单全投。"""
    body = ev._exemplar_draft(["出场怎么交钱"], "2099-01-01", model_gap=["出场怎么交钱"])
    assert "model_gap" in body and "P4" in body
    assert "model_gap" not in ev._exemplar_draft(["出场怎么交钱"], "2099-01-01")


def test_shadow_not_in_default_all_sequence():
    """shadow 要真栈 + 烧强档 token，默认不进 nightly（同 eval_skills live 的理由）。"""
    import inspect
    src = inspect.getsource(ev.main)
    assert "args.with_shadow" in src
