from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agents.info.src.providers import build_weather_provider
from agents.info.src.providers.qweather import QWeatherProvider


def test_qweather_credentials_take_precedence_over_implicit_mock_mode(monkeypatch):
    key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv("WEATHER_VENDOR", "mock")
    monkeypatch.setenv("QWEATHER_PROJECT_ID", "project-id")
    monkeypatch.setenv("QWEATHER_KEY_ID", "credential-id")
    monkeypatch.setenv("QWEATHER_PRIVATE_KEY", key)

    assert isinstance(build_weather_provider(), QWeatherProvider)
