from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

from aiogram.exceptions import TelegramAPIError
from aiogram.types import BusinessConnection, Message

from assistant.codex_client import CodexClient, CodexError, CodexLimitError
from assistant.config import Settings
from assistant.language import detect_language
from assistant.prompts import SKIP_TOKEN, build_system_prompt, build_user_prompt
from assistant.store import HistoryStore, RuntimeState, SeenMessages, ensure_private_dir

logger = logging.getLogger(__name__)


class Outcome(str, Enum):
    PREVIEWED = "previewed"
    SENT = "sent"
    DUPLICATE = "duplicate"
    IGNORED_BOT = "ignored_bot"
    NO_CONNECTION = "no_connection"
    FOREIGN_CONNECTION = "foreign_connection"
    CONNECTION_DISABLED = "connection_disabled"
    UNSUPPORTED = "unsupported"
    OWNER_MESSAGE = "owner_message"
    OWNER_ACTIVE = "owner_active"
    PAUSED = "paused"
    LIMITED = "limited"
    SKIPPED = "skipped"
    PERMISSION_DENIED = "permission_denied"
    ERROR = "error"


@dataclass
class Preview:
    connection_id: str
    chat_id: int
    chat_name: str
    language: Optional[str]
    incoming: str
    reply: str
    note: str = ""


def print_preview(preview: Preview) -> None:
    logger.info("PREVIEW, not sent | chat=%s | language=%s | incoming=%s | reply=%s | note=%s",
                preview.chat_name, preview.language, preview.incoming, preview.reply, preview.note)


def describe(message: Message) -> Optional[str]:
    if getattr(message, "text", None):
        return message.text
    if getattr(message, "caption", None):
        caption = message.caption
    else:
        caption = ""
    media = (
        ("voice", "voice message"), ("video_note", "video message"), ("photo", "photo"),
        ("video", "video"), ("audio", "audio"), ("document", "file"),
        ("sticker", "sticker"), ("animation", "animation"), ("contact", "contact"),
        ("location", "location"), ("venue", "place"),
    )
    for attr, label in media:
        value = getattr(message, attr, None)
        if value:
            if attr == "sticker":
                label += " " + (getattr(value, "emoji", None) or "")
            return "[{}]{}".format(label, " " + caption if caption else "")
    return None


def can_reply(connection: BusinessConnection) -> bool:
    rights = getattr(connection, "rights", None)
    if rights is not None and getattr(rights, "can_reply", None) is not None:
        return bool(rights.can_reply)
    return bool(getattr(connection, "can_reply", False))


def read_profile(path: Path) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()


class Assistant:
    def __init__(self, settings: Settings, generator: Any, *, clock: Callable[[], float] = time.time,
                 preview_sink: Callable[[Preview], None] = print_preview) -> None:
        self.settings = settings
        self.generator = generator
        self.clock = clock
        ensure_private_dir(settings.data_dir)
        self.history = HistoryStore(settings.data_dir / "history.json", settings.history_limit)
        self.seen = SeenMessages(settings.data_dir / "seen.json")
        self.state = RuntimeState(settings.data_dir / "state.json")
        self.connections: Dict[str, BusinessConnection] = {}
        self.outcomes: Deque[Outcome] = deque(maxlen=100)
        self.takeovers: Dict[str, float] = {}
        self.locks: Dict[str, asyncio.Lock] = {}
        self.preview_sink = preview_sink
        self._last_error_notice = 0.0
        self._last_limit_notice = 0.0

    def _record(self, outcome: Outcome) -> Outcome:
        self.outcomes.append(outcome)
        return outcome

    def _chat_key(self, connection_id: str, chat_id: int) -> str:
        return "{}:{}".format(connection_id, chat_id)

    def _preview(self, preview: Preview) -> None:
        self.preview_sink(preview)
        path = self.settings.data_dir / "previews.jsonl"
        ensure_private_dir(path.parent)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(preview.__dict__, ensure_ascii=False) + "\n")

    def on_business_connection(self, connection: BusinessConnection) -> None:
        self.connections[connection.id] = connection
        owner = getattr(getattr(connection, "user", None), "id", None)
        if owner != self.settings.owner_id:
            logger.warning("Ignoring foreign business connection %s", connection.id)
        else:
            logger.info("Business connection %s: user=%s enabled=%s can_reply=%s",
                        connection.id, owner, connection.is_enabled, can_reply(connection))

    async def _connection(self, bot: Any, connection_id: str) -> Optional[BusinessConnection]:
        connection = self.connections.get(connection_id)
        if connection is not None:
            return connection
        try:
            connection = await bot.get_business_connection(connection_id)
        except TelegramAPIError:
            logger.exception("Could not load business connection")
            return None
        self.connections[connection_id] = connection
        return connection

    def _blocked(self, key: str) -> Optional[Outcome]:
        state = self.state.value
        now = self.clock()
        if state["paused"]:
            return Outcome.PAUSED
        if state["limit_until"] > now:
            return Outcome.LIMITED
        if self.takeovers.get(key, 0) > now:
            return Outcome.OWNER_ACTIVE
        return None

    async def _generate(self, connection_id: str, message: Message, incoming: str, language: Optional[str]) -> str:
        history = self.history.get(connection_id, message.chat.id)
        profile = read_profile(self.settings.profile_path)
        from datetime import datetime
        system = build_system_prompt(profile, datetime.now().astimezone())
        prompt = build_user_prompt(history, language)
        return await self.generator.generate(instructions=system, prompt=prompt)

    async def on_business_message(self, bot: Any, message: Message) -> Outcome:
        connection_id = getattr(message, "business_connection_id", None)
        if not connection_id:
            return self._record(Outcome.NO_CONNECTION)
        key = self._chat_key(connection_id, message.chat.id)
        duplicate_key = "{}:{}".format(key, message.message_id)
        if not self.seen.first_time(duplicate_key):
            return self._record(Outcome.DUPLICATE)
        if getattr(message, "sender_business_bot", None) is not None or (message.from_user and message.from_user.is_bot):
            return self._record(Outcome.IGNORED_BOT)
        connection = await self._connection(bot, connection_id)
        if connection is None:
            return self._record(Outcome.NO_CONNECTION)
        if getattr(connection.user, "id", None) != self.settings.owner_id:
            return self._record(Outcome.FOREIGN_CONNECTION)
        if not connection.is_enabled:
            return self._record(Outcome.CONNECTION_DISABLED)
        incoming = describe(message)
        if incoming is None:
            return self._record(Outcome.UNSUPPORTED)
        sender_id = getattr(message.from_user, "id", None)
        sender_name = getattr(message.from_user, "full_name", "Unknown") if message.from_user else "Unknown"
        if sender_id == self.settings.owner_id:
            self.history.add(connection_id, message.chat.id, role="owner", name=sender_name, text=incoming, lang=detect_language(incoming), ts=self.clock())
            if self.settings.owner_takeover_minutes:
                self.takeovers[key] = self.clock() + self.settings.owner_takeover_minutes * 60
            return self._record(Outcome.OWNER_MESSAGE)
        language = detect_language(incoming) or self.history.last_language(connection_id, message.chat.id)
        self.history.add(connection_id, message.chat.id, role="other", name=sender_name, text=incoming, lang=language, ts=self.clock())
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            blocked = self._blocked(key)
            if blocked:
                return self._record(blocked)
            try:
                reply = (await self._generate(connection_id, message, incoming, language)).strip()
            except CodexLimitError as exc:
                until = exc.resets_at if exc.resets_at and exc.resets_at > self.clock() else self.clock() + self.settings.limit_cooldown_minutes * 60
                self.state.update(limit_until=until)
                logger.warning("ChatGPT plan usage limit reached; paused until %s", until)
                if self.settings.auto_send and self.clock() - self._last_limit_notice >= 600:
                    self._last_limit_notice = self.clock()
                    try:
                        await bot.send_message(self.settings.owner_id, "ChatGPT plan usage limit reached. Reply generation is paused until the limit resets.")
                    except TelegramAPIError:
                        logger.exception("Could not notify the owner about the plan limit")
                return self._record(Outcome.LIMITED)
            except CodexError:
                logger.exception("Reply generation failed")
                if self.settings.auto_send and self.clock() - self._last_error_notice >= 600:
                    self._last_error_notice = self.clock()
                    try:
                        await bot.send_message(self.settings.owner_id, "Assistant reply generation failed. Check the service logs.")
                    except TelegramAPIError:
                        logger.exception("Could not notify the owner")
                return self._record(Outcome.ERROR)
            if not reply or reply == SKIP_TOKEN:
                return self._record(Outcome.SKIPPED)
            preview = Preview(connection_id, message.chat.id, getattr(message.chat, "full_name", None) or str(message.chat.id), language, incoming, reply)
            if not self.settings.auto_send:
                self._preview(preview)
                return self._record(Outcome.PREVIEWED)
            blocked = self._blocked(key)
            if blocked:
                preview.note = "Owner activity began while the reply was being generated."
                self._preview(preview)
                return self._record(blocked)
            try:
                fresh = await bot.get_business_connection(connection_id)
            except TelegramAPIError:
                preview.note = "Could not recheck the Telegram Business connection."
                self._preview(preview)
                return self._record(Outcome.PERMISSION_DENIED)
            if getattr(fresh.user, "id", None) != self.settings.owner_id or not fresh.is_enabled or not can_reply(fresh):
                preview.note = "Telegram Business connection is disabled or cannot reply."
                self._preview(preview)
                return self._record(Outcome.PERMISSION_DENIED)
            try:
                for start in range(0, len(reply), 4000):
                    await bot.send_message(message.chat.id, reply[start:start + 4000], business_connection_id=connection_id)
            except TelegramAPIError:
                logger.exception("Could not send the generated reply")
                return self._record(Outcome.ERROR)
            self.history.add(connection_id, message.chat.id, role="owner", name="Owner", text=reply, lang=language, ts=self.clock())
            return self._record(Outcome.SENT)

    def pause(self) -> None:
        self.state.update(paused=True)

    def resume(self) -> None:
        self.state.clear()
        self.takeovers.clear()

    def status_text(self) -> str:
        mode = "AUTO-SEND" if self.settings.auto_send else "PREVIEW: replies are only shown in the logs"
        state = "paused" if self.state.value["paused"] else "running"
        limit = self.state.value["limit_until"]
        limit_text = "none" if limit <= self.clock() else time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(limit))
        return "Mode: {}\nState: {}\nUsage limit until: {}\nChats in history: {}".format(mode, state, limit_text, self.history.chat_count())

    def start_text(self) -> str:
        mode = "avtomatik yuborish" if self.settings.auto_send else "PREVIEW (javoblar faqat logda ko‘rinadi)"
        state = "pauzada" if self.state.value["paused"] else "ishlayapti"
        takeover = self.settings.owner_takeover_minutes
        return ("Salom! Men Telegram Business uchun shaxsiy yordamchi botman.\n"
                "Holat: {}; rejim: {}.\n"
                "Ulash: Telegram → Settings → Telegram Business → Chatbots → bot username → chatlarni tanlash → javob berish ruxsati (Telegram Premium kerak).\n"
                "Buyruqlar: /pause, /resume, /status.\n"
                "Siz chatga yozganingizda bot {} daqiqa jim turadi. Pul, qarz, uchrashuv yoki va’dalarni tasdiqlamayman. Shaxsiy ma’lumotlarni profilga kiritmang. profile.md faylini to‘ldiring. Preview’dan mamnun bo‘lgachgina AUTO_SEND=true qiling.").format(state, mode, takeover)


def make_codex_client(settings: Settings) -> CodexClient:
    return CodexClient(settings.data_dir / "codex_credentials.json", model=settings.codex_model, timeout=settings.timeout_seconds)


def build_assistant(settings: Settings, **kwargs: Any) -> Assistant:
    return Assistant(settings, make_codex_client(settings), **kwargs)
