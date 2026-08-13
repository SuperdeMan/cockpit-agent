"""`_relevance_material`：summarize 话术取材的相关性打包（demo-mkemhn c47671f5）。

营养表这类大列表此前被 `[:3000]` 盲截，排在截断点之后的条目对 LLM 等于不存在——
「蘸酱麦辣大四角的热量」在菜单/订单预览里都出现过，营养 material 却截在它之前，
于是诚实地答了「没查到」。取材器是纯函数，零 LLM 零网络。
"""
from __future__ import annotations

import json

from agents.mcp_bridge.src.agent import McpBridgeAgent

_pack = McpBridgeAgent._relevance_material


def _nutrition_blob(target: str, *, filler: int) -> str:
    """构造一份把目标条目压在 filler 条之后的营养表（单条 ≈40 字符）。"""
    rows = [f"{{\"name\":\"填充餐品第{i}号\",\"energy\":\"{500 + i}kJ\"}}"
            for i in range(filler)]
    rows.append(f"{{\"name\":\"{target}\",\"energy\":\"2247kJ\",\"protein\":\"25g\"}}")
    return "\n".join(rows)


def test_target_item_beyond_the_old_cutoff_survives_packing():
    target = "蘸酱麦辣大四角"
    blob = _nutrition_blob(target, filler=200)     # 远超 3000 字符
    material = _pack("蘸酱麦辣大四角的热量是多少", "", {"data": blob})

    assert len(material) <= 3000 + 40
    assert target in material
    assert "已按与问题的相关性筛选" in material


def test_short_content_passes_through_verbatim():
    payload = {"data": "巨无霸 2247kJ"}
    text = "# API Response\n巨无霸营养表"
    material = _pack("巨无霸多少大卡", text, payload)

    assert material == text + "\n" + json.dumps(payload, ensure_ascii=False)


def test_question_without_signal_falls_back_to_head_truncation():
    """问句提不出 bigram（太短/纯符号）：没有更好的依据就退回头部截断，不假装有。"""
    blob = _nutrition_blob("目标条目", filler=200)
    material = _pack("嗯？", "", {"data": blob})

    assert material == ("\n" + json.dumps({"data": blob},
                                          ensure_ascii=False))[:3000]


def test_relevant_rows_keep_original_order():
    """选中的片段按原文顺序拼回——榜单/列表的相对顺序是信息，不能按得分洗牌。"""
    rows = [f"{{\"name\":\"无关填充第{i}号\",\"v\":{i}}}" for i in range(150)]
    rows.insert(10, "{\"name\":\"麦辣鸡腿堡\",\"energy\":\"2100kJ\"}")
    rows.insert(80, "{\"name\":\"麦辣鸡翅\",\"energy\":\"1100kJ\"}")
    material = _pack("麦辣鸡腿堡和麦辣鸡翅的热量", "", {"data": "\n".join(rows)})

    assert material.index("麦辣鸡腿堡") < material.index("麦辣鸡翅")
