import os
import pytest
from unittest.mock import patch


def test_default_settings():
    from src.config import Settings

    s = Settings()
    assert s.app_name == "Keeper System"
    assert s.log_level == "INFO"


def test_keepa_key_from_env():
    with patch.dict(os.environ, {"KEEPA_API_KEY": "mykey123"}):
        from src.config import Settings

        s = Settings()
        assert s.keepa_api_key == "mykey123"


def test_database_url_override():
    with patch.dict(os.environ, {"DATABASE_URL": "sqlite+aiosqlite:///:memory:"}):
        from src.config import Settings

        s = Settings()
        assert "sqlite" in s.database_url


def test_optional_fields_none():
    from src.config import Settings

    s = Settings()
    assert s.smtp_host is None or isinstance(s.smtp_host, str)
    assert s.telegram_bot_token is None or isinstance(s.telegram_bot_token, str)


def test_settings_cached():
    from src.config import get_settings

    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
