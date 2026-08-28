"""ActionabilityClassifier 的契约（B6 §2/§5）。

三条判据决定这套东西站不站得住，逐条钉成断言：

1. **它是形态判据，不是领域词表**（§2.3 纪律）。特征里出现任何 VAL 对象名 / 对象
   中文名 / 能力 intent 段，就已经退化成「给某个对象写特判」了——那是范例与等价类
   台账的活，不是这里的。源码级断言当场抓。
2. **主链零行为变化**（§5 第 3 条，shadow 铁律）。它的全部价值就是不生效。
3. **该族真的被它接住了**：裸专名判 CLARIFY、补上任何谓述标记就判 EXECUTE，
   且**换一个从没见过的专名照样成立**——这是它与「写特判」的分水岭。
"""
from __future__ import annotations

import os
import re

import yaml

from orchestrator.cloud.actionability import (
    Actionability, _MARKER_CLASSES, _REDUPLICATION_RE, classify,
    extract_features,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
_KNOWLEDGE = os.path.join(_ROOT, "orchestrator", "edge", "knowledge")


class _Focus:
    def __init__(self, last_intent=""):
        self.last_intent = last_intent


def _domain_vocabulary() -> set[str]:
    """VAL 知识库里的**领域词**：对象 id、中文名、能力 intent 的对象段。

    从知识库派生而不是手抄一份（同 B4 `VEHICLE_INTENTS` 的做法）——手抄的那份
    迟早与知识库漂移，而这条断言一旦漂移就等于不存在。
    """
    vocab: set[str] = set()
    with open(os.path.join(_KNOWLEDGE, "commands.yaml"), encoding="utf-8") as f:
        commands = yaml.safe_load(f) or {}
    for name, spec in (commands.get("objects") or {}).items():
        vocab.add(str(name))
        display = str((spec or {}).get("display_name") or "").strip()
        if display:
            vocab.add(display)
        for intent in ((spec or {}).get("edge_intents") or []):
            vocab.update(str(intent).split("."))
    return {word for word in vocab if word}


# ── 1. 特征里不许有领域词汇 ────────────────────────────────────────────────

def test_domain_vocabulary_probe_is_not_empty():
    """先证明这条断言扫得到东西——空集合会让它永远绿。"""
    vocab = _domain_vocabulary()
    assert len(vocab) > 50, f"知识库派生词表只有 {len(vocab)} 条，扫描口径不对"
    assert "空调" in vocab or "trunk" in vocab


def test_no_marker_is_domain_vocabulary():
    """任一语法标记撞上领域词 = 已经退化成对象特判。"""
    vocab = _domain_vocabulary()
    for name, words in _MARKER_CLASSES.items():
        for word in words:
            assert word not in vocab, (
                f"{name} 类里的 `{word}` 是 VAL 领域词——特征必须是句法/形态量，"
                "不是字面对象表（B6 §2.3）")


def test_no_marker_contains_a_multi_char_domain_word():
    """反向也扫一遍：领域词整体出现在某个标记里同样不许（如「开空调」）。"""
    vocab = {w for w in _domain_vocabulary() if len(w) >= 2}
    for name, words in _MARKER_CLASSES.items():
        for word in words:
            hits = [v for v in vocab if v in word]
            assert not hits, f"{name} 类的 `{word}` 里含领域词 {hits}"


def test_marker_classes_are_all_consumed():
    """表里有类而特征提取不看它，等于声明了一个不生效的特征。"""
    features = extract_features("请把空调打开一下吧，明天几点的？不要着急")
    assert set(features.markers) - {"reduplication"} <= set(_MARKER_CLASSES)
    assert len(features.markers) >= 5


# ── 2. 判定行为 ────────────────────────────────────────────────────────────

def test_bare_proper_noun_asks_instead_of_guessing():
    for utterance in ("华润大厦", "上海", "静安寺"):
        verdict = classify(utterance)
        assert verdict.decision is Actionability.CLARIFY, utterance
        assert verdict.confidence >= 0.8


def test_unseen_proper_noun_behaves_the_same():
    """**这条是它与「写特判」的分水岭**：语料里从没出现过的专名照样判 CLARIFY。

    检索式修法在这里必然失败——裸专名之间 IDF-Dice 全 0.000（findings §25），
    而形态判据根本不看内容。
    """
    for utterance in ("鹿特丹港务大楼", "曲阜孔庙", "Zhongguancun"):
        assert classify(utterance).decision is Actionability.CLARIFY, utterance


def test_any_predication_marker_flips_it_to_execute():
    """补上任一谓述标记就该直接执行——`nq.landmark.explicit` 要守的正是这一侧。"""
    for utterance in ("导航到华润大厦", "带我去华润大厦", "华润大厦怎么走",
                      "上海明天天气怎么样", "查一下华润大厦"):
        verdict = classify(utterance)
        assert verdict.decision is Actionability.EXECUTE, utterance


def test_focus_explains_the_ellipsis():
    """有结构焦点时不反问：已经有活跃语境还问一遍是打断，不是澄清。

    用「就它」而不是「换一批」：后者自带谓词「换」与数词「一」，两条路都判 EXECUTE，
    **区分不出焦点这个特征有没有在起作用**（同 §4.3「A/B 之前先证明两臂真的不同」）。
    """
    assert classify("就它").decision is Actionability.CLARIFY
    assert classify("就它", focus=_Focus("nearby.search")
                    ).decision is Actionability.EXECUTE


def test_long_utterance_is_never_treated_as_a_bare_object():
    """裸对象是个**量**（短 + 零标记），长句即便零标记也不该被当成裸对象。"""
    long_text = "华润大厦静安寺陆家嘴外滩人民广场徐家汇虹桥枢纽浦东机场"
    assert len(long_text) > 14
    assert classify(long_text).decision is Actionability.EXECUTE


def test_reduplication_is_a_morphological_verb_signal():
    assert _REDUPLICATION_RE.search("看看我的待办")
    assert not _REDUPLICATION_RE.search("华润大厦")


def test_empty_input_does_not_crash_and_stays_low_confidence():
    verdict = classify("   ")
    assert verdict.decision is Actionability.CLARIFY
    assert verdict.confidence <= 0.5


def test_reject_is_declared_but_not_produced_in_v1():
    """v1 刻意不产出 REJECT（拒识判的是**受话**，与「说没说清」正交）。

    这条不是「以后再说」的占位——它防的是有人看见枚举里有 REJECT 就顺手补一个
    恒不命中的分支，让 shadow 读数里凭空多一档没人产出的决策。
    """
    samples = ["华润大厦", "导航到华润大厦", "嗯那个", "明天去上海吧", "", "换一批"]
    assert Actionability.REJECT not in {classify(s).decision for s in samples}


# ── 3. shadow 铁律：主链零行为变化 ─────────────────────────────────────────

def test_planner_never_reads_the_shadow_verdict():
    """planning.py 只许**写** `<计划>.actionability`，一个字都不许拿它做判断。

    行为测试证明不了这件事（今天没读不代表明天没读）；源码断言可以。

    ⚠ **判据形态 2026-08-16 改过一次（Q7 EL1/OR2），留痕。** 原判据是
    `uses == ["plan.actionability", "_actionability.classify"]`——它钉的是
    「出现了几次、按什么顺序」，而红线是**只写不读**。于是新增第二条计划出口
    （`focus_deterministic`）时它红了，可红线一点没破：新那次同样是写。
    「测试红了先问是修坏了还是前提变了」（§4.3）——此处是前提变了，
    所以改判据、不回退防御。新判据直接表达红线，且**不随出口条数漂移**：

      ① `_actionability` 模块只许被调 `classify`（多一个入口就多一条进决策的路）；
      ② 每一处 `.actionability` 都必须是**赋值左侧**——`==`、`if x.actionability`、
         当参数传，一律算读；
      ③ 每一处写入前，**同一个计划变量**的 `plan_mode` 必须已经定稿
         （结构上不可能反过来影响计划）。
    """
    with open(os.path.join(_ROOT, "orchestrator", "cloud", "planning.py"),
              encoding="utf-8") as f:
        src = f.read()
    # ① 模块入口
    called = set(re.findall(r"_actionability\.(\w+)", src))
    assert called == {"classify"}, (
        f"planning.py 动了 actionability 模块的别的入口: {sorted(called)}")
    # ② 只写不读。写入形态是 `<var>.actionability =`（`==` 不算），其余全判读。
    reads = re.findall(r"(?<![\w.])(\w+)\.actionability\b(?!\s*=(?!=))", src)
    assert reads == [], f"planning.py 把 shadow 判定拿去读了: {reads}——它只许被写"
    # ③ 每一处写入都在**该计划自己**定稿之后
    writes = list(re.finditer(r"(?<![\w.])(\w+)\.actionability\s*=(?!=)", src))
    assert writes, "一处写入都没有？shadow 观测被整个摘掉了"
    for match in writes:
        var = match.group(1)
        mode_at = src.find(f"{var}.plan_mode")
        assert 0 <= mode_at < match.start(), (
            f"{var}.actionability 写在 {var}.plan_mode 定稿之前——"
            f"结构上就有可能反过来影响计划")


def test_engine_shadow_attrs_never_touch_decisions():
    """engine 侧同款：只在 span attrs 里出现。"""
    with open(os.path.join(_ROOT, "orchestrator", "cloud", "engine.py"),
              encoding="utf-8") as f:
        src = f.read()
    assert src.count("_actionability_attrs") == 2      # 定义 + span 里那一次
    assert "if plan.actionability" not in src


# ── C6-C：条件式约束陈述（2026-08-28，QA P1-03 / adv T54）────────────────
# 「REJECT 声明了但 v1 不产出」的成本第一次在真栈兑现成误执行：整句只是一条
# 「以后怎么做事」的约束，planner 却产出了 reroute 加了个咖啡途经点。

def test_conditional_constraint_statement_is_clarify_not_execute():
    """T54 原句。它谓述标记一大把，所以这一支必须**排在谓述判定之前**。"""
    v = classify("如果找不到她的地点就直接问我，不要猜另一个城市")
    assert v.decision is Actionability.CLARIFY
    assert v.features.conditional_constraint is True


def test_a_real_conditional_request_still_executes():
    """对照一：条件式**动作请求**（语料 cp.adaptive.rain-umbrella 的形态）不许被拦。"""
    v = classify("如果明天下雨就提醒我带伞")
    assert v.decision is Actionability.EXECUTE
    assert v.features.conditional_constraint is False


def test_plain_negation_inside_a_condition_is_not_a_constraint():
    """对照二：**禁止式**否定与普通否定要分开。
    「如果明天**不**下雨」是在说世界状态，不是在约束助手怎么选。"""
    v = classify("如果明天不下雨就提醒我洗车")
    assert v.decision is Actionability.EXECUTE
    assert v.features.conditional_constraint is False


def test_prohibition_without_a_condition_is_not_a_constraint_statement():
    """两条形态是**合取**：只有禁止式否定、没有条件从句时不判本形态
    （「空调别开」归 runtime.polarity 的极性判据，不归这里）。"""
    assert classify("空调别开").features.conditional_constraint is False


def test_prohibitive_words_come_from_the_single_polarity_declaration():
    """禁止式词表必须是 `runtime.polarity.NEG_WORDS` 本身，不是抄过来的第二份。"""
    from runtime.polarity import NEG_WORDS
    from orchestrator.cloud.actionability import _PROHIBITIVE
    assert set(_PROHIBITIVE) == set(
        NEG_WORDS.removeprefix("(?:").removesuffix(")").split("|"))
    assert "别" in _PROHIBITIVE and "不" not in _PROHIBITIVE
