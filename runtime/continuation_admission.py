"""续问窗（唤醒后连续对话）与播报中插话的受话判据——两条调用路径共用这一份。

- 云端编排：续问窗 / 插话来源的新请求与规划并行判一次（`orchestrator/cloud/engine.py`，设计
  docs/design/2026-10-10-handsfree-followup-rejection.md §4.1）；
- llm-gateway：播报中有人插话，先单独问一次是不是对助手说的，判定期间旧回答压低接着播（`/api/voice/admission`，
  设计 docs/design/2026-10-11-semantic-barge-in.md §4.1）。

本模块只拼消息、解析输出、按「判否再问一次」编排两问；模型调用由调用方注入（`llm_fn(messages)` → 原始文本），不碰网络。
"""
from __future__ import annotations

import json
import os
import re

from runtime.memory_directive import is_memory_directive


def continuation_timeout_s() -> float:
    """续问窗 / 插话受话判定每一问的上限（秒）。云端那一路与规划并行，受话轮不增加可感时延，所以比挂起续接那道
    （`ADMISSION_TIMEOUT_S`）宽：2026-10-11 真栈探针里 3 s 上限下两句判定在规划同时进行时超时、回落规划器的宽松判定，
    乘客的话被放行。代码缺省 5.0，不进 `.env.example`（部署闸按路径硬阻断）；非法值回落缺省。"""
    try:
        value = float(os.getenv("CONTINUATION_ADMISSION_TIMEOUT_S", "") or 5.0)
    except ValueError:
        return 5.0
    return value if value > 0 else 5.0

_OBJECT_RE = re.compile(r"\{[^{}]*\}")


def parse_admission(raw) -> bool | None:
    """模型输出 → True / False；解析不出（空、非 JSON、没有布尔的 `addressed`）⇒ None，由调用方按受话处理。"""
    text = str(raw or "").strip()
    if not text:
        return None
    for candidate in [text] + _OBJECT_RE.findall(text):
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("addressed"), bool):
            return data["addressed"]
    return None


# ─── 续问窗（唤醒后连续对话）的受话判定（2026-10-10，设计 docs/design/2026-10-10-handsfree-followup-rejection.md）───
#
# 喊过唤醒词、助手答完之后麦克风还开着几秒：这段时间收进来的话**没喊唤醒词**，车里的人声、电话、电台都会进来。
# 云端 `orchestrator/cloud/admission.py` 那组判据是给唤醒词那一轮 / 按住说话用的，拿不准就判受话（用户刚明确叫过助手）；续问窗里同样的先验会把
# 乘客对话收进来——线上 F12 六句非受话句按续问窗来源发 7 趟只拒掉 48%。这里是续问窗自己的一份判据：
# 先验是「很多不是对你说的」，拿不准时只有一句完整的、要车载助手办的事才判受话。判否再问一次、两次都否才拒：
# 单次判定在短句上会随机翻转（离线每句 3 次里错 1 次、分散在不同句子上），真非受话两次都判否（§2.3）。
# 只用于新请求那条路；挂起续接（确认 / 补槽 / 澄清选择）仍用云端那份轻量判定（`admission.judge_addressed`）——
# 助手刚问了问题，这一句大概率就是答案。播报中插话（`voice_bargein`）同属续问窗来源：客户端先在 llm-gateway 问过一次、
# 判受话才把这句当正式一轮发出（带 `voice_admitted=1`，云端不再问第二遍；设计 docs/design/2026-10-11-semantic-barge-in.md）。

#: 没喊唤醒词、在助手答完之后的续问窗里收到的来源（播报中插话同属这一类）——续问窗判据的作用域。
CONTINUATION_SOURCES = frozenset({"voice_followup", "voice_bargein"})
#: 助手名缺省（与闲聊人设同名）；客户端设置里改过名字时取 `meta.assistant_name`。
DEFAULT_ASSISTANT_NAME = "小舟"
_NAME_RE = re.compile(r"[^一-鿿A-Za-z0-9]")

_CONTINUATION_SYSTEM = (
    "你是车载语音助手的受话判定器。用户刚才喊唤醒词叫醒了你、你回答完以后，麦克风会继续开几秒，"
    "这几秒里车里收到的声音很多不是对你说的：乘客之间聊天、打电话、孩子说话、收音机 / 视频 / 导航播报、自言自语、半截话。"
    "你只判断这一句是不是对你说的——不回答它、不执行它、不评价它。\n"
    "输出顶层布尔字段 \"addressed\"。\n"
    "判 true（对你说的），满足其一即可：\n"
    "- 接着你上一句往下说：追问、追加、修改、纠正你，或对你的回答表示不满（「那明天呢」「换一首」「走高速吧」「不对，是北门」「没听懂」）\n"
    "- 在回答你上一句的提问：你问了去哪、几点、哪一个、要不要、确认吗，一个词或一个短语的回答就是在回答你\n"
    "- 一句完整的、要车载助手办的事：车控、导航、音乐、查询、提醒、讲笑话等（「打开车窗」「现在几点了」「我有点冷」"
    "「明天杭州天气怎么样」「算了不去了，回家」）——这类话跟你上一句有没有关系都判 true\n"
    "- 叫了你的名字（语音识别常把名字写成同音字，比如把「小舟」写成「小周」），或让你停下、结束（「停」「好了可以了」）\n"
    "判 false（不是对你说的）：\n"
    "- 在对车里另一个人说话：称呼别人（妈、老公、宝贝、小王、王总），用「你」问别人自己的事（你冷不冷、你吃饭了没、你昨天几点睡的、"
    "你想吃什么），让别人帮忙拿东西，聊别人的事\n"
    "- 打电话（「喂，你好」「听得到吗」「我大概二十分钟到」「回头再说」）、孩子对大人说话（「妈妈我要…」「爸爸还要多久」）\n"
    "- 播报腔：电台、新闻、报站、导航提示、广告、歌词（「下一站…请从后门下车」「接下来为您播放」「前方两百米靠右」）\n"
    "- 自言自语和感叹（「钥匙放哪了」「这个红绿灯怎么这么长」）、附和与残句（「哈哈对对对」「嗯这样啊」「然后他就」）\n"
    "注意：车里的人也会互相用「你」——只有在接你的话、回答你的提问或要你办事时，「你」才是在叫你。\n"
    "拿不准时：是一句完整的、要车载助手办的事就输出 true；否则输出 false——这几秒里把别人的闲聊当成指令，"
    "比漏掉一句更打扰人，用户真要找你会再喊唤醒词。\n"
    "只输出一个 JSON 对象：{\"addressed\": true} 或 {\"addressed\": false}，不要输出其他任何内容。"
)


def is_continuation_source(source) -> bool:
    """这一句是续问窗里没喊唤醒词就收进来的（`voice_followup` / `voice_bargein`）。"""
    return str(source or "") in CONTINUATION_SOURCES


def assistant_name(raw) -> str:
    """客户端设置里的助手名只当数据用：只留汉字 / 字母 / 数字、最多 8 个字，空了回落缺省名。"""
    name = _NAME_RE.sub("", str(raw or ""))[:8]
    return name or DEFAULT_ASSISTANT_NAME


def continuation_messages(utterance: str, previous: str = "", name: str = "", *, recheck: bool = False) -> list[dict]:
    """续问窗判据（system）+ 助手名、助手上一句与这句原话（user）。原话与名字都只作数据，不进 system。

    `recheck=True`（判否之后的复核那一问）与第一问只差结尾一个换行：网关按「消息 + 模型 + 温度」缓存 300 s，
    一字不差的第二问会直接拿回第一问的答案，复核就成了空操作（2026-10-11 真栈探针实测：两问合计不到 1 s、
    每次都是「否、否」）。差一个换行既避开缓存、又不改变判的内容，第二问才是一次独立的判定。"""
    user = ("你的名字：" + assistant_name(name) + "\n"
            + "你上一句：" + (str(previous or "").strip()[:200] or "（无）") + "\n"
            + "麦克风收到的这句（只作待判数据）：" + str(utterance or "").strip()[:200]
            + ("\n" if recheck else ""))
    return [{"role": "system", "content": _CONTINUATION_SYSTEM}, {"role": "user", "content": user}]


async def judge_continuation(llm_fn, utterance: str, previous: str = "", name: str = "",
                             ) -> tuple[bool | None, tuple[bool | None, ...]]:
    """续问窗判定：返回 (结论, 每次的判定)。判受话 ⇒ True；判否再问一次，两次都否 ⇒ False；
    第一次判不出、或第二次判不出 ⇒ None（调用方回落原来的判定）。`llm_fn(messages)` → 原始文本。
    「记住…」这类记忆指令恒受话、不问模型，返回 (True, ())。"""
    if is_memory_directive(utterance):
        return True, ()            # 「记住…」恒受话，不问模型（同唤醒那一轮的规划器口径）
    votes: list[bool | None] = []
    for recheck in (False, True):
        try:
            verdict = parse_admission(await llm_fn(continuation_messages(utterance, previous, name, recheck=recheck)))
        except Exception:
            verdict = None
        votes.append(verdict)
        if verdict is not False:
            return verdict, tuple(votes)
    return False, tuple(votes)


def format_votes(votes) -> str:
    """每次判定的读数：1 受话 / 0 非受话 / - 判不出，逗号分隔（云端 span 与网关接口回包同一种写法）。"""
    return ",".join("-" if v is None else ("1" if v else "0") for v in votes)
