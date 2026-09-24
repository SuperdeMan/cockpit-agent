"""安全信号判据——**唯一实现**（卡 Q9，2026-08-15；2026-08-27 从 `agents/_sdk` 迁入 runtime）。

为什么不在各 Agent 里各写一份：阶段 1 落地时 manual-rag 与 road-safety
已经各自长出了一套词表，chitchat 还需要第三套。本仓 §4.3 刚为「容器时区」付过学费——
**同一件事有三份各自正确的实现，就迟早会有第四份是错的**。这里是那句话的预防版：
第三个消费方出现的**当天**就收口，而不是等它错了再收。

## 为什么 2026-08-27 从 `agents/_sdk` 搬到了 `runtime/`

因为出现了**第四个消费方，而它够不着 `agents/`**：云端编排要在**输入侧**扫本轮原话
（卡 C1-B——告警登记不能是「恰好走了 manual-rag 这条路由」的副作用），而
`orchestrator/cloud/Dockerfile` 只 `COPY runtime`，没有 `agents/`。
落点判据就是这条**镜像依赖闭包**：谁都够得着的那一份才叫唯一实现
（同 `polarity.py` / `cntime.py` / `clock.py` 的迁入理由）。

三个判据，互不重叠：
  · `alert_level(text)`  车辆告警（警示语境词 × 关键系统）→ "critical" | "amber" | ""
  · `driver_state(text)` 驾驶员**此刻**的状态（疲劳/饮酒/不适）→ "alcohol" | "fatigue" | "unwell" | ""
    （`driver_state_mentioned` 是它的上一层：这句话提到了哪类风险，话题问句 / 假设句也算）
  · `alert_signal(text)` 从原话里取**告警的名字**（不是整句）

判据形态的两条纪律，都是实测换来的：
  ① **要语境不要对象**：「胎压多少正常」是普通问题、「胎压黄灯亮了」才是告警。
     只列对象（胎压/机油）会把前者一起误伤。
  ② **认不出就返回空，绝不回落到某一档**。阶段 1 首版在 `safety.driver_state`
     入口写了 `driver_state(text) or "fatigue"`，于是「慢一点开可以吗」被答成
     「您现在的状态不适合继续开——困倦时的反应时间和酒后接近」——
     **系统声称了一件用户根本没说的事**，与 nearby 那几例假个性化同族。
"""
from __future__ import annotations

import re

from runtime.affirmation import is_acknowledgment_only
from runtime.clause_split import split_clauses
from runtime.question_shape import (DIRECTIVE_MARKERS, HYPOTHETICAL_FRAMES,
                                    is_non_directive_question)

# ── 车辆告警 ─────────────────────────────────────────────────────────────
#: **警示灯**的名字。刻意逐个列举，**不用「灯亮」这类通配**——
#: chitchat 兜底会看到全部流量，「大灯亮了」「氛围灯亮着好看」都含「灯亮」，
#: 用通配会把它们一起答成「请降低车速、就近检查处理」。
#: ⚠ 车上大多数灯是**正常功能灯**（大灯/雾灯/日行灯/氛围灯/阅读灯/刹车灯/转向灯），
#: 它们一个都不在这张表里。**宁可漏一个告警，也不要对着一盏正常的灯劝人停车。**
WARNING_LIGHTS = ("故障灯", "警告灯", "报警灯", "警示灯", "指示灯",
                  "机油灯", "水温灯", "胎压灯", "电池灯", "发动机灯",
                  "abs灯", "epc灯", "黄灯", "红灯", "故障码")
#: 警示**语境**动词/现象。与灯无关的告警形态（异响、冒烟、失灵…）走这条。
ALERT_VERBS = ("报警", "警告", "警示", "故障", "漏气", "掉压", "亏气",
               "异响", "冒烟", "起火", "失灵", "过热", "打滑", "抖动")
#: 兼容旧名（`alert_signal` 取名字时两张表都要扫）。
ALERT_CONTEXT = WARNING_LIGHTS + ALERT_VERBS
#: 命中这些系统 = 立即停车族；其余告警 = 尽快处理族。
CRITICAL_SYSTEMS = ("机油", "水温", "制动", "刹车", "转向", "气囊",
                    "电池", "起火", "冒烟", "失灵")

ADVICE_CRITICAL = (
    "这属于需要立即处置的警告：请尽快在安全位置靠边停车、熄火，"
    "不要继续行驶，并联系救援或前往就近服务点检查。"
)
ADVICE_AMBER = (
    "这属于需要尽快处理的警示：请降低车速、避免长时间或高速行驶，"
    "就近检查处理。"
)


# ── 解除陈述（QA T47 裁决 A，2026-09-19）────────────────────────────────────
#: 告警**已经不在了**的说法。这是放宽一道安全约束的作用域，词表刻意只收
#: **完成态**（带「了 / 已」）与「不亮 / 没亮 / 误报」这类明确否定告警存在的说法；
#: 光秃秃的「修好」「排除」不收——「帮我排除一下故障」是指令不是陈述。
#: **意图陈述不算解除**：「我会靠边停车检查」「马上去修」「先开到服务区再看」一个词都不在这里，
#: 这正是交接页那句「『我会靠边』不是『已排除故障』」。
RESOLVED_MARKERS = (
    "灭了", "熄了", "熄灭了", "不亮了", "没亮", "没有亮", "不再亮",
    "处理好了", "修好了", "弄好了", "搞好了", "解决了", "排除了",
    "没问题了", "没事了", "恢复正常", "恢复了", "正常了",
    "没有故障", "没故障", "没有报警", "没报警",
    "已排除", "已解决", "已修好", "已处理", "已恢复",
    "误报", "误触", "虚惊",
)
#: 解除陈述必须**点名告警对象**——具名灯 / 现象词 / 关键系统 / 告警泛称。
#: 「处理好了」「没事了」单独出现不算：焦点里挂着机油灯时，用户说「刚才那个提醒处理好了」
#: 不是在说灯。刻意不收裸「灯」：「大灯灭了」说的是一盏正常功能灯，不该撤掉一条 critical。
#: 宁可让用户多说一次「机油灯灭了」，也不要凭一句对象不明的「好了」放宽安全约束。
ALERT_REFERENCES = WARNING_LIGHTS + ALERT_VERBS + CRITICAL_SYSTEMS + (
    "告警", "警报", "警告", "故障")
#: 标记词前面紧邻（≤4 字）出现这些词 ⇒ 是被否定的解除（「还没修好了」「没有排除了」），不算。
#: 表里刻意不含「不」——「不亮了」自己就以「不」开头。
_RESOLUTION_NEGATIONS = ("还没", "尚未", "没有", "没", "未")


def _split_resolution(clause: str) -> tuple[bool, str]:
    """一分句里「告警已解除」的陈述 → `(是否解除, 仍要按告警识别的那部分文本)`。

    五道门，判据只用封闭词类 + 本模块自己的告警表：
    ① 含解除标记；② 标记之前点名了告警对象（对象在前、状态在后——「机油灯灭了」，
    而「灭了机油灯」不是中文里陈述的形态）；③ 标记前面紧邻（≤4 字）没有否定词；
    ④ 不是问句、不是给助手的指令（「灯灭了吗」「帮我把故障灯灭了」都不算）；
    ⑤ 标记之后若又出现告警词（「机油灯灭了但是水温灯亮了」），前半算解除、
    **后半照常按新告警识别**——用户真正想说的是后一半，别让它跟着前半一起被抹掉。
    """
    c = (clause or "").strip()
    if not c or is_non_directive_question(c) or any(m in c for m in DIRECTIVE_MARKERS):
        return False, c
    lower = c.lower()
    hits = sorted((lower.find(m), m) for m in RESOLVED_MARKERS if m in lower)
    if not hits:
        return False, c
    first_at, _ = hits[0]
    head = lower[:first_at]
    if not any(ref in head for ref in ALERT_REFERENCES):
        return False, c
    if any(neg in head[-4:] for neg in _RESOLUTION_NEGATIONS):
        return False, c
    last_at, last_marker = hits[-1]
    return True, c[last_at + len(last_marker):]


def _clause_resolved(clause: str) -> bool:
    return _split_resolution(clause)[0]


def alert_resolved(text: str) -> bool:
    """原话里有没有一条「告警已解除」的陈述（任一分句即可）。

    消费方（四个，同一份判据）：云侧输入扫描据此**清掉**会话焦点里的告警并挡住粘性接力；
    road-safety 据此不再按会话里那条旧告警答「您这次会话里还有未解除的…」；
    chitchat 据此不再往 prompt 里塞「未解除告警」那一行；`alert_level`/`alert_signal`
    自己据此把解除那一分句排除在告警识别之外（否则「机油灯灭了」会被当成一条新告警登记回去）。
    """
    t = text or ""
    return any(_clause_resolved(c) for c in (split_clauses(t) or [t]))


def _unresolved_text(text: str) -> str:
    """去掉解除陈述后仍要按告警识别的原话；没有解除陈述时**逐字返回原话**（既有行为一个字不变）。"""
    t = text or ""
    parts = [_split_resolution(c) for c in (split_clauses(t) or [t])]
    if not any(resolved for resolved, _ in parts):
        return t
    return "，".join(rest for _, rest in parts if rest)


def alert_level(text: str) -> str:
    """车辆告警等级。返回 "critical" | "amber" | ""（不是告警）。

    命中条件 = **具名警示灯** 或 **告警现象动词**。两者都不含时一律返回 ""——
    「大灯亮了」「氛围灯亮着好看」不是告警。解除陈述那一分句不参与识别
    （「机油灯灭了」不是告警；「机油灯灭了，现在还能开吗」也不是）。
    """
    t = _unresolved_text(text).lower()
    if not (any(w in t for w in WARNING_LIGHTS) or any(w in t for w in ALERT_VERBS)):
        return ""
    return "critical" if any(w in t for w in CRITICAL_SYSTEMS) else "amber"


def alert_signal(text: str) -> str:
    """告警的名字。取命中的词，**不取整句**——整句进会话态会把用户的措辞
    变成告警名字（「慢一点开可以吗」不是一个告警）。

    ⚠ 系统名只在**命中词自己没带系统名**时才前缀（2026-08-26 QA 实录修）。
    `ALERT_CONTEXT` 里的具名灯本身就含系统名（`机油灯`/`水温灯`），而
    `CRITICAL_SYSTEMS` 又会独立扫出「机油」/「水温」，无条件拼接的结果是
    **「机油机油灯」「水温水温灯」**——它原样进焦点、进卡片、进播报话术，
    vehicle T35-36 与 family T62-63 四轮实录。
    这个 bug 能活下来是因为既有断言只查 `len(sig) <= 12`：**长度对、内容错**。
    所以下面那条回归断言钉的是**具体返回值**，不是形状。
    """
    t = _unresolved_text(text)
    hit = next((w for w in ALERT_CONTEXT if w in t), "")
    if not hit:
        return ""
    system = next((w for w in CRITICAL_SYSTEMS if w in t), "")
    if not system or system in hit:
        return hit
    return f"{system}{hit}"


def alert_advice(level: str) -> str:
    return ADVICE_CRITICAL if level == "critical" else ADVICE_AMBER


# ── 驾驶员状态 ───────────────────────────────────────────────────────────
#: 顺序有意义：先判**不可让步**的（酒后/药物），再判疲劳，再判不适。
#: 词表只收**明确**的表述——「有点累」这类模糊说法不进，宁可漏接也不要在用户
#: 只是随口一说时给出一段劝阻（同「宁可漏接上云，不要端侧替用户按按钮」）。
DRIVER_STATE_WORDS = (
    ("alcohol", ("喝了酒", "喝酒", "酒后", "喝了两杯", "醉", "宿醉", "吃了感冒药",
                 "吃了药犯困")),
    # 追加批 K：「好困 / 有点困 / 困了 / 困得 / 困死」——最常见的犯困自述此前一个都认不出（「我好困」答天气）。
    ("fatigue", ("睁不开眼", "困到", "太困", "很困", "好困", "有点困", "困了", "困得", "困死",
                 "犯困", "打瞌睡", "打盹", "疲劳驾驶", "熬夜", "一夜没睡", "没合眼", "累得不行", "撑不住")),
    ("unwell", ("头晕", "眼花", "胸闷", "心慌", "发烧", "很难受", "不太舒服")),
)

DRIVER_STATE_ADVICE = {
    "alcohol": {
        "level": "critical", "signal": "酒后/服药驾驶",
        "speech": "喝过酒或服用可能致困的药物之后，请不要驾驶——这不是车速能补偿的风险。"
                  "建议就近安全停车，叫代驾或打车回去。",
        "follow_up": "需要我帮您叫代驾或查附近能停车的地方吗？",
        # 追加批 K：提到这类风险（话题问句 / 假设句）时的回答——同一类安全常识，不断言用户此刻的状态。
        "topic_speech": "喝过酒或吃了可能致困的药，都别开车——代谢快慢因人而异，没有哪个等待时间能保证安全，"
                        "喝得多的话第二天也可能没代谢完。血液酒精含量每 100 毫升达到 20 毫克就算酒驾，"
                        "80 毫克算醉驾、要追究刑事责任。需要用车请叫代驾或打车。",
    },
    "fatigue": {
        "level": "critical", "signal": "疲劳驾驶",
        "speech": "您现在的状态不适合继续开——困倦时的反应时间和酒后接近，"
                  "而且犯困往往在自己意识到之前就发生了。"
                  "请就近找服务区或安全位置停车，先休息 15–20 分钟。",
        "follow_up": "要我帮您找最近的服务区吗？",
        "topic_speech": "疲劳时的反应时间和酒后接近，而且犯困往往在自己意识到之前就发生了。"
                        "没睡好就尽量别开长途；路上一犯困，就尽早找服务区或安全位置停车，休息 15–20 分钟再走。",
    },
    "unwell": {
        "level": "amber", "signal": "驾驶员身体不适",
        "speech": "身体不舒服时驾驶风险明显升高。建议先在安全位置停车缓一缓，"
                  "症状没有缓解就不要继续开。",
        "follow_up": "要我帮您找最近的休息点或医院吗？",
        "topic_speech": "身体不舒服时驾驶风险会明显升高，头晕、胸闷、发烧这类症状尤其别硬撑。"
                        "开车时出现不适，先在安全位置停车缓一缓，症状没有缓解就别继续开，必要时就医。",
    },
}

def driver_state_of_signal(signal: str | None) -> str:
    """会话告警的名字 → 驾驶员状态（它是 `DRIVER_STATE_ADVICE[*]["signal"]` 之一时）；车辆告警返回 ""（追加批 M）。

    会话告警只存 `{level, signal, ts}`；消费方据此分辨「会话里挂着的是犯困 / 饮酒 / 不适」还是一盏灯，
    答那个状态自己的话术，而不是车辆故障口吻（「在它排除之前…熄火，并联系救援」）。
    """
    return next((state for state, spec in DRIVER_STATE_ADVICE.items() if spec["signal"] == (signal or "")), "")


# ── 此刻自述 vs 提到这类风险（追加批 K，2026-09-24）─────────────────────────────────────────
# 修前 `driver_state` 是纯词表包含，三类句子被当成「用户此刻就是这个状态」——会话里登记一条 critical 告警、
# 用户听到「您现在的状态不适合继续开」：① 否定（「我没喝酒」「别熬夜」「我不头晕」）；② 话题问句（「疲劳驾驶
# 有什么危害」「喝酒后多久能开车」）；③ 假设句（「如果喝了酒还能开车吗」「万一开车时犯困怎么办」）。
# 拆两层：`driver_state_mentioned` = 提到了哪类风险（只排除否定）；`driver_state` = 此刻自述（再排除②③）。
# 会话告警只由后者登记；提到但非自述时答 `topic_speech`（不断言「您现在」、不登记）。
# 判据只用封闭词类（否定 / 程度副词 / 假设框架 / 人称与时间锚），不按场景扩词表。

#: 状态词**紧前**是这些 ⇒ 这一处不算（「我没喝酒」「没有喝酒」「别熬夜」「我不头晕」；「被困了」不是犯困，
#: 「被醉驾撞了」不是自己喝了酒）。只看紧前：「没怎么喝酒」的「没」隔着「怎么」——喝了一点，照旧算饮酒。
_STATE_BLOCKERS = ("没有", "没", "不", "别", "未", "被")
#: 以否定字结尾、却不是否定的词（「开车特别犯困」）。
_NOT_BLOCKERS = ("特别",)
#: 否定与状态词之间的程度副词（「不太困了」「没那么困了」「不怎么困了」）——**饮酒不跳**：酒没有「一点点不算」。
_DEGREE_ADVERBS = ("怎么", "那么", "太", "很")
#: 以「困」结尾的词后面跟这些字是另一个词（「好困难」「有点困惑」「很困扰」），不是犯困。
_KUN_COMPOUNDS = ("难", "惑", "扰", "境")
#: 话题名词：它们能当「一类风险」的名字用（「疲劳驾驶有什么危害」「醉驾怎么处罚」「喝酒后多久能开车」）。
_TOPIC_NOUNS = ("疲劳驾驶", "酒后", "醉驾", "醉酒", "喝酒", "熬夜")
#: 话题名词后面紧跟这些 ⇒ 是发生过的事（「熬夜了」「喝酒了」「疲劳驾驶了」），照旧是自述。
_REALIZED_MARKS = ("了", "过")
#: 说话人 / 此刻的锚：句子里有它们，话题名词就是在说自己（「我疲劳驾驶了怎么办」「昨晚熬夜，开车要注意什么」）。
_SPEAKER_NOW_MARKS = ("我", "咱", "本人", "刚", "昨", "今天", "今晚", "今早", "一直", "已经", "现在", "这会儿")
#: 「要是」在「主要是 / 只要是 / 需要是」里不是假设框架（「我主要是太困了」是自述）。
_NOT_A_FRAME_BEFORE_YAOSHI = ("主", "只", "需")
_CLAUSE_BREAK_RE = re.compile(r"[，,。；;！!？?、\s]")


def _negated(head: str) -> bool:
    return head.endswith(_STATE_BLOCKERS) and not head.endswith(_NOT_BLOCKERS)


def _blocked(t: str, start: int, word: str, state: str) -> bool:
    head = t[:start]
    if _negated(head):
        return True
    if state != "alcohol" and any(head.endswith(adverb) and _negated(head[:-len(adverb)])
                                  for adverb in _DEGREE_ADVERBS):
        return True
    after = t[start + len(word):start + len(word) + 1]
    return word.endswith("困") and after in _KUN_COMPOUNDS


def _state_hits(t: str):
    """每一处**没被否定**的状态词：`(state, start, word)`，按 `DRIVER_STATE_WORDS` 的顺序。"""
    for state, words in DRIVER_STATE_WORDS:
        for word in words:
            start = t.find(word)
            while start >= 0:
                if not _blocked(t, start, word, state):
                    yield state, start, word
                start = t.find(word, start + 1)


def _in_hypothetical(t: str, start: int) -> bool:
    """状态词所在分句里、它前面有假设框架（「如果喝了酒…」「万一开车时犯困…」）。"""
    clause_head = _CLAUSE_BREAK_RE.split(t[:start])[-1]
    for frame in HYPOTHETICAL_FRAMES:
        at = clause_head.find(frame)
        while at >= 0:
            if not (frame == "要是" and at > 0 and clause_head[at - 1] in _NOT_A_FRAME_BEFORE_YAOSHI):
                return True
            at = clause_head.find(frame, at + 1)
    return False


def _topic_question(t: str) -> bool:
    """整句是问句、且没有说话人 / 此刻的锚——话题名词在这里是一类风险的名字，不是自述。"""
    return is_non_directive_question(t) and not any(m in t for m in _SPEAKER_NOW_MARKS)


def _as_topic(t: str, start: int, word: str) -> bool:
    """这一处状态词落在某个话题名词里，且那个名词后面不是「了 / 过」。"""
    end = start + len(word)
    for noun in _TOPIC_NOUNS:
        at = t.find(noun)
        while at >= 0:
            if at <= start and end <= at + len(noun) and t[at + len(noun):at + len(noun) + 1] not in _REALIZED_MARKS:
                return True
            at = t.find(noun, at + 1)
    return False


def driver_state_mentioned(text: str) -> str:
    """这句话**提到了**哪类驾驶员风险（否定的不算，话题问句 / 假设句算）。返回值同 `driver_state`。

    只供「怎么答」用（提到但非自述 ⇒ `topic_speech`）；**会话告警只由 `driver_state` 登记**。
    """
    t = (text or "").strip()
    return next((state for state, _, _ in _state_hits(t)), "") if t else ""


def driver_state(text: str) -> str:
    """驾驶员**此刻**的状态（自述）。返回 "alcohol" | "fatigue" | "unwell" | ""。

    提到了（`driver_state_mentioned`）、且不在假设框架里、且不是「话题名词 + 问句 + 无说话人 / 此刻锚」。
    ⚠ **认不出返回空串，调用方不许 `or "fatigue"` 兜底**——见模块 docstring 纪律 ②。
    """
    t = (text or "").strip()
    if not t:
        return ""
    topic_question = _topic_question(t)
    for state, start, word in _state_hits(t):
        if _in_hypothetical(t, start):
            continue
        if topic_question and _as_topic(t, start, word):
            continue
        return state
    return ""


# ── 拒绝安全建议（追加批 K，K-1）─────────────────────────────────────────────────────────
# 会话里挂着告警时，「别提醒我，继续开就行」是在**拒绝安全建议**——不是别的话题：它的回答是拿着告警「立场不改」的兜底谈话，
# 可规划器会给它出澄清卡（SF4，`8cee1699` 第 2 趟）、被「无动作」重试催出一步查天气、或催坏成技术失败（`36a92009` 真栈）。
# 三类说法，全是封闭短语：
# 推开建议（别提醒 / 不用管 / 别啰嗦）、坚持继续开（继续开 / 不用停 / 不休息）、自我担保（我没事 / 撑得住）。
# 问句不算（「现在还能继续开吗」是在问能不能，不是拒绝）；「继续开」后面带宾语的不算（「继续开导航」「继续开空调」）；
# 「不用提醒我带伞」后面带宾语的不算（那是提醒事项）；「要不休息一下」是提议休息。
_REFUSAL_RES = (
    re.compile(r"(?:别|不用|不要|不必|无需|用不着)再?(?:提醒我?(?=$|[了啦吧啊呀，,。！!~\s])|管我|管了|啰嗦|唠叨|劝我?)"
               r"|少(?:啰嗦|唠叨)"),
    re.compile(r"(?<![不别没])(?:继续|接着)开(?=$|[车就吧了呗啊着，,。！!~\s])"
               r"|(?:不用|不要|不想|不需要|不必|没必要|用不着|不打算)(?:停|休息|歇)"
               r"|(?<!要)不(?:停车|休息|歇)"),
    re.compile(r"我(?:真|都|还)?没事(?=$|[的了啦吧啊呀儿，,。！!~\s])|(?:撑|扛|顶)得住|我(?:还)?能行"),
)


def refuses_safety_advice(text: str) -> bool:
    """整句**只是**在拒绝安全建议：每个分句要么是拒绝说法（问句不算），要么只是应答（「好了 / 行」，`runtime.affirmation`），
    且至少有一个拒绝分句。「别提醒我，帮我找个地方」不算——后半句是一个请求，它的澄清卡 / 计划仍归模型。
    只在会话里挂着告警时有意义，由调用方判。"""
    t = (text or "").strip()
    refused = False
    for clause in (split_clauses(t) or [t]) if t else []:
        if not is_non_directive_question(clause) and any(p.search(clause) for p in _REFUSAL_RES):
            refused = True
        elif not is_acknowledgment_only(clause):
            return False
    return refused
