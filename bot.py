from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramUnauthorizedError

from assistant.codex_auth import CodexAuthError, connect_chatgpt
from assistant.codex_client import CodexClient, CodexError
from assistant.config import ConfigError, SETTING_KEYS, Settings, env_file_warning, load_settings, settings_from_process_env
from assistant.handlers import ALLOWED_UPDATES, build_router
from assistant.language import detect_language
from assistant.prompts import build_system_prompt, build_user_prompt
from assistant.service import build_assistant, read_profile
from assistant.store import atomic_write_json, ensure_private_dir

logger = logging.getLogger("telegram_assistant")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram Business personal assistant")
    parser.add_argument("--settings-from-env", action="store_true", help="Read Railway environment settings")
    parser.add_argument("--check-config", action="store_true", help="Validate settings without showing secrets")
    parser.add_argument("--check-codex", action="store_true", help="Check ChatGPT plan OAuth and model access")
    parser.add_argument("--connect-codex", action="store_true", help="Authorize ChatGPT plan use in a browser")
    parser.add_argument("--try", dest="try_text", help="Generate a one-off reply")
    return parser.parse_args()


def _bootstrap_files(settings: Settings) -> None:
    ensure_private_dir(settings.data_dir)
    credentials_path = settings.data_dir / "codex_credentials.json"
    if not credentials_path.exists() and settings.codex_credentials_json:
        try:
            credentials = json.loads(settings.codex_credentials_json)
        except ValueError as exc:
            raise ConfigError("CODEX_CREDENTIALS_JSON is not valid JSON in the environment variables.") from exc
        if not isinstance(credentials, dict) or not credentials.get("access_token") or not credentials.get("refresh_token"):
            raise ConfigError("CODEX_CREDENTIALS_JSON is incomplete in the environment variables.")
        atomic_write_json(credentials_path, credentials)
        logger.info("Imported ChatGPT plan credentials to the persistent data volume.")
    if settings.profile_content and not settings.profile_path.exists():
        settings.profile_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(settings.profile_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(settings.profile_content)
        logger.info("Imported profile content to the persistent data volume.")


def _local_settings(require_telegram: bool = True) -> Settings:
    settings = load_settings(Path(".env"), require_telegram=require_telegram)
    warning = env_file_warning(Path(".env"))
    if warning:
        logger.warning(warning)
    return settings


async def _check_codex(settings: Settings) -> int:
    _bootstrap_files(settings)
    client = CodexClient(settings.data_dir / "codex_credentials.json", model=settings.codex_model, timeout=settings.timeout_seconds)
    try:
        result = await client.inspect()
    except (CodexError, CodexAuthError) as exc:
        print(json.dumps({"auth": "ChatGPT plan OAuth", "ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "auth": result["auth"], "scopes": result["scopes"], "model_count": len(result["models"]),
                      "models": result["models"]}, ensure_ascii=False))
    return 0


async def _try_reply(settings: Settings, text: str) -> int:
    _bootstrap_files(settings)
    client = CodexClient(settings.data_dir / "codex_credentials.json", model=settings.codex_model, timeout=settings.timeout_seconds)
    language = detect_language(text)
    profile = read_profile(settings.profile_path)
    from datetime import datetime
    instructions = build_system_prompt(profile, datetime.now().astimezone())
    prompt = build_user_prompt([{"role": "other", "name": "Other", "text": text}], language)
    try:
        reply = await client.generate(instructions=instructions, prompt=prompt)
    except CodexError as exc:
        print("Codex subscription request failed: {}".format(exc))
        return 1
    print(json.dumps({"language": language, "incoming": text, "reply": reply}, ensure_ascii=False, indent=2))
    return 0


async def _run_bot(settings: Settings) -> int:
    _bootstrap_files(settings)
    assistant = build_assistant(settings)
    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(assistant))
    try:
        me = await bot.get_me()
        logger.info("Bot @%s is running", me.username)
        logger.info("Mode: %s", "AUTO-SEND" if settings.auto_send else "PREVIEW")
        logger.info("Codex auth: %s", settings.codex_auth_mode)
        logger.info("ChatGPT subscription endpoint: %s", "https://api.openai.com/v1/responses")
        if assistant.state.value["paused"]:
            logger.warning("Assistant starts paused.")
        if not read_profile(settings.profile_path):
            logger.warning("profile.md is empty or missing.")
        await dispatcher.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    except TelegramUnauthorizedError:
        logger.error("Telegram rejected the bot token. Check TELEGRAM_BOT_TOKEN in your local settings.")
        return 2
    finally:
        await bot.session.close()
    return 0


async def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.connect_codex:
        try:
            connect_chatgpt(Path("data/codex_credentials.json"))
        except CodexAuthError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    try:
        if args.settings_from_env:
            settings = settings_from_process_env(require_telegram=not (args.check_config or args.check_codex or args.try_text is not None))
        else:
            settings = _local_settings(require_telegram=not (args.check_config or args.check_codex or args.try_text is not None))
        if args.check_config:
            _bootstrap_files(settings)
            print(json.dumps({"ok": True, "mode": "AUTO-SEND" if settings.auto_send else "PREVIEW",
                              "auth": settings.codex_auth_mode, "credential_file_exists": (settings.data_dir / "codex_credentials.json").exists()}, ensure_ascii=False))
            return 0
        if args.check_codex:
            return await _check_codex(settings)
        if args.try_text is not None:
            return await _try_reply(settings, args.try_text)
        return await _run_bot(settings)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
