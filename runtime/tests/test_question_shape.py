"""`runtime/question_shape` 的两向断言 + 「判据零领域词」的源码级钉子。

这份判据 2026-08-27 从 `orchestrator/edge/fast_intent` 下沉到 runtime，理由写在模块
docstring 里（云侧要用同一条，而云侧镜像够不着 edge）。下沉的那一刻起它变成了
**安全闸的输入**（云侧「问句 + 写车控步」守卫），所以它自己也要有两向覆盖：
挡住的那一半和**不许误伤**的那一半各写一遍——收窄/放宽面只写一边守不住。
"""
from __future__ import annotations

import os

import pytest
import yaml

from runtime.question_shape import (
    CAPABILITY_ASKS, CHOICE_ASKS, DIRECTIVE_MARKERS, HYPOTHETICAL_FRAMES,
    MANNER_ASKS, HOW_TO_ACTIONS, OPERATION_VERBS, POLITE_TAILS, PROPERTY_ASKS,
    QUESTION_TAILS, REFERENCE_ASKS,
    is_non_directive_question,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_COMMANDS = os.path.join(_ROOT, "orchestrator", "edge", "knowledge", "commands.yaml")

_CLASSES = {
    "QUESTION_TAILS": QUESTION_TAILS,
    "CAPABILITY_ASKS": CAPABILITY_ASKS,
    "PROPERTY_ASKS": PROPERTY_ASKS,
    "CHOICE_ASKS": CHOICE_ASKS,
    "REFERENCE_ASKS": REFERENCE_ASKS,
    "MANNER_ASKS": MANNER_ASKS,
    "HYPOTHETICAL_FRAMES": HYPOTHETICAL_FRAMES,
    "DIRECTIVE_MARKERS": DIRECTIVE_MARKERS,
    "HOW_TO_ACTIONS": HOW_TO_ACTIONS,
    "POLITE_TAILS": POLITE_TAILS,
}


def _domain_vocabulary() -> set[str]:
    """VAL 知识库里的领域词（对象 id / 中文名 / intent 段）。

    从知识库派生而不是手抄——手抄那份迟早与知识库漂移，
    而这条断言一旦漂移就等于不存在（同 `test_actionability` 的做法）。
    """
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
    """先证明这条断言扫得到东西——空集合会让它永远绿。"""
    vocab = _domain_vocabulary()
    assert len(vocab) > 50, f"知识库派生词表只有 {len(vocab)} 条，扫描口径不对"
    assert "空调" in vocab


def test_no_feature_word_is_domain_vocabulary():
    """任一特征词撞上领域词 = 这份「形态判据」已经退化成对象特判。

    它守的是云侧那道安全闸的**性质**：闸的判据必须与「这句话在说哪个对象」无关，
    否则 R2.1「不在编排核心加领域字面量」那条铁律就在安全面上被绕过去了。
    """
    vocab = _domain_vocabulary()
    for name, words in _CLASSES.items():
        for word in words:
            assert word not in vocab, (
                f"{name} 里的 `{word}` 是 VAL 领域词——判据必须是句法/形态量")


def test_operation_verbs_are_single_char_function_words():
    """操作动词表是**单字**闭类；一旦有人往里加「打开天窗」这类词，这条当场红。"""
    for verb in OPERATION_VERBS:
        assert len(verb) == 1, f"OPERATION_VERBS 里的 `{verb}` 不是单字动词"


# ── 2. 是提问（写操作必须被挡） ────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "这车的天窗最大能开多大",
    "红色机油灯亮了怎么办",
    "车窗能不能自己关",
    "要是天窗一直开着会怎么样",
    "后备箱能开吗？",
    "空调最低多少度",
    "双闪什么时候用",
    # W19-c 真栈（run 0920b，w6 g05 trace 611a4134c32745eb）：座椅加热话题下「它有几档」被规划成
    # `seat.heating.on` 并**真的执行了**——数量疑问词「几档 / 几级」与条件疑问「什么条件 / 什么情况」
    # 不在表里，问句闸没认出它是在问。同趟「它最多能设几档」「它在什么条件下会自动关闭」同形。
    "它有几档",
    "它最多能设几档",
    "座椅加热有几级",
    "它在什么条件下会自动关闭",
    "什么情况下双闪会自动打开",
])
def test_questions_are_recognised(text):
    assert is_non_directive_question(text) is True, text


# ── 3. 不是提问（祈使句不许被误伤） ────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "打开天窗",
    "帮我把车窗关一下",          # 礼貌请求带疑问壳，仍是指令
    "能帮我关下车窗吗",
    "请把空调调到 24 度",
    "温度如何调高",              # 方式问法 + 操作动词 ⇒ 仍是指令
    "调到三档",                  # 对照：说了具体档位就是指令，不带数量疑问词
    "座椅加热开到二档",
])
def test_directives_are_not_mistaken_for_questions(text):
    assert is_non_directive_question(text) is False, text


def test_manner_ask_without_operation_verb_is_a_question():
    """`MANNER_ASKS` 只有在**不带操作动词**时才算提问——这一对是同一条判据。"""
    assert is_non_directive_question("这个功能为什么会自己触发") is True
    assert is_non_directive_question("为什么要把温度调那么高") is False


@pytest.mark.parametrize("text", [
    "雨刮器怎么打开",
    "车载冰箱如何开启",
    "这个功能怎么关闭",
    "前风挡雨刮怎么使用",
    "怎么打开雨刮器",
    "雨刮器怎么打开一下",
    "空调滤网怎么换",
])
def test_object_first_how_to_shape_is_a_question_without_asr_punctuation(text):
    """对象在前的“怎么操作”是方法询问；ASR 没有问号也不得执行成车控。"""
    assert is_non_directive_question(text) is True


@pytest.mark.parametrize("text", [
    "防滑链应该装在哪个轮子上",
    "冬天轮胎有什么要求",
    "开长途前要注意些什么",      # 追加批 L：「注意些什么」修前不算提问
    "雨天开车需要注意些什么",
])
def test_choice_and_requirements_shapes_are_questions_without_punctuation(text):
    """选择/要求型咨询也不是执行命令，云侧写闸应看见它们的问句形态。"""
    assert is_non_directive_question(text) is True


@pytest.mark.parametrize("text", [
    "帮我把座椅加热打开",
    "帮我打开雨刮器",
    "现在把空调关闭",
    "温度如何调高",
])
def test_explicit_execution_shapes_remain_directives(text):
    assert is_non_directive_question(text) is False


# ── 评审二轮 R9（2026-09-22）：「把」是句法结构，不是授权证据 ────────────────
#
# 「怎么把车窗打开」曾被显式排除在方法问句之外（manual-rag v2 的「既有祈使合同」），于是端侧
# 直接执行成 window.open——问怎么做的被当成要执行。方式疑问词 + 「把 / 将」处置式 + 操作动作
# 是方法询问；「帮我把车窗关上好吗」这类礼貌执行句（祈使标记 / 礼貌尾 + 祈使主体）照旧是请求。
# 「温度如何调高」（调 / 设 / 升 / 降类调节动词）的既有合同不在本批扩大。

@pytest.mark.parametrize("text", [
    "怎么把车窗打开",
    "如何把空调关闭",
    "咋把天窗打开",
    "怎样把座椅加热打开",
    "怎么才能把后备箱打开",
    "怎么把音乐关了",
])
def test_how_to_with_a_ba_frame_is_a_question(text):
    assert is_non_directive_question(text) is True, text


@pytest.mark.parametrize("text", [
    "请告诉我怎么关闭空调",        # 元请求：请求解释，不是请求控车
    "告诉我怎么开天窗",
    "请说说雨刮怎么用",
    "给我讲讲天窗是怎么开的",
    "教我怎么打开后备箱",
    "请问车窗能不能打开",          # 「请问」是提问前缀，不是祈使标记
    "请问怎么打开后备箱",
    "问一下座椅加热有几档",
])
def test_explanation_requests_and_ask_prefixes_are_questions(text):
    assert is_non_directive_question(text) is True, text


@pytest.mark.parametrize("text", [
    "帮我把车窗关上好吗",          # 礼貌执行句对照
    "请把车窗打开",
    "麻烦把空调关一下",
    "请问能帮我把车窗打开吗",      # 「请问」之后仍是礼貌请求
    "帮我深入调研固态电池，查完告诉我",   # 「告诉我」在句尾是交付要求，不是元请求
    "怎么把温度调高",              # 调节类动词的既有合同不动
])
def test_polite_execution_requests_stay_directives(text):
    assert is_non_directive_question(text) is False, text


def test_ask_prefix_and_explain_request_tables_are_closed_function_word_classes():
    from runtime.question_shape import ASK_PREFIXES, EXPLAIN_REQUESTS
    assert "请问" in ASK_PREFIXES and "告诉我" in EXPLAIN_REQUESTS
    vocab = _domain_vocabulary()
    for word in (*ASK_PREFIXES, *EXPLAIN_REQUESTS):
        assert word not in vocab, word


# ── 4. 礼貌尾词（评审 2026-09-19 §4 最小对比对，2026-09-20）──────────────────
# 「帮我把窗关上好吗」是礼貌请求、「关窗会影响通风吗」是效果询问——不能只看问号。
# 判据：礼貌尾词 + **祈使主体**（动词打头，或「把 / 将 + 对象 + 动词」）⇒ 请求；
# 主体不是祈使 ⇒ A-not-A 提问。两向各写一半。

@pytest.mark.parametrize("text", [
    "把车窗关上好吗",
    "帮我把窗关上好吗",
    "关掉音乐好不好",
    "开一下天窗可以吗",
    "现在把空调关闭好吗",
    "请把空调调到24度行吗",
])
def test_polite_tail_after_an_imperative_body_is_a_request(text):
    assert is_non_directive_question(text) is False, text


@pytest.mark.parametrize("text", [
    "关窗会影响通风吗",                          # 效果询问
    "仪表上水温警告灯一直亮着，这样开下去行不行",   # 安全问句：动词不在句首、没有「把」框架
    "今天空气好吗",
    "这一家评价好不好",
    "确认可以吗",                                # W01：裸确认询问不是授权
    "可以吗",
    "行不行",
    "空调开到26度行吗",                          # 对象在前、无「把」：分不开请求与询问，宁可当提问
    "要是开着天窗下雨了行吗",                     # 假设框架
    "现在开天窗合适吗",
])
def test_polite_tail_without_an_imperative_body_stays_a_question(text):
    assert is_non_directive_question(text) is True, text


def test_polite_tails_are_a_closed_function_word_class():
    for tail in POLITE_TAILS:
        assert tail.endswith(("吗", "么", "好", "行", "以", "成")), tail



# ── 批 5 W18：祈使开头（处置式 / 礼貌前缀） ─────────────────────────────────

@pytest.mark.parametrize("text", ["把全车门解锁", "将空调调到26度", "请打开车窗", "麻烦关一下天窗",
                                  "帮我把后备箱打开", "先把音乐停了", "替我查一下路况"])
def test_imperative_openings(text):
    from runtime.question_shape import is_imperative_opening
    assert is_imperative_opening(text), text


@pytest.mark.parametrize("text", ["走滨海大道", "深南大道", "晚上九点", "科苑南路店", "第二个",
                                  "把", "", "我把钥匙忘车里了吗"])
def test_non_imperative_openings(text):
    from runtime.question_shape import is_imperative_opening
    assert not is_imperative_opening(text), text


# ── 评审三轮追加批 E（2026-09-23）：列举问法与原因问法 ─────────────────────────
# E1：「有哪些 / 有什么 / 哪些 / 哪几 / 哪部 / 哪首」此前不在任何一类里 ⇒ 端侧本地复算「天窗有什么用」打开天窗、
# 「空调有什么模式」开空调、「氛围灯有哪些颜色」开氛围灯、「有什么好看的电影」播视频——正是本模块 docstring
# 第一段要挡的形态。与 `MANNER_ASKS` 同一条配对：句中带操作动词仍是指令。
# E2：原因问法只用于**查询**的让路（端侧只有读数，答不了「为什么」），不参与写操作的问句判定。

@pytest.mark.parametrize("text", [
    "天窗有什么用",
    "空调有什么模式",
    "氛围灯有哪些颜色",
    "座椅有哪些模式",
    "香氛有什么味道",
    "有什么好看的电影",
    "适合全家看的电影有哪些",
    "哪部电影好看",
    "哪首歌最好听",           # 「哪首歌适合开车听」不在此列：「开车」里的「开」按操作动词配对（与方式问法同样粗）
    "驾驶模式有哪几种",
])
def test_enumeration_asks_are_questions(text):
    assert is_non_directive_question(text) is True, text


@pytest.mark.parametrize("text", [
    "空调有哪些模式，开个制冷",      # 列举问 + 操作动词：用户同时下了指令
    "没有什么问题，把车窗打开",      # 「没有什么」不是列举问
    "没什么事，关掉音乐",
    "帮我看看有什么歌",              # 祈使标记在前
    "天窗有什么用，打开看看",
    "打开车窗，没有什么问题",        # 「没有什么」在后、其后无操作动词：只有「没」的排除挡得住
])
def test_enumeration_words_do_not_veto_a_directive(text):
    assert is_non_directive_question(text) is False, text


def test_enumeration_and_reason_tables_are_closed_function_word_classes():
    from runtime.question_shape import ENUMERATION_ASKS, REASON_ASKS
    vocab = _domain_vocabulary()
    for word in (*ENUMERATION_ASKS, *REASON_ASKS):
        assert word not in vocab, word


@pytest.mark.parametrize("text", [
    "给我讲讲新能源车冬天续航为什么会下降",
    "为什么续航下降这么快",
    "电量为什么掉得这么快",
    "胎压为什么报警",
    "续航下降是什么原因",
    "电量怎么这么低",
    "胎压报警是怎么回事",
    "续航怎么会差这么多",
])
def test_reason_asks_are_recognised(text):
    from runtime.question_shape import asks_for_reason
    assert asks_for_reason(text) is True, text


@pytest.mark.parametrize("text", [
    "续航还有多少",
    "电量还剩多少",
    "告诉我还剩多少电",
    "胎压多少",
    "续航怎么样",
    "介绍一下这车的续航",
])
def test_value_questions_are_not_reason_asks(text):
    from runtime.question_shape import asks_for_reason
    assert asks_for_reason(text) is False, text


# ── 评审三轮追加批 F（F-4，2026-09-23）：原因式问法只看问词之后的操作动词 ─────────────
# 方式问法与操作动词按字配对、不看位置：「空调」的「调」、「关不上」的「关」让下面几句判成指令，端侧真的执行了
# `hvac.on` / `sunroof.close`。原因式问法（`REASON_ASKS`）改成只看问词**之后**；「关不上 / 打不开 / 调不动」这类
# 可能补语的否定式、「开不开」这类正反问、「没关上」这类已然否定都不是在下指令。

@pytest.mark.parametrize("text", [
    "空调为什么不制冷",
    "天窗为什么关不上",
    "空调怎么不出风",
    "空调开不了是怎么回事",
    "车窗怎么没关上",
    "空调为啥一直在响",
    "座椅加热怎么会自己关掉",
])
def test_reason_questions_are_questions_wherever_the_subject_puts_an_operation_char(text):
    assert is_non_directive_question(text) is True, text


@pytest.mark.parametrize("text", [
    "为什么要把温度调那么高",      # 钉住的合同：问词之后有「调」⇒ 仍是指令
    "为什么不开空调",              # 问词之后有「开」：建议式，仍是指令
    "怎么这么热，空调开大点",
    "温度如何调高",                # 方式问法维持原配对
    "调低一点怎么样",
    "空调怎么调",
])
def test_reason_pairing_keeps_the_existing_directive_contracts(text):
    assert is_non_directive_question(text) is False, text


def test_information_request_predicate():
    from runtime.question_shape import INFO_REQUEST_VERBS, is_information_request
    for text in ("推荐三部适合全家看的电影", "给我推荐几本适合小学生读的书", "介绍一下北京有哪些著名的历史建筑",
                 "天窗有什么用", "为什么冬天续航会下降", "说说你能帮我做哪些事", "请问天窗怎么打开"):
        assert is_information_request(text) is True, text
    for text in ("打开空调", "帮我订一张明天去上海的机票", "导航去公司", "明天早上八点提醒我开会", "云岚国际中心"):
        assert is_information_request(text) is False, text
    vocab = _domain_vocabulary()
    for word in INFO_REQUEST_VERBS:
        assert word not in vocab, word


# ── 追加批 F 续：列举问里名词短语自带操作字（「调节功能 / 开启方式 / 加热档位」）──────────────
# `07e9ea5c` 知识集复查时本地复算：「车窗有哪些开启方式」端侧执行 `window.open`、「座椅有哪些调节功能」`seat.on`、
# 「座椅有哪些加热档位」`seat.heating.on`——列举词之后的操作字是名词短语的修饰语，不是指令。只有**另起的分句**里
# 出现操作动词才算同时下了指令（「空调有哪些模式，开个制冷」照旧是指令）。

@pytest.mark.parametrize("text", [
    "座椅有哪些调节功能",
    "车窗有哪些开启方式",
    "空调有哪些调节方式",
    "座椅有哪些加热档位",
    "空调为什么调节不了温度",      # 双字动词的「做不到」形态
])
def test_operation_characters_inside_the_asked_noun_phrase_do_not_make_a_directive(text):
    assert is_non_directive_question(text) is True, text


# ── 追加批 G：原话自带的「要系统操作」句法证据（显式输入「不受话」重试的两处消费）──────────────────
# 云侧 `explicit_input_not_addressed` 把第一轮「不受话」重试一次；第二轮被催出来的计划里有写车控 / 需确认步，
# 而原话一个操作证据都没有（「可以，已为您执行」→ 真栈关了双闪）⇒ 作废。反过来，第二轮如实答「受话、零步」时，
# 没有操作证据的原话（问候 / 附和）交谈话作答；带操作证据的（「把全车门解锁」）照旧诚实报失败。

@pytest.mark.parametrize("text", [
    "把后备箱打开",
    "把全车门解锁",          # 「解锁」不在动词表里，靠「把」字处置式
    "打开空调",
    "温度调高一点",
    "请锁车",               # 礼貌前缀的祈使开头
    "麻烦关一下天窗",
    "帮我把座椅加热打开",
    "换一首歌",             # 只有操作动作词（`HOW_TO_ACTIONS`）
])
def test_operation_cue_is_carried_by_the_utterance(text):
    from runtime.question_shape import carries_operation_cue
    assert carries_operation_cue(text) is True, text


@pytest.mark.parametrize("text", [
    "可以，已为您执行",       # 真栈 `a59b1621` RS21：第二轮被催出 `warning_light.close`
    "你好，请只回复一句问候",   # 发布验收探针原句：「请」在句中，不是祈使开头
    "啊",
    "hello",
    "我不想排队",
    "好的，谢谢",
    "妈你到哪了",
    "我有点冷",              # 隐式车控：没有句法证据（批 G 的已知代价，见设计 §10）
    "请问你叫什么名字",        # 「请问」是提问前缀，不是祈使开头
])
def test_utterances_without_an_operation_cue(text):
    from runtime.question_shape import carries_operation_cue
    assert carries_operation_cue(text) is False, text


# ── 追加批 H：「几个」计数问（设计 §11）───────────────────────────────────────────────────────
# 本地复算：23 句计数问里 13 句被端侧执行成写车控——「空调有几个风量档」调风量、「这车有几个座位」`seat.on`、
# 「后备箱能放几个行李箱」`trunk.open`。批 6 只收了「几档 / 几级 / 几种」，刻意不收「几个」（「开几个车窗」是模糊的祈使）。
# 区别在「几」前面：「有 / 分 / 共 / 能 / 可以 / 最多 + … + 几 + 量词」是在问，「动词 + 几 + 量词」是指令。

@pytest.mark.parametrize("text", [
    "空调有几个风量档",
    "这车有几个座位",
    "座椅加热有几个档位",
    "空调有几个出风口",
    "座椅加热分几个档",
    "一共几个座位",
    "后备箱能放几个行李箱",
    "座椅能调几个方向",        # 「能」与「几」之间的操作字是被问的能力，不是指令
    "香氛有几个味道",
    "大灯有几个模式",
    "座椅有几个记忆位",
    "空调温度能调几度",
    "雨刮有几个速度",
    "这车可以坐几个人",
    "车窗最多开几个",
])
def test_count_questions_are_questions(text):
    assert is_non_directive_question(text) is True, text


@pytest.mark.parametrize("text", [
    "开几个车窗",              # 批 6 不收「几个」的理由：「几」= 一些，模糊的祈使
    "找几个充电站",
    "放几首歌",
    "音量调大几格",
    "开几分钟窗",
    "温度调低几度",
    "车窗开了几个，都关上",
    "后排有几个窗开着，关一下",   # 计数问 + 另起分句的操作动词 ⇒ 仍是指令（同列举问）
    "能帮我调高几度吗",          # 祈使标记在前
    "车上没有几个人",            # 「没有几个」是否定陈述，不是计数问
])
def test_indefinite_ji_is_not_a_count_question(text):
    assert is_non_directive_question(text) is False, text


def test_count_question_tables_are_closed_function_word_classes():
    from runtime.question_shape import COUNT_HEADS, COUNT_UNITS
    vocab = _domain_vocabulary()
    for word in (*COUNT_HEADS, *COUNT_UNITS):
        assert word not in vocab, word


# ── 追加批 L（2026-09-24；设计 §15）：规范 / 注意事项 / 条件问法 ────────────────────────────────

from runtime.question_shape import is_reference_question  # noqa: E402


@pytest.mark.parametrize("text", [
    "开长途前要注意些什么",                   # 真栈被规划成 scene.activate / scene.create
    "去惠州要注意些什么", "冬天轮胎有什么要求", "它在什么条件下会自动关闭",
    "请问开长途前要注意些什么",
    "看看有没有预警，有的话说下开车要注意什么",  # 两个分句都是提问
])
def test_reference_questions(text):
    assert is_reference_question(text) is True, text


@pytest.mark.parametrize("text", [
    "导航去公司，看看路上什么情况",            # 前一分句是指令
    "帮我查一下开车要注意什么",               # 祈使标记
    "去惠州怎么充电",                        # 方法问句：答案可以就是一份充电规划
    "介绍一下北京有哪些著名的历史建筑",        # 求介绍：答案可以是一次深度调研
    "如果深圳今天不下雪，就导航去深圳湾公园",   # 条件指令
    "开长途前，要注意些什么",                 # 前一分句是碎片，不算提问——宁可不接管
    "",
])
def test_not_reference_questions(text):
    assert is_reference_question(text) is False, text


# ── 追加批 N（N-2，2026-09-24；设计 §17）：查询请求 + 疑问框架是求信息的请求，但不改问句判据 ──────────────────

from runtime.question_shape import LOOKUP_REQUESTS, is_information_request  # noqa: E402


@pytest.mark.parametrize("text", [
    "你帮我查查卤牛肉怎么做呀？", "帮我查一下小米SU7的官方续航是多少", "搜一下这首歌叫什么", "查下明天几点日落",
])
def test_lookup_requests_with_a_question_frame_are_information_requests(text):
    assert is_information_request(text) is True, text


@pytest.mark.parametrize("text", ["替我查一下路况", "帮我查一下明天的天气", "查一下附近的充电桩", "搜一下周杰伦的歌"])
def test_lookup_directives_without_a_question_frame_are_not(text):
    assert is_information_request(text) is False, text


def test_lookup_requests_do_not_change_the_question_judgment():
    """只进 `is_information_request`：端侧写否决与云侧问句闸看的 `is_non_directive_question` 一个字不变。"""
    assert is_non_directive_question("你帮我查查卤牛肉怎么做呀") is False
    assert is_non_directive_question("帮我查一下小米SU7的官方续航是多少") is False


def test_lookup_request_table_is_a_closed_function_word_class():
    vocab = _domain_vocabulary()
    for word in LOOKUP_REQUESTS:
        assert word not in vocab, word
