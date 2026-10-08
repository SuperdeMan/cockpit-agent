"""目的地候选卡（`dest_choice`）的续接：写候选、续接轮「第 N 个」按序回填名字。

充电（泛目的地澄清）与导航（近处借名 vs 外地本体，docs/design/2026-10-04-destination-borrowed-name.md）共用这一份，
序数判据只有这一处。引擎补槽把用户原话原样灌进槽位：说「第一个」时槽值就是字面「第一个」，Agent 侧要能解序号，
否则会拿字面去搜 POI（旅程 B2-3：真栈选到当前位置旁的无关站）。候选按卡片渲染序存在会话共享态里，命中即消费清空；
没有候选 / 越界原样返回，走后续正常解析。
"""
from __future__ import annotations

import json
import logging
import re

_ORD_CHARS = r"[一二两三四五六七八九十\d]"
ORDINAL_DEST_RE = re.compile(rf"^第?\s*({_ORD_CHARS})\s*[个家项处]?$")
#: 句中的序号：带「第」，或阿拉伯数字带量词（「换第二个」「第2个吧」「2个」）。不带「第」的汉字数字不算——「换一家」的「一」不是序号
_ORDINAL_IN_RE = re.compile(rf"第\s*({_ORD_CHARS})\s*[个家项处]?|(\d)\s*[个家项处]")
_CN_ORD = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_log = logging.getLogger("agent.dest_choice")


def _ord_value(v: str) -> int:
    return int(v) if v.isdigit() else _CN_ORD.get(v, 0)


def ordinal_index(text: str) -> int:
    """「第一个」「2」「第二家」→ 1 起的序号；不是纯序号 ⇒ 0。"""
    m = ORDINAL_DEST_RE.match((text or "").strip())
    return _ord_value(m.group(1)) if m else 0


def ordinal_in(text: str) -> int:
    """原话里带的序号：整句就是序号的同 `ordinal_index`；补槽答案常带着「换」「吧」这类词（「换第二个」）⇒ 句中找；没有 ⇒ 0。"""
    idx = ordinal_index(text)
    if idx:
        return idx
    m = _ORDINAL_IN_RE.search(text or "")
    return _ord_value(m.group(1) or m.group(2)) if m else 0


def _stored(item: dict) -> dict:
    out = {"name": item.get("name", ""), "address": item.get("address", "")}
    lat, lng = item.get("lat"), item.get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)) and (lat or lng):
        out.update(lat=float(lat), lng=float(lng))   # 选中的是一个具体地点：续接时直接用它，不再重新搜
    return out


async def save_choices(ctx, key: str, items: list[dict]) -> None:
    """写候选（序 = 卡片渲染序，带坐标的连坐标一起存）；写不进去只影响续接回填，不影响本轮回答。"""
    if ctx is None:
        return
    try:
        await ctx.save_shared_state(key, {"items": [
            _stored(it) for it in items if isinstance(it, dict) and it.get("name")]})
    except Exception as exc:
        _log.debug("dest choices save skipped: %s", exc)


async def _load(ctx, key: str) -> list[dict]:
    try:
        data = await ctx.load_shared_state(key)
        d = json.loads(data) if isinstance(data, str) else (data or {})
    except Exception:
        return []
    return [it for it in (d.get("items") or []) if isinstance(it, dict)]


async def resolve_choice(ctx, key: str, answer: str) -> dict | None:
    """续接轮的槽值是字面序号，或就是候选里的名字（车机点选会发名字）⇒ 那个候选（命中即消费清空）；否则 None。"""
    text = (answer or "").strip()
    if ctx is None or not text:
        return None
    items = await _load(ctx, key)
    idx = ordinal_index(text)
    pick = (items[idx - 1] if 0 < idx <= len(items)
            else next((it for it in items if it.get("name") == text), None))
    if not pick or not pick.get("name"):
        return None
    try:
        await ctx.save_shared_state(key, {})   # 消费即清
    except Exception:
        pass
    _log.info("dest choice %r -> %r", text, pick["name"])
    return dict(pick)


async def resolve_ordinal(ctx, key: str, dest: str) -> str:
    """槽值是字面序号 ⇒ 按上一轮候选回填真名（命中即消费清空）；否则原样返回。"""
    idx = ordinal_index(dest)
    if not idx or ctx is None:
        return dest
    items = await _load(ctx, key)
    if 0 < idx <= len(items) and items[idx - 1].get("name"):
        name = str(items[idx - 1]["name"])
        try:
            await ctx.save_shared_state(key, {})   # 消费即清
        except Exception:
            pass
        _log.info("dest ordinal %r -> %r", dest, name)
        return name
    return dest
