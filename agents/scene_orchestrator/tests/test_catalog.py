"""词表校验（catalog.py）单测 + **与 VAL/edge_call 的契约测试**。

契约测试是本模块存在的理由：0.1.0 的场景词表与 VAL commands.yaml 漂移，导致动作静默失效
（roadmap §8）。这里对着**真实的** edge_call.action_to_structured + VAL 跑一遍——凡 catalog
判为合法的动作，必须能被翻译且被 VAL 校验接受，否则测试红。
"""
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
# edge_call 内部 `from val import VAL`（端侧进程工作目录即 orchestrator/edge）——
# 与既有 orchestrator/edge/tests/* 同款做法，让契约测试能加载真实的翻译器 + VAL。
_EDGE = os.path.join(_ROOT, "orchestrator", "edge")
if _EDGE not in sys.path:
    sys.path.insert(0, _EDGE)

from agents.scene_orchestrator.src import catalog as C


@pytest.fixture(scope="module")
def cat():
    return C.load_catalog()


# ── 加载 ────────────────────────────────────────────────────────────────────

def test_load_catalog_from_repo(cat):
    assert "aircon" in cat.objects and "seat" in cat.objects
    assert cat.entities.get("positions"), "entities.yaml 未加载（位置校验会失效）"


def test_missing_catalog_raises(tmp_path, monkeypatch):
    """词表缺失必须诚实抛错——静默空词表会让所有动作被判非法，比崩溃更难查。

    模拟镜像内 Dockerfile 漏 COPY 词表的情形（本地开发有仓库回退路径，故压掉候选序）。
    """
    monkeypatch.setattr(C, "_knowledge_candidates", lambda p: [str(tmp_path)])
    with pytest.raises(C.CatalogError):
        C.load_catalog()


def test_empty_objects_raises(tmp_path, monkeypatch):
    (tmp_path / "commands.yaml").write_text("objects: {}\n", encoding="utf-8")
    monkeypatch.setattr(C, "_knowledge_candidates", lambda p: [str(tmp_path)])
    with pytest.raises(C.CatalogError):
        C.load_catalog()


# ── 动作校验 ────────────────────────────────────────────────────────────────

def test_valid_hvac(cat):
    ok, a, _ = C.validate_action(
        {"type": "vehicle.control", "command": "hvac.set",
         "params": {"temperature": "22"}}, cat)
    assert ok and a["params"]["temperature"] == "22"
    assert a["require_confirm"] is False


def test_hallucinated_object_rejected(cat):
    ok, _, reason = C.validate_action(
        {"type": "vehicle.control", "command": "massage.on", "params": {}}, cat)
    assert not ok and "massage" in reason


def test_hallucinated_param_dropped_with_note(cat):
    ok, a, note = C.validate_action(
        {"type": "vehicle.control", "command": "hvac.set",
         "params": {"temperature": "22", "wind": "3"}}, cat)
    assert ok and "wind" not in a["params"] and "wind" in note


def test_out_of_range_clamped(cat):
    ok, a, note = C.validate_action(
        {"type": "vehicle.control", "command": "hvac.set",
         "params": {"temperature": "99"}}, cat)
    assert ok and a["params"]["temperature"] == "32" and "32" in note


def test_danger_action_require_confirm_forced(cat):
    """§8.1：LLM 说 require_confirm=false 也强制改 true。"""
    ok, a, _ = C.validate_action(
        {"type": "vehicle.control", "command": "seat.recline",
         "params": {"position": "front_left", "angle": "160"}, "require_confirm": False}, cat)
    assert ok and a["require_confirm"] is True


def test_trunk_require_confirm(cat):
    ok, a, _ = C.validate_action(
        {"type": "vehicle.control", "command": "trunk.open", "params": {}}, cat)
    assert ok and a["require_confirm"] is True


def test_ambient_light_ok_not_danger(cat):
    ok, a, _ = C.validate_action(
        {"type": "vehicle.control", "command": "ambient_light.set",
         "params": {"brightness": "10", "color": "warm_white"}}, cat)
    assert ok and a["require_confirm"] is False
    assert a["params"] == {"brightness": "10", "color": "warm_white"}


def test_media_action_allowed_after_p1_4(cat):
    """P1.4：端侧 `_dispatch_cloud_actions` 已放开 media.control 回流 → 媒体动作合法，
    且 action.type 派生成 media.control（口径同 edge_call.action_type_for）。"""
    ok, a, _ = C.validate_action(
        {"type": "media.control", "command": "media.play", "params": {}}, cat)
    assert ok and a["type"] == "media.control"


def test_media_play_only_via_media_object(cat):
    """`music.play` 走不通——edge_call 把 play 归一成 start，而 music 没声明 start
    （VAL 实测「暂不支持哦」）。能起播的只有 media；digest 因此只推荐 media/radio。"""
    ok, _, reason = C.validate_action(
        {"type": "media.control", "command": "music.play", "params": {}}, cat)
    assert not ok and "play" in reason        # 报错用原词，不是归一后的 start
    digest = C.catalog_digest(cat)
    assert "- media(" in digest and "- music(" not in digest


def test_voice_forbidden_rejected(cat):
    ok, _, reason = C.validate_action(
        {"type": "vehicle.control", "command": "low_beam.open", "params": {}}, cat)
    assert not ok and "语音" in reason


def test_mode_in_vocabulary(cat):
    ok, a, _ = C.validate_action(
        {"type": "vehicle.control", "command": "hvac.set",
         "params": {"temperature": "22", "mode": "外循环"}}, cat)
    assert ok and a["params"]["mode"] == "外循环"


def test_hallucinated_mode_rejects_action(cat):
    ok, _, reason = C.validate_action(
        {"type": "vehicle.control", "command": "hvac.set",
         "params": {"mode": "蹦迪"}}, cat)
    assert not ok and "蹦迪" in reason


def test_setter_without_params_rejected(cat):
    """set 无参数 = 空操作（VAL 静默不生效）→ 剔除，别让用户以为做了。"""
    ok, _, _ = C.validate_action(
        {"type": "vehicle.control", "command": "volume.set", "params": {}}, cat)
    assert not ok


def test_operate_alias_on_open(cat):
    ok, a, _ = C.validate_action(
        {"type": "vehicle.control", "command": "fragrance.on", "params": {}}, cat)
    assert ok and a["command"] == "fragrance.on"


def test_navigate_action(cat):
    ok, a, _ = C.validate_action({"type": "navigate", "payload": {"destination": "家"}}, cat)
    assert ok and a["payload"]["destination"] == "家"
    ok2, _, _ = C.validate_action({"type": "navigate", "payload": {}}, cat)
    assert not ok2


def test_scene_mode_open_vocabulary(cat):
    """scene_mode 值域开放（用户可造场景，D1）——Agent 自己追加，任意场景键都合法。"""
    ok, a, _ = C.validate_action(
        {"type": "vehicle.control", "command": "scene_mode.set",
         "params": {"mode": "钓鱼模式"}}, cat)
    assert ok and a["params"]["mode"] == "钓鱼模式"
    ok2, _, _ = C.validate_action(
        {"type": "vehicle.control", "command": "scene_mode.set",
         "params": {"mode": "off"}}, cat)
    assert ok2


# ── 快照 / 恢复（D5）────────────────────────────────────────────────────────

def test_affected_state_keys():
    assert C.affected_state_keys(
        {"type": "vehicle.control", "command": "hvac.set", "params": {}}) == (
        "hvac_on", "hvac_temp", "hvac_wind_speed")
    assert C.affected_state_keys(
        {"type": "vehicle.control", "command": "seat.recline",
         "params": {"angle": "160"}}) == ("seat_recline",)
    assert C.affected_state_keys({"type": "navigate", "payload": {}}) == ()


def test_restore_from_snapshot():
    a = {"type": "vehicle.control", "command": "hvac.set", "params": {"temperature": "26"}}
    r, _ = C.restore_action(a, {"hvac_temp": 21, "hvac_on": True})
    assert r["command"] == "hvac.set" and r["params"]["temperature"] == "21"


def test_restore_falls_back_to_defaults_when_snapshot_missing():
    """快照缺键 → 反向默认表（D5）：hvac 24 / volume 50 / 氛围灯关 / 座椅复位 90。"""
    r, _ = C.restore_action(
        {"type": "vehicle.control", "command": "hvac.set", "params": {}}, {})
    assert r["params"]["temperature"] == "24"
    r, _ = C.restore_action(
        {"type": "vehicle.control", "command": "volume.set", "params": {}}, {})
    assert r["params"]["level"] == "50"
    r, _ = C.restore_action(
        {"type": "vehicle.control", "command": "ambient_light.set", "params": {}}, {})
    assert r["command"] == "ambient_light.close"
    r, _ = C.restore_action(
        {"type": "vehicle.control", "command": "seat.recline",
         "params": {"position": "front_left", "angle": "160"}}, {})
    assert r["command"] == "seat.recline" and r["params"]["angle"] == "90"
    assert r["params"]["position"] == "front_left"


def test_restore_unsupported_object_is_honest():
    r, note = C.restore_action(
        {"type": "vehicle.control", "command": "driving_mode.set",
         "params": {"mode": "eco"}}, {})
    assert r is None and note


def test_restore_seat_still_requires_confirm(cat):
    """D5：恢复动作里含座椅等危险类，照走 NEED_CONFIRM——不能因为"是还原"就免确认。"""
    r, _ = C.restore_action(
        {"type": "vehicle.control", "command": "seat.recline",
         "params": {"angle": "160"}}, {"seat_recline": 95}, cat)
    assert r["require_confirm"] is True and r["params"]["angle"] == "95"
    r2, _ = C.restore_action(
        {"type": "vehicle.control", "command": "hvac.set", "params": {}},
        {"hvac_temp": 21}, cat)
    assert r2["require_confirm"] is False


# ── 条件 key 白名单（P2 地基）+ digest ─────────────────────────────────────

def test_condition_keys(cat):
    keys = cat.condition_keys()
    for k in ("battery", "gear", "speed_kmh", "hour", "hvac_temp", "seat_recline"):
        assert k in keys
    assert "moon_phase" not in keys


def test_catalog_digest(cat):
    d = C.catalog_digest(cat)
    assert "aircon" in d and "ambient_light" in d and "seat" in d
    assert "music" not in d and "scene_mode" not in d      # 媒体 P0 剔除；scene_mode Agent 自管
    assert len(d) < 3000, f"digest 过长（{len(d)}），会撑爆编译 prompt"


# ── 契约：catalog 判合法 ⇒ edge_call 可翻译 ⇒ VAL 接受 ──────────────────────

_GOLDEN = [
    ("hvac.set", {"temperature": "22"}),
    ("hvac.set", {"temperature": "26", "mode": "外循环"}),
    ("hvac.close", {}),
    ("ambient_light.set", {"brightness": "30", "color": "warm_white"}),
    ("ambient_light.close", {}),
    ("volume.set", {"level": "0"}),
    ("seat.recline", {"position": "front_left", "angle": "160"}),
    ("fragrance.on", {}),
    ("fragrance.close", {}),
    ("window.close", {}),
    ("scene_mode.set", {"mode": "camping"}),
    ("scene_mode.set", {"mode": "off"}),
    ("screen.brightness.set", {"level": "40"}),
    ("driving_mode.set", {"mode": "eco"}),
    ("media.play", {}),          # P1.4 媒体放开：起播只能经 media（music.play 会被 VAL 拒）
    ("media.close", {}),
    ("radio.open", {}),
]


@pytest.mark.parametrize("command,params", _GOLDEN)
def test_contract_catalog_action_reaches_val(cat, command, params):
    """catalog 放行的动作，必须能过 edge_call 翻译 + VAL 校验/门控（驻车态）。"""
    from val import VAL
    from orchestrator.edge.edge_call import action_to_structured

    ok, cleaned, _ = C.validate_action(
        {"type": "vehicle.control", "command": command, "params": params}, cat)
    assert ok, f"{command} 被 catalog 拒了"

    val = VAL()
    objects = (val.commands or {}).get("objects") or {}
    structured = action_to_structured(
        cleaned["command"], cleaned["params"],
        known_objects=set(objects), object_defs=objects)
    assert structured is not None, f"{command} edge_call 翻译不出来（词表/别名漂移）"

    executed, speech = val.execute(structured)
    assert executed, f"{command} 被 VAL 拒绝：{speech}"


def test_contract_scene_mode_reaches_val_state(cat):
    """scene_mode 状态位真的写进 VAL 状态镜像（硬伤 6 的回归护栏）。"""
    from val import VAL
    from orchestrator.edge.edge_call import action_to_structured

    val = VAL()
    objects = (val.commands or {}).get("objects") or {}
    ok, cleaned, _ = C.validate_action(
        {"type": "vehicle.control", "command": "scene_mode.set",
         "params": {"mode": "钓鱼模式"}}, cat)
    assert ok
    structured = action_to_structured(cleaned["command"], cleaned["params"],
                                      known_objects=set(objects), object_defs=objects)
    executed, _ = val.execute(structured)
    assert executed and val.state["scene_mode"] == "钓鱼模式"


def test_magnitude_param_canonicalized(cat):
    """LLM 时而用 brightness 时而用 level 传亮度（对 VAL 等价）——落库前统一到规范名，
    否则 P2 的 assert/幂等/恢复会因参数名漂移对不上，回读话术也会渲染成「氛围灯%」。"""
    ok, a, _ = C.validate_action(
        {"type": "vehicle.control", "command": "ambient_light.set",
         "params": {"level": "20"}}, cat)
    assert ok and a["params"] == {"brightness": "20"}
    ok, a, _ = C.validate_action(
        {"type": "vehicle.control", "command": "hvac.set", "params": {"level": "22"}}, cat)
    assert ok and a["params"] == {"temperature": "22"}
    # 风速是 attr 段命令，level 不能被归成温度
    ok, a, _ = C.validate_action(
        {"type": "vehicle.control", "command": "aircon.wind_speed.set",
         "params": {"level": "3"}}, cat)
    assert ok and a["params"] == {"level": "3"}


def test_media_restore_returns_to_pre_activation_state(cat):
    """退出场景把音乐**精确**还原到激活前的播放态——paused ≠ stopped（真栈实测：激活前
    是暂停，旧实现退出后变成停止）。"""
    a = {"type": "media.control", "command": "media.play", "params": {}}
    assert C.affected_state_keys(a) == ("media",)
    for snap, expect in (({"media": "stopped"}, "media.close"),
                         ({"media": "paused"}, "media.pause"),
                         ({"media": "playing"}, "media.play"),
                         ({}, "media.close")):
        r, _ = C.restore_action(a, snap, cat)
        assert r["command"] == expect and r["type"] == "media.control", snap
