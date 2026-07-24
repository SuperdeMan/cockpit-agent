"""M1b Shadow NLU 解析层单测（RFC §5-3）：批量回复 id 对位 / domain 白名单 / 截断 / 断点契约。"""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "shadow_nlu_under_test",
    os.path.join(os.path.dirname(__file__), "..", "..", "test", "eval_shadow_nlu.py"))
sn = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sn)

_BATCH = [{"idx": 10, "text": "a"}, {"idx": 11, "text": "b"}, {"idx": 12, "text": "c"}]


def test_parse_batch_id_alignment_and_whitelist():
    raw = ('[{"id":1,"domain":"setting","slots":{"object":"座椅"}},'
           '{"id":2,"domain":"made_up_domain","slots":{}},'
           '{"id":3,"domain":"navi","slots":"not-a-dict"}]')
    out = sn.parse_batch_reply(raw, _BATCH)
    assert [r["idx"] for r in out] == [10, 11, 12]
    assert out[0]["llm_domain"] == "setting" and out[0]["llm_slots"] == {"object": "座椅"}
    assert out[1]["llm_domain"] == "unknown"       # 白名单外 → unknown，不发明桶
    assert out[2]["llm_slots"] == {}               # slots 非 dict → 空


def test_parse_batch_missing_id_dropped_for_retry():
    """缺位条目不返回（不落盘）→ 断点续跑自动重试（RFC §2.2）。"""
    raw = '[{"id":1,"domain":"media","slots":{}}]'
    out = sn.parse_batch_reply(raw, _BATCH)
    assert [r["idx"] for r in out] == [10]


def test_parse_batch_garbage_and_slot_truncation():
    assert sn.parse_batch_reply("不是 JSON", _BATCH) == []
    raw = ('[{"id":1,"domain":"setting","slots":{'
           + ",".join(f'"k{i}":"{"v" * 200}"' for i in range(12)) + '}}]')
    out = sn.parse_batch_reply(raw, [{"idx": 0, "text": "a"}])
    assert len(out[0]["llm_slots"]) <= 8            # 键数截断
    assert all(len(v) <= 80 for v in out[0]["llm_slots"].values())  # 值长截断


def test_extract_json_array_from_noise():
    assert sn._extract_json_array('噪声 [1,2] 尾') == "[1,2]"
