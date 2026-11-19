from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import jwt

from assistant.store import atomic_write_json, ensure_private_dir

AUTH_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
REQUIRED_SCOPE = "chatgpt.tokens.use.direct"


class CodexAuthError(RuntimeError):
    pass


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _http_json(url: str, *, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read(2_000_000)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(100_000).decode("utf-8", "replace"))
            error = payload.get("error", "") if isinstance(payload, dict) else ""
            code = error.get("code", "") if isinstance(error, dict) else error
            if not isinstance(code, str):
                code = ""
        except Exception:
            code = ""
        if code in ("invalid_grant", "invalid_token", "access_denied"):
            # Only expose the OAuth error code; never print the response body,
            # which could contain sensitive values.
            raise CodexAuthError("ChatGPT authorization failed ({0}). Start a fresh sign-in with --connect-codex.".format(code)) from exc
        raise CodexAuthError("ChatGPT sign-in was rejected. Check authorization and try again.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CodexAuthError("ChatGPT sign-in request failed. Check the network and try again.") from exc
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CodexAuthError("ChatGPT sign-in returned an invalid response.") from exc


def _validate_id_token(token: str, client_id: str, nonce: str) -> Dict[str, Any]:
    discovery = _http_json("https://auth.openai.com/.well-known/openid-configuration")
    jwks_uri = discovery.get("jwks_uri")
    if not jwks_uri:
        raise CodexAuthError("ChatGPT sign-in metadata is incomplete.")
    try:
        key = jwt.PyJWKClient(jwks_uri).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256", "ES256"], audience=client_id, issuer=ISSUER, options={"require": ["exp", "iss", "aud", "nonce", "sub"]})
    except Exception as exc:
        raise CodexAuthError("ChatGPT identity token could not be validated.") from exc
    if claims.get("nonce") != nonce:
        raise CodexAuthError("ChatGPT sign-in nonce validation failed.")
    return claims


def _credential_record(tokens: Dict[str, Any], client_id: str, host_id: str, nonce: str) -> Dict[str, Any]:
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    id_token = tokens.get("id_token")
    scope_value = tokens.get("scope", "")
    scopes = sorted(set(scope_value.split()))
    if not all(isinstance(v, str) and v for v in (access, refresh, id_token, client_id)):
        raise CodexAuthError("ChatGPT did not return all required credentials.")
    if REQUIRED_SCOPE not in scopes:
        raise CodexAuthError("ChatGPT plan access was not granted. Reconnect and approve plan usage.")
    claims = _validate_id_token(id_token, client_id, nonce)
    return {
        "client_id": client_id,
        "ext_agent_host_id": host_id,
        "subject": claims["sub"],
        "email": claims.get("email"),
        "issuer": ISSUER,
        "id_token": id_token,
        "access_token": access,
        "refresh_token": refresh,
        "scopes": scopes,
        "expires_at": int(time.time()) + int(tokens.get("expires_in", 3600)),
        "token_type": tokens.get("token_type", "Bearer"),
    }


def load_credentials(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CodexAuthError("Codex subscription credentials are missing or invalid. Run python bot.py --connect-codex.") from exc
    if not isinstance(value, dict) or not value.get("access_token") or not value.get("refresh_token"):
        raise CodexAuthError("Codex subscription credentials are incomplete. Reconnect with --connect-codex.")
    if REQUIRED_SCOPE not in value.get("scopes", []):
        raise CodexAuthError("Codex credentials do not include ChatGPT plan access. Reconnect with --connect-codex.")
    return value


def save_credentials(path: Path, credentials: Dict[str, Any]) -> None:
    atomic_write_json(Path(path), credentials)


def _refresh_sync(path: Path, credentials: Dict[str, Any]) -> Dict[str, Any]:
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": credentials["client_id"],
        "refresh_token": credentials["refresh_token"],
        "resource": RESOURCE,
    }).encode("utf-8")
    tokens = _http_json(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    updated = dict(credentials)
    updated.update({
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token", credentials["refresh_token"]),
        "id_token": tokens.get("id_token", credentials.get("id_token")),
        "expires_at": int(time.time()) + int(tokens.get("expires_in", 3600)),
        "scopes": sorted(set(tokens.get("scope", " ".join(credentials.get("scopes", []))).split())),
        "token_type": tokens.get("token_type", "Bearer"),
    })
    if not updated.get("access_token") or REQUIRED_SCOPE not in updated["scopes"]:
        raise CodexAuthError("ChatGPT plan token refresh did not preserve the required permission.")
    save_credentials(path, updated)
    return updated


class CredentialManager:
    def __init__(self, path: Path, *, refresh_skew_seconds: int = 90) -> None:
        self.path = Path(path)
        self.refresh_skew_seconds = refresh_skew_seconds
        self._lock = asyncio.Lock()

    async def access_token(self, *, force_refresh: bool = False) -> str:
        async with self._lock:
            credentials = load_credentials(self.path)
            if force_refresh or int(credentials.get("expires_at", 0)) <= int(time.time()) + self.refresh_skew_seconds:
                credentials = await asyncio.to_thread(_refresh_sync, self.path, credentials)
            return str(credentials["access_token"])

    def metadata(self) -> Dict[str, Any]:
        credentials = load_credentials(self.path)
        return {"auth": "ChatGPT plan OAuth", "subject": credentials.get("subject"), "expires_at": credentials.get("expires_at"), "scopes": credentials.get("scopes", [])}


def connect_chatgpt(path: Path, *, app_name: str = "Telegram Personal Assistant") -> Dict[str, Any]:
    destination = Path(path)
    ensure_private_dir(destination.parent)
    host_id_path = destination.parent / "codex_host_id"
    if host_id_path.exists():
        host_id = host_id_path.read_text(encoding="utf-8").strip()
    else:
        host_id = "urn:uuid:{}".format(uuid.uuid4())
        fd = os.open(str(host_id_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(host_id)

    # Dynamic registration returns an issued client ID. Keep it even if the
    # first code exchange expires, so retries use the registered client.
    client_id_path = destination.parent / "codex_client_id"
    registered_client_id = None
    if client_id_path.exists():
        registered_client_id = client_id_path.read_text(encoding="utf-8").strip() or None

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    redirect_uri = "http://127.0.0.1:{}/auth/callback".format(port)
    callback: Dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/auth/callback":
                self.send_error(404)
                return
            callback.update({key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()})
            body = b"ChatGPT sign-in received. You can close this browser tab and return to the terminal."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    server.timeout = 300
    worker = threading.Thread(target=server.handle_request, daemon=True)
    worker.start()
    params = {
        "client_id": registered_client_id or "dynamic_agent_client",
        "ext_agent_host_id": host_id, "response_type": "code", "redirect_uri": redirect_uri,
        "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
        "resource": RESOURCE, "state": state, "nonce": nonce,
        "code_challenge_method": "S256", "code_challenge": challenge,
    }
    if not registered_client_id:
        params["agent_name_hint"] = app_name
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    print("A browser will open for ChatGPT subscription authorization.")
    if not webbrowser.open(url):
        print("Open this authorization page in your browser:")
        print(url)
    worker.join(timeout=300)
    server.server_close()
    if worker.is_alive() or not callback:
        raise CodexAuthError("ChatGPT sign-in timed out.")
    if callback.get("state") != state:
        raise CodexAuthError("ChatGPT sign-in state validation failed.")
    if callback.get("error"):
        raise CodexAuthError("ChatGPT sign-in was not approved: {}.".format(callback["error"]))
    code = callback.get("code")
    client_id = callback.get("client_id")
    if registered_client_id:
        if client_id and client_id != registered_client_id:
            raise CodexAuthError("ChatGPT returned a different registered account. Start a fresh sign-in.")
        client_id = registered_client_id
    if not code or not client_id:
        raise CodexAuthError("ChatGPT sign-in callback did not include the required values.")
    if client_id != "dynamic_agent_client":
        fd = os.open(str(client_id_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(client_id)
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code", "client_id": client_id, "code": code,
        "code_verifier": verifier, "redirect_uri": redirect_uri, "resource": RESOURCE,
    }).encode("utf-8")
    tokens = _http_json(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    credentials = _credential_record(tokens, client_id, host_id, nonce)
    save_credentials(destination, credentials)
    print("ChatGPT plan access was saved securely in {}.".format(destination))
    return credentials
