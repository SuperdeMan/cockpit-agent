"""信息类 Provider 工厂。按环境变量选择 real/mock；构造失败回退 mock（不阻断 PoC）。"""
import logging
import os

from .base import WeatherProvider
from .mock import MockWeatherProvider

logger = logging.getLogger("agent.info.providers")


def _load_qweather_private_key():
    """和风 JWT 私钥原料。返回 str/bytes 交由 QWeatherJWT 健壮解析（PEM / 裸 base64 / 种子均可）。

    优先 QWEATHER_PRIVATE_KEY（直接粘贴）；否则 QWEATHER_PRIVATE_KEY_PATH——是真实文件就读文件，
    不是文件则容错当作"直接贴进来的私钥内容"（兼容误填到 PATH 字段的情况）。
    """
    inline = os.getenv("QWEATHER_PRIVATE_KEY")
    if inline:
        return inline
    path = os.getenv("QWEATHER_PRIVATE_KEY_PATH")
    if path:
        path = path.strip()
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        logger.warning("QWEATHER_PRIVATE_KEY_PATH 不是文件，按内联私钥内容处理")
        return path  # 容错：值即私钥内容
    return None


def build_weather_provider() -> WeatherProvider:
    vendor = os.getenv("WEATHER_VENDOR", "mock")
    if vendor == "qweather":
        host = os.getenv("QWEATHER_HOST", "devapi.qweather.com")
        project_id = os.getenv("QWEATHER_PROJECT_ID")
        key_id = os.getenv("QWEATHER_KEY_ID")
        private_key = _load_qweather_private_key()
        try:
            if project_id and key_id and private_key:  # JWT（和风新版，优先）
                from .qweather import QWeatherProvider, QWeatherJWT
                return QWeatherProvider(
                    jwt_auth=QWeatherJWT(project_id, key_id, private_key), host=host)
            if os.getenv("QWEATHER_KEY"):               # API Key（旧版）
                from .qweather import QWeatherProvider
                return QWeatherProvider(api_key=os.getenv("QWEATHER_KEY"), host=host)
        except Exception as e:  # 构造失败（缺包/密钥格式错）不阻断，回退 mock
            logger.warning("QWeatherProvider init failed, falling back to mock: %s", e)
    return MockWeatherProvider()
