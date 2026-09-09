"""AR05 结构化契约的版本号。**端云同源一份。**

客户端按它判断服务端支持到哪一版契约（`session_info.contract_version`）。
端侧与云侧各写一个字面量，第一次 bump 就会出现「端说 ar05.1、云说 ar05.2」
——而客户端只看得到其中一个，于是按错的那版解析。

bump 规则：只在契约**语义**变化（字段含义/缺省语义改变、字段删除）时 +1；
**新增字段不 bump**——加字段本身向后兼容，旧客户端读不到它时行为逐字不变。
"""
from __future__ import annotations

AR05_CONTRACT_VERSION = "ar05.1"
