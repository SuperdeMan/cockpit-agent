"""商户规格组真机取证（Q12 规格维，2026-08-21）——把官方 productAttrs 扫成台账。

## 它存在的理由

`agents/mcp_bridge/servers.yaml` 里每个规格槽都要声明**它对应哪个官方规格组**
（`input_schema.<slot>.groups`）。这些组名**不能靠常见叫法猜**：2026-08-21 真机
一扫就发现在瑞幸身上「冰量」这一组**根本不存在**（冰档位是「温度」组的取值），
而代码里猜的 `ice→{冰量,冰度,加冰}` / `milk→{奶底,奶类,乳基底,奶制品}` 一条都
匹配不上——声明齐全、planner 也填对了槽，用户仍然被答「这款饮品不支持"少冰"」。

所以本脚本产出的是**观测样本台账**（不是声明）：
`agents/mcp_bridge/knowledge/merchant_specs_observed.yaml`。门禁方向**单向**——
契约里声明的组名/项名必须在台账里出现过；台账里有而没声明的，只说明「还没有
消费方」，不判红。

⚠ 样本不是全集。声明一个真实存在、但本次没扫到的组名时，正确处置是**扩样本**
（加种子词重跑），不是放宽门禁。

## 跑法（需要 .env 里的商户凭证；只调 write:false 的只读工具）

    python scripts/probe_merchant_specs.py --dept 324590            # 只打印
    python scripts/probe_merchant_specs.py --dept 324590 --write    # 落台账

`--dept` 不给时先按坐标查一家**营业中**的门店（打烊门店的 detail 拿不到规格）。
本脚本是**取证脚本不是准入闸**，不进 CI（同 `probe_qa_regression.py` 的定位）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "gen" / "python") not in sys.path:
    sys.path.insert(0, str(_ROOT / "gen" / "python"))

try:
    sys.stdout.reconfigure(encoding="utf-8")     # Windows GBK 宿主常驻放大器
except Exception:
    pass

from agents.mcp_bridge.src.mcp_client import HttpMcpClient   # noqa: E402
from scripts.dev_stack_lib import read_root_env              # noqa: E402

LEDGER = _ROOT / "agents" / "mcp_bridge" / "knowledge" / "merchant_specs_observed.yaml"
_URL = "https://gwmcp.lkcoffee.com/order/user/mcp"
#: 种子词刻意覆盖不同品类——规格组随品类差别很大（咖啡有咖啡豆/浓度，茶有茶风味/
#: 小料，只扫拿铁会漏掉一半组名）。串行调用，同高德 QPS 教训不并发打商户。
_SEEDS = ("生椰拿铁", "美式", "拿铁", "瑞纳冰", "茶", "柠檬茶", "厚乳拿铁",
          "杨枝甘露", "轻乳茶", "橙C")
#: 深圳科技园一带的坐标，只在没给 --dept 时用来找一家营业中的门店。
_PROBE_LNG, _PROBE_LAT = 113.9412, 22.5410


async def _open_dept(client) -> tuple[int, str]:
    result = await client.call_tool(
        "queryShopList",
        {"deptName": "", "longitude": _PROBE_LNG, "latitude": _PROBE_LAT})
    shops = ((result or {}).get("data") or {}).get("data") or []
    for shop in shops:
        if str(shop.get("workStatus") or "").strip() == "营业中":
            return int(shop["deptId"]), str(shop.get("deptName") or "")
    raise SystemExit(
        "附近没有营业中的瑞幸门店——打烊门店取不到 productAttrs，换个时间再扫。")


async def scan(dept_id: int) -> tuple[dict[str, set[str]], dict[int, str]]:
    token = read_root_env(_ROOT, {"LUCKIN_MCP_TOKEN"}).get("LUCKIN_MCP_TOKEN", "")
    if not token:
        raise SystemExit("根 .env 里没有 LUCKIN_MCP_TOKEN")
    client = HttpMcpClient("luckin", _URL, {"Authorization": f"Bearer {token}"},
                           timeout_s=25.0)
    await client.start()
    try:
        await client.initialize()
        if not dept_id:
            dept_id, dept_name = await _open_dept(client)
            print(f"未指定 --dept，取营业中门店 {dept_id}（{dept_name}）")
        groups: dict[str, set[str]] = {}
        products: dict[int, str] = {}
        for seed in _SEEDS:
            found = await client.call_tool(
                "searchProductForMcp", {"deptId": dept_id, "query": seed})
            for product in (((found or {}).get("data") or {}).get("data") or [])[:3]:
                product_id = product.get("productId")
                if not isinstance(product_id, int) or product_id in products:
                    continue
                products[product_id] = str(product.get("productName") or "")
                detail = await client.call_tool(
                    "queryProductDetailInfo",
                    {"deptId": dept_id, "productId": product_id})
                attrs = (((detail or {}).get("data") or {}).get("data")
                         or {}).get("productAttrs") or []
                shown = []
                for group in attrs:
                    if not isinstance(group, dict):
                        continue
                    name = str(group.get("attributeName") or "").strip()
                    if not name:
                        continue
                    # `canSelected` 是「这一项现在能不能选」——**不能拿它筛台账**：
                    # 台账记的是「这个组存在过、这个项名存在过」，某款商品此刻
                    # 售罄不改变名字的存在性。
                    subs = [str(sub.get("attributeName") or "").strip()
                            for sub in group.get("productSubAttrs") or []
                            if isinstance(sub, dict)
                            and str(sub.get("attributeName") or "").strip()]
                    groups.setdefault(name, set()).update(subs)
                    shown.append(f"{name}[{'/'.join(subs)}]")
                print(f"  {products[product_id]}: {' | '.join(shown)}")
        return groups, products
    finally:
        await client.close()


def render(groups: dict[str, set[str]], products: dict[int, str],
           dept_id: int, scanned_on: str) -> str:
    lines = [
        "# merchant_specs_observed.yaml — 商户规格组**真机观测台账**（Q12 规格维，2026-08-21）",
        "#",
        "# ## 契约（读之前先看这段）",
        "#",
        "# 本表是**观测样本，不是声明**。由 `scripts/probe_merchant_specs.py` 扫官方",
        "# `queryProductDetailInfo` 产出，只进不出地记录「哪些规格组名与项名真实存在过」。",
        "#",
        "# 门禁方向**单向**（`agents/mcp_bridge/tests/test_merchant_spec_contract.py`）：",
        "#   `servers.yaml` 的 `input_schema.<slot>.groups` / `aliases` 的键，",
        "#   **必须在本表里出现过**——反向不要求（表里有而没声明的只说明还没有消费方）。",
        "#",
        "# ⚠ **样本不是全集。** 要声明一个真实存在但本次没扫到的组名时，正确处置是",
        "# **扩样本**（给脚本加种子词重扫），不是放宽门禁——门禁存在的唯一理由就是",
        "# 「组名不许靠常见叫法猜」。",
        "#",
        "# ## 它为什么存在",
        "#",
        "# 2026-08-21 真机首扫：代码里猜的 `ice→{冰量,冰度,加冰}` 在瑞幸**一个都不存在**",
        "# ——冰档位（冰/少冰/去冰）是**「温度」组的取值**；`milk→{奶底,奶类,乳基底,",
        "# 奶制品}` 同样全部落空（真实是 奶/奶基/奶油）；`sweetness→{糖度,甜度}` 漏了",
        "# 美式族用的「糖」。于是这三个槽声明齐全、planner 也填对了，用户仍然被答",
        '# 「这款饮品不支持“少冰”」——**声明了 ≠ 可达**（契约 §9.29 的商户版）。',
        "",
        "schema_version: 1",
        "",
        "merchants:",
        "  luckin:",
        f"    scanned_on: {scanned_on}",
        f"    dept_id: {dept_id}",
        f"    products_scanned: {len(products)}",
        "    groups:",
    ]
    for name in sorted(groups):
        subs = "".join(f"\n        - {value}" for value in sorted(groups[name]))
        lines.append(f"      {name}:{subs}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dept", type=int, default=0, help="门店 deptId（不给则自动找一家营业中的）")
    parser.add_argument("--write", action="store_true", help="落盘台账（默认只打印）")
    parser.add_argument("--scanned-on", default="", help="台账里记的扫描日期（YYYY-MM-DD）")
    args = parser.parse_args()

    groups, products = asyncio.run(scan(args.dept))
    print("\n=== 观测到的规格组 ===")
    for name in sorted(groups):
        print(f"  {name}: {sorted(groups[name])}")
    if not args.write:
        print("\n（只打印。要落台账加 --write）")
        return 0
    if not args.scanned_on:
        # 墙钟不由本脚本自己拍脑袋——扫描日期必须显式传，避免台账里出现
        # 与实际取证日期不符的 provenance。
        print("--write 时必须显式给 --scanned-on YYYY-MM-DD", file=sys.stderr)
        return 2
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(render(groups, products, args.dept, args.scanned_on),
                      encoding="utf-8")
    print(f"\n台账已写 {LEDGER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
