"""speech 通道 markdown 归一单测（2026-07-12 决策：不上渲染、后端出口硬剥）。"""
from agents._sdk.grounding import strip_markdown_speech


def test_strip_bold_code_heading_quote_bullet():
    raw = ("# 结论\n"
           "> 引用一句\n"
           "**固态电池**的`能量密度`更高。\n"
           "- 要点甲\n"
           "* 要点乙\n"
           "1. 保留数字分行要点")
    out = strip_markdown_speech(raw)
    assert "**" not in out and "`" not in out and "#" not in out and ">" not in out
    assert "固态电池的能量密度更高。" in out
    assert "要点甲" in out and "要点乙" in out
    assert "1. 保留数字分行要点" in out          # 数字序号行是刻意的，不动


def test_strip_table_to_readable_lines():
    raw = ("对比如下：\n"
           "| 型号 | 续航 |\n"
           "|---|---|\n"
           "| A | 700km |\n"
           "| B | 620km |")
    out = strip_markdown_speech(raw)
    assert "|" not in out and "---" not in out
    assert "型号，续航" in out and "A，700km" in out


def test_strip_link_and_fence():
    raw = "详见[官方公告](https://x.com/a)。\n```python\nprint(1)\n```\n完毕。"
    out = strip_markdown_speech(raw)
    assert "官方公告" in out and "https://" not in out and "```" not in out


def test_plain_text_fast_path_untouched():
    plain = "杭州今天多云，28到33度。1. 上午出行 2. 傍晚有风"
    assert strip_markdown_speech(plain) == plain
    assert strip_markdown_speech("") == ""
    # 单个星号（乘号）与行内 > 比较不误伤
    assert strip_markdown_speech("3*4=12，5>2") == "3*4=12，5>2"


def test_parse_synth_rescues_truncated_json_answer():
    """啰嗦 provider 的长 answer 撑爆 max_tokens → JSON 截断：抢救 answer 已生成部分，
    绝不把 JSON 外壳当话术（真栈 @MiniMax 实测整段 {"answer":... 被念出来）。"""
    from agents._sdk.grounding import parse_synth
    trunc = '{"answer": "固态电池产业化进入密集期。\\n一、时间表清晰\\n据36氪报道，比亚迪计划'
    out = parse_synth(trunc)
    assert out is not None
    assert out["answer"].startswith("固态电池产业化进入密集期。")
    assert "一、时间表清晰" in out["answer"]
    assert '{"answer"' not in out["answer"]          # JSON 外壳绝不外泄
    assert out["confidence"] == "low"                 # 截断内容降置信

    # 完整 JSON 不受影响
    full = '{"answer": "结论。", "key_points": [], "confidence": "high", "used_sources": [1]}'
    assert parse_synth(full)["confidence"] == "high"
    # JSON 外壳但没有 answer：交调用方诚实兜底
    assert parse_synth('{"key_points": ["半截') is None
    # 纯文本仍走剥编号路径
    assert parse_synth("1. 甲\n2. 乙")["answer"] == "甲 乙"


def test_parse_synth_survives_naked_quotes_in_answer():
    """badcase 6ce027fe：answer 字符串里裸英文双引号（马拉多纳的"上帝之手"）令整份 JSON
    非法——边界式抢救须取到**真正的字段结尾**，不得在裸引号处拦腰截断。"""
    from agents._sdk.grounding import parse_synth
    raw = ('{"answer": "两队渊源颇深（1986年马拉多纳的"上帝之手"等经典对决），'
           '这场半决赛也被称为"宿命对决"。", "key_points": ["渊源"], '
           '"confidence": "high", "used_sources": [1]}')
    out = parse_synth(raw)
    assert out is not None
    assert out["answer"].endswith("也被称为\"宿命对决\"。")   # 完整取到句号，裸引号保留为文本
    assert "上帝之手" in out["answer"]
    assert out["confidence"] == "high"                        # 边界闭合时后续字段仍可信


def test_extract_json_str_field_boundaries():
    from agents._sdk.grounding import extract_json_str_field
    # 正常闭合
    v, closed = extract_json_str_field('{"a": "文本", "b": []}', "a", ("b",))
    assert v == "文本" and closed is True
    # 裸引号不当边界
    v, closed = extract_json_str_field('{"a": "他说"你好"再见", "b": 1}', "a", ("b",))
    assert v == '他说"你好"再见' and closed is True
    # 截断：无边界取到末尾
    v, closed = extract_json_str_field('{"a": "没写完就断', "a", ("b",))
    assert v == "没写完就断" and closed is False
    # 转义序列反解
    v, _ = extract_json_str_field('{"a": "一\\n二\\"三\\"", "b": 1}', "a", ("b",))
    assert v == '一\n二"三"'
    # 字段缺失
    assert extract_json_str_field('{"x": 1}', "a", ("b",)) == ("", False)
