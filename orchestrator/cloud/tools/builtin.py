"""Built-in deterministic tool implementations."""
from __future__ import annotations

import ast
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ToolInputError(ValueError):
    pass


def _shanghai_tz():
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), name="Asia/Shanghai")


_BINARY_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}
_UNARY_OPS = {
    ast.UAdd: lambda a: a,
    ast.USub: lambda a: -a,
}


def math_eval(slots: dict, _now_fn=None) -> tuple[dict, str]:
    expression = (slots.get("expression") or slots.get("text") or "").strip()
    if not expression or len(expression) > 160:
        raise ToolInputError("invalid expression")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolInputError("invalid expression") from exc
    if sum(1 for _ in ast.walk(tree)) > 32:
        raise ToolInputError("expression is too complex")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ToolInputError("only numbers are allowed")
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 10:
                raise ToolInputError("exponent is too large")
            return _BINARY_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](visit(node.operand))
        raise ToolInputError("unsupported expression")

    try:
        result = visit(tree)
    except (ArithmeticError, OverflowError) as exc:
        raise ToolInputError(str(exc)) from exc
    if isinstance(result, float) and not math.isfinite(result):
        raise ToolInputError("result is not finite")
    return {"result": result}, f"计算结果是{result}"


_UNITS = {
    "mm": ("length", 0.001),
    "cm": ("length", 0.01),
    "m": ("length", 1.0),
    "km": ("length", 1000.0),
    "g": ("mass", 0.001),
    "kg": ("mass", 1.0),
    "m/s": ("speed", 1.0),
    "km/h": ("speed", 1 / 3.6),
}


def unit_convert(slots: dict, _now_fn=None) -> tuple[dict, str]:
    try:
        value = float(slots.get("value", ""))
    except (TypeError, ValueError) as exc:
        raise ToolInputError("value must be numeric") from exc
    source = (slots.get("from_unit") or "").strip()
    target = (slots.get("to_unit") or "").strip()

    if source in ("C", "°C", "celsius") and target in ("F", "°F", "fahrenheit"):
        converted = value * 9 / 5 + 32
    elif source in ("F", "°F", "fahrenheit") and target in ("C", "°C", "celsius"):
        converted = (value - 32) * 5 / 9
    else:
        if source not in _UNITS or target not in _UNITS:
            raise ToolInputError("unsupported unit")
        source_dim, source_factor = _UNITS[source]
        target_dim, target_factor = _UNITS[target]
        if source_dim != target_dim:
            raise ToolInputError("incompatible units")
        converted = value * source_factor / target_factor

    if abs(converted - round(converted)) < 1e-12:
        converted = int(round(converted))
    else:
        converted = round(converted, 6)
    return (
        {"value": converted, "unit": target, "from_value": value, "from_unit": source},
        f"{value:g}{source}等于{converted}{target}",
    )


def datetime_parse(slots: dict, now_fn=None) -> tuple[dict, str]:
    text = (
        slots.get("text") or slots.get("value") or slots.get("datetime") or ""
    ).strip()
    if not text:
        raise ToolInputError("missing datetime text")
    now_fn = now_fn or (lambda: datetime.now(_shanghai_tz()))
    now = now_fn()
    if now.tzinfo is None:
        now = now.replace(tzinfo=_shanghai_tz())

    normalized = re.sub(r"\s+", "", text)
    if normalized in {"今天", "今日", "本日", "今天是几号", "今天几号", "今日几号", "今天日期", "今日日期",
                      "今天星期几", "今天周几", "今日星期几", "今日周几"}:
        weekday = "一二三四五六日"[now.weekday()]
        date = now.date()
        return (
            {"date": date.isoformat(), "weekday": f"星期{weekday}",
             "timezone": str(now.tzinfo)},
            f"今天是{date.year}年{date.month}月{date.day}日，星期{weekday}。",
        )

    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=now.tzinfo)
    except ValueError:
        day_offset = 0
        if "后天" in text:
            day_offset = 2
        elif "明天" in text or "明晚" in text:
            day_offset = 1
        match = re.search(r"(\d{1,2})(?:[:点时](\d{1,2})?)", text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
        elif "今晚" in text or "明晚" in text:
            hour, minute = 19, 0
        else:
            raise ToolInputError("unsupported datetime format")
        if hour > 23 or minute > 59:
            raise ToolInputError("invalid time")
        date = (now + timedelta(days=day_offset)).date()
        parsed = datetime(
            date.year, date.month, date.day, hour, minute, tzinfo=now.tzinfo)

    iso = parsed.isoformat()
    return {"iso8601": iso, "timezone": str(parsed.tzinfo)}, f"时间已确定为{iso}"
