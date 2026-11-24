"""Adversarial probe: an undecryptable newest copy must not surface as a 500."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from core import session_vault as vault
from core.config import settings
from schemas.accounts import AccountCreate
from services.accounts import add_account
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_restore_with_a_rotated_vault_key_is_a_4xx(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_account(AccountCreate(account_id="a1", session_name="a1"))
    monkeypatch.setattr(settings.session_vault, "key", vault.new_vault_key())
    session = settings.telegram.session_dir / "a1.session"
    session.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(session)) as db, db:
        db.execute("create table sessions (server_address text, auth_key blob)")
        db.execute("insert into sessions values ('dc', ?)", (b"k",))
    vault.snapshot_session(session)
    session.unlink()
    monkeypatch.setattr(settings.session_vault, "key", vault.new_vault_key())
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/a1/session/restore")
    assert resp.status_code < 500
