"""「这句话的主语是不是会话里的另一个东西」——受话边界之外的第四维（唯一实现，批 7 ④）。

## 它挡的是什么

端侧分类器认的是「对象 × 动作词」，主语是谁它不看。W19-c 视窗实验（2026-09-20）三臂恒定的 4 组里
3 组是同一形态：用户刚在云端聊完「问界 M9」，接着问「它续航多久 / 那它的纯电续航呢 / 那它的续航呢」
——端侧命中「续航」⇒ `battery.query`，秒回**这辆车**的剩余电量。指代物只活在云端历史里，端侧
根本看不见它；这一句在端侧被接走，云端连尝试解指代的机会都没有。

判据只有一条：句首（可带「那 / 那么 / 然后 / 还有 / 另外 / 对了」这类引子）的主语是**回指代词**
（它 / 它的 / 它们）或**远指 + 车辆量词**（那款 / 那台 / 那辆 / 那部 / 那个车…）——说话人指的是刚才
谈到的另一个东西，不是这辆车。

## 刻意不收的

- 「这车 / 这辆 / 这个 / 这款」：坐在车里说「这车电量多少」指的就是本车（近指 = 在场的东西）。
- 裸「那个」：口头填充词（「那个，电量还有多少」）。
- 「它」出现在句中而非句首：「把它关掉」没有对象词本来就出不了本地意图；「空调它怎么不制冷」
  主语是空调，「它」只是复指。

## 出口只盖查询

写操作不经这里：带对象词的写指令里「它」是冗余的（「那它也关掉吧」——对象要靠上一轮回填，
那是 `_backfill_object` 的事）；没有对象词的写指令端侧本来就产不出意图。三条入口（单句 / 拆分 /
混合拆分）共用 `classify_structured` 出口，判据与 `question_shape` / `polarity` / `reported_speech`
三维同一个落点。

## 为什么住在 runtime/

端侧 `orchestrator/edge/fast_intent.py` 是第一个消费方；云侧镜像够不着 `orchestrator/edge`，
两边镜像都 COPY 了 `runtime`（同前三维的落点判据）。判据零领域词，由
`runtime/tests/test_anaphora.py` 的源码级断言守着。
"""
from __future__ import annotations

import re

#: 句首引子：话语标记，不改变主语。「那」单独出现也算（「那它的续航呢」）。
LEADING_PARTICLES = ("那么", "那就", "那", "然后", "还有", "另外", "对了", "嗯", "哦", "呃")
#: 回指主语：代词，或远指 + 车辆量词。全是封闭虚词 / 量词，**零领域词**。
ANAPHORIC_SUBJECTS = ("它们", "它", "那款车", "那台车", "那辆车", "那部车", "那个车",
                      "那款", "那台", "那辆", "那部")

_PARTICLE_ALT = "|".join(sorted(map(re.escape, LEADING_PARTICLES), key=len, reverse=True))
_SUBJECT_ALT = "|".join(sorted(map(re.escape, ANAPHORIC_SUBJECTS), key=len, reverse=True))
ANAPHORIC_SUBJECT_RE = re.compile(
    rf"^(?:(?:{_PARTICLE_ALT})[，,、\s]*)*(?:{_SUBJECT_ALT})(?:的)?")


def has_anaphoric_subject(text: str | None) -> bool:
    """「它续航多久」「那它的纯电续航呢」「那款车加速几秒」→ True；
    「电量还有多少」「这车续航多少」「那个，还有多少电」「把它关掉」→ False。"""
    t = (text or "").strip()
    if not t:
        return False
    return bool(ANAPHORIC_SUBJECT_RE.match(t))
