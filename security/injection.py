"""注入防护：工具参数 schema 校验 + 数据区隔离标记 + prompt injection 检测。

ws8 P1: 增强注入检测——覆盖中英文变体，用于 Planner 入口拦截。
"""
from __future__ import annotations
import re


# ── Prompt Injection 检测（ws8 P1）─────────────────────────────────────────

_INJECTION_PATTERNS = [
    # 英文变体
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(all\s+)?prior\s+instructions",
    r"forget\s+(your|all)\s+(previous\s+)?rules",
    r"you\s+are\s+now",
    r"system\s*:",
    r"override\s+safety",
    r"bypass\s+(all\s+)?restrictions",
    r"act\s+as\s+if",
    r"pretend\s+you\s+are",
    r"new\s+instructions",
    r"disregard\s+(all\s+)?previous",
    r"<\|.*?\|>",                    # special tokens
    r"\[INST\]",                     # Llama format
    r"###\s*system",                 # system prompt markers
    # 中文变体
    r"忽略.*之前.*指令",
    r"忽略.*指令",
    r"无视.*规则",
    r"你现在是",
    r"系统提示",
    r"假装你是",
    r"扮演.*角色",
    r"新指令",
    r"覆盖.*安全",
]

_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def detect_injection(text: str) -> bool:
    """检测用户输入中的 prompt injection 模式。返回 True 表示疑似注入。"""
    if not text:
        return False
    return bool(_INJECTION_RE.search(text))


def sanitize_injection(text: str) -> str:
    """清洗疑似注入内容（替换为 [filtered]）。"""
    if not text:
        return text
    return _INJECTION_RE.sub("[filtered]", text)


# ── 槽位校验 ──────────────────────────────────────────────────────────────

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
        return sanitize_injection(text)


# ── 数据区隔离 ─────────────────────────────────────────────────────────────

def wrap_data_section(text: str) -> str:
    """把用户输入包装为数据区（明确标记为非指令）。"""
    return f"<user-data>\n{text}\n</user-data>"


def wrap_reference_section(text: str) -> str:
    """把检索资料包装为参考区。"""
    return f"<reference-data>\n{text}\n</reference-data>"
