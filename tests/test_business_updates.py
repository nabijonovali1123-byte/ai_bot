from __future__ import annotations

from types import SimpleNamespace

import pytest

from assistant.config import Settings
from assistant.service import Assistant, Outcome, can_reply, describe
from assistant.codex_client import CodexLimitError
from tests.conftest import FakeGenerator


class FakeBot:
    def __init__(self, connection):
        self.connection = connection
        self.lookups = []
        self.sent = []

    async def get_business_connection(self, connection_id):
        self.lookups.append(connection_id)
        return self.connection

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return True


@pytest.mark.asyncio
async def test_preview_does_not_send_and_duplicate_is_ignored(settings, business_connection, message_factory):
    previews = []
    generator = FakeGenerator(["Assalomu alaykum"])
    assistant = Assistant(settings, generator, preview_sink=previews.append)
    assistant.on_business_connection(business_connection)
    bot = FakeBot(business_connection)
    message = message_factory("Salom", message_id=5)
    assert await assistant.on_business_message(bot, message) == Outcome.PREVIEWED
    assert await assistant.on_business_message(bot, message) == Outcome.DUPLICATE
    assert bot.sent == []
    assert bot.lookups == []
    assert len(previews) == 1
    assert (settings.data_dir / "previews.jsonl").stat().st_mode & 0o777 == 0o600
    assert generator.calls and "Salom" in generator.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_owner_message_starts_takeover(settings, business_connection, message_factory):
    assistant = Assistant(settings, FakeGenerator())
    assistant.on_business_connection(business_connection)
    bot = FakeBot(business_connection)
    outcome = await assistant.on_business_message(bot, message_factory("Men yozdim", sender=100))
    assert outcome == Outcome.OWNER_MESSAGE
    assert len(assistant.history.get("conn", 300)) == 1
    assert not bot.sent


@pytest.mark.asyncio
async def test_auto_send_rechecks_connection_and_chunks_reply(tmp_path, business_connection, message_factory):
    settings = Settings(owner_id=100, auto_send=True, data_dir=tmp_path / "data", profile_path=tmp_path / "profile.md")
    connection = business_connection
    assistant = Assistant(settings, FakeGenerator(["x" * 8100]))
    assistant.on_business_connection(connection)
    bot = FakeBot(connection)
    outcome = await assistant.on_business_message(bot, message_factory("Hello", message_id=12))
    assert outcome == Outcome.SENT
    assert bot.lookups == ["conn"]
    assert [len(item[1]) for item in bot.sent] == [4000, 4000, 100]
    assert all(item[2]["business_connection_id"] == "conn" for item in bot.sent)


@pytest.mark.asyncio
async def test_permission_removed_prevents_auto_send(tmp_path, business_connection, message_factory):
    settings = Settings(owner_id=100, auto_send=True, data_dir=tmp_path / "data", profile_path=tmp_path / "profile.md")
    assistant = Assistant(settings, FakeGenerator(["reply"]))
    assistant.on_business_connection(business_connection)
    revoked = SimpleNamespace(id="conn", user=SimpleNamespace(id=100), is_enabled=True,
                              rights=SimpleNamespace(can_reply=False), can_reply=False)
    bot = FakeBot(revoked)
    assert await assistant.on_business_message(bot, message_factory("Hi", message_id=13)) == Outcome.PERMISSION_DENIED
    assert not bot.sent


def test_describe_and_reply_compatibility():
    assert describe(SimpleNamespace(text=None, caption=None, voice=object(), video_note=None, photo=None,
                                    video=None, audio=None, document=None, sticker=None, animation=None,
                                    contact=None, location=None, venue=None)) == "[voice message]"
    assert can_reply(SimpleNamespace(rights=SimpleNamespace(can_reply=True), can_reply=False)) is True


def test_pause_state_survives_restart(tmp_path):
    settings = Settings(owner_id=100, data_dir=tmp_path / "data", profile_path=tmp_path / "profile.md")
    first = Assistant(settings, FakeGenerator())
    first.pause()
    second = Assistant(settings, FakeGenerator())
    assert second.state.value["paused"] is True


@pytest.mark.asyncio
async def test_unknown_connection_is_looked_up_then_cached(settings, business_connection, message_factory):
    assistant = Assistant(settings, FakeGenerator(["one", "two"]))
    bot = FakeBot(business_connection)
    assert await assistant.on_business_message(bot, message_factory("Hi", message_id=31)) == Outcome.PREVIEWED
    assert await assistant.on_business_message(bot, message_factory("Hello", message_id=32)) == Outcome.PREVIEWED
    assert bot.lookups == ["conn"]


@pytest.mark.asyncio
async def test_foreign_and_disabled_connections_do_not_generate(settings, message_factory):
    foreign = SimpleNamespace(id="conn", user=SimpleNamespace(id=999), is_enabled=True,
                              rights=SimpleNamespace(can_reply=True), can_reply=True)
    generator = FakeGenerator()
    assistant = Assistant(settings, generator)
    assistant.on_business_connection(foreign)
    assert await assistant.on_business_message(FakeBot(foreign), message_factory("Hi", message_id=41)) == Outcome.FOREIGN_CONNECTION
    disabled = SimpleNamespace(id="conn", user=SimpleNamespace(id=100), is_enabled=False,
                               rights=SimpleNamespace(can_reply=True), can_reply=True)
    assistant2 = Assistant(settings, generator)
    assistant2.on_business_connection(disabled)
    assert await assistant2.on_business_message(FakeBot(disabled), message_factory("Hi", message_id=42)) == Outcome.CONNECTION_DISABLED
    assert generator.calls == []


@pytest.mark.asyncio
async def test_bot_sender_and_skip_are_ignored(settings, business_connection, message_factory):
    generator = FakeGenerator(["[SKIP]"])
    assistant = Assistant(settings, generator)
    assistant.on_business_connection(business_connection)
    bot = FakeBot(business_connection)
    assert await assistant.on_business_message(bot, message_factory("bot", message_id=51, is_bot=True)) == Outcome.IGNORED_BOT
    assert await assistant.on_business_message(bot, message_factory("ok", message_id=52)) == Outcome.SKIPPED
    assert bot.sent == []


@pytest.mark.asyncio
async def test_pause_blocks_generation_and_resume_clears_state(settings, business_connection, message_factory):
    generator = FakeGenerator()
    assistant = Assistant(settings, generator)
    assistant.on_business_connection(business_connection)
    assistant.pause()
    assert await assistant.on_business_message(FakeBot(business_connection), message_factory("Hi", message_id=61)) == Outcome.PAUSED
    assert generator.calls == []
    assistant.resume()
    assert assistant.state.value["paused"] is False


@pytest.mark.asyncio
async def test_usage_limit_persists_cooldown(settings, business_connection, message_factory):
    generator = FakeGenerator([CodexLimitError("usage limit")])
    assistant = Assistant(settings, generator)
    assistant.on_business_connection(business_connection)
    outcome = await assistant.on_business_message(FakeBot(business_connection), message_factory("Hi", message_id=71))
    assert outcome == Outcome.LIMITED
    assert assistant.state.value["limit_until"] > assistant.clock()
    restored = Assistant(settings, FakeGenerator())
    assert restored.state.value["limit_until"] == assistant.state.value["limit_until"]


def test_history_is_separated_by_connection_and_chat(settings):
    from assistant.store import HistoryStore
    history = HistoryStore(settings.data_dir / "separate-history.json", 30)
    history.add("c1", 1, role="other", name="A", text="only one", lang="en")
    assert len(history.get("c1", 1)) == 1
    assert history.get("c2", 1) == []
    assert history.get("c1", 2) == []
