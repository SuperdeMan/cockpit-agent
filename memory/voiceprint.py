"""声纹模板的纯计算层（M4 P4）：向量归一、余弦、模板合成、三态判定、occupant_id 分配。

**为什么判定在 memory 而不是 llm-gateway**（RFC §2.1）：判定要看全部模板，放网关就得把模板
下发给一个无状态服务——生物特征数据一旦扩散就删不干净，而 GDPR 硬删是本仓库已经立过的红线
（M2 关系边）。网关只做「音频→向量」，向量进来、结论出去。

**为什么单独成文件**：与 `weighting.py`/`relation.py` 同款——纯函数无 IO，判定逻辑可以脱离 PG
单测；`pg_store` 只负责取模板和落库。

判定是三态收敛到二值的：accept 之外的一切（不够像 / 分不开 / 没模板 / 模型换了）**一律回
`primary`**。不是 guest 也不是 unknown——`primary` 是存量语义（今天所有记忆都在它名下），
降级到它等于逐字回落到今天；造一个新身份等于凭空多出一个空记忆空间，用户体感是「车失忆了」。
"""
from __future__ import annotations
import math
import os
import re

# 判定阈值。**首版由 P4a-1 合成音色探针钉死**（`test/e2e_voiceprint_probe.py`，4 音色×6 句）：
#   同人 p5≈0.49 / 异人 p95≈0.65（同性别合成音色重叠），单看分布选不出阈值；
#   但端到端识别（三段均值模板 vs 单句探针）在 thr∈[0.45,0.70] 全区间结果**完全相同**——
#   真正起作用的控制量是 margin 不是 threshold，两次跑认对率 83%/100%、**认错率恒 0%**。
#
# **2026-07-26 按真人实测下调 0.62 → 0.45**（真机第三批）：真人真麦的同人余弦
# 落在 **0.52**，而异人（另一位已注册乘员）只有 **0.12**——0.62 把同人一并卡掉，
# 现象是「录了两个人，谁说话都认成同一个」。
# **合成音色把这件事标反了**：TTS 音色彼此共享信道特征，异人余弦高达 0.65，逼得阈值往上抬；
# 真人真麦的异人分离度远好于合成（0.12 vs 0.65），阈值反而该往下放。
# **拿代理数据标定的常量，一定要在真实分布上复标一次。**
# margin 不动：它才是判据（本次同人-异人差 0.41，是 margin 的 8 倍），且真人数据目前只有一个
# 会话的量——不在一个数据点上同时拧两个旋钮。网关每次 identify 打分数日志，后续按线上分布再收。
DEFAULT_THRESHOLD = 0.45
DEFAULT_MARGIN = 0.05
# 注册时各段两两余弦的下限：低于此值说明三段根本不像同一个人（混了别人/噪声/中途换人），
# 建出来的模板此后谁都认不准。**宁可拒绝建模板，也不要建一个坏模板。**
# 实测：合格注册最低 0.53、三段不同人最高 0.48——**只有 0.05 的窗**，故取 0.50 居中。
# 窗这么窄意味着它对真人语音很可能要重调（合成音色的段间一致性偏高），列入余项。
DEFAULT_MIN_CONSISTENCY = 0.50

_OCC_RE = re.compile(r"^occ-(\d+)$")


# **空串按「未设置」处理**（仓库既有惯例，见 `loop.py::_env_int`）：compose 用
# `${VAR:-}` 显式列名时注入的是**空字符串**，而 `os.getenv(name, default)` 只在
# 「键不存在」时给默认值——键存在但为空就返回空串，默认值形同虚设。
# 2026-07-26 洁癖盘点接线 compose 时当场踩到：模型路径被置空 → 声纹面直接 disabled。
def _env(name: str, default: str) -> str:
    return (os.getenv(name) or "").strip() or default


def threshold() -> float:
    return float(_env("VOICEPRINT_THRESHOLD", str(DEFAULT_THRESHOLD)))


def margin() -> float:
    return float(_env("VOICEPRINT_MARGIN", str(DEFAULT_MARGIN)))


def min_consistency() -> float:
    return float(_env("VOICEPRINT_MIN_CONSISTENCY", str(DEFAULT_MIN_CONSISTENCY)))


def l2_normalize(vec) -> list[float]:
    """L2 归一。零向量原样返回（不制造 NaN——下游 cosine 会给 0 分，自然落 below_threshold）。"""
    v = [float(x) for x in (vec or [])]
    n = math.sqrt(sum(x * x for x in v))
    if n <= 1e-12:
        return v
    return [x / n for x in v]


def cosine(a, b) -> float:
    """两个**已归一**向量的余弦。维度不等返回 0（跨模型比对没有意义，调用方应先按 model 过滤）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))


def mean_template(samples: list[list[float]]) -> list[float]:
    """多段样本合成模板：各段先归一、取均值、再归一。

    先归一再平均是必须的——否则录得响的那一段会因为模长大而主导模板，模板就偏向「那一次的
    录音条件」而不是「这个人」。
    """
    norm = [l2_normalize(s) for s in samples if s]
    if not norm:
        return []
    dim = len(norm[0])
    norm = [s for s in norm if len(s) == dim]
    acc = [0.0] * dim
    for s in norm:
        for i, x in enumerate(s):
            acc[i] += x
    return l2_normalize([x / len(norm) for x in acc])


def self_consistency(samples: list[list[float]]) -> float:
    """各段两两余弦的均值。单段返回 1.0（没有可比对象，不因此判坏）。"""
    norm = [l2_normalize(s) for s in samples if s]
    if len(norm) < 2:
        return 1.0 if norm else 0.0
    dim = len(norm[0])
    norm = [s for s in norm if len(s) == dim]
    if len(norm) < 2:
        return 0.0
    pairs = [cosine(norm[i], norm[j])
             for i in range(len(norm)) for j in range(i + 1, len(norm))]
    return sum(pairs) / len(pairs)


def decide(scored: list[tuple[str, float]], *, thr: float | None = None,
           mrg: float | None = None) -> dict:
    """把 (occupant_id, score) 列表收敛成一个判定。

    scored 由调用方**过滤掉 stale 模板后**传入（跨模型的余弦不是同一个尺度上的数）。

    四态：
      no_templates      还没人注册过 → primary（=今天的行为）
      below_threshold   最像的也不够像 → primary
      ambiguous         够像但和第二名拉不开 → primary（两个家庭成员声音接近时，分不开就别分）
      accept            认出来了
    """
    thr = threshold() if thr is None else thr
    mrg = margin() if mrg is None else mrg
    ranked = sorted(scored or [], key=lambda t: t[1], reverse=True)
    if not ranked:
        return {"occupant_id": "primary", "decision": "no_templates",
                "score": 0.0, "runner_up": 0.0}
    top_id, top = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    if top < thr:
        return {"occupant_id": "primary", "decision": "below_threshold",
                "score": top, "runner_up": runner}
    if len(ranked) > 1 and (top - runner) < mrg:
        return {"occupant_id": "primary", "decision": "ambiguous",
                "score": top, "runner_up": runner}
    return {"occupant_id": top_id, "decision": "accept", "score": top, "runner_up": runner}


def allocate_occupant_id(existing: list[str]) -> str:
    """分配 occupant_id。

    **第一个注册的人拿到 `primary`**（RFC §3.4）——这是本设计里后果最大的一条。今天全部存量
    记忆都写在 `primary` 名下；若首个注册者拿到 `occ-1`，他一注册完，自己过去说过的偏好、
    常去地点、家人关系当场全部失联。堵在分配这一步，而不是事后做数据迁移。

    其后按 occ-2、occ-3… 递增（跳过 occ-1 是刻意的：primary 就是 1 号，不再造一个空号）。
    """
    have = set(existing or [])
    if "primary" not in have:
        return "primary"
    used = {int(m.group(1)) for x in have if (m := _OCC_RE.match(x))}
    n = 2
    while n in used:
        n += 1
    return f"occ-{n}"
