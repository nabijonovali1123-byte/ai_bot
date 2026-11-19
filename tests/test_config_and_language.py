from __future__ import annotations

from assistant.config import ConfigError, load_settings, settings_from_process_env
from assistant.language import detect_language


def test_defaults_and_dotenv_source_are_isolated(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=123:abc\nOWNER_ID=42\nAUTO_SEND=false\n", encoding="utf-8")
    monkeypatch.setenv("AUTO_SEND", "true")
    settings = load_settings(env_file)
    assert settings.auto_send is False
    assert settings.owner_id == 42
    assert "123:abc" not in repr(settings)


def test_invalid_token_is_not_echoed(tmp_path):
    path = tmp_path / ".env"
    path.write_text("TELEGRAM_BOT_TOKEN=secret-value\nOWNER_ID=42\n", encoding="utf-8")
    try:
        load_settings(path)
    except ConfigError as exc:
        assert "secret-value" not in str(exc)
    else:
        raise AssertionError("Expected configuration error")


def test_process_environment_settings_are_removed(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("OWNER_ID", "100")
    monkeypatch.setenv("CODEX_CREDENTIALS_JSON", "secret-json")
    monkeypatch.setenv("KEEP_ME", "yes")
    settings = settings_from_process_env()
    assert settings.from_environment
    assert "TELEGRAM_BOT_TOKEN" not in __import__("os").environ
    assert "CODEX_CREDENTIALS_JSON" not in __import__("os").environ
    assert __import__("os").environ["KEEP_ME"] == "yes"


def test_language_detection():
    assert detect_language("Salom, qalesan?") == "uz"
    assert detect_language("Bugun bo'shmisan?") == "uz"
    assert detect_language("Салом, яхшимисиз?") == "uz"
    assert detect_language("Привет, как дела?") == "ru"
    assert detect_language("Hi, are you free tonight?") == "en"
    assert detect_language("It's my dog's ball") == "en"
    assert detect_language("👍") is None
