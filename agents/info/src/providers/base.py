"""天气/信息 Provider 接口。所有气象厂商实现 WeatherProvider。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Weather:
    city: str = ""
    temp: str = ""          # 当前温度 ℃
    text: str = ""          # 天气现象（晴/多云/小雨…）
    feels_like: str = ""    # 体感温度 ℃
    humidity: str = ""      # 相对湿度 %
    wind_dir: str = ""      # 风向
    wind_scale: str = ""    # 风力等级
    precip: str = ""        # 过去 1 小时降水量 mm
    pressure: str = ""      # 大气压 hPa
    visibility: str = ""    # 能见度 km
    cloud: str = ""         # 云量 %
    dew_point: str = ""     # 露点温度 ℃
    update_time: str = ""   # 数据更新时间


@dataclass
class ForecastDay:
    """单日天气预报。"""
    date: str = ""              # 日期 YYYY-MM-DD
    text_day: str = ""          # 白天天气现象
    text_night: str = ""        # 夜间天气现象
    temp_high: str = ""         # 最高温度 ℃
    temp_low: str = ""          # 最低温度 ℃
    wind_dir: str = ""          # 风向
    wind_scale: str = ""        # 风力等级
    humidity: str = ""          # 相对湿度 %
    precip: str = ""            # 预计降水量 mm
    uv_index: str = ""          # 紫外线指数
    sunrise: str = ""           # 日出 HH:MM
    sunset: str = ""            # 日落 HH:MM


@dataclass
class WeatherAlert:
    """天气预警/警报。"""
    title: str = ""             # 预警标题（如 "北京市气象台发布暴雨蓝色预警"）
    level: str = ""             # 预警等级（蓝/黄/橙/红）
    type_name: str = ""         # 预警类型（暴雨/大风/高温…）
    text: str = ""              # 预警详情
    pub_time: str = ""          # 发布时间


@dataclass
class LifeIndex:
    """生活指数。"""
    category: str = ""          # 指数类别（运动/洗车/紫外线…）
    name: str = ""              # 指数名称
    level: str = ""             # 等级（适宜/较适宜/较不宜…）
    text: str = ""              # 建议描述


class WeatherProvider(ABC):
    @abstractmethod
    async def overview(self, city: str,
                       meta: dict | None = None) -> WeatherOverview:
        """聚合实时天气、预报、空气、生活指数和预警。

        当前天气是必要数据；其他分区由实现方按能力尽力返回，失败时置空而不覆盖
        已成功的真实数据。
        """
        ...

    @abstractmethod
    async def now(self, city: str, meta: dict | None = None) -> Weather:
        """查询城市实时天气。meta 透传 trace_id/span_id 供可观测（可选）。"""
        ...

    @abstractmethod
    async def forecast(self, city: str, days: int = 3,
                       meta: dict | None = None) -> list[ForecastDay]:
        """查询城市未来 N 天天气预报。days 通常 3 或 7，由厂商能力决定。"""
        ...

    @abstractmethod
    async def alerts(self, city: str,
                     meta: dict | None = None) -> list[WeatherAlert]:
        """查询城市当前生效的天气预警。无预警返回空列表。"""
        ...

    @abstractmethod
    async def indices(self, city: str,
                      meta: dict | None = None) -> list[LifeIndex]:
        """查询城市生活指数（运动/洗车/紫外线等）。"""
        ...

    @abstractmethod
    async def air_quality(self, city: str,
                          meta: dict | None = None) -> AirQuality:
        """查询城市实时空气质量（AQI/PM2.5/PM10 等）。"""
        ...


@dataclass
class AirQuality:
    """空气质量数据。"""
    aqi: str = ""               # AQI 数值
    category: str = ""          # 类别（优/良/轻度/中度/重度/严重）
    primary_pollutant: str = "" # 首要污染物
    pm2p5: str = ""             # PM2.5 浓度 μg/m³
    pm10: str = ""              # PM10 浓度 μg/m³
    no2: str = ""               # NO2 浓度
    o3: str = ""                # O3 浓度
    co: str = ""                # CO 浓度
    so2: str = ""               # SO2 浓度
    update_time: str = ""       # 更新时间


@dataclass
class WeatherOverview:
    """一张综合天气卡需要的可选天气分区。"""
    now: Weather = field(default_factory=Weather)
    forecast: list[ForecastDay] = field(default_factory=list)
    air_quality: AirQuality = field(default_factory=AirQuality)
    indices: list[LifeIndex] = field(default_factory=list)
    alerts: list[WeatherAlert] = field(default_factory=list)
    alerts_available: bool = True


# ── 联网搜索 Provider ──────────────────────────────────────────────

@dataclass
class SearchResult:
    """搜索结果条目。

    ``snippet`` 是搜索引擎给的短摘要（1~2 句）；``content`` 是正文级原料
    （如 Exa ``contents.text``）。接地合成优先用 ``content``，缺失才退回 ``snippet``。
    """
    title: str = ""
    url: str = ""
    snippet: str = ""           # 摘要/描述（短）
    source: str = ""            # 来源域名
    published: str = ""         # 发布时间 ISO（用于时效展示/排序，可为空）
    content: str = ""           # 正文（长，可为空）


class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, limit: int = 5,
                     meta: dict | None = None, **kwargs) -> list[SearchResult]:
        """联网搜索。meta 透传 trace_id/span_id 供可观测（可选）。

        ``**kwargs`` 容纳 provider 特有的可选项（如 Exa 的 ``recency_days``/``category``）；
        不支持的 provider 忽略即可，保证降级链上各实现签名兼容。
        """
        ...


# ── 新闻 Provider ──────────────────────────────────────────────────

@dataclass
class NewsItem:
    """新闻条目。"""
    title: str = ""
    summary: str = ""           # 摘要
    source: str = ""            # 来源
    publish_time: str = ""      # 发布时间


class NewsProvider(ABC):
    @abstractmethod
    async def headlines(self, topic: str = "", limit: int = 5,
                        meta: dict | None = None) -> list[NewsItem]:
        """获取新闻头条/摘要。topic 可为空（综合热点）。"""
        ...


# ── 股票 Provider ──────────────────────────────────────────────────

@dataclass
class Quote:
    """股票/指数行情。"""
    name: str = ""              # 名称
    symbol: str = ""            # 代码
    price: str = ""             # 当前价
    change: str = ""            # 涨跌额
    change_pct: str = ""        # 涨跌幅 %
    market_time: str = ""       # 行情时间


@dataclass
class StockCandle:
    """一根日 K 线。数值保留字符串，避免服务端破坏厂商精度。"""
    date: str = ""
    open: str = ""
    high: str = ""
    low: str = ""
    close: str = ""
    volume: str = ""


# ── 赛事 Provider ──────────────────────────────────────────────────

@dataclass
class SportsFixture:
    """单场赛事。结构化真实数据，不经 LLM，杜绝比分/对阵编造。"""
    league: str = ""            # 赛事名（如 FIFA 世界杯）
    league_id: int = 0          # api-football league id（用于按日期查后客户端精确过滤）
    round: str = ""             # 轮次（如 小组赛-第2轮）
    home: str = ""              # 主队
    away: str = ""              # 客队
    home_logo: str = ""         # 主队队徽 URL（可空）
    away_logo: str = ""         # 客队队徽 URL（可空）
    home_goals: str = ""        # 主队进球（未开赛为空）
    away_goals: str = ""        # 客队进球
    status: str = ""            # 归一化状态：finished/live/scheduled/other
    status_text: str = ""       # 状态中文（已结束/进行中/未开赛/推迟…）
    elapsed: str = ""           # 进行中的比赛分钟数（可空）
    kickoff: str = ""           # 开赛时间 ISO（带 timezone）


class SportsProvider(ABC):
    @abstractmethod
    async def fixtures(self, date: str = "", league: int = 0, season: int = 0,
                       live: bool = False, timezone: str = "Asia/Shanghai",
                       meta: dict | None = None) -> list[SportsFixture]:
        """查询赛事。date=YYYY-MM-DD（空=不限）；league/season 过滤；live=仅进行中。"""
        ...


class StockProvider(ABC):
    @abstractmethod
    async def quote(self, symbol: str,
                    meta: dict | None = None) -> Quote:
        """查询股票/指数行情。symbol 可以是代码或名称。"""
        ...

    @abstractmethod
    async def history(self, symbol: str, limit: int = 20,
                      meta: dict | None = None) -> list[StockCandle]:
        """查询近期日线，按日期从旧到新排列。"""
        ...

    @abstractmethod
    async def index(self, name: str = "上证",
                    meta: dict | None = None) -> Quote:
        """查询大盘指数行情。"""
        ...
