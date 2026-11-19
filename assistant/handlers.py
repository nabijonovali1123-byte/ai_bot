from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import BusinessConnection, Message

from assistant.service import Assistant

ALLOWED_UPDATES = ["message", "business_connection", "business_message"]


def build_router(assistant: Assistant) -> Router:
    router = Router(name="personal-assistant")

    @router.business_connection()
    async def business_connection_handler(connection: BusinessConnection) -> None:
        assistant.on_business_connection(connection)

    @router.business_message()
    async def business_message_handler(message: Message, bot: Bot) -> None:
        await assistant.on_business_message(bot, message)

    @router.message(Command("start"))
    async def start_handler(message: Message) -> None:
        if message.business_connection_id is not None:
            return
        sender_id = message.from_user.id if message.from_user else None
        if sender_id == assistant.settings.owner_id:
            await message.answer(assistant.start_text())
        else:
            await message.answer("Bu shaxsiy yordamchi bot. Bu yerda suhbatlashib bo‘lmaydi; egasiga bevosita yozing.")

    @router.message(Command("pause"))
    async def pause_handler(message: Message) -> None:
        if message.business_connection_id is not None:
            return
        if message.from_user and message.from_user.id == assistant.settings.owner_id:
            assistant.pause()
            await message.answer("Assistant paused.")

    @router.message(Command("resume"))
    async def resume_handler(message: Message) -> None:
        if message.business_connection_id is not None:
            return
        if message.from_user and message.from_user.id == assistant.settings.owner_id:
            assistant.resume()
            await message.answer("Assistant resumed.")

    @router.message(Command("status"))
    async def status_handler(message: Message) -> None:
        if message.business_connection_id is not None:
            return
        if message.from_user and message.from_user.id == assistant.settings.owner_id:
            await message.answer(assistant.status_text())

    return router
