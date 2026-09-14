"""`runtime/reported_speech` 的两向断言 + 「判据零领域词」的源码级钉子。

它是写操作的否决面（`classify_structured` 出口，与 question_shape / polarity 同一个落点），
所以两向都要有：挡住的那一半（播报 / 转述语域）和**不许误伤**的那一半（正常「播放新闻」）。
正例的第一条就是 2026-09-11 真栈探针里那句把媒体置成 playing 的原话，不改一个字。
"""
from __future__ import annotations

import os

import pytest
import yaml

from runtime.reported_speech import BROADCAST_FRAMES, is_reported_speech

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_COMMANDS = os.path.join(_ROOT, "orchestrator", "edge", "knowledge", "commands.yaml")


def _domain_vocabulary() -> set[str]:
    """VAL 知识库里的领域词（对象 id / 中文名 / intent 段）——从知识库派生，不手抄。"""
    with open(_COMMANDS, encoding="utf-8") as handle:
        commands = yaml.safe_load(handle) or {}
    vocab: set[str] = set()
    for name, spec in (commands.get("objects") or {}).items():
        vocab.add(str(name))
        display = str((spec or {}).get("display_name") or "").strip()
        if display:
            vocab.add(display)
        for intent in ((spec or {}).get("edge_intents") or []):
            vocab.update(str(intent).split("."))
    return {word for word in vocab if word}


# ── 1. 判据零领域词 ────────────────────────────────────────────────────────

def test_domain_vocabulary_probe_is_not_empty():
    vocab = _domain_vocabulary()
    assert len(vocab) > 50, f"知识库派生词表只有 {len(vocab)} 条，扫描口径不对"
    assert "空调" in vocab


def test_no_frame_word_is_domain_vocabulary():
    """任一语域标记撞上领域词 = 这份「语域判据」退化成了「新闻词表黑名单」。"""
    vocab = _domain_vocabulary()
    for frame in BROADCAST_FRAMES:
        assert frame not in vocab, f"`{frame}` 是 VAL 领域词——判据必须是语域标记"
        for media in ("新闻", "音乐", "歌", "广播", "电台", "视频", "有声书"):
            assert media not in frame, f"`{frame}` 含媒体对象词 `{media}`——那是词表黑名单，不是语域"


# ── 2. 挡住的那一半：播报 / 转述语域 ───────────────────────────────────────

@pytest.mark.parametrize("text", [
    # 2026-09-11 真栈探针首轮：端侧 0.97s 判成 media.play 的原话（trace ba82484fa8c54b9a）
    "欢迎收听今天的节目，本台记者为您报道新闻。",
    "本台记者报道，项目建设已经进入第二阶段。",
    "各位听众朋友大家好，下面请听一段音乐。",
    "以上是今天的全部内容，感谢收听。",
    "据报道，明天本市将有大到暴雨，请注意开窗通风。",
    "接下来为您播出天气预报。",
    "新华社消息：某地举办音乐节，欢迎收看。",
])
def test_broadcast_register(text):
    assert is_reported_speech(text) is True, text


# ── 3. 不许误伤的那一半：对助手的正常请求 ─────────────────────────────────

@pytest.mark.parametrize("text", [
    "播放新闻", "我要听体育新闻", "来段新闻", "帮我播今天的头条", "听听新闻",
    "把新闻关掉", "查一下今天人工智能行业的重要新闻",
    "播放记者会直播",              # 「记者」单独出现是正常指令
    "我妈说让我把车窗关上",        # 用户转达指令：不是播报
    "给我放首歌", "打开空调", "现在几点了", "",
])
def test_user_requests_pass(text):
    assert is_reported_speech(text) is False, text
