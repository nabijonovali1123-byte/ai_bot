from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from assistant.codex_auth import CredentialManager

API_BASE = "https://api.openai.com/v1"


class CodexError(RuntimeError):
    pass


class CodexAuthError(CodexError):
    pass


class CodexTimeout(CodexError):
    pass


class CodexLimitError(CodexError):
    def __init__(self, detail: str, resets_at: Optional[float] = None) -> None:
        self.detail = detail[:400]
        self.resets_at = resets_at
        super().__init__("ChatGPT plan usage limit reached" + (" (resets {})".format(datetime.fromtimestamp(resets_at, timezone.utc).isoformat()) if resets_at else ""))


def _limit_error(status: int, message: str, headers: Optional[Dict[str, str]] = None) -> Optional[CodexLimitError]:
    if status != 429 and not re.search(r"usage limit|rate.?limit|quota|subscription_sharing_usage_limit", message, re.I):
        return None
    resets_at: Optional[float] = None
    for key in ("x-ratelimit-reset", "ratelimit-reset"):
        raw = (headers or {}).get(key)
        if raw:
            try:
                value = float(raw)
                resets_at = value if value > time.time() else time.time() + value
                break
            except ValueError:
                pass
    return CodexLimitError(message, resets_at)


def _response_error(status: int, body: str, headers: Optional[Dict[str, str]] = None) -> CodexError:
    try:
        payload = json.loads(body)
        error = payload.get("error", {})
        message = str(error.get("message") or error.get("code") or body)
        code = str(error.get("code") or "")
    except (ValueError, AttributeError):
        message, code = body, ""
    limited = _limit_error(status, message + " " + code, headers)
    if limited:
        return limited
    if status == 401 or status == 403:
        return CodexAuthError("ChatGPT plan authorization failed. Reconnect with --connect-codex.")
    return CodexError("OpenAI Responses request failed (HTTP {}). {}".format(status, message[:400]))


class CodexClient:
    def __init__(self, credentials_path: Path, *, model: Optional[str] = None, timeout: int = 120,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.credentials = CredentialManager(Path(credentials_path))
        self.model = model
        self.timeout = timeout
        self._model_cache: List[str] = []
        self.transport = transport

    async def list_models(self) -> List[Dict[str, str]]:
        token = await self.credentials.access_token()
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.get(API_BASE + "/models", headers={"Authorization": "Bearer " + token})
            if response.status_code >= 400:
                raise _response_error(response.status_code, response.text, dict(response.headers))
            payload = response.json()
        models = payload.get("models", [])
        result = [{"slug": item.get("slug"), "display_name": item.get("display_name", item.get("slug"))}
                  for item in models if item.get("visibility") == "list" and item.get("slug")]
        if not result:
            raise CodexError("No ChatGPT plan models are available for this account.")
        self._model_cache = [item["slug"] for item in result]
        return result

    async def _selected_model(self) -> str:
        if self.model:
            return self.model
        if not self._model_cache:
            await self.list_models()
        return self._model_cache[0]

    async def generate(self, *, instructions: str, prompt: str) -> str:
        model = await self._selected_model()
        request_data = {
            "model": model,
            "instructions": instructions,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            "store": False,
            "stream": True,
        }
        refreshed = False
        while True:
            token = await self.credentials.access_token(force_refresh=refreshed)
            try:
                async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                    async with client.stream("POST", API_BASE + "/responses", headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                    }, json=request_data) as response:
                        if response.status_code in (401, 403) and not refreshed:
                            refreshed = True
                            continue
                        if response.status_code >= 400:
                            body = (await response.aread()).decode("utf-8", "replace")
                            raise _response_error(response.status_code, body, dict(response.headers))
                        chunks: List[str] = []
                        completed = False
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                continue
                            try:
                                event = json.loads(data)
                            except ValueError:
                                continue
                            event_type = event.get("type")
                            if event_type == "response.output_text.delta":
                                chunks.append(str(event.get("delta", "")))
                            elif event_type == "response.failed":
                                error = event.get("response", {}).get("error", {})
                                code = str(error.get("code", ""))
                                message = str(error.get("message", code or "Response failed"))
                                limited = _limit_error(429 if "limit" in code else 0, message + " " + code)
                                if limited:
                                    raise limited
                                if code in ("invalid_api_key", "authentication_error", "invalid_token"):
                                    raise CodexAuthError("ChatGPT plan authorization failed. Reconnect with --connect-codex.")
                                raise CodexError("OpenAI Responses generation failed. {}".format(message[:400]))
                            elif event_type == "response.completed":
                                completed = True
                            elif event_type in ("response.incomplete", "error"):
                                message = str(event.get("message") or event.get("error", {}).get("message") or "Response was incomplete")
                                limited = _limit_error(0, message)
                                if limited:
                                    raise limited
                                raise CodexError("OpenAI Responses stream was incomplete. {}".format(message[:300]))
                        if not completed:
                            raise CodexError("OpenAI Responses stream ended before response.completed.")
                        result = "".join(chunks).strip()
                        if not result:
                            raise CodexError("OpenAI Responses returned an empty reply.")
                        return result
            except httpx.TimeoutException as exc:
                raise CodexTimeout("ChatGPT plan response timed out.") from exc
            except httpx.HTTPError as exc:
                raise CodexError("Could not reach the OpenAI Responses service.") from exc

    async def inspect(self) -> Dict[str, Any]:
        metadata = self.credentials.metadata()
        models = await self.list_models()
        return {"auth": metadata["auth"], "subject": metadata["subject"], "expires_at": metadata["expires_at"],
                "scopes": metadata["scopes"], "models": models}
