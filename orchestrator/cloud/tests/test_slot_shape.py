"""槽位值形状契约（C3-A）：判据两向断言 + 声明面的值域门禁 + 零领域词钉子。

三段各守一件事：

1. **判据本身**——挡住的那一半和**不许误伤**的那一半各写一遍。收窄面只写一边
   守不住：`item_name` 的全部价值在于「新请求进不来」，而它的全部风险在于
   「真商品名被判成新请求」，两者必须同批钉死。
2. **声明面的值域**——`slot_shapes` 的形状名不在运行期校验（桥的镜像够不着
   判据模块，抄一份就是第二份声明），所以拼错只能靠这里当场红。
3. **零领域词**——形状判据一旦认识某个商品名/商户名，它就从「形态判据」退化成
   对象特判，R2.1 那条铁律在补槽面被绕过去。做法抄 `test_question_shape`：
   词表从知识库派生，不手抄。
"""
from __future__ import annotations

import ast
import inspect
import os
import re

import pytest
import yaml

from orchestrator.cloud import slot_shape
from orchestrator.cloud.slot_shape import (
    DEFAULT_SHAPE_BY_SLOT, SHAPES, shape_of, verdict,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_SERVERS = os.path.join(_ROOT, "agents", "mcp_bridge", "servers.yaml")
_AGENTS_DIR = os.path.join(_ROOT, "agents")
_COMMANDS = os.path.join(
    _ROOT, "orchestrator", "edge", "knowledge", "commands.yaml")


# ── 1. order_id：写路径身份，匹配即定案 ──────────────────────────────────

@pytest.mark.parametrize("text", [
    "12345678901234",                       # 10–40 位纯数字
    "1234567890",
    "订单号是 ABC-123456",
    "单号：mcd20260828xyz",
])
def test_order_id_shapes_are_conclusive_slot_values(text):
    """匹配上就是订单号——它不可能是别的，所以短路成 False（定案）。"""
    assert verdict(["order_id"], text) is False, text


@pytest.mark.parametrize("text", [
    "附近的咖啡店",
    "上次麦当劳那单",
    "123456",                               # 位数不够，不是订单号形状
    "帮我查一下",
])
def test_non_order_id_shapes_are_topic_change(text):
    """真栈复现过：追问订单号时用户换题，整句被填进 `order_id`，下一跳直奔退款确认。"""
    assert verdict(["order_id"], text) is True, text


def test_order_id_falls_back_by_slot_name_without_declaration():
    """**漏声明的代价必须是「和以前一样严」，不是「突然变松」。**

    这条形状在本机制诞生前就对全部商户生效；靠声明兜底会让某个 capability
    忘了写一行 YAML 就放松一条写路径身份闸。
    """
    assert shape_of("order_id") == "order_id"
    assert shape_of("order_id", {}) == "order_id"
    # 显式声明优先（将来真要换一种订单号形状时，声明说了算）
    assert shape_of("order_id", {"order_id": "item_name"}) == "item_name"


# ── 2. item_name：名词短语，匹配**不定案** ───────────────────────────────

@pytest.mark.parametrize("text", [
    "附近的川菜馆",                          # 真栈 T45：新检索词
    "麦当劳的第二个和川菜的第二个哪个贵",      # 真栈 T46：疑问词
    "先来一份薯条，再要个圣代",                # 分句符 = 一段话不是一个名字
    "帮我找家评分高一点的店看看有什么便宜的套餐可以点",   # 超长
])
def test_item_name_rejects_sentences(text):
    assert verdict(["item_query"], text, {"item_query": "item_name"}) is True, text


@pytest.mark.parametrize("text", [
    "巨无霸",
    "标准美式",
    "马来咖喱风味薄皮肉骨鸡随心配",           # 真机菜单里最长的在售商品名（14 字）
    "泰式炭烤风味猪猪堡随心配",
    "全部",                                  # 归一成整份菜单是**桥的**职责（C3-C）
])
def test_item_name_does_not_reject_real_product_names(text):
    """**不误伤**那一半。

    ⚠ 那条 14 字的真商品名是这条测试存在的全部理由：方案草案把长度上限写成 12，
    照抄就会当场把它判成换话题。上限来自真机菜单观测，不是拍脑袋。
    """
    assert verdict(["item_query"], text, {"item_query": "item_name"}) is None, text


def test_item_name_match_is_not_conclusive():
    """匹配上什么都不证明——`点一杯拿铁` 完全长得像餐品名，它却是一句完整新指令。

    所以 `item_name` 只会返回 True/None，绝不返回 False：定案权留给
    `_is_topic_change` 后面那些通用判据（量词结构、动作动词开头…）。
    """
    for text in ("点一杯拿铁", "巨无霸", "要两杯"):
        assert verdict(["item_query"], text, {"item_query": "item_name"}) is not False


# ── 3. 多槽合流与未知形状 ────────────────────────────────────────────────

def test_any_slot_saying_no_wins():
    """多槽时**任一槽判「不像」即换题**：宁可重新规划，也不要把新请求塞进任何一个槽。"""
    declared = {"item_query": "item_name"}
    assert verdict(["item_query", "quantity"], "附近的川菜馆", declared) is True
    # order_id 定案 + item_query 不表态 ⇒ 定案
    assert verdict(["order_id", "item_query"], "12345678901234", declared) is False


def test_unknown_shape_name_behaves_as_undeclared():
    """认不出的形状名**不抛错也不静默收紧**——值域校验属于声明期（下面那两条门禁）。

    运行期再判一次只会把「有人拼错了一个词」表现成真栈上「补槽突然全废」。
    """
    assert verdict(["item_query"], "巨无霸", {"item_query": "itemname"}) is None
    assert verdict(["item_query"], "附近的川菜馆", {"item_query": "itemname"}) is None


def test_no_slot_no_verdict():
    assert verdict([], "随便什么话", {}) is None
    assert verdict(["store_hint"], "科苑南路店", {}) is None


# ── 4. 声明面值域门禁（运行期不校验，靠这里当场红） ──────────────────────

def _declared_shape_pairs() -> list[tuple[str, str, str, list]]:
    """(来源, capability, 槽名 -> 形状名, 该 capability 声明的槽) 全量盘点。"""
    pairs: list[tuple[str, str, str, list]] = []
    with open(_SERVERS, encoding="utf-8") as handle:
        servers = yaml.safe_load(handle) or {}
    for server in (servers.get("servers") or []):
        for group in ("tools", "workflows", "local_capabilities"):
            for spec in (server.get(group) or []):
                shapes = spec.get("slot_shapes") or {}
                name = str(spec.get("intent") or spec.get("name") or "")
                for slot, shape in shapes.items():
                    pairs.append(("servers.yaml", name, f"{slot}={shape}",
                                  list(spec.get("slots") or [])))
    for entry in sorted(os.listdir(_AGENTS_DIR)):
        path = os.path.join(_AGENTS_DIR, entry, "manifest.yaml")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        for cap in (data.get("capabilities") or []):
            for slot, shape in (cap.get("slot_shapes") or {}).items():
                pairs.append((f"{entry}/manifest.yaml",
                              str(cap.get("intent") or ""), f"{slot}={shape}",
                              list(cap.get("slots") or [])))
    return pairs


def test_declaration_probe_is_not_empty():
    """先证明这条门禁扫得到东西——盘点口径写错会让它永远绿。"""
    pairs = _declared_shape_pairs()
    assert pairs, "全仓一处 slot_shapes 声明都没扫到，盘点口径不对"
    assert any(pair[2] == "item_query=item_name" for pair in pairs)


def test_every_declared_shape_name_is_implemented():
    """形状名的值域**只在这里守**：桥的镜像里没有 `orchestrator/`，把已知形状名
    抄一份到桥里就是第二份声明——正是本字段要消灭的那类问题。"""
    for source, intent, pair, _slots in _declared_shape_pairs():
        shape = pair.split("=", 1)[1]
        assert shape in SHAPES, (
            f"{source} 的 {intent} 声明了未实现的形状 `{shape}`；"
            f"已实现的是 {sorted(SHAPES)}")


def test_every_declared_shape_slot_exists():
    """声明一个 planner 永远填不到的槽等于没声明（判据同 `candidate_slot`）。"""
    for source, intent, pair, slots in _declared_shape_pairs():
        slot = pair.split("=", 1)[0]
        assert slot in slots, (
            f"{source} 的 {intent} 给不存在的槽 `{slot}` 声明了形状；"
            f"它的 slots 是 {slots}")


def test_default_shape_table_only_holds_implemented_shapes():
    for slot, shape in DEFAULT_SHAPE_BY_SLOT.items():
        assert shape in SHAPES, f"槽名兜底表里的 `{slot}` 指向未实现的形状 {shape}"


# ── 5. 零领域词 ─────────────────────────────────────────────────────────

def _domain_vocabulary() -> set[str]:
    """VAL 知识库派生的领域词（对象 id / 中文名 / intent 段）。

    从知识库派生而不是手抄——手抄那份迟早与知识库漂移，而这条断言一旦漂移
    就等于不存在（做法同 `test_question_shape` / `test_actionability`）。
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
    return {word for word in vocab if word and len(word) > 1}


def test_domain_vocabulary_probe_is_not_empty():
    vocab = _domain_vocabulary()
    assert len(vocab) > 50, f"知识库派生词表只有 {len(vocab)} 条，扫描口径不对"
    assert "空调" in vocab


def _judgment_code() -> str:
    """判据本体的源码：**剥掉全部 docstring 与注释**再扫。

    两条都要剥，理由不同也同样必要：docstring 与注释讲的是**这条判据的来历**
    （真栈原话、为什么这么定），出现领域词是正常的、甚至是必须的；而 `_QUESTION_RE`
    这类**模式里**出现领域词才是「形态判据退化成对象特判」。
    `ast.unparse` 一步做完：留下的正好是模式、常量与名字。
    """
    tree = ast.parse(inspect.getsource(slot_shape))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None) or []
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_shape_predicates_contain_no_domain_word():
    """判据里出现任何领域词 = 这份「形态判据」已经退化成对象特判。

    ⚠ ASCII 词按**词边界**匹配，中文词按子串：`volume.dec` 派生出来的 `dec`
    是 `declared` 的子串，纯子串扫描会把一个正常的形参名报成领域词。
    真正的风险面是中文对象名/商品名，那一半仍然是子串匹配、一个字都不放过。
    """
    body = _judgment_code()
    for word in _domain_vocabulary():
        if re.fullmatch(r"[A-Za-z0-9_]+", word):
            hit = re.search(rf"\b{re.escape(word)}\b", body) is not None
        else:
            hit = word in body
        assert not hit, (
            f"slot_shape 判据里出现了 VAL 领域词 `{word}`——"
            f"形状必须是句法/结构量，不能认识具体对象")


def test_shape_table_is_the_only_place_that_names_a_shape():
    """加一种形状=加一行表，不改主循环（同 `retry_policy` 的表驱动纪律）。"""
    assert set(SHAPES) == {"order_id", "item_name", "ordinal"}
    for name, fn in SHAPES.items():
        assert callable(fn), name
