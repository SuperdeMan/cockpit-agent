"""**红线契约：声纹不是鉴权因子**（M4 P4，RFC §6.1）。

`occupant_id` 的可达范围只有**记忆域**（recall / remember / AppendTurn / relation）。
它不得参与：granted_scopes 的计算、权限判定、VAL、require_confirm 合成、支付。

为什么这条要用源码级断言钉死，而不只是行为测试：行为测试只能证明「当前这条路径没提权」，
源码断言能挡住**未来某次顺手的改动**——比如有人为了「乘客不能开后备箱」而在权限分支里读一下
occupant_id。那一步看起来很合理，但它把「认错人」的后果从「看到别人的口味偏好」升级成
「替别人花钱 / 开车门」，而声纹是可被录音重放的。

身份识别与授权是两件事。识别错了只该损失个性化，不该损失安全。
"""
from __future__ import annotations

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


def _src(*parts: str) -> str:
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ── 安全层源码不得认识 occupant ────────────────────────────────────────────

def test_security_layer_never_mentions_occupant():
    """权限引擎/scope 定义里出现 occupant 就是提权路径的开端。"""
    sec_dir = os.path.join(_ROOT, "security")
    for root, _dirs, files in os.walk(sec_dir):
        if "__pycache__" in root or "tests" in root:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            src = _src(os.path.relpath(os.path.join(root, fn), _ROOT))
            assert "occupant" not in src, (
                f"security/{fn} 出现 occupant——声纹不得参与权限判定（RFC §6.1 红线）")


def test_val_never_mentions_occupant():
    """VAL 是车控唯一路径。它认识说话人，就等于声纹能开车门。"""
    assert "occupant" not in _src("orchestrator", "edge", "val.py")


def test_confirm_enforcement_never_mentions_occupant():
    """require_confirm 合成（M0a 中央闸）不得按说话人放宽或收紧。"""
    src = _src("orchestrator", "cloud", "executor.py")
    assert "occupant" not in src, "executor 出现 occupant——确认闸不得看说话人"


def test_payment_gateway_never_mentions_occupant():
    pg = os.path.join(_ROOT, "payment-gateway")
    if not os.path.isdir(pg):
        return
    for fn in os.listdir(pg):
        if fn.endswith(".py"):
            assert "occupant" not in _src("payment-gateway", fn), (
                f"payment-gateway/{fn} 出现 occupant——声纹不得作支付授权因子")


# ── 编排层：occupant 只能进 prefs / PlanContext，不能进 granted ──────────────

def test_build_context_keeps_occupant_out_of_permission_branch():
    """`build_context` 里 occupant 的赋值必须与 granted 的计算完全无关。

    做法：取出 granted 计算段（从 raw_scopes 到 fail-open 分支结束）的源码切片，
    断言其中不含 occupant。
    """
    src = _src("orchestrator", "cloud", "context.py")
    # 验收修正：裸切片在锚点顺序被改后会得到空串 → 断言恒真且静默空转（守红线的
    # 测试自己被一次无害重排改废）。先钉两锚点存在与顺序，再断内容非空。
    assert "raw_scopes = meta.get" in src, "granted 计算锚点丢失——重构后请更新本测试"
    assert "# M4 P4：本轮说话人" in src, "occupant 赋值锚点丢失——重构后请更新本测试"
    start = src.index("raw_scopes = meta.get")
    end = src.index("# M4 P4：本轮说话人")
    assert start < end, ("occupant 赋值被移到 granted 计算之前——顺序是本红线测试的"
                         "前提，移动后请人工确认 granted 段不读 occupant 再更新锚点")
    granted_block = src[start:end]
    assert granted_block.strip(), "切片为空——锚点漂移，测试已不覆盖任何代码"
    assert "occupant" not in granted_block, (
        "granted_scopes 计算段出现 occupant——权限不得随说话人变化")


def test_planning_permission_filter_never_mentions_occupant():
    """能力过滤（_filter_by_permission）按 granted_scopes 走，与说话人无关。"""
    src = _src("orchestrator", "cloud", "planning.py")
    assert "occupant" not in src, "planning 出现 occupant——能力可达性不得随说话人变化"


def test_edge_fast_intent_never_mentions_occupant():
    """端侧快路径（车控/媒体毫秒路径）不认识说话人。"""
    assert "occupant" not in _src("orchestrator", "edge", "fast_intent.py")


# ── 正向：它确实进了记忆域（否则这一期白做）──────────────────────────────

def test_occupant_does_reach_memory_recall():
    """反向护栏：断言 occupant 真的接进了召回——否则上面几条全绿也只是「没接」。"""
    src = _src("orchestrator", "cloud", "context.py")
    assert "occupant_id=getattr(ctx, \"occupant_id\", \"\")" in src


def test_occupant_does_reach_agent_sdk_context():
    src = _src("agents", "_sdk", "server.py")
    assert "occupant_id" in src, "SDK 未从 meta 取 occupant_id——Agent 侧隔离不成立"
