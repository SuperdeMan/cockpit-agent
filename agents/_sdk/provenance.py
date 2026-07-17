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
from typing import NoReturn

logger = logging.getLogger("sdk.provenance")


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


def log_resolution(domain: str, vendor: str, real: bool) -> None:
    """统一决议日志（启动期一次）。print 保证容器 stdout 可 grep（同 llm_runtime 启动行惯例）。"""
    line = f"provider[{domain}]={vendor}(real)" if real else f"provider[{domain}]={vendor}"
    logger.info(line)
    print(line, flush=True)
