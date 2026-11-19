from __future__ import annotations

import asyncio
import os
import time

import pytest

from assistant import codex_auth
from assistant.codex_auth import CodexAuthError, CredentialManager, _credential_record, load_credentials, save_credentials


def test_credentials_are_private_and_scope_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_auth, "_validate_id_token", lambda token, client, nonce: {"sub": "user-1", "email": "a@example.test"})
    tokens = {"access_token": "access-secret", "refresh_token": "refresh-secret", "id_token": "id-secret",
              "scope": "openid profile offline_access resource.invoke chatgpt.tokens.use.direct", "expires_in": 3600}
    record = _credential_record(tokens, "oaiapp_123", "urn:uuid:test", "nonce")
    path = tmp_path / "credentials.json"
    save_credentials(path, record)
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_credentials(path)["subject"] == "user-1"
    with pytest.raises(CodexAuthError):
        _credential_record(dict(tokens, scope="openid profile"), "oaiapp_123", "host", "nonce")


def test_expired_access_token_refreshes_and_persists(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    save_credentials(path, {"access_token": "old", "refresh_token": "refresh", "client_id": "oaiapp_x",
                            "expires_at": int(time.time()) - 5,
                            "scopes": ["chatgpt.tokens.use.direct"]})
    def fake_refresh(file_path, credentials):
        updated = dict(credentials, access_token="new", refresh_token="rotated", expires_at=int(time.time()) + 3600)
        save_credentials(file_path, updated)
        return updated
    monkeypatch.setattr(codex_auth, "_refresh_sync", fake_refresh)
    token = asyncio.run(CredentialManager(path).access_token())
    assert token == "new"
    assert load_credentials(path)["refresh_token"] == "rotated"
