"""中文复合句的分句——**「什么词把两个诉求分开」的唯一声明处**。

## 为什么落在 runtime/

两个消费方够不着彼此：端侧 `orchestrator/edge/fast_intent.py` 拿它拆「一句话里有
几条车控指令」，云侧 `orchestrator/cloud/engine.py` 拿它拆「一句话里有几个诉求」
（C5-A 覆盖度观测）。而 `orchestrator/cloud/Dockerfile` **没有** `COPY orchestrator/edge`
——落点判据是镜像依赖闭包，同 `polarity.py` / `question_shape.py` 那两次。

## 两边共用的是表，不是语义

端侧拆完每一段**要各自解出一条命令**（还要接「和」的二次拆分、并列对象展开、
场景句整句拦截）；云侧拆完只问「这一段有没有被计划覆盖」。**语义不同、表相同**：
「并且/然后/顺便/再/逗号」把两个诉求分开，这件事与谁在读它无关。

§4.3 那条「同一件事有三份各自正确的实现，就迟早会有第四份是错的」在这里是**预防**
用法：云侧要的分句能力和端侧已有的那份是同一件事，所以不新起一张表。
反过来，端侧那些**特有**的处理（`_resplit_on_he` / `_expand_paired_objects` /
场景句拦截）留在端侧——它们是「怎么解命令」，不是「怎么断句」。

⚠ 本模块**不认识任何领域词**：表里只有连词与标点，源码级断言在
`runtime/tests/test_clause_split.py` 里钉着。
"""
from __future__ import annotations

import re

#: 分句分隔符。**这是全仓唯一的一份**（端侧 `_SPLIT_MARKERS` 直接引用它）。
#: 「还有」带负向前瞻是为了不切「还有多少电」这类疑问；「并」排掉「合并/并且」。
SPLIT_MARKERS = re.compile(
    r"[，,]?\s*(?:并且|同时|然后|接着|顺便|顺带|还有(?!多少|几|没)|另外)\s*|"
    r"(?<![合])并(?![且])\s*|"
    r"[，,]\s*再\s*|"
    r"[，,]\s*"
)
#: 同一套分隔符，带捕获组——`re.split` 会把分隔符本身也交回来，供分组判
#: 「顺承还是补语」（端侧 `_split_parts_with_sep` 的输入）。
SPLIT_MARKERS_CAPTURING = re.compile(f"({SPLIT_MARKERS.pattern})")


def split_clauses(text: str) -> list[str]:
    """按分隔符表拆句，返回 strip 后的非空段；无分隔符时原样返回单段。

    纯函数、零副作用、零领域词。**不做任何语义判断**——「这一段是不是一条命令」
    「这一段是不是肯定的」都是调用方的事，本模块只管断句。
    """
    return [part.strip() for part in SPLIT_MARKERS.split(text or "")
            if part and part.strip()]
