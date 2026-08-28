"""槽位值形状契约（C3，MiniMax QA 修复批第 3 批）——**判据本体的唯一实现**。

## 它解决什么

`wait_slot` 续接的默认方向是错的：**「除非证明是换话题，否则当槽值」**。
于是 `_is_topic_change` 每漏一种说法，那句话就被整句塞进槽里——真栈 T44-T46
连吞三轮：「附近的川菜馆」「麦当劳的第二个和川菜的第二个哪个贵」全被当成
`item_query` 拿去菜单里搜，答「没查到"附近的川菜馆"」。补词表没有终点：
规则的边界比说法的边界窄，这是 reminder 那条 replace hint 退役时就写下的判据。

方向反转成 **「槽值必须长得像这个槽」**。先例就在被它取代的那段代码里：
`order_id` 只接受两种显式形状、其余一律换题（写路径身份不许靠自由文本猜）。
本模块把那个特例升成通用机制。

## 分工：名字在 Agent，判据在中央

- **哪个槽是什么形状**是领域知识 ⇒ 声明在 `manifest.yaml` / `servers.yaml`
  的 `slot_shapes`（`槽位名 -> 形状名`），经 `Capability.slot_shapes` 到编排。
- **形状怎么判**是通用判据 ⇒ 只在这里实现，**零领域词**（不出现任何商品名/
  商户名/城市名；`test_slot_shape.py` 有源码级断言守着）。

同 `verification` 的分工（领域期望由 Agent 声明、中央只跑通用求值器），
也同 B6 `input_schema`（值域契约声明在 servers.yaml、消费方在桥）。

## 三值语义（刻意不是 bool）

- `False` = **这就是槽值**，定案，不再走通用换题判据；
- `True`  = **长得不像这个槽的值**，判换话题；
- `None`  = 形状没有异议，但**不定案**——交回通用判据继续判。

第三个值是必需的：`order_id` 匹配上就是权威（除了订单号它不可能是别的），
而 `item_name` 匹配上什么都不证明——「点一杯拿铁」完全长得像餐品名，
它却是一句完整新指令（既有的「动词+数量+量词+宾语」判据认得它）。
把两者压成一个 bool 就必然要牺牲其中一条。
"""
from __future__ import annotations

import re

#: 「这是一次新检索」的词表**单一来源**（C3-B）：与 `candidate_query` 同一份。
#: 此前 `_NEW_SEARCH_RE` 与 `_is_topic_change` 各自演化，是 B1 那个 bug 的形态
#: 在词表层的复发——同一个判据抄两份，迟早给同一句话两个答案。
from .candidate_query import NEW_SEARCH_RE

#: 疑问词（形态判据，零领域词）。问句不是槽位答案——同 `_is_topic_change`
#: 那条既有判据，这里只是把它收进形状里，让「长得像不像」一次判完。
_QUESTION_RE = re.compile(r"哪个|哪些|哪家|哪一个|什么|怎么|多少|有没有|吗|呢|[?？]")

#: 分句符：一句话里出现它就不再是一个**名词短语**，而是一段话。
_CLAUSE_RE = re.compile(r"[，,；;。!！]")

#: `item_name` 的长度上限。**来自真机观测而不是拍脑袋**：2026-08-13 麦当劳
#: `query-meals` 真机菜单里最长的在售商品名是「马来咖喱风味薄皮肉骨鸡随心配」
#: （14 字），瑞幸同族更短。取 20 留足余量——长度是这条形状里**最弱**的一个信号，
#: 真正干活的是上面三条结构判据；把它压到 12（方案草案值）会当场误伤那条 14 字的真商品名。
_ITEM_NAME_MAX = 20


def _order_id(text: str) -> bool | None:
    """订单号：写路径身份，只接受两种**显式引用**，其余一律换题。

    形状取自两家官方订单号（10–40 位纯数字），以及带「订单号/单号」标签的
    有界字母数字 id。真栈长会话复现过：系统追问订单号后用户换题说「附近的咖啡店」，
    旧逻辑把整句填成 `order_id`，下一跳直接生成「准备退款，确认吗」——
    确认闸挡住了最终写入，但错误意图已经被推进到危险边界。

    「上次麦当劳那单」也走换题重新规划，由账本归属解析，而不是直塞权威 id 槽。
    """
    t = (text or "").strip()
    if re.fullmatch(r"[0-9]{10,40}", t):
        return False
    if re.fullmatch(
        r"(?:订单号|单号)\s*(?:是|为|[:：])?\s*[0-9A-Za-z][0-9A-Za-z_-]{2,63}",
        t,
        flags=re.IGNORECASE,
    ):
        return False
    return True


def _item_name(text: str) -> bool | None:
    """商品/餐品名：一个**名词短语**，不是一句话。

    四条结构信号，命中任一即判「长得不像」：新检索词、疑问词、分句符、超长。
    匹配上**不定案**（返回 None）——见模块 docstring 的第三值说明。
    """
    t = (text or "").strip()
    if not t:
        return True
    if NEW_SEARCH_RE.search(t):
        return True
    if _QUESTION_RE.search(t):
        return True
    if _CLAUSE_RE.search(t):
        return True
    if len(t) > _ITEM_NAME_MAX:
        return True
    return None


#: 形状名 → 判据。**加一种形状=加一行**，`_is_topic_change` 主体不动
#: （同 `retry_policy` 的表驱动纪律）。
SHAPES: dict[str, object] = {
    "order_id": _order_id,
    "item_name": _item_name,
}

#: 声明缺省时按**槽名**兜底的形状。目前只有一条，而且它是刻意的：
#: `order_id` 的严格形状在本机制诞生**之前**就对全部商户生效，
#: 不能因为「某个 capability 忘了声明」而放松一条写路径身份闸——
#: 漏声明的代价必须是「和以前一样严」，不是「突然变松」。
#: 显式声明优先于本表。
DEFAULT_SHAPE_BY_SLOT: dict[str, str] = {"order_id": "order_id"}


def shape_of(slot_name: str, declared: dict | None = None) -> str:
    """这个槽该按哪个形状判：声明优先，缺省按槽名兜底，都没有则空串。"""
    name = str(slot_name or "").strip()
    if not name:
        return ""
    value = str((declared or {}).get(name) or "").strip()
    if value:
        return value
    return DEFAULT_SHAPE_BY_SLOT.get(name, "")


def verdict(slot_names, text: str, declared: dict | None = None) -> bool | None:
    """对一组待补槽给出三值判定（语义见模块 docstring）。

    多槽时的合流序：**任一槽判「不像」即换题**（宁可重新规划，也不要把一句
    新请求塞进任何一个槽）；否则任一槽定案即定案；都不表态则返回 None。

    形状名认不出时**当没声明处理**（返回 None 这一路），不抛错也不静默收紧——
    值域校验属于声明期（`test_slot_shape.py` 逐条比对 SHAPES），
    运行期再判一次只会让一个拼错的形状名在真栈上表现成「补槽突然全废」。
    """
    verdicts = []
    for slot in (slot_names or []):
        fn = SHAPES.get(shape_of(slot, declared))
        if fn is None:
            continue
        verdicts.append(fn(text))
    if any(v is True for v in verdicts):
        return True
    if any(v is False for v in verdicts):
        return False
    return None
