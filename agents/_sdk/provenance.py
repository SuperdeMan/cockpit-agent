"""Provider 决议真实性护栏（数据真实性治理 P0）。

设计：docs/design/2026-07-17-data-authenticity-governance.md（§4 D2 层1/层3）；
契约登记：docs/conventions.md §9.3。

两个约定，所有 Provider 工厂（`agents/*/src/providers/__init__.py`）收口处使用：

1. **fail-fast**：显式要求真实数据（vendor env 显式填了非 mock 值，或配了该域专属
   凭证）而构造不出真实 Provider 时，`fail()` 抛 ProviderConfigError——Agent 启动
   即炸、日志说清缺什么，绝不静默回退 mock。默认 env（全 mock/空）永不触发，
   CI 与离线开发照旧全 mock 可跑。
2. **决议日志**：无论 real 还是 mock，工厂返回前调用 `log_resolution()`，输出统一
   格式一行 `provider[<domain>]=<vendor>(real)` / `provider[<domain>]=mock`；
   全栈审计：`docker compose logs | grep "provider\\["`。

运行期（构造成功后）真实源调用失败**不**归本模块管：按各域既有惯例诚实降级
（weather/alerts/stock 先例：宁可说拿不到，不给假数据）。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import NoReturn

logger = logging.getLogger("sdk.provenance")


def _strict_forbidden(domain: str) -> bool:
    """严格栈（REQUIRE_REAL_PROVIDERS=on，治理 P2）：该域 mock 决议是否被禁止。
    豁免 REQUIRE_REAL_EXEMPT（逗号分隔域名；默认 parking=支付设计即模拟、
    knowledge=车书暂无真实实现——接入 pgvector 后应移出豁免）。默认 off：
    CI 与离线开发全 mock 照跑。"""
    if os.getenv("REQUIRE_REAL_PROVIDERS", "off").strip().lower() not in ("on", "true", "1", "yes"):
        return False
    exempt = {d.strip() for d in
              os.getenv("REQUIRE_REAL_EXEMPT", "parking,knowledge").split(",") if d.strip()}
    return domain not in exempt


class ProviderConfigError(RuntimeError):
    """显式配置了真实数据源但不可用。修配置，而不是带着假数据继续跑。"""


def fail(domain: str, why: str, cause: Exception | None = None) -> NoReturn:
    """fail-fast：仅在「显式 real 意图」下调用，抛 ProviderConfigError（含可读原因）。"""
    msg = (f"provider[{domain}] 显式配置真实数据源但不可用：{why}"
           f"——fail-fast，不静默回退 mock；如确要 mock 请清掉相关 env")
    logger.error(msg)
    err = ProviderConfigError(msg)
    if cause is not None:
        raise err from cause
    raise err


def log_resolution(domain: str, vendor: str, real: bool, provider=None) -> None:
    """统一决议日志（启动期一次）。print 保证容器 stdout 可 grep（同 llm_runtime 启动行惯例）。
    传 provider 时顺带盖来源章（attach() 出卡时读）——决议=日志+章，一处收口。
    严格栈（REQUIRE_REAL_PROVIDERS=on）下 mock 决议在此拒绝（豁免见 _strict_forbidden）。"""
    line = f"provider[{domain}]={vendor}(real)" if real else f"provider[{domain}]={vendor}"
    logger.info(line)
    print(line, flush=True)
    if not real and _strict_forbidden(domain):
        raise ProviderConfigError(
            f"REQUIRE_REAL_PROVIDERS=on：provider[{domain}] 决议为 mock——严格栈禁止；"
            f"补齐该域凭证，或把 {domain} 加入 REQUIRE_REAL_EXEMPT")
    if provider is not None:
        try:
            provider.provenance_vendor = vendor
            provider.provenance_mode = "real" if real else "mock"
        except Exception:   # __slots__ 等极端情况：不阻断构造
            pass


def attach(card, source, *, mode: str = "", fetched_at: str = "", note: str = ""):
    """给 ui_card 盖 `_prov` 真实性标记（契约 conventions §9.3；治理 P1）。

    - ``source``：provider 实例（读 log_resolution 盖的章）或 vendor 字符串。
    - ``mode`` 缺省从章取（real/mock）；``degraded``/``cached`` 由调用方显式传（note 说明原因）。
    - ``fetched_at`` 缺省=当下（ISO8601 本地时区）——数据获取时刻，非渲染时刻。
    - card 为 None 原样返回；``card_group`` 打在成员卡上（同源场景）。返回 card 便于内联。
    """
    if card is None:
        return None
    if isinstance(source, str):
        vendor, default_mode = source, "real"
    else:
        vendor = getattr(source, "provenance_vendor", "") or "unknown"
        default_mode = getattr(source, "provenance_mode", "") or "real"
    prov = {
        "mode": mode or default_mode,
        "vendor": vendor,
        "fetched_at": fetched_at or datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if note:
        prov["note"] = note
    if card.get("type") == "card_group":
        for item in card.get("items") or []:
            if isinstance(item, dict):
                item.setdefault("_prov", dict(prov))
        return card
    card["_prov"] = prov
    return card
