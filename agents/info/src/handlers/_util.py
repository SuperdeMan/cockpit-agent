"""info Agent 跨域共享工具：繁→简转换 / 上海时区 now / 坐标标签判定。

无 agent/handlers 依赖（避免循环导入）；各 handler 与 agent.py 均从此处导入。
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:                                   # 繁→简（台/港源新闻标题/摘要归一简体）；纯 Python 轻量
    from zhconv import convert as _zhconv_convert
except Exception:                      # 未装则降级原样返回，不阻断（守卫式）
    _zhconv_convert = None


def _to_simplified(text: str) -> str:
    """繁体→简体中文。zhconv 未装/异常时原样返回（对已是简体的文本幂等无副作用）。"""
    if not text or _zhconv_convert is None:
        return text or ""
    try:
        return _zhconv_convert(text, "zh-cn")
    except Exception:
        return text


def _shanghai_now() -> datetime:
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except ZoneInfoNotFoundError:
        return datetime.now(timezone(timedelta(hours=8), name="Asia/Shanghai"))


def _is_coordinate_label(value: str) -> bool:
    """防止 mock/异常上游把 ``lng,lat`` 直接展示给用户。"""
    try:
        lng, lat = str(value).split(",", 1)
        float(lng)
        float(lat)
        return True
    except (TypeError, ValueError):
        return False
