"""车书 Agent —— 知识类生态 Agent 范本。演示 RAG：retrieve（检索）+ generate（生成）。

Phase 1：使用 Provider 适配层（mock/真实只读手册索引可切换）。

2026-08-15（阶段 1 / 卡 Q9）加了四道**确定性**护栏。它们存在的理由是同一句话：
**「不要编造」写在 system prompt 里不是护栏，只是一句请求。**
  ① 检索零命中 → 直接诚实弃权，**不调生成 LLM**（2026-09-26 起真实索引多一次只看目录、
     不见正文的章节路由；它选不出章节时同样零材料弃权）。
  ② 来源类型（manual/web/mock）随资料一起传到话术层与卡片；
     非真实手册来源**不得**被表述成「本车型手册」。
  ③ 安全信号（警告灯/亮灯/漏气/失灵…）命中 → 先给**确定性分级处置**；
     且在没有真实手册的情况下**不进 LLM**，避免把演示数值说成权威值。
  ④ 卡片盖 `_prov`（此前 manual-rag/road-safety/chitchat 三个 Agent 覆盖为 0）。

2026-09-26 检索前两件事（collector 真实问法）：
  · 检索用哪句话：原话是权威；只有原话自己解析不出时（指代主语「它有几档」、规划器把
    多诉求句拆成了多步而本步只占其中一个分句）才用规划器写的 `question` 槽。安全分级
    始终只看原话。
  · 词法检索零命中、没把握（主题覆盖率 < 0.7），或问句含手册不认识的实词 → 目录路由
    （`toc_router.py`）补章节；路由只选目录编号，不见正文、不作答。
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from itertools import zip_longest
import logging
import os
import re

from agents._sdk import BaseAgent, AgentResult
from agents._sdk.provenance import attach
from runtime.anaphora import has_anaphoric_subject
from runtime.clause_split import split_clauses
from runtime.safety_signal import alert_advice, alert_level, alert_signal
from .providers import build_knowledge_retriever
from .providers.base import CONFIDENT_COVERAGE
from .toc_router import SCOPE_OTHER_VEHICLE, ManualTocRouter

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
_MAX_CHUNKS = 4
_BIGRAM_TEXT_RE = re.compile(r"[\u3400-\u9fffA-Za-z0-9]+")


def _bigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for part in _BIGRAM_TEXT_RE.findall(text or ""):
        grams.update(part[i:i + 2] for i in range(len(part) - 1))
    return grams


def _retrieval_question(raw: str, slot: str) -> tuple[str, str]:
    """(检索用的问题, 依据)。原话优先；只有原话自己解析不出时才用规划器写的槽：

    - 指代主语（「它有几档」「那它的续航呢」，判据唯一实现 `runtime.anaphora`）：指代物只在
      会话历史里，规划器补全的「座椅加热有几档」才可检索；
    - 规划器把多诉求句拆成了多步、本步的槽只落在其中一个分句里（「打开后备箱，再告诉我
      空调有哪些模式」→「空调有哪些模式」）：拿整句检索会把另一步的对象也搜进来。
    槽横跨多个分句（规划器把一个多问句合并成一步）时仍用原话——原话才带全部诉求。
    """
    raw, slot = (raw or "").strip(), (slot or "").strip()
    if not slot or _bigrams(slot) == _bigrams(raw):
        return raw or slot, "raw"
    if not raw:
        return slot, "slot"
    if has_anaphoric_subject(raw):
        return slot, "anaphora"
    clauses = [clause for clause in split_clauses(raw) if clause.strip()]
    if len(clauses) > 1:
        wanted = _bigrams(slot)
        owners = [clause for clause in clauses
                  if wanted and len(wanted & _bigrams(clause)) * 2 >= len(wanted)]
        if len(owners) == 1:
            return slot, "clause"
    return raw, "raw"


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


def _merge_routed(routed: list, lexical: list, lexical_first: bool = False) -> list:
    """两路都选中的页最可信、排最前；其余路由页与词法页交替，至多四块。
    路由页一律排前会把原本正确的词法首页挤下去（「三元锂电池平时充到多少」）。

    `lexical_first`：词法首页覆盖率够线（用户的词都在，只是章节名对不上）时，
    词法首页钉在第一——章节名不含车主叫法的正确首页（「制动液多久换一次」→ 保养计划页）不让
    路由的共选页挤下去；其后仍是共选页（按词法顺序）、再路由页与其余词法页交替。"""
    routed_order = [chunk.page_start for chunk in routed]
    lexical_pages = {chunk.page_start for chunk in lexical}
    pinned, rest = (lexical[:1], lexical[1:]) if lexical_first else ([], lexical)
    # 共选页用词法那一块：它带着视觉匹配 / 同页配图与覆盖率。
    agreed = [chunk for chunk in rest if chunk.page_start in routed_order]
    if not lexical_first:
        agreed.sort(key=lambda chunk: routed_order.index(chunk.page_start))
    merged = [*pinned, *agreed]
    only_routed = [chunk for chunk in routed if chunk.page_start not in lexical_pages]
    only_lexical = [chunk for chunk in rest if chunk.page_start not in routed_order]
    for pair in zip_longest(only_routed, only_lexical):
        merged.extend(chunk for chunk in pair if chunk is not None)
    return merged[:_MAX_CHUNKS]


class ManualRagAgent(BaseAgent):
    def __init__(self, retriever=None):
        super().__init__(_MANIFEST)
        self.kb = retriever or build_knowledge_retriever()
        # 只有带目录的真实索引才有路由；mock/web 语料没有章节可选。
        table_of_contents = getattr(self.kb, "table_of_contents", None)
        document = getattr(self.kb, "document", None)
        self._toc_router = (
            ManualTocRouter(table_of_contents(), title=str(
                (document or {}).get("title") or "车型用户手册"))
            if callable(table_of_contents) else None)

    async def _route_if_unsure(self, question: str, chunks) -> tuple[list, str, list[str]]:
        """词法零命中、没把握，或问句含手册不认识的实词时，按目录补章节：两路都选中的页排前，
        其余路由页与词法页交替，至多四块（`_merge_routed`）。路由没找到章节时保留原词法结果；只有「手册不认识的实词 +
        路由明确判别的车型」才作废词法近似。确定性范围闸（别的车型、本车没有的对象、手册
        没有的专名）、无内容问句与仪表灯 / 图标问法在它之前拦下，LLM 越不过去。"""
        if self._toc_router is None:
            return list(chunks), "lexical", []
        if any(image.match_kind in {"visual_alias", "visual_caption"}
               for chunk in chunks for image in getattr(chunk, "images", ())):
            return list(chunks), "lexical", []
        # 问句里有手册不认识的实词（奇骏 / 刮车 / 小冰箱）时，词法再自信也要问一次路由：
        # 覆盖率分不开「车主叫法」和「别家车型」，LLM 的常识分得开。
        unknown = self.kb.unknown_subject_terms(question)
        # 有把握 = 覆盖率够线且主题词在首页的章节路径里。只在正文里撞上的首页（「运动模式到底
        # 在哪切换」→ 讲特殊路况的页、「停车监控在哪打开」→ 智能领航）覆盖率再高也要再路由。
        if chunks and not unknown and chunks[0].confident:
            return list(chunks), "lexical", []
        if self.kb.route_block_reason(question):
            return list(chunks), "lexical", []
        verdict = await self._toc_router.route(self.llm, question)
        sections = list(verdict.sections)
        if unknown and verdict.scope == SCOPE_OTHER_VEHICLE:
            # 手册不认识的实词 + 路由明确判「别的车型」→ 词法近似作废（「奇骏的电池容量」
            # 不能拿本车的储电量作答）。只认这一种：「没找到章节」「不是用车问题」都不作废——
            # 模型的判断在温度 0 下也有方差，否决面越小误伤越少。
            return [], "toc_router_rejected", []
        if not sections:
            return list(chunks), "lexical", []
        routed = await self.kb.retrieve_sections(question, sections, top_k=_MAX_CHUNKS)
        if not routed:
            return list(chunks), "lexical", sections
        stage = "toc_router" if not chunks else "lexical+toc_router"
        lexical_first = bool(chunks) and not unknown and chunks[0].coverage >= CONFIDENT_COVERAGE
        return _merge_routed(routed, list(chunks), lexical_first), stage, sections

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
        raw = str(intent.raw_text or "").strip()
        slot = str(intent.slots.get("question", "") or "").strip()
        # 安全分级与告警信号只认用户原话（安全红线 6）；规划器的槽只可能参与检索。
        question = raw or slot
        if not question:
            # 只回答的能力不许挂补槽（安全红线 5；执行器会把 NEED_SLOT 判成契约违规、这句根本发不出去）——就是一句普通回答
            return AgentResult(speech="您想了解车辆的哪方面？")

        level = _safety_level(question)
        vehicle_model = str(
            intent.slots.get("vehicle_model", "")
            or ((meta or {}).get("vehicle_model", "") if hasattr(meta, "get") else "")
        ).strip()
        lookup, basis = _retrieval_question(raw, slot)
        # 按分句取的槽被范围闸否决、原话却没有：否决它的词是规划器写进去的（「胎压应该补到
        # 多少kPa」的 kPa，手册只写 bar），不是用户说的，回到原话。指代补全不回退：那里原话
        # 只有代词，否决可能正是对的（「那它的纯电续航呢」补全出来是别家车型）。
        if (basis == "clause" and self._toc_router is not None
                and self.kb.scope_veto(lookup) and not self.kb.scope_veto(raw)):
            lookup, basis = raw, "raw_slot_vetoed"
        chunks = await self.kb.retrieve(                   # 1) retrieve
            lookup, vehicle_model=vehicle_model)
        chunks, stage, sections = await self._route_if_unsure(lookup, chunks)
        trace = {"retrieval": stage,
                 **({"retrieval_basis": basis} if basis != "raw" else {}),
                 **({"toc_sections": sections} if sections else {})}
        logger.info("manual retrieval stage=%s basis=%s sections=%s chunks=%s",
                    stage, basis, sections, [chunk.page_start for chunk in chunks])

        # ① 零命中短路：不调生成 LLM。安全信号仍要给处置建议——**没有资料不等于没有风险**。
        if not chunks:
            speech = "手册里没有查到这方面的内容，建议联系客服或前往服务点确认。"
            if level:
                speech = f"{alert_advice(level)}{speech}"
            return AgentResult(speech=speech,
                               data=self._safety_data(level, question, **trace),
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
                data=self._safety_data(level, question, source_type=source_type, **trace),
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
                    visual_match=image.caption, **trace),
                ui_card=self._card(chunks, source_type),
            )

        context_block = "\n\n".join(
            f"[资料{i}｜{c.source or '来源未标注'}]"
            f"{''.join(f'｜配图：{image.caption}' for image in getattr(c, 'images', ())) }"
            f"\n{c.content}"
            for i, c in enumerate(chunks, start=1)
        )
        # 检索用的是规划器补全的问题时，把用户原话一并给出：答案要回应的是用户说的那句。
        asked = (f"【用户原话】{raw}\n【问题】{lookup}" if basis != "raw" and raw
                 else f"【问题】{lookup}")
        messages = [                                        # 2) generate
            {"role": "system",
             "content": _SYSTEM_MANUAL if authoritative else _SYSTEM_GENERIC},
            {"role": "user", "content": f"【参考资料】\n{context_block}\n\n{asked}"},
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
                    **trace,
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
                    grounding_rejected="numeric", **generation_data, **trace),
                ui_card=self._card(chunks, source_type),
            )

        # 安全信号（有权威手册）：处置建议**前置**，不让它淹没在模型话术里。
        speech = f"{alert_advice(level)}{answer}" \
            if level else answer
        return AgentResult(
            speech=speech,
            data=self._safety_data(
                level, question, source_type=source_type, **generation_data, **trace),
            ui_card=self._card(chunks, source_type),
        )
