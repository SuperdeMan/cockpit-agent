"""视觉入口 Agent（M4 P4）：「那是什么」单帧图片问答。

**本 Agent 拿不到图**——它只拿到一个 frame_id，转给 llm-gateway，由网关从内存帧库取图
拼多模态请求（RFC §5.1「图像永远不进对话链」）。这不是绕弯：图像走 meta 会撑爆 gRPC meta，
还会整条进 obs 采集，那是隐私事故。

两条诚实降级（铁律③在视觉上的直接推论）：
1. **没帧就说没帧**——frame_id 缺失或已过期（TTL 120s），绝不退化成纯文本让模型编一个
   「那可能是一座写字楼」。看图问答编出来的答案比查不到严重得多：用户没法判断真假。
2. **看图模型不可用就说不可用**——不静默回落到看不了图的聊天大脑（P4b 探针实测：
   qwen3.7-max 对多模态 content 直接 400；而档位解析对不认识的模型是静默回落 primary，
   那样连报错都不会有）。故视觉走**独立的 qwen-vl 档 + 请求级 pin**，整条降级链都能看图。
"""
from __future__ import annotations
import logging
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT

logger = logging.getLogger("agent.vision")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

_SYSTEM = (
    "你是车载助手的「看一看」能力。用户在车里指着窗外或车内的东西问你那是什么，"
    "你会看到摄像头当前画面。请用一两句口语化中文直接回答，像同车的人随口告诉他一样。"
    "看得清就说是什么、有什么特点；看不清就直说看不清，不要猜测、不要罗列可能性、"
    "不要描述整张图的构图。绝不编造品牌名、店名或地名。"
)

# 帧引用的 meta 键（与 llm-gateway/server.py::_msgs 同一契约）
_FRAME_META = "vision_frame_id"


class VisionAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)

    async def handle(self, intent, ctx, meta) -> AgentResult:
        question = (intent.slots.get("question") or intent.raw_text or "").strip()
        frame_id = (meta.get(_FRAME_META) or intent.slots.get("frame_id") or "").strip()

        if not frame_id:
            # 没抓到帧：可能是端侧没命中触发词、没授权摄像头、或用的是文本输入。
            # R9 契约：话术型拒绝用 OK 而不是 FAILED（FAILED 会被聚合器吞成裸「处理失败」）。
            return AgentResult(
                speech="我这会儿没拿到画面。你对着要问的东西再说一次「那是什么」，我就能看了。",
                data={"_prov": {"mode": "unavailable", "reason": "no_frame"}})

        provider = os.getenv("VISION_PROVIDER", "qwen-vl")
        model = os.getenv("VISION_MODEL", "")
        try:
            answer = await self.llm.complete(
                [{"role": "system", "content": _SYSTEM},
                 {"role": "user", "content": question or "这是什么？"}],
                model=model, temperature=0.3, max_tokens=200, timeout=30,
                # 能力性 pin：看图必须打到 VL 档，不跟随用户切的聊天大脑。
                extra_meta={"llm_provider": provider, "llm_model": model,
                            _FRAME_META: frame_id})
        except Exception as e:
            # 网关对「声明了要看图但帧没了」回 FAILED_PRECONDITION——**必须与「模型挂了」
            # 分开说**：前者用户再问一次就好，后者再问也没用。含混成一句会让用户白试。
            if "frame unavailable" in str(e).lower() or "FAILED_PRECONDITION" in str(e):
                logger.info("vision frame expired: %s", frame_id)
                return AgentResult(
                    speech="刚才那一眼已经过去了，再对着它说一次「那是什么」。",
                    data={"_prov": {"mode": "unavailable", "reason": "frame_expired"}})
            logger.warning("vision complete failed: %s", e)
            return AgentResult(
                speech="我现在看不了图，稍后再试试。",
                data={"_prov": {"mode": "unavailable", "reason": "vision_model_unavailable"}})

        answer = (answer or "").strip()
        if not answer:
            return AgentResult(speech="这张画面我没看清，换个角度再问一次？",
                               data={"_prov": {"mode": "unavailable", "reason": "empty_answer"}})
        return AgentResult(
            speech=answer,
            data={"answer": answer, "frame_id": frame_id,
                  # 三重诚实标注的第一重（同 MCP 演示商户惯例）：PoC 没有真的车外摄像头，
                  # 画面来自浏览器摄像头。卡片角标与设置文案是另外两重。
                  "_prov": {"mode": "real", "vendor": provider, "source": "simulated_camera"}},
            ui_card={"type": "vision_answer", "answer": answer,
                     "question": question, "simulated": True})
