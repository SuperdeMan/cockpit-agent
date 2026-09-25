"""后视镜加热：原话 → 意图 → 对象 / 操作 → VAL → 话术，五段都要通（评审四轮待办，2026-09-25）。

修前：`commands.yaml` 的后视镜 modes 早就声明了 heating，端侧却只有折叠 / 展开——

- 端侧规则的后视镜分支只认 折叠 / 收 / 展开 / 打开，「右侧后视镜加热打开」里的「打开」被接成 `rear_view_mirror.unfold`；
- VAL 的模拟分支把 `operate == "open"` 一律当展开，云侧哪天派下 `rear_view_mirror.heating.open` 也会执行成展开、念「后视镜已展开」；
- 「打开后视镜除雾 / 后视镜除霜」落到前挡除雾——开错了另一个物理开关。

加热与折叠 / 展开共用 open / close 两个 operate，区分只在 `mode == "heating"`：规则、命名、VAL 模拟、话术键四处都先判加热。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from edge_call import decode_intent  # noqa: E402
from fast_intent import classify_structured, is_local, structured_to_legacy  # noqa: E402
from val import VAL  # noqa: E402


def _parse(text):
    structured = classify_structured(text)
    assert structured is not None, text
    data = structured["data"]
    return structured, (data.get("object"), data.get("operate"), data.get("mode"), data.get("positions"))


@pytest.mark.parametrize("text, operate, positions", [
    ("右侧后视镜加热打开", "open", ["右侧"]),          # 修前 rear_view_mirror.unfold
    ("打开后视镜加热", "open", None),
    ("打开左侧后视镜加热", "open", ["左侧"]),
    ("关闭后视镜加热", "close", None),
    ("后视镜加热关掉", "close", None),
    ("打开后视镜除雾", "open", None),                  # 修前 front_defogger.open
    ("后视镜除霜", "open", None),
    ("关闭后视镜除雾", "close", None),
])
def test_mirror_heating_is_parsed_named_and_local(text, operate, positions):
    structured, parsed = _parse(text)
    assert parsed == ("rear_view_mirror", operate, "heating", positions)
    name = structured_to_legacy(structured)["name"]
    assert name == f"rear_view_mirror.heating.{operate}"
    assert is_local(name)


def test_the_edge_path_heats_and_leaves_the_mirror_unfolded():
    val = VAL()
    structured, _ = _parse("右侧后视镜加热打开")
    ok, _speech = val.execute(structured, confirmed=True)
    assert ok and val.state["rear_view_mirror_heating"] is True
    assert val.state["rear_view_mirror"] == "unfolded"
    off, _ = _parse("关闭后视镜加热")
    ok, _speech = val.execute(off, confirmed=True)
    assert ok and val.state["rear_view_mirror_heating"] is False
    assert val.state["rear_view_mirror"] == "unfolded"          # 修前 operate=close ⇒ 折叠


@pytest.mark.parametrize("intent, heating", [
    ("rear_view_mirror.heating.open", True), ("rear_view_mirror.heating.close", False)])
def test_the_cloud_path_heats_too(intent, heating):
    """云侧派下来的意图走 `decode_intent` → VAL；修前 VAL 把 open 一律当展开。"""
    val = VAL()
    val.state["rear_view_mirror_heating"] = not heating
    structured = decode_intent(intent)
    assert structured["data"] == {"object": "rear_view_mirror", "operate": intent.rsplit(".", 1)[1], "mode": "heating"}
    ok, _speech = val.execute(structured, confirmed=True)
    assert ok and val.state["rear_view_mirror_heating"] is heating
    assert val.state["rear_view_mirror"] == "unfolded"


def test_the_full_speech_names_heating_and_the_position():
    """详细档（`detailed`）与多意图合并播报念全句；修前念的是「右侧后视镜已展开」。"""
    val = VAL()
    structured, _ = _parse("右侧后视镜加热打开")
    ok, speech = val.execute(structured, answer_length="detailed", confirmed=True)
    assert ok and speech == "右侧后视镜加热已打开"
    structured, _ = _parse("关闭后视镜加热")
    ok, speech = val.execute(structured, answer_length="detailed", confirmed=True)
    assert ok and speech == "后视镜加热已关闭"


@pytest.mark.parametrize("text, name, state", [
    ("打开后视镜", "rear_view_mirror.unfold", "unfolded"),
    ("折叠后视镜", "rear_view_mirror.fold", "folded"),
    ("展开右侧后视镜", "rear_view_mirror.unfold", "unfolded"),
])
def test_fold_and_unfold_are_unchanged(text, name, state):
    val = VAL()
    val.state["rear_view_mirror"] = "folded" if state == "unfolded" else "unfolded"
    structured, _ = _parse(text)
    assert structured_to_legacy(structured)["name"] == name
    ok, _speech = val.execute(structured, confirmed=True)
    assert ok and val.state["rear_view_mirror"] == state
    assert val.state["rear_view_mirror_heating"] is False


def test_windshield_defog_is_unchanged():
    assert _parse("打开前挡除雾")[1][0] == "front_defogger"
    assert _parse("打开后挡风玻璃除雾")[1][0] == "rear_defogger"


def _gate():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    spec = importlib.util.spec_from_file_location("_capability_gate", os.path.join(root, "test", "eval_capability_integrity.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_mode_fidelity_lane_is_green_on_the_real_knowledge():
    gate = _gate()
    reach, _orphans, _objects = gate._reachable()
    assert gate.lane_mode_fidelity(reach, {}) == []


def test_the_mode_fidelity_lane_catches_the_pre_fix_mirror(monkeypatch):
    """话术 / 验证两条车道只查键存在：修前加热意图解成 `rear_view_mirror_unfold_success` 与 `rear_view_mirror` 状态，两条都绿。
    把 VAL 退回修前那两处（加热不单独判），模式保真车道恰好报出这两个意图。"""
    gate = _gate()
    real_key, real_sim = VAL._build_response_key, VAL._simulate

    def pre_fix_key(self, obj, operate, data):
        if obj == "rear_view_mirror" and (data or {}).get("mode") == "heating":
            return "rear_view_mirror_unfold_success" if operate == "open" else "rear_view_mirror_fold_success"
        return real_key(self, obj, operate, data)

    def pre_fix_sim(self, obj, operate, data):
        if obj == "rear_view_mirror" and (data or {}).get("mode") == "heating":
            return ("rear_view_mirror", "unfolded" if operate == "open" else "folded")
        return real_sim(self, obj, operate, data)

    monkeypatch.setattr(VAL, "_build_response_key", pre_fix_key)
    monkeypatch.setattr(VAL, "_simulate", pre_fix_sim)
    reach, _orphans, _objects = gate._reachable()
    errs = gate.lane_mode_fidelity(reach, {})
    assert len(errs) == 2 and all("rear_view_mirror.heating." in e for e in errs), errs
