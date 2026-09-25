"""位置词表：镜像逐条对账 + 唯一一份扫描算法（评审四轮待办，2026-09-25）。

修前三份词表各记各的（entities 22 个词、端侧规则 12 个、云侧焦点 8 个）：「打开后排左车窗」端侧只认出「后排」⇒ 两扇后窗都开；
「右前车窗」认不出位置 ⇒ 按缺省范围执行；云侧焦点把「后排左」记成「后排」⇒ 下一轮「关掉」关两扇。
"""
from __future__ import annotations

import os
import re

import pytest
import yaml

from runtime.positions import POSITIONS, distinct_positions, scan_positions

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ENTITIES = os.path.join(_ROOT, "orchestrator", "edge", "knowledge", "entities.yaml")


def _authority() -> dict[str, tuple[str, ...]]:
    with open(_ENTITIES, encoding="utf-8") as handle:
        table = (yaml.safe_load(handle) or {}).get("positions") or {}
    return {str(word): tuple(value) if isinstance(value, list) else (str(value),) for word, value in table.items()}


# ── 1. 镜像与权威逐条相同 ──────────────────────────────────────────────────────

def test_the_mirror_is_the_entities_table_word_for_word():
    authority = _authority()
    assert authority, "entities.yaml 没有 positions 节"
    assert POSITIONS == authority


def test_no_service_module_keeps_its_own_position_table():
    """第二份词表的形状：同一个源文件里出现三个以上带引号的位置词。测试与镜像本身除外。"""
    pattern = re.compile(r"""["'](%s)["']""" % "|".join(
        re.escape(w) for w in sorted(POSITIONS, key=len, reverse=True)))
    offenders = []
    for base in ("orchestrator", "agents", "runtime", "llm-gateway", "memory", "security", "proactive", "registry"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(_ROOT, base)):
            dirnames[:] = [d for d in dirnames if d not in ("tests", "node_modules", "__pycache__")]
            for name in filenames:
                path = os.path.join(dirpath, name)
                if not name.endswith(".py") or os.path.samefile(path, os.path.join(_ROOT, "runtime", "positions.py")):
                    continue
                with open(path, encoding="utf-8", errors="replace") as handle:
                    words = set(pattern.findall(handle.read()))
                if len(words) >= 3:
                    offenders.append((os.path.relpath(path, _ROOT), sorted(words)))
    assert offenders == []


# ── 2. 扫描：正向最大匹配、互不重叠、按出现顺序 ───────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("打开后排左车窗", ["后排左"]),              # 修前端侧 ['后排'] = 两扇后窗
    ("打开前排右座椅加热", ["前排右"]),          # 修前 ['前排'] = 两个前座
    ("打开后排中间座椅加热", ["后排中间"]),       # 修前 ['后排']
    ("打开右前车窗", ["右前"]),                  # 修前认不出
    ("折叠右侧后视镜", ["右侧"]),
    ("关闭所有位置的车窗", ["所有位置"]),
    ("打开副驾驶位车窗", ["副驾驶位"]),          # 不先撞上「驾驶位」
    ("打开主驾驶位车窗", ["主驾驶"]),            # 起点 0 上最长的是「主驾驶」，不看后面的「驾驶位」
    ("打开主驾和副驾车窗", ["主驾", "副驾"]),
    ("打开副驾和主驾车窗", ["副驾", "主驾"]),     # 按原话出现顺序
    ("主驾车窗和主驾座椅", ["主驾"]),            # 同一个词只记一次
    ("打开车窗", []),
    ("", []),
])
def test_scan(text, expected):
    assert scan_positions(text) == expected


def test_every_word_reads_back_as_itself():
    for word in POSITIONS:
        assert scan_positions(f"打开{word}车窗") == [word], word


def test_the_result_does_not_depend_on_the_table_order():
    words = list(POSITIONS)
    samples = [f"打开{a}和{b}" for a in words for b in words] + ["主驾驶位", "后排左侧", "前排右边", "左后排"]
    for text in samples:
        assert scan_positions(text, words) == scan_positions(text, list(reversed(words))), text


def test_a_caller_may_pass_its_own_loaded_table():
    table = {"主驾": "front_left", "副驾": "front_right"}
    assert scan_positions("主驾和副驾驶", table) == ["主驾", "副驾"]


# ── 3. 同一个座位只算一次 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("words, expected", [
    (["副驾驶", "副驾"], ["副驾驶"]),              # 同一个座位的两个说法
    (["右前", "副驾"], ["右前"]),
    (["主驾", "驾驶位"], ["主驾"]),
    (["前排", "主驾"], ["前排"]),                  # 已被「前排」覆盖
    (["主驾", "前排"], ["主驾", "前排"]),           # 「前排」还多出副驾：两个都留
    (["主驾", "副驾"], ["主驾", "副驾"]),
    (["后排左", "后排右"], ["后排左", "后排右"]),
    (["驾驶员", "驾驶员"], ["驾驶员"]),            # 不在词表里：按字面去重、不猜
    (["", "  ", "副驾"], ["副驾"]),
])
def test_distinct_positions(words, expected):
    assert distinct_positions(words) == expected
