"""本地调试脚本：覆盖端侧快路径、云端慢路径、确认闭环。
前置：bash start-local.sh 启动所有服务。

用法：
  python debug-local.py              # 全量调试
  python debug-local.py --fast       # 只测快路径
  python debug-local.py --confirm    # 测试确认闭环（需要云端 Planner 可用）
"""
import sys
import grpc
from cockpit.orchestrator.v1 import orchestrator_pb2, orchestrator_pb2_grpc
from cockpit.common.v1 import common_pb2
from cockpit.agent.v1 import agent_pb2, agent_pb2_grpc
from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc

EDGE = "localhost:50070"
LLM = "localhost:50052"


def call_edge(text, session_id="debug", is_confirmation=False):
    """调 Edge Orchestrator，返回 (speech, actions, need_confirm, follow_up)。"""
    ch = grpc.insecure_channel(EDGE)
    stub = orchestrator_pb2_grpc.EdgeOrchestratorStub(ch)
    req = orchestrator_pb2.HandleRequest(
        text=text,
        session_id=session_id,
        is_confirmation=is_confirmation,
        context=common_pb2.ContextRef(session_id=session_id, user_id="u1", vehicle_id="v1"),
    )
    for ev in stub.Handle(req, timeout=30):
        if ev.HasField("final"):
            return (
                ev.final.speech,
                [{"type": a.type, "payload": dict(a.payload.fields) if a.payload else {}}
                 for a in ev.final.actions],
                ev.final.need_confirm,
                ev.final.follow_up,
            )
    return "无响应", [], False, ""


def test_fast_path():
    """链路 1: 车控快路径（端侧秒回）"""
    print("\n=== 链路 1: 车控快路径 ===")
    cases = [
        ("打开空调26度", "hvac.set"),
        ("关闭空调", "hvac.off"),
        ("下一首", "media.next"),
        ("关闭车窗", "window.close"),
    ]
    for text, expected_intent in cases:
        speech, actions, _, _ = call_edge(text)
        action_types = [a["type"] for a in actions]
        has_vehicle = any("vehicle" in t for t in action_types)
        status = "✓" if has_vehicle else "✗"
        print(f"  {status} '{text}' → {speech[:50]}  actions={action_types}")


def test_cloud_path():
    """链路 2: 云端慢路径（需要 Cloud Planner 可用）"""
    print("\n=== 链路 2: 云端慢路径 ===")
    cases = [
        "讲个笑话",
        "附近的充电站",
    ]
    for text in cases:
        speech, actions, _, _ = call_edge(text)
        # 降级话术说明云端不可达（Go gateway 没启动）
        degraded = "网络" in speech or "暂时" in speech
        status = "⚠降级" if degraded else "✓"
        print(f"  {status} '{text}' → {speech[:60]}")


def test_confirm_loop():
    """链路 3: 确认闭环（需要 Cloud Planner + food-ordering Agent 可用）"""
    print("\n=== 链路 3: 确认闭环 ===")
    sess = "debug-confirm"

    # 第 1 轮：触发订位
    speech, actions, need_confirm, follow_up = call_edge(
        "订川菜馆今晚7点两位", session_id=sess
    )
    print(f"  1st: speech='{speech[:50]}' need_confirm={need_confirm}")
    if follow_up:
        print(f"       follow_up='{follow_up}'")

    if not need_confirm:
        print("  ⚠ 未触发确认（可能 Planner 未命中 food.reserve），跳过确认测试")
        return

    # 第 2 轮：确认
    speech2, actions2, need2, _ = call_edge(
        "确认", session_id=sess, is_confirmation=True
    )
    print(f"  2nd: speech='{speech2[:50]}' need_confirm={need2}")
    if "订好" in speech2:
        print("  ✓ 确认闭环打通！")
    elif need2:
        print("  ✗ 确认后仍需确认——闭环未打通")
    else:
        print(f"  ⚠ 预期外结果: {speech2[:60]}")


def test_llm_gateway():
    """验证 LLM Gateway（MiMo API）"""
    print("\n=== LLM Gateway (MiMo) ===")
    ch = grpc.insecure_channel(LLM)
    stub = llm_pb2_grpc.LLMGatewayStub(ch)
    req = llm_pb2.CompleteRequest(
        messages=[llm_pb2.Message(role="user", content="你好，用一句话回复")],
        model="mimo-v2.5-pro",
        max_tokens=50,
    )
    try:
        resp = stub.Complete(req, timeout=15)
        print(f"  ✓ model={resp.model_used} → {resp.content[:60]}")
    except grpc.RpcError as e:
        print(f"  ✗ LLM 调用失败: {e.details()}")


def main():
    args = set(sys.argv[1:])
    print("=== 本地调试 ===")
    print(f"Edge Orchestrator: {EDGE}")
    print(f"LLM Gateway: {LLM}")

    test_fast_path()

    if "--fast" not in args:
        test_cloud_path()
        test_llm_gateway()

    if "--confirm" in args or "--fast" not in args:
        test_confirm_loop()

    print("\n=== 调试完成 ===")


if __name__ == "__main__":
    main()
