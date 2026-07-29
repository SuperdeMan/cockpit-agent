"""关键词层打分的性质测试（M5 P2-D4：逐单字符 → bigram + 长度归一）。

这份测试钉的不是某个具体分值，是**两条性质**：
  ① 中文虚词不该把分抬起来（旧算法「的/我/一/个」在任何 desc 里都命中）；
  ② **加长描述不该改变 top-1**——deep-research 的 manifest 里白纸黑字写着
     「desc 刻意保持原句不加长……曾把『自驾游路线安排』从 trip-planner 吸过来」。
     那句注释是**倒果为因的物证**：描述写法在为打分算法的缺陷让路。性质②守住了，
     那条注释才可以放心删掉。
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "gen" / "python")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from registry.store import Store  # noqa: E402


def _cap(intent, desc="", examples=()):
    return SimpleNamespace(intent=intent, description=desc, examples=list(examples))


def _manifest(agent_id, caps, perms=()):
    return SimpleNamespace(agent_id=agent_id, capabilities=caps,
                           requires_permissions=list(perms), category="ecosystem")


def test_function_words_do_not_lift_score():
    """「我想要一个的」全是虚词——不该命中任何实义能力。"""
    m = _manifest("charging-planner", [
        _cap("charging.find", "查找附近充电桩并按功率排序", ["附近哪里有充电桩"])])
    assert Store._score(m, "", "我想要一个的") == 0.0


def test_content_words_still_hit():
    m = _manifest("charging-planner", [
        _cap("charging.find", "查找附近充电桩并按功率排序", ["附近哪里有充电桩"])])
    assert Store._score(m, "", "附近哪里有充电桩") > 0.6


def test_exact_intent_still_wins():
    m = _manifest("x", [_cap("a.b", "毫不相关的描述")])
    assert Store._score(m, "a.b", "完全对不上的话") == 1.0


def test_longer_description_does_not_steal_top1():
    """性质②：把 deep-research 的 desc 加长三倍，「自驾游路线安排」仍应归 trip-planner。

    旧算法下这是**必然翻车**的（分子是 query 字符在 hay 里的命中数，hay 越长越容易命中）；
    新算法分子是 bigram 重合、分母是 query 自身长度，与 hay 长度解耦。"""
    trip = _manifest("trip-planner", [
        _cap("trip.plan", "多日行程规划：把自驾游的路线、住宿、用餐安排成逐日计划",
             ["帮我把这次自驾游的路线安排一下"])])
    short = "对一个主题做深度调研，产出带引用的分节报告"
    long = short + "——拆成多视角子问题、并行联网检索正文级资料、覆盖出行路线安排、" \
                   "自驾游攻略、住宿餐饮比较、行程时间规划等各类主题的系统性研究"
    q = "帮我把这次自驾游的路线安排一下"
    for desc in (short, long):
        research = _manifest("deep-research", [_cap("research.run", desc, [])])
        assert Store._score(trip, "", q) > Store._score(research, "", q), \
            f"desc 长度改变了 top-1（len={len(desc)}）——长度偏置又回来了"


def test_real_manifests_keep_recall_top1():
    """拿真实 manifests 做一遍冒烟（细粒度断言在 test/eval_registry_resolve.py 的基线里）。"""
    from agents._sdk.manifest import load_manifest
    store = Store()
    for p in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        m = load_manifest(p)
        store.register(m, f"{m.agent_id}:0")
    for query, expect in (("附近哪里有充电桩", "charging-planner"),
                          ("导航去北京南站", "navigation"),
                          ("深入研究一下人工智能的发展趋势", "deep-research")):
        top = store.resolve("", query, 1, [])
        assert top and top[0][0].manifest.agent_id == expect, \
            f"{query!r} → {top[0][0].manifest.agent_id if top else None}，期望 {expect}"
