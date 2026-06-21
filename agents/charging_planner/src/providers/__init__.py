"""充电 Provider 工厂。"""
from .mock import MockChargingProvider


def build_charging_provider():
    """构建充电 Provider。有 key 时用真实实现，否则 mock。"""
    import os
    # 预留：真实厂商 key 检测
    # if os.getenv("TELADIAN_API_KEY"):
    #     from .teladian import TeladianChargingProvider
    #     return TeladianChargingProvider()
    return MockChargingProvider()
