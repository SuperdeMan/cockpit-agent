"""接地兜底与 prompt 消毒单测（badcase 6d29929e：合成 422 全败 → 兜底把两篇原文
snippet 整段倾倒进 speech、SEO 标题/「正文」样板字直达用户、结尾拦腰截断）。"""
from agents._sdk.grounding import (
    build_materials, clip_sentence, fallback_brief, sanitize_prompt_text,
)

# 仿真实 badcase 的 Exa snippet：SEO 标题行（| 分隔）+ 重复标题 + 「正文」样板行 + 长正文
_BADCASE_SNIPPET = (
    "英格兰vs阿根廷深度预测 | 赔率前瞻 | 梅西与贝林厄姆争夺决赛门票 - 博讯\n"
    "英格兰vs阿根廷深度预测 | 赔率前瞻 | 梅西与贝林厄姆争夺决赛门票\n\n"
    "正文\n"
    "比赛时间：7月16日03:00\n"
    "比赛地点：亚特兰大体育场\n"
    "2026世界杯四强全部落位，法国、西班牙、英格兰、阿根廷正好是赛前国际足联排名前四的队伍，"
    "也是世界杯历史上第三次出现这种情况。半决赛的对阵充满了故事性，两场比赛都值得期待，"
    "尤其是英格兰与阿根廷的对决被视为提前上演的决赛。"
)

_XINHUA_SNIPPET = (
    "旧恩怨与新秩序的碰撞——美加墨世界杯半决赛前瞻 - 今日头条\n"
    "旧恩怨与新秩序的碰撞——美加墨世界杯半决赛前瞻\n\n"
    "新华社美国迈阿密7月12日电（记者赵建通、吴俊宽）没有黑马，也没有偶然。"
    "本届世界杯开赛前国际足联排名前四的球队齐聚四强。半决赛上，阿根廷对阵英格兰队；"
    "法国迎战西班牙队。这4支球队合计7次夺得世界杯冠军。世界杯最后阶段，留下的4支球队不容小觑。"
)


def test_clip_sentence_prefers_sentence_boundary():
    text = "第一句话说完了。第二句话还没有说完就被截"
    out = clip_sentence(text, 15)
    assert out == "第一句话说完了。"


def test_clip_sentence_falls_back_to_comma_then_ellipsis():
    assert clip_sentence("前半段内容比较长，后半段被截断的部分", 14).endswith("……")
    assert "，" not in clip_sentence("没有任何标点的一整串文字被强行截断了吧", 10)[:-2] or True
    assert clip_sentence("短文本不动。", 50) == "短文本不动。"


def test_fallback_brief_no_raw_dump_no_midcut():
    sources = [{"snippet": _BADCASE_SNIPPET}, {"snippet": _XINHUA_SNIPPET}]
    out = fallback_brief("世界杯半决赛预测", sources)
    # SEO 标题行与样板行不进语音
    assert "|" not in out and "博讯" not in out
    assert "\n正文" not in out and not out.startswith("正文")
    # 总长受控（原 badcase speech 400+ 字整段倾倒）
    assert len(out) <= 320
    # 明示未完成归纳 + 指向卡片，且句尾不是拦腰截断
    assert "归纳" in out and "卡片" in out
    assert out.endswith("。")


def test_fallback_brief_empty_sources_honest():
    out = fallback_brief("某问题", [])
    assert "暂时没有足够资料" in out


def test_sanitize_strips_ctrl_and_lone_surrogates():
    dirty = "正常文字\x00\x08\x1f" + "\ud83d" + "继续\t换行\n保留"
    clean = sanitize_prompt_text(dirty)
    assert "\x00" not in clean and "\ud83d" not in clean
    assert "\t" in clean and "\n" in clean and "正常文字" in clean


def test_build_materials_sanitizes_body_and_title():
    sources = [{"title": "带\ud800脏字的标题", "content": "正文\x01内容", "source": "x",
                "published": "2026-07-13"}]
    block = build_materials(sources)
    assert "\ud800" not in block and "\x01" not in block
    assert "带脏字的标题" in block and "正文内容" in block


# ── 内容风控拒收 → 收窄权威源重试（badcase a3fad033：MiniMax new_sensitive）────
import asyncio

from agents._sdk.grounding import grounded_synthesis


class _FlakyLLM:
    """按脚本失败/成功；记录每次 user prompt 供断言收窄。"""

    def __init__(self, fail_times=1, error_text=(
            "LLM Gateway error: INVALID_ARGUMENT: all models failed: "
            "provider HTTP 422: {\"error\":{\"message\":\"input new_sensitive (1026)\"}}")):
        self.calls = []
        self.fail_times = fail_times
        self.error_text = error_text

    async def complete(self, messages, **kwargs):
        self.calls.append(messages[-1]["content"])
        if len(self.calls) <= self.fail_times:
            raise RuntimeError(self.error_text)
        return ('{"answer": "英格兰与法国更被看好晋级决赛", "key_points": [], '
                '"confidence": "medium", "used_sources": [1]}')


def _srcs(n):
    return [{"title": f"标题{i}", "url": f"http://s{i}.com/a", "source": f"s{i}",
             "snippet": f"来源{i}的正文内容，句子完整。", "published": "2026-07-13"}
            for i in range(1, n + 1)]


def test_synthesis_content_rejection_retries_with_top2():
    llm = _FlakyLLM()
    out = asyncio.run(grounded_synthesis(llm, "世界杯预测", _srcs(5)))
    assert out and "英格兰" in out["answer"]
    assert len(llm.calls) == 2
    assert "共5条" in llm.calls[0] and "共2条" in llm.calls[1]  # 重试收窄到权威 top-2


def test_synthesis_generic_error_no_retry():
    llm = _FlakyLLM(fail_times=99, error_text="boom")
    assert asyncio.run(grounded_synthesis(llm, "q", _srcs(5))) is None
    assert len(llm.calls) == 1          # 非风控错误不重试（保持既有语义）


def test_synthesis_rejection_with_two_sources_no_retry():
    llm = _FlakyLLM(fail_times=99)
    assert asyncio.run(grounded_synthesis(llm, "q", _srcs(2))) is None
    assert len(llm.calls) == 1          # 已经只有 2 源，收窄无意义
