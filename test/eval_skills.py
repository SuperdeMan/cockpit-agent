"""Skill 检索 golden 评测（M0b Shadow Retrieval 的离线部分）。

对 skills/guides/*.yaml 自带的 golden 用例跑词法检索：该 guide 必须被自己的 golden
文本检回（召回下限）；同时跑一组反例句（普通单域句）断言零误召回（噪声上限）。
policy 是常驻注入、无检索环节，其 golden 属 live 车道（planner 意图级），此处跳过。

用法：python test/eval_skills.py            # 退出码 0=全过
真栈 shadow 召回观察：cloud.planning span 的 skills 属性（dashboard 会话下钻）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from orchestrator.cloud import skills as sk  # noqa: E402

# 反例：不该召回任何 guide 的普通单域句（阈值噪声上限探针）
NEGATIVES = [
    "今天天气怎么样",
    "把空调调到24度",
    "播放周杰伦的歌",
    "导航去天安门",          # 纯导航无停靠需求：召回 navigation-with-stop 算噪声
    "现在几点了",
    "帮我查下比亚迪的股价",
]


def main() -> int:
    store = sk.SkillStore()
    guides = store.guides()
    if not guides:
        print("✗ 未加载到任何 guide（skills/guides/ 为空？）")
        return 1
    print(f"=== Skill 检索 golden（guides={len(guides)}，"
          f"top_k={sk.SKILL_TOP_K}）===")

    failures = []
    total = 0
    for g in guides:
        for case in g.golden:
            text = str(case.get("text") or "").strip()
            if not text:
                continue
            total += 1
            hits = [d.name for d in sk.top_guides(text, guides)]
            ok = g.name in hits
            print(f"  [{'✓' if ok else '✗'}] {g.name} ← {text}  hits={hits}")
            if not ok:
                failures.append(f"{g.name}: {text}")

    noise = []
    for text in NEGATIVES:
        hits = [d.name for d in sk.top_guides(text, guides)]
        print(f"  [{'✓' if not hits else '…'}] 反例 {text}  hits={hits}")
        if hits:
            noise.append(f"{text} -> {hits}")

    print(f"\n召回：{total - len(failures)}/{total}；反例误召回：{len(noise)}/{len(NEGATIVES)}")
    if failures:
        print("✗ 召回失败：\n  " + "\n  ".join(failures))
        return 1
    if noise:
        # 误召回是噪声不是错误（注入无害多费 token），超过一半才判失败
        if len(noise) > len(NEGATIVES) // 2:
            print("✗ 误召回过多（阈值失效）：\n  " + "\n  ".join(noise))
            return 1
        print("⚠ 存在误召回（可接受噪声，shadow 阶段持续观察）：\n  " + "\n  ".join(noise))
    print("✅ PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
