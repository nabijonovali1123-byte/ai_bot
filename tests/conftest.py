from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from assistant.config import Settings


class FakeGenerator:
    def __init__(self, replies=None):
        self.replies = list(replies or ["Hello!"])
        self.calls = []

    async def generate(self, *, instructions, prompt):
        self.calls.append({"instructions": instructions, "prompt": prompt})
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@pytest.fixture
def settings(tmp_path):
    return Settings(bot_token="12345:fake", owner_id=100, data_dir=tmp_path / "data",
                    profile_path=tmp_path / "profile.md")


@pytest.fixture
def message_factory():
    def make(text="Hi", *, sender=200, chat=300, message_id=1, connection_id="conn", is_bot=False,
             business_bot=None, media=None):
        from_user = SimpleNamespace(id=sender, is_bot=is_bot, full_name="Owner" if sender == 100 else "Friend")
        values = {"text": text, "caption": None, "voice": None, "photo": None, "sticker": None,
                  "video": None, "audio": None, "document": None, "animation": None,
                  "contact": None, "location": None, "venue": None, "video_note": None}
        if media:
            values[media] = SimpleNamespace(emoji="🙂") if media == "sticker" else object()
        return SimpleNamespace(business_connection_id=connection_id, chat=SimpleNamespace(id=chat, full_name="Friend"),
                               message_id=message_id, from_user=from_user, sender_business_bot=business_bot, **values)
    return make


@pytest.fixture
def business_connection():
    return SimpleNamespace(id="conn", user=SimpleNamespace(id=100), is_enabled=True,
                           rights=SimpleNamespace(can_reply=True), can_reply=True)
