"""目录路由：词法检索没把握时，让 LLM 在手册目录（封闭集合）里选章节。

词法层只认字面：「停车时有人刮车能录下来吗」「车顶上凸出来那个是干嘛的」「后门怎么设置成
从里面打不开」在手册里分别叫哨兵模式、激光雷达、车门儿童锁，一个字都对不上。同义词表
补不完这种描述式问法。

这里 LLM 只做两件事：从获准索引还原出的目录编号里挑 ≤3 个；说明这句话问的是不是这台车
（`scope`）。它不产答案、不产页码、不产检索词——编号由代码校验存在后取对应真实页，答案仍由
正文生成并过数值接地；路由失败或超时就当它没说话（回落词法结果），不因为多了一层而多一种
失败。`scope` 只在问句含手册不认识的实词时才被 Agent 用来作废词法近似（`agent.py`）。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Sequence

from agents.manual_rag.src.toc import TocEntry

logger = logging.getLogger(__name__)

MAX_SECTIONS = 3
ROUTER_TIMEOUT_S = 8.0
SCOPE_THIS_VEHICLE = "this_vehicle"
SCOPE_OTHER_VEHICLE = "other_vehicle"
SCOPE_OFF_TOPIC = "off_topic"
_SCOPES = frozenset({SCOPE_THIS_VEHICLE, SCOPE_OTHER_VEHICLE, SCOPE_OFF_TOPIC})

_SYSTEM = (
    "你是《{title}》的目录检索器。用户在车上问了一个问题，请做两件事：\n"
    "1. scope：这句话问的是不是这台车。问这台车的使用、功能、规格、保养、应急 → "
    "\"this_vehicle\"；问的是其他品牌或其他车型 → \"other_vehicle\"；不是用车问题"
    "（天气、手机、闲聊等）→ \"off_topic\"。\n"
    "2. sections：从【目录】里选出最可能写着答案的章节编号，最多 3 个，按相关度从高到低；"
    "只选你确信会写到这个问题的章节，章节名只是字面相近不算；scope 不是 this_vehicle "
    "或找不到相关章节时给空数组。\n"
    '只输出 JSON，例如 {{"scope": "this_vehicle", "sections": ["T012", "T013"]}}，不要解释。'
)
_JSON_RE = re.compile(r"\{.*\}", re.S)
_ENTRY_RE = re.compile(r"T\d{3}")


@dataclass(frozen=True)
class RouteVerdict:
    sections: tuple[str, ...] = ()
    # 空串 = 没有给出可用的判断（解析失败 / 调用失败 / 值不在词表里）。
    scope: str = ""


def parse_verdict(raw: str, valid_ids: Sequence[str]) -> RouteVerdict:
    """模型输出 → 路由结论。只收目录里真实存在的编号，去重、按原顺序、至多三个；
    scope 只收词表内的值；任何解析失败都是空结论（= 路由没有说话）。"""
    match = _JSON_RE.search(str(raw or ""))
    if not match:
        return RouteVerdict()
    try:
        payload = json.loads(match.group(0))
    except (TypeError, ValueError):
        return RouteVerdict()
    if not isinstance(payload, dict):
        return RouteVerdict()
    scope = str(payload.get("scope") or "").strip()
    scope = scope if scope in _SCOPES else ""
    sections = payload.get("sections")
    allowed = set(valid_ids)
    picked: list[str] = []
    if isinstance(sections, list) and scope != SCOPE_OTHER_VEHICLE and scope != SCOPE_OFF_TOPIC:
        for item in sections:
            entry_id = str(item or "").strip().upper()
            if _ENTRY_RE.fullmatch(entry_id) and entry_id in allowed and entry_id not in picked:
                picked.append(entry_id)
            if len(picked) >= MAX_SECTIONS:
                break
    return RouteVerdict(tuple(picked), scope)


class ManualTocRouter:
    """持有目录与提示词；LLM 客户端在调用时传入（Agent 的 `self.llm` 可能被替换）。"""

    def __init__(self, toc: Sequence[TocEntry], *, title: str):
        self._toc = list(toc)
        self._valid_ids = [entry.entry_id for entry in self._toc]
        self._system = _SYSTEM.format(title=title or "车型用户手册")
        self._toc_block = "\n".join(f"{entry.entry_id} {entry.label}" for entry in self._toc)

    async def route(self, llm, question: str) -> RouteVerdict:
        if not question.strip() or not self._toc:
            return RouteVerdict()
        messages = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": f"【目录】\n{self._toc_block}\n\n【问题】{question}"},
        ]
        try:
            raw = await llm.complete(
                messages, temperature=0.0, max_tokens=100, timeout=ROUTER_TIMEOUT_S)
        except RuntimeError as exc:
            # 与答案生成同一口径：LLMClient 把服务商/传输失败归一成 RuntimeError；
            # 编程错误照常抛出，不伪装成“路由没结论”。
            logger.warning("manual toc routing failed: %s", exc)
            return RouteVerdict()
        return parse_verdict(raw, self._valid_ids)
