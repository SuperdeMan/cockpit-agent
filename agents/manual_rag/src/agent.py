"""车书 Agent —— 知识类生态 Agent 范本。演示 RAG：retrieve（检索）+ generate（生成）。

Phase 1：使用 Provider 适配层（mock/真实只读手册索引可切换）。

2026-08-15（阶段 1 / 卡 Q9）加了四道**确定性**护栏。它们存在的理由是同一句话：
**「不要编造」写在 system prompt 里不是护栏，只是一句请求。**
  ① 检索零命中 → 直接诚实弃权，**一次 LLM 都不调**。
  ② 来源类型（manual/web/mock）随资料一起传到话术层与卡片；
     非真实手册来源**不得**被表述成「本车型手册」。
  ③ 安全信号（警告灯/亮灯/漏气/失灵…）命中 → 先给**确定性分级处置**；
     且在没有真实手册的情况下**不进 LLM**，避免把演示数值说成权威值。
  ④ 卡片盖 `_prov`（此前 manual-rag/road-safety/chitchat 三个 Agent 覆盖为 0）。
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import logging
import os
import re

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT
from agents._sdk.provenance import attach
from runtime.safety_signal import alert_advice, alert_level, alert_signal
from .providers import build_knowledge_retriever

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

_SYSTEM_MANUAL = (
    "你是车型手册问答助手。只依据【参考资料】回答用户问题，简洁口语化，两三句话内。"
    "若资料中没有相关信息，明确说『手册里没有查到，建议联系客服』，不要编造。"
)
# 非真实手册来源（演示语料/联网检索）：资料**不绑定任何车型**，措辞必须如实。
# 这不是「换个说法」——它决定用户会不会拿一个通用参考值去给自己的车充气。
_SYSTEM_GENERIC = (
    "你是用车知识助手。【参考资料】不是本车的车型手册，而是通用资料，"
    "**不绑定任何具体车型**。只依据【参考资料】回答，简洁口语化，两三句话内。"
    "涉及具体数值时必须说明这是通用参考、请以车辆铭牌或随车手册为准。"
    "资料中没有的内容明确说没有查到，不要编造。"
)

# 安全信号判据的**唯一实现**在 `runtime/safety_signal.py`（road-safety 与
# chitchat 是同一份的另外两个消费方）。这里曾经有一份本地副本——收口发生在
# 第三个消费方出现的**当天**，不是等它错了再收（§4.3 时区族那笔账）。
_UNVERIFIED_NUMBERS = "具体数值请以车辆铭牌或随车手册为准，我这里没有本车型的权威数据。"
_UNGROUNDED_NUMBER = (
    "检索到了相关手册内容，但生成答案中的数值无法从引用片段核对。"
    "请查看屏幕中的手册原文，或联系小米汽车服务中心确认。"
)
_GENERATION_UNAVAILABLE_MANUAL = (
    "已找到相关手册原文，但摘要生成暂时不可用，请查看屏幕中的手册内容，或稍后再试。"
)
_GENERATION_UNAVAILABLE_GENERIC = (
    "已找到相关参考资料，但摘要生成暂时不可用，请查看屏幕中的资料内容，或稍后再试。"
)
_NON_RETRYABLE_GENERATION_ERRORS = (
    "RESOURCE_EXHAUSTED",
    "INVALID_ARGUMENT",
    "PERMISSION_DENIED",
    "UNAUTHENTICATED",
    "FAILED_PRECONDITION",
)
logger = logging.getLogger(__name__)
_safety_level = alert_level

_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<number>\d+(?:[.,]\d+)?)\s*(?P<wan>万)?\s*"
    r"(?P<unit>km/h|公里/小时|bar|%|公里|km|个月|分钟|min|小时|mm|cm|"
    r"年|月|天|秒|℃|°c|h|s|m|l|w|v)?",
    re.IGNORECASE,
)
_UNIT_FAMILY = {
    "km/h": "speed", "公里/小时": "speed", "bar": "pressure", "%": "percent",
    "公里": "distance", "km": "distance", "年": "years", "个月": "months",
    "月": "months", "天": "days", "小时": "hours", "h": "hours",
    "分钟": "minutes", "min": "minutes", "秒": "seconds", "s": "seconds",
    "mm": "length_mm", "cm": "length_cm", "m": "length_m",
    "l": "volume", "w": "power", "v": "voltage", "℃": "temperature",
    "°c": "temperature",
}


def _numeric_claims(text: str) -> list[tuple[Decimal, str, str]]:
    """抽取需要接地的数值声明。裸整数多为列表编号，只有带单位或小数才检查。"""
    claims: list[tuple[Decimal, str, str]] = []
    for match in _NUMBER_RE.finditer(text or ""):
        raw_number = match.group("number").replace(",", "")
        unit = (match.group("unit") or "").casefold()
        if not unit and "." not in raw_number:
            continue
        try:
            number = Decimal(raw_number)
            if match.group("wan"):
                number *= 10000
        except InvalidOperation:
            continue
        claims.append((number.normalize(), _UNIT_FAMILY.get(unit, unit), match.group(0)))
    return claims


def _ungrounded_numeric_claims(answer: str, chunks) -> list[str]:
    answer_claims = _numeric_claims(answer)
    if not answer_claims:
        return []
    materials = [c.content for c in chunks]
    material_claims = [_numeric_claims(text) for text in materials]
    missing: list[str] = []
    for number, family, raw in answer_claims:
        grounded = False
        for text, claims in zip(materials, material_claims):
            if any(number == other and (not family or not other_family
                                        or family == other_family)
                   for other, other_family, _ in claims):
                grounded = True
                break
            # 表格常把单位放在列头、数字放在后续单元格（如“轮胎压力 (bar) ... 2.9”）。
            # 同一页同时出现该数值和同单位即可视为接地；不跨 chunk 借单位。
            normalized = text.casefold().replace(",", "")
            if str(number) in normalized and any(
                    marker in normalized for marker, mapped in _UNIT_FAMILY.items()
                    if mapped == family):
                grounded = True
                break
        if not grounded:
            missing.append(raw.strip())
    return missing


class ManualRagAgent(BaseAgent):
    def __init__(self, retriever=None):
        super().__init__(_MANIFEST)
        self.kb = retriever or build_knowledge_retriever()

    @staticmethod
    def _safety_data(level: str, question: str, **extra) -> dict:
        """`data` 载荷。安全信号命中时经**保留键 `_safety_alert`** 声明会话级安全态
        （编排通用消费，登记 conventions §9.1 同族；契约与校验在
        `orchestrator/cloud/context.py::_valid_safety_alert`）。

        为什么要跨轮：QA 轮 SF3 三轮实测——红色机油灯之后第二轮答天气、
        第三轮执行音量。**一次安全警告必须是会话状态，不能是一句话说完就没了。**
        """
        data = {"safety_signal": level, **extra}
        if level:
            # signal 取原话里命中的那个词，不取整句——整句进 prompt 会把
            # 用户的措辞当成告警名字（「慢一点开可以吗」不是一个告警）。
            data["_safety_alert"] = {
                "level": level, "signal": alert_signal(question) or "车辆告警"}
        return data

    def _card(self, chunks, source_type: str) -> dict:
        sources = list(dict.fromkeys(c.source for c in chunks if c.source))
        images = []
        seen_images: set[str] = set()
        for chunk in chunks:
            for image in getattr(chunk, "images", ()):
                if image.asset_id in seen_images:
                    continue
                seen_images.add(image.asset_id)
                images.append({
                    "asset_id": image.asset_id,
                    "caption": image.caption,
                    **({"description": image.description} if image.description else {}),
                    "page_start": image.page_start,
                    "media_type": image.media_type,
                    "data_uri": image.data_uri,
                    "sha256": image.sha256,
                    "width": image.width,
                    "height": image.height,
                    "bbox": list(image.bbox),
                    "role": image.role,
                    "match_kind": image.match_kind,
                })
        card = {
            "type": "manual",
            "source_type": source_type,
            "sources": sources,
            "chunks": [{
                "content": c.content,
                "source": c.source,
                **({"score": round(float(c.score), 6)} if c.score else {}),
                **({"document_id": c.document_id} if c.document_id else {}),
                **({"vehicle_model": c.vehicle_model} if c.vehicle_model else {}),
                **({"page_start": c.page_start} if c.page_start else {}),
                **({"page_end": c.page_end} if c.page_end else {}),
                **({"section_path": list(c.section_path)} if c.section_path else {}),
                **({"asset_ids": [image.asset_id for image in c.images]}
                   if getattr(c, "images", ()) else {}),
            } for c in chunks],
            "images": images,
        }
        document = getattr(self.kb, "document", None)
        if isinstance(document, dict):
            card["document"] = {
                key: document[key] for key in (
                    "document_id", "title", "publisher", "vehicle_model", "revision",
                    "source_sha256", "content_sha256", "visual_assets_sha256",
                    "visual_asset_count", "visual_skipped_asset_count",
                ) if document.get(key)
            }
        revision = str((document or {}).get("revision", "")) \
            if isinstance(document, dict) else ""
        return attach(
            card,
            self.kb,
            data_time=revision,
            data_time_label="手册版本" if revision else "",
        )

    async def _generate_answer(self, messages) -> tuple[str | None, str]:
        """Retry one transient LLM RuntimeError, then keep the cited card.

        LLMClient normalizes provider and transport failures to RuntimeError.
        Configuration, quota, and authentication errors are not useful to
        retry; other RuntimeErrors get one bounded retry. Programming errors
        stay visible instead of being mislabeled as provider degradation.
        """

        try:
            return await self.llm.complete(
                messages, temperature=0.2, max_tokens=200), ""
        except RuntimeError as exc:
            logger.warning("manual answer generation failed: %s", exc)
            if any(marker in str(exc).upper()
                   for marker in _NON_RETRYABLE_GENERATION_ERRORS):
                return None, "degraded"
        try:
            answer = await self.llm.complete(
                messages, temperature=0.2, max_tokens=200)
            return answer, "recovered"
        except RuntimeError as exc:
            logger.warning("manual answer generation retry failed: %s", exc)
            return None, "degraded"

    async def handle(self, intent, ctx, meta) -> AgentResult:
        question = intent.raw_text or intent.slots.get("question", "")
        if not question:
            return AgentResult(status=NEED_SLOT, speech="您想了解车辆的哪方面？")

        level = _safety_level(question)
        vehicle_model = str(
            intent.slots.get("vehicle_model", "")
            or ((meta or {}).get("vehicle_model", "") if hasattr(meta, "get") else "")
        ).strip()
        chunks = await self.kb.retrieve(                   # 1) retrieve
            question, vehicle_model=vehicle_model)

        # ① 零命中短路：不调 LLM。安全信号仍要给处置建议——**没有资料不等于没有风险**。
        if not chunks:
            speech = "手册里没有查到这方面的内容，建议联系客服或前往服务点确认。"
            if level:
                speech = f"{alert_advice(level)}{speech}"
            return AgentResult(speech=speech,
                               data=self._safety_data(level, question),
                               ui_card=self._card([], ""))

        # 来源类型取本轮实际检索到的资料（混合来源时只要有一条不是真手册，
        # 就按非权威处理——**权威性取最低的那条**，不取最高的）。
        types = {getattr(c, "source_type", "manual") or "manual" for c in chunks}
        source_type = ("manual" if types == {"manual"}
                       else "mock" if "mock" in types else "web")
        authoritative = source_type == "manual"

        # ③ 安全信号 + 无权威手册 → **不进 LLM**，只给确定性处置。
        # 理由：这一档下模型唯一能引用的就是演示语料里的数值，说出去就是把
        # 通用参考冒充成本车权威值（QA 轮 I-036 的完整形态）。
        if level and not authoritative:
            advice = alert_advice(level)
            return AgentResult(
                speech=f"{advice}{_UNVERIFIED_NUMBERS}",
                data=self._safety_data(level, question, source_type=source_type),
                ui_card=self._card(chunks, source_type))

        # 受控视觉目录已经把俗称/正式 caption 与 PDF 内具体图片绑定，并在启动时逐 blob
        # 校验。此时不再让 LLM 在同一张告警表的相邻行之间猜一次（真实生产曾把安全带
        # “背宝剑小人”猜成安全气囊）。有目录说明就直接确定性转述；没有说明仍走正文生成。
        visual_matches = [
            image for chunk in chunks for image in getattr(chunk, "images", ())
            if image.match_kind in {"visual_alias", "visual_caption"}
            and image.description
        ]
        if authoritative and visual_matches:
            image = visual_matches[0]
            description = image.description.rstrip("。！？! ") + "。"
            document = getattr(self.kb, "document", None)
            manual_title = str((document or {}).get("title") or "车型用户手册") \
                if isinstance(document, dict) else "车型用户手册"
            if image.role == "warning_icon":
                answer = f"根据《{manual_title}》的图标目录，这是“{image.caption}”。{description}"
            else:
                answer = f"根据《{manual_title}》，{description}"
            speech = f"{alert_advice(level)}{answer}" if level else answer
            return AgentResult(
                speech=speech,
                data=self._safety_data(
                    level, question, source_type=source_type,
                    visual_match=image.caption),
                ui_card=self._card(chunks, source_type),
            )

        context_block = "\n\n".join(
            f"[资料{i}｜{c.source or '来源未标注'}]"
            f"{''.join(f'｜配图：{image.caption}' for image in getattr(c, 'images', ())) }"
            f"\n{c.content}"
            for i, c in enumerate(chunks, start=1)
        )
        messages = [                                        # 2) generate
            {"role": "system",
             "content": _SYSTEM_MANUAL if authoritative else _SYSTEM_GENERIC},
            {"role": "user", "content": f"【参考资料】\n{context_block}\n\n【问题】{question}"},
        ]
        answer, generation_state = await self._generate_answer(messages)
        if answer is None:
            fallback = (_GENERATION_UNAVAILABLE_MANUAL if authoritative
                        else _GENERATION_UNAVAILABLE_GENERIC)
            speech = f"{alert_advice(level)}{fallback}" if level else fallback
            return AgentResult(
                speech=speech,
                data=self._safety_data(
                    level,
                    question,
                    source_type=source_type,
                    generation_degraded="llm_runtime_error",
                ),
                ui_card=self._card(chunks, source_type),
            )
        generation_data = (
            {"generation_retry": "recovered"}
            if generation_state == "recovered" else {}
        )

        # 「只依据资料」不能只停在 prompt。真实手册最危险的是模型把 2.9 改成另一个
        # 看起来同样精确的数：带单位/小数的数值若无法在本轮引用片段内核对，整段弃权。
        ungrounded = _ungrounded_numeric_claims(answer, chunks) if authoritative else []
        if ungrounded:
            speech = f"{alert_advice(level)}{_UNGROUNDED_NUMBER}" \
                if level else _UNGROUNDED_NUMBER
            return AgentResult(
                speech=speech,
                data=self._safety_data(
                    level, question, source_type=source_type,
                    grounding_rejected="numeric", **generation_data),
                ui_card=self._card(chunks, source_type),
            )

        # 安全信号（有权威手册）：处置建议**前置**，不让它淹没在模型话术里。
        speech = f"{alert_advice(level)}{answer}" \
            if level else answer
        return AgentResult(
            speech=speech,
            data=self._safety_data(
                level, question, source_type=source_type, **generation_data),
            ui_card=self._card(chunks, source_type),
        )
