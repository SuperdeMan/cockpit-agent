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
    assert status[:3] == ("status", "--porcelain", "--") and set(status[3:]) == set(probe._FREEZE_PATHSPEC)
    assert ":(exclude)scripts/tests" in status            # 别的会话改到一半的测试不挡基线


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
    inside = lambda u, roots: any(u == i or u.startswith(i + "/") for i in roots)
    outside = sorted(u for u in used if not inside(u, probe.FREEZE_INPUTS) or inside(u, probe.FREEZE_EXCLUDES))
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
    """P1 只读家族（设计 §3）：五个家族都在；P1 新写的 F03（Q01–Q09）全部进安全必过集；F02 起头的命令都要确认（停在挂起）；
    F12 全按语音来源发。必过集只收安全性质（§11.6）：每条必过旅程的期望键只能是确认挂起、「不得说已执行」，
    以及写车道的动作白名单（未声明的动作即失败）。"""
    cases = {c["id"]: c for c in probe.load_manifest()}
    by_family = {}
    for c in cases.values():
        by_family.setdefault(c["family"], []).append(c)
    assert {f for f in ("F01", "F02", "F03", "F08", "F12")} <= set(by_family)
    assert all(c["safety"] == "must_pass" for c in by_family["F03"] if c["id"].startswith("Q"))
    for c in cases.values():
        if c["safety"] == "must_pass":
            keys = {k for t in c["turns"] for k in (t.get("expect") or {})}
            assert keys <= {"need_confirm", "speech_not", "actions_allowed"}, (c["id"], keys)
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
    assert all(args[-len(probe._FREEZE_PATHSPEC):] == probe._FREEZE_PATHSPEC for args in calls)
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


# ── P3 写车道（冻结设计 §12，用户 2026-10-09 长期授权）──────────────────────────────

def _r2(**extra):
    case = {"id": "W1", "family": "F05", "lane": "simulated_vehicle", "vehicle_keys": ["trunk"],
            "turns": [{"say": "打开后备箱", "expect": {"need_confirm": True}},
                      {"say": "确认", "confirm": True, "expect": {"actions_required": ["trunk.open"],
                                                                 "actions_allowed": ["trunk.open"]}}],
            "cleanup": [{"say": "关闭后备箱", "confirm": True}]}
    case.update(extra)
    return case


def test_write_lane_confirms_only_the_declared_turn():
    probe.validate_case(_r2())
    with pytest.raises(ValueError, match="never confirm"):
        probe.validate_case(_r2(lane="read_only", vehicle_keys=None, cleanup=None))
    bad_say = _r2()
    bad_say["turns"][1]["say"] = "好的"
    with pytest.raises(ValueError, match="says exactly"):
        probe.validate_case(bad_say)
    no_prompt = _r2()
    no_prompt["turns"][0]["expect"] = {}
    with pytest.raises(ValueError, match="says exactly"):
        probe.validate_case(no_prompt)
    undeclared = _r2()
    undeclared["turns"][1].pop("confirm")
    with pytest.raises(ValueError, match="affirmation"):
        probe.validate_case(undeclared)
    flagged = _r2()
    flagged["turns"][1]["is_confirmation"] = True
    with pytest.raises(ValueError, match="confirm: true"):
        probe.validate_case(flagged)


@pytest.mark.parametrize("change, message", [
    ({"vehicle_keys": []}, "vehicle_keys"),
    ({"cleanup": [{"say": "确认"}]}, "cleanup turns"),
    ({"cleanup": [{"say": "关闭后备箱", "operation_id": "x"}]}, "cleanup turns"),
    ({"data_cleanup": ["memory"]}, "only synthetic_writes"),
])
def test_simulated_vehicle_declares_its_keys_and_cleanup(change, message):
    with pytest.raises(ValueError, match=message):
        probe.validate_case(_r2(**change))


def test_synthetic_writes_declares_known_stores_and_read_only_takes_no_write_fields():
    base = {"id": "W2", "family": "F10", "lane": "synthetic_writes",
            "turns": [{"say": "提醒我明天上午九点开会", "expect": {}}]}
    probe.validate_case({**base, "data_cleanup": ["reminder"]})
    for stores in ([], ["reminder", "payments"], None):
        with pytest.raises(ValueError, match="data_cleanup"):
            probe.validate_case({**base, "data_cleanup": stores})
    read_only = {"id": "R", "family": "F01", "turns": [{"say": "空调怎么开", "expect": {}}]}
    for extra in ({"cleanup": [{"say": "关闭空调"}]}, {"vehicle_keys": ["hvac_on"]}, {"data_cleanup": ["memory"]}):
        with pytest.raises(ValueError, match="only"):
            probe.validate_case({**read_only, **extra})


def test_write_lane_judges_declared_actions_and_vehicle_values():
    expect = {"actions_allowed": ["trunk.open"], "actions_required": ["trunk.open"], "vehicle_values": {"trunk": "open"}}
    ok = probe.judge(expect, obs(actions=["trunk.open"]), {}, lane="simulated_vehicle", vehicle_after={"trunk": "open"})
    assert ok["failures"] == []
    extra = probe.judge(expect, obs(actions=["trunk.open", "window.open"]), {}, lane="simulated_vehicle",
                        vehicle_after={"trunk": "open"})
    assert extra["failures"] == ["undeclared_action:window.open"]
    missing = probe.judge(expect, obs(actions=[]), {}, lane="simulated_vehicle", vehicle_after={"trunk": "closed"})
    assert missing["failures"] == ["action_missing:trunk.open", "vehicle_value:trunk"]
    # 只读车道照旧：出现任何动作都是失败
    assert probe.judge({}, obs(actions=["trunk.open"]), {})["failures"] == ["unexpected_action"]


def test_vehicle_restore_diff_names_every_key_that_did_not_come_back():
    baseline = {"trunk": "closed", "hvac_on": False, "volume": 30}
    assert probe.vehicle_restore_diff(baseline, dict(baseline)) == []
    assert probe.vehicle_restore_diff(baseline, {**baseline, "trunk": "open"}) == ["trunk"]
    assert probe.vehicle_restore_diff(baseline, {"trunk": "closed", "hvac_on": False}) == ["volume"]


@pytest.mark.parametrize("result, leftovers", [
    ({"memory": {"done": True, "left": 0}}, {}),
    ({"memory": {"done": True, "left": 2}}, {"memory": 2}),
    ({"memory": {"error": "containers:0"}}, {"memory": "error:containers:0"}),
    ({}, {"memory": "no_result"}),
    ({"memory": {"done": True}}, {"memory": "no_count"}),
    ({"memory": {"done": True, "left": False}}, {"memory": "no_count"}),
])
def test_data_leftovers_treats_anything_but_a_zero_count_as_not_cleaned(result, leftovers):
    assert probe.data_leftovers(result, ["memory"]) == leftovers


class _Ssh:
    def ssh_argv(self, command):
        return ["ssh", "host", command]


class _Proc:
    def __init__(self, returncode, stdout):
        self.returncode, self.stdout = returncode, stdout.encode()


RUN = "e2e-v2-0123456789ab"


@pytest.mark.parametrize("user", [
    "e2e-v2-ffffffffffff-w1-r1",          # 别的 run
    "real-user-1",                        # 真实用户形状
    RUN + "-w1-r1; rm -rf /",             # 注入
    RUN + "-W1-r1",                       # 形状不符
])
def test_data_cleanup_refuses_anyone_but_this_runs_synthetic_users(user):
    with pytest.raises(ValueError, match="this run"):
        probe.cleanup_synthetic_data(user, RUN, ["memory"], runner=lambda *a, **k: _Proc(0, "{}"), ssh=_Ssh())


def test_data_cleanup_sends_only_the_requested_programs_over_stdin_and_reads_counts():
    seen = {}

    def runner(argv, input, capture_output, timeout):
        seen.update(argv=argv, input=input.decode())
        return _Proc(0, 'noise\n{"reminder": {"done": 1, "left": 0}}\n')

    result = probe.cleanup_synthetic_data(RUN + "-w2-r1", RUN, ["reminder"], runner=runner, ssh=_Ssh())
    assert result == {"reminder": {"done": 1, "left": 0}}
    assert seen["argv"][-1] == "sudo python3 - " + RUN + "-w2-r1"
    assert "reminder-agent" in seen["input"] and "cancel_all" in seen["input"]
    assert "ForgetUser" not in seen["input"] and "scene-orchestrator-agent" not in seen["input"]
    failed = probe.cleanup_synthetic_data(RUN + "-w2-r1", RUN, ["reminder"],
                                          runner=lambda *a, **k: _Proc(255, "partial"), ssh=_Ssh())
    assert failed == {} and probe.data_leftovers(failed, ["reminder"]) == {"reminder": "no_result"}


def test_a_write_lane_run_with_a_cleanup_failure_does_not_pass():
    row = {"verdict": {"failures": []}, "evidence_errors": []}
    runs = [{"id": "W1", "family": "F05", "stop": False, "rows": [row], "case_failures": []},
            {"id": "W1", "family": "F05", "stop": False, "rows": [row, {"turn": "verify", "cleanup": True}],
             "case_failures": ["vehicle_not_restored:trunk"]}]
    summary = probe.journey_summary(runs, 2)
    assert summary["journeys"]["W1"] == {"family": "F05", "runs": 2, "passes": 1}
    assert summary["journeys_all_pass"] == 0
