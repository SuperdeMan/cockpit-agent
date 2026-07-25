"""带权偏好注入渲染契约测试（M2 记忆图谱 P0-b）。

两条要求同时成立：
1. **存量兼容（红线）**：全部条目 weight=0 时输出与加权前**逐字一致**——不扰动已绿的
   旅程（B3-3 记忆族对注入文本敏感，M0b 迁移时就因 policy 文本巧合撞过负向断言）。
2. 带权偏好按强度排序，用**确定性人话强度词**渲染——「有多常用」是系统持有的事实，
   让 LLM 自己揣摩会把「说过一次」和「每周三次」说成一样。
"""
from orchestrator.cloud.context import _render_memory, _strength_label


def _legacy(text, conf=0.9, scope="profile.taste", prov="user_stated"):
    """M2 之前的召回结果形态：没有 weight 字段。"""
    return {"text": text, "scope": scope, "provenance": prov, "confidence": conf}


def _weighted(text, weight, conf=0.5):
    return {"text": text, "scope": "profile.taste", "provenance": "agent_inferred",
            "confidence": conf, "weight": weight}


# ── 存量兼容（红线）───────────────────────────────────────────────────────

def test_legacy_only_renders_exactly_as_before():
    """逐字锁死加权前的输出格式：标题、`- [tag | conf | prov] text` 行、尾部空行。"""
    out = _render_memory([_legacy("用户喜欢吃辣")])
    assert out == ("已知用户记忆（仅在与当前任务相关时参考，勿向用户暴露置信度）：\n"
                   "- [profile.taste | 0.90 | user_stated] 用户喜欢吃辣\n\n")


def test_legacy_missing_weight_key_is_not_a_crash():
    out = _render_memory([{"text": "只有文本"}])
    assert "只有文本" in out


def test_empty_and_blank_inputs():
    assert _render_memory(None) == ""
    assert _render_memory([]) == ""
    assert _render_memory([{"text": "   "}]) == ""


def test_legacy_section_still_capped_at_three():
    """未加权段沿用旧的「最多 3 条」，不因 top-N 放宽而膨胀 prompt。"""
    out = _render_memory([_legacy(f"老条目{i}") for i in range(5)])
    assert out.count("- [") == 3


# ── 带权渲染 ─────────────────────────────────────────────────────────────

def test_strength_labels_are_deterministic():
    assert _strength_label(0.9) == "常用"
    assert _strength_label(0.7) == "常用"
    assert _strength_label(0.6) == "明确说过"
    assert _strength_label(0.5) == "明确说过"
    assert _strength_label(0.2) == "偶尔提过"


def test_weighted_items_sorted_by_strength():
    out = _render_memory([_weighted("偶尔提过的", 0.2), _weighted("很常用的", 0.85)])
    assert out.index("很常用的") < out.index("偶尔提过的")
    assert "（常用）" in out and "（偶尔提过）" in out


def test_weighted_block_hides_confidence_numbers():
    """不向 prompt 暴露置信度数字——强度用人话说，别让模型拿数字做算术。"""
    out = _render_memory([_weighted("常用偏好", 0.85, conf=0.42)])
    assert "0.42" not in out and "0.85" not in out


def test_mixed_renders_two_sections():
    out = _render_memory([_weighted("带权偏好", 0.8), _legacy("老条目")])
    assert "已知用户偏好（按强度排序" in out
    assert "相关记忆：" in out
    assert out.index("带权偏好") < out.index("老条目")


def test_weighted_only_has_no_legacy_header():
    out = _render_memory([_weighted("带权偏好", 0.8)])
    assert "相关记忆：" not in out
    assert "勿向用户暴露置信度" not in out


def test_top_n_widened_but_budget_respected():
    """条数从 3 放宽到 5（今天 top-3 会被久远的推断偏好挤掉常用的），但预算仍夹紧。"""
    items = [_weighted(f"偏好{i}", 0.9 - i * 0.1) for i in range(8)]
    out = _render_memory(items)
    assert out.count("- ") <= 5
    assert len(out) <= 400 + 2      # _MEMORY_BUDGET + 尾部换行


def test_zero_weight_item_never_lands_in_weighted_block():
    """weight=0 是「未参与加权」不是「强度为零」——不能被渲染成「偶尔提过」。"""
    out = _render_memory([{"text": "老条目", "confidence": 0.9, "weight": 0}])
    assert "（偶尔提过）" not in out
    assert "- [" in out
