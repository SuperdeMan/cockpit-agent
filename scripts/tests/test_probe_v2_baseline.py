"""The baseline must expose product failures without confusing missing evidence with success."""
import json
import asyncio
import hashlib

import pytest
import yaml

from scripts import probe_v2_baseline as probe


def obs(**kwargs):
    return {"speech": "空调有自动模式", "actions": [], "card_text": "{}", **kwargs}


def test_planned_manual_is_not_dispatched_or_presented():
    detail = {"turn": {"intents": "manual.query"}, "spans": [
        {"node": "cloud.planning", "attrs": {"plan": "manual.query"}}]}
    result = probe.judge({"manual": True}, obs(), detail)
    assert result["failures"] == ["manual_not_dispatched", "manual_not_presented"]


def test_successful_agent_without_card_is_a_presentation_failure():
    detail = {"spans": [{"node": "agent.call", "attrs": {"agent_id": "manual-rag"}}]}
    result = probe.judge({"manual": True}, obs(), detail)
    assert result["manual_dispatched"]
    assert result["failures"] == ["manual_not_presented"]


def test_manual_in_group_is_visible_but_action_still_fails():
    detail = {"spans": [{"attrs": {"agent": "manual-rag"}}]}
    card = {"type": "card_group", "items": [{"type": "weather"}, {"type": "manual"}]}
    result = probe.judge({"manual": True}, obs(card_text=json.dumps(card), actions=["hvac.on"]), detail)
    assert result["failures"] == ["unexpected_action"]


@pytest.mark.parametrize("changes", [{"need_confirm": True}, {"operation_id": "op-1"}])
def test_confirmation_requires_both_visible_prompt_and_address(changes):
    assert "pending_missing" in probe.judge({"need_confirm": True}, obs(**changes), {})["failures"]


def test_internal_error_cannot_pass_as_speech():
    assert "technical_failure" in probe.judge({}, obs(speech="Agent 内部错误：RuntimeError"), {})["failures"]


def test_an_unrequested_confirmation_is_not_success_even_without_actions():
    assert probe.judge({}, obs(need_confirm=True, operation_id="op-1"), {})["failures"] == ["unexpected_confirmation"]


def test_freeze_refuses_dirty_inputs(monkeypatch):
    monkeypatch.setattr(probe, "_git", lambda *args: " M runtime/x.py")
    with pytest.raises(ValueError, match="clean"):
        probe.freeze("a"*40, "minimax", "MiniMax-M3")


def test_freeze_checks_only_the_inputs_of_the_run(monkeypatch):
    """共享工作树里别的会话未提交的文档不挡基线：状态只按跑数输入的路径查（2026-10-09 实测被两份文档挡住）。"""
    calls = []

    def git(*args):
        calls.append(args)
        return "" if args[0] == "status" else "0" * 40

    monkeypatch.setattr(probe, "_git", git)
    monkeypatch.setattr(probe, "load_cases", lambda corpus=probe.CORPUS: [])
    try:
        probe.freeze("a" * 40, "minimax", "MiniMax-M3")
    except Exception:
        pass                                        # 只看状态查询的参数，后面的组装不在本用例
    status = next(c for c in calls if c[0] == "status")
    assert status[:3] == ("status", "--porcelain", "--") and set(status[3:]) == set(probe.FREEZE_INPUTS)


def test_freeze_inputs_cover_what_the_runner_imports_and_reads():
    """运行器导入的仓库模块、读取的语料与来源证据表都要落在冻结输入里——新加一处 `agents/` 导入而忘了算进去，这里会红。"""
    import json
    import os
    import subprocess
    import sys

    # 独立进程里只导入运行器：同一测试进程里别的用例早已导入的 agents/、orchestrator/ 不能算成运行器的输入
    root = os.path.abspath(probe.ROOT)
    program = (
        "import json, os, sys\n"
        f"root = {root!r}\n"
        "sys.path[:0] = [root, os.path.join(root, 'gen', 'python')]\n"
        "import scripts.probe_v2_baseline\n"
        "files = [getattr(m, '__file__', '') or '' for m in list(sys.modules.values())]\n"
        "print(json.dumps(sorted({os.path.relpath(f, root).replace(os.sep, '/') for f in files"
        " if f and os.path.abspath(f).startswith(root)})))\n")
    out = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=120, check=True)
    used = set(json.loads(out.stdout.strip().splitlines()[-1]))
    used |= {probe.CORPUS.relative_to(probe.ROOT).as_posix(), probe.SOURCE_EVIDENCE.relative_to(probe.ROOT).as_posix()}
    # 清单本身与它引用的每个来源（v2 语料、7 月旅程）同样是跑数输入
    used.add(probe.MANIFEST.relative_to(probe.ROOT).as_posix())
    used |= {"test/" + e["source"] for e in yaml.safe_load(probe.MANIFEST.read_text(encoding="utf-8"))["journeys"]}
    outside = sorted(u for u in used if not any(u == i or u.startswith(i + "/") for i in probe.FREEZE_INPUTS))
    assert not outside, outside


# ── 核心旅程清单（docs/design/2026-10-09-v2-core-journey-freeze.md）──────────────────────────────

def test_the_manifest_resolves_every_reference_exactly_once():
    cases = probe.load_manifest()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) >= 30
    assert {c["family"] for c in cases} <= probe.FAMILIES and {c["lane"] for c in cases} <= set(probe.LANES)
    v2 = next(c for c in cases if c["id"] == "V201")
    assert v2["source_family"] == "mixed_answer_pending" and v2["family"] == "F02"   # 分类以清单为准，来源分类留着
    weather = next(c for c in cases if c["id"] == "B1-4")
    assert weather["source_kind"] == "journey" and weather["location"] == {"lat": "22.5333", "lng": "113.9505"}
    assert weather["turns"][1]["expect"]["speech_not"]            # 7 月判据原样带过来，由共用模块判
    # v2 运行器不支持的 7 月原语记进 unsupported（跑数时整条记「未测」）：跳过条件、过程区事件
    by_id = {c["id"]: c for c in cases}
    assert by_id["A5-2"]["unsupported"] == ["skip_journey_if_speech_any"]
    assert by_id["A3-2"]["unsupported"] == ["expect.process_min"]
    assert "unsupported" not in weather


def test_the_manifest_refuses_a_journey_that_would_confirm(tmp_path):
    """7 月 A7-1 停车缴费第二轮是 `say: 确认`：静态护栏挡下（支付本来就不进冻结集）。"""
    manifest = tmp_path / "m.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "split": "regression", "journeys": [
        {"id": "A7-1", "source": "journeys/target_a.yaml", "family": "F08", "lane": "read_only"}]},
        allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="affirmation"):
        probe.load_manifest(manifest)


@pytest.mark.parametrize("entry, message", [
    ({"id": "V201", "source": "eval_corpus/v2_runtime/seed.yaml", "family": "F99", "lane": "read_only"}, "family"),
    ({"id": "V201", "source": "eval_corpus/v2_runtime/seed.yaml", "family": "F02", "lane": "anything"}, "lane"),
    ({"id": "NOPE", "source": "eval_corpus/v2_runtime/seed.yaml", "family": "F02", "lane": "read_only"}, "exactly once"),
    ({"id": "V201", "source": "../scripts/probe_v2_baseline.py", "family": "F02", "lane": "read_only"}, "under test/"),
])
def test_the_manifest_rejects_bad_entries(tmp_path, entry, message):
    manifest = tmp_path / "m.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "split": "regression", "journeys": [entry]}), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        probe.load_manifest(manifest)


def test_case_meta_only_takes_a_voice_input_source():
    """条目级 meta 下发给编排：只收语音来源（F12 受话与拒识），别的键、文字来源一律拒绝。"""
    base = {"id": "X", "family": "addressing", "turns": [{"say": "现在几点了"}]}
    probe.validate_case({**base, "meta": {"input_source": "voice_followup"}})
    probe.validate_case({**base, "meta": {"input_source": "ptt"}})
    with pytest.raises(ValueError, match="voice source"):
        probe.validate_case({**base, "meta": {"input_source": "text"}})
    with pytest.raises(ValueError, match="only takes input_source"):
        probe.validate_case({**base, "meta": {"current_lat": "22.5"}})


def test_the_p1_corpora_cover_their_families_and_stay_read_only():
    """P1 只读家族（设计 §3）：五个家族都在；F03 全部进安全必过集；F02 起头的命令都要确认（停在挂起）；F12 全按语音来源发。"""
    cases = {c["id"]: c for c in probe.load_manifest()}
    by_family = {}
    for c in cases.values():
        by_family.setdefault(c["family"], []).append(c)
    assert {f for f in ("F01", "F02", "F03", "F08", "F12")} <= set(by_family)
    assert all(c["safety"] == "must_pass" for c in by_family["F03"])
    pending_starts = [c for c in by_family["F02"] if c["id"].startswith("P")]
    assert pending_starts and all(c["turns"][0]["expect"].get("need_confirm") for c in pending_starts)
    voice = [c for c in by_family["F12"]]
    assert voice and all((c.get("meta") or {}).get("input_source") == "voice_followup" for c in voice)
    assert cases["R02"]["known_red"]                          # 文本上分不出的乘客转述，照跑照报


def test_journey_keys_are_judged_by_the_shared_module_once():
    card = {"type": "manual", "items": []}
    result = probe.judge({"speech_not": ["没找到"], "cards_any": ["weather"], "need_confirm": True},
                         obs(speech="没找到相关内容", card_text=json.dumps(card)), {}, elapsed_s=1.0)
    journey = [f for f in result["failures"] if f.startswith("journey:")]
    assert any("speech_not" in f for f in journey) and any("cards_any" in f for f in journey)
    # 确认挂起只由 v2 那条判（它还要求 operation_id），不再由共用模块重复报一遍
    assert "pending_missing" in result["failures"] and not any("need_confirm" in f for f in journey)


def test_skip_reasons_record_lanes_unsupported_primitives_and_named_providers():
    case = {"lane": "read_only", "providers": ["AMAP_KEY"]}
    assert probe._skip_reason(case, {"read_only"}, set()) == ""
    assert probe._skip_reason(case, {"read_only"}, {"AMAP_KEY"}) == "provider:AMAP_KEY"
    assert probe._skip_reason({**case, "lane": "simulated_vehicle"}, {"read_only"}, set()) == "lane:simulated_vehicle"
    assert probe._skip_reason({**case, "unsupported": ["confirm"]}, {"read_only"}, set()) == "unsupported:confirm"


def test_inputs_unchanged_ignores_commits_outside_the_inputs(monkeypatch):
    """共享 main 上别的会话提交别处不算「跑数期间运行器变了」；跑数输入有改动或未提交才算。"""
    answers = {"status": "", "diff": ""}
    calls = []

    def git(*args):
        calls.append(args)
        return answers[args[0]]

    monkeypatch.setattr(probe, "_git", git)
    assert probe._inputs_unchanged("a" * 40)
    assert all(args[-len(probe.FREEZE_INPUTS):] == probe.FREEZE_INPUTS for args in calls)
    answers["diff"] = "scripts/probe_v2_baseline.py"
    assert not probe._inputs_unchanged("a" * 40)
    answers.update(diff="", status=" M test/eval_corpus/v2_runtime/seed.yaml")
    assert not probe._inputs_unchanged("a" * 40)


def test_a_journey_meets_the_bar_only_when_every_run_passes():
    good = {"verdict": {"failures": []}, "evidence_errors": []}
    bad = {"verdict": {"failures": ["journey:speech_not 命中禁词"]}, "evidence_errors": []}
    runs = [{"id": "J1", "family": "F01", "stop": False, "rows": [good, good]},
            {"id": "J1", "family": "F01", "stop": False, "rows": [good, bad]},
            {"id": "J2", "family": "F08", "stop": False, "rows": [good, {"skipped": "no_pending"}]},
            {"id": "J2", "family": "F08", "stop": False, "rows": [good]}]
    summary = probe.journey_summary(runs, 2)
    assert summary["journeys"]["J1"] == {"family": "F01", "runs": 2, "passes": 1}
    assert summary["journeys"]["J2"]["passes"] == 2 and summary["journeys_all_pass"] == 1


def test_recursive_redaction_keeps_image_evidence_without_payload():
    source = {"items": [{"images": [{"data_uri": "data:private", "sha256": "proof"}]}]}
    result = probe._redact(source)
    assert result["items"][0]["images"][0] == {"data_uri": "[image:12 chars]", "sha256": "proof"}
    assert source["items"][0]["images"][0]["data_uri"] == "data:private"


def test_seed_is_regression_and_never_automatically_confirms():
    cases = probe.load_cases()
    assert len(cases) == 20
    assert len({c["family"] for c in cases}) >= 6
    assert not {"merchant.write", "payment.invoke"} & set(probe.SCOPES)
    assert all(t["say"] == "取消" for c in cases for t in c["turns"] if t.get("cancel_pending"))


def test_full_state_probe_does_not_drop_a_new_vehicle_signal(monkeypatch):
    state = {"trunk": "closed", "rear_view_mirror_heating": False}
    async def read(_):
        return dict(state)
    monkeypatch.setattr(probe.audit, "_vehicle_state", read)
    good = asyncio.run(probe.audit._settled_vehicle_state(
        "stub", attempts=2, expected=state, include_unmanaged=True))
    assert good.settled and good.value == state
    wrong = asyncio.run(probe.audit._settled_vehicle_state(
        "stub", attempts=2, expected={**state, "rear_view_mirror_heating": True}, include_unmanaged=True))
    assert not wrong.settled
    assert wrong.reachable and not wrong.missing


@pytest.fixture
def source_card(tmp_path, monkeypatch):
    source = "仅在压力恢复且显示未更新时才复位；仍报警则停止并检查。"
    document = {"document_id": "synthetic", "source_sha256": "a"*64, "content_sha256": "b"*64}
    resource = {"schema_version": 1, "documents": {"synthetic": {
        "source_sha256": "a"*64, "content_sha256": "b"*64,
        "guards": [{"id": "guard", "subjects": ["胎压"], "parts": [{
            "id": "condition", "label": "原文", "page": 1, "start": 0, "end": len(source),
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
        }]}]}}}
    path = tmp_path / "source.yaml"
    path.write_text(yaml.safe_dump(resource, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(probe, "SOURCE_EVIDENCE", path)
    card = {"type": "manual", "_prov": {"mode": "real"}, "document": document,
            "chunks": [{"page_start": 1, "content": source}]}
    return card, source


def test_source_probe_compares_whole_condition_instead_of_shared_keywords(source_card):
    card, source = source_card
    detail = {"spans": [{"attrs": {"agent_id": "manual-rag"}}], "llm_calls": []}
    good = probe.judge({"manual": True, "source_evidence": True},
                       obs(speech=source, card_text=json.dumps(card)), detail, query="胎压灯该如何复位？")
    assert not good["failures"]
    bad = probe.judge({"manual": True, "source_evidence": True},
                      obs(speech=source.replace("且显示未更新", ""), card_text=json.dumps(card)),
                      detail, query="胎压灯该如何复位？")
    assert bad["failures"] == ["source_condition_lost:condition"]


@pytest.mark.parametrize("mutation", ["hash", "source", "model_rewrite"])
def test_source_probe_rejects_unproven_or_regenerated_evidence(source_card, mutation):
    card, source = source_card
    detail = {"llm_calls": []}
    if mutation == "hash":
        card["document"]["source_sha256"] = "c"*64
    elif mutation == "source":
        card["chunks"][0]["content"] = "无需任何前提，直接复位。"
    else:
        detail["llm_calls"] = [{"caller": "manual-rag"}]
    assert probe.judge_source_evidence("胎压灯复位？", obs(speech=source), detail, card)


def test_custom_source_corpus_is_read_only_and_does_not_change_frozen_twenty():
    from scripts.probe_manual_rag_full_coverage import validate_live_query_safety
    corpus = probe.CORPUS.with_name("manual_source_evidence.yaml")
    cases = probe.load_cases(corpus)
    result = validate_live_query_safety([
        {"id": case["id"], "query": turn["say"]} for case in cases for turn in case["turns"]])
    assert result["fast_intent_none"] == 6
    assert all(turn["expect"]["source_evidence"] for case in cases for turn in case["turns"])
    assert len(probe.load_cases()) == 20


def test_image_assertion_rejects_an_illustration_in_place_of_the_controlled_icon():
    expected = {"image_assets": ["manual:p192:warning"]}
    card = {"type": "manual", "images": [{"asset_id": "manual:p257:illustration"}]}
    assert probe.judge(expected, obs(card_text=json.dumps(card)), {})["failures"] == [
        "manual_image_missing:manual:p192:warning"]
    card["images"] = [{"asset_id": "manual:p192:warning"}]
    assert not probe.judge(expected, obs(card_text=json.dumps(card)), {})["failures"]


def test_source_visual_questions_pass_the_unchanged_read_only_preflight():
    from scripts.probe_manual_rag_full_coverage import validate_live_query_safety
    cases = probe.load_cases(probe.CORPUS.with_name("manual_source_visual.yaml"))
    result = validate_live_query_safety([
        {"id": case["id"], "query": turn["say"]} for case in cases for turn in case["turns"]])
    assert result["fast_intent_none"] == 2
