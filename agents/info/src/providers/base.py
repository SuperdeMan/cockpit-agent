"""天气/信息 Provider 接口。所有气象厂商实现 WeatherProvider。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Weather:
    city: str = ""
    temp: str = ""          # 当前温度 ℃
    text: str = ""          # 天气现象（晴/多云/小雨…）
    feels_like: str = ""    # 体感温度 ℃
    humidity: str = ""      # 相对湿度 %
    wind_dir: str = ""      # 风向
    wind_scale: str = ""    # 风力等级
    update_time: str = ""   # 数据更新时间


class WeatherProvider(ABC):
    @abstractmethod
    async def now(self, city: str, meta: dict | None = None) -> Weather:
        """查询城市实时天气。meta 透传 trace_id/span_id 供可观测（可选）。"""
        ...
