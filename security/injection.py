"""注入防护：工具参数 schema 校验 + 数据区隔离标记。"""
from __future__ import annotations
import re


# LLM 规划产出的 JSON 计划，每个 step 的 slots 需通过 schema 校验
# 这里定义基础校验规则：字段类型、必填、取值范围

class SlotValidator:
    """按 Agent capability 的 slots 声明校验 LLM 产出的槽位。"""

    @staticmethod
    def validate_slots(slots: dict, required_slots: list[str],
                      slot_types: dict[str, str] = None) -> list[str]:
        """校验槽位。返回错误列表（空=通过）。
        slot_types: {"temp": "number", "keyword": "string"} — 可选类型约束
        """
        errors = []
        for req in required_slots:
            if req not in slots or not str(slots[req]).strip():
                errors.append(f"missing required slot: {req}")

        if slot_types:
            for k, v in slots.items():
                if k in slot_types:
                    expected = slot_types[k]
                    if expected == "number":
                        try:
                            float(v)
                        except (ValueError, TypeError):
                            errors.append(f"slot {k} should be number, got: {v}")
                    elif expected == "integer":
                        if not str(v).isdigit():
                            errors.append(f"slot {k} should be integer, got: {v}")

        return errors

    @staticmethod
    def sanitize_text(text: str) -> str:
        """基础文本清洗（防注入用）。去除潜在的指令注入标记。"""
        # 移除常见的注入标记模式
        patterns = [
            r"ignore\s+(all\s+)?previous\s+instructions",
            r"忽略.*指令",
            r"system\s*prompt",
            r"<\|.*?\|>",
        ]
        cleaned = text
        for p in patterns:
            cleaned = re.sub(p, "[filtered]", cleaned, flags=re.IGNORECASE)
        return cleaned


def wrap_data_section(text: str) -> str:
    """把用户输入包装为数据区（明确标记为非指令）。"""
    return f"<user-data>\n{text}\n</user-data>"


def wrap_reference_section(text: str) -> str:
    """把检索资料包装为参考区。"""
    return f"<reference-data>\n{text}\n</reference-data>"
