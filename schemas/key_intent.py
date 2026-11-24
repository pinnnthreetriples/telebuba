"""Contracts for the key-intent journal: a flow that makes a NEW Telegram auth key.

A re-key, a second device or a sign-in with the new API writes an intent BEFORE the key
exists and closes it once everything the key needs is recorded. An intent still open after a
crash tells the repair what was in flight. Only hashes, a one-way mark of a key and the
device fingerprint are kept, never a key or a credential. No behaviour, no I/O.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

KeyIntentKind = Literal["rekey", "second_device", "sign_in"]
KeyIntentStage = Literal["started", "signed_in", "swapped"]
KeyIntentOutcome = Literal["done", "undone", "recovered", "incomplete", "failed"]


class KeyIntent(BaseModel):
    """One journal row."""

    id: int
    account_id: str = Field(min_length=1)
    kind: KeyIntentKind
    device_type: str
    # The file stem the flow writes (a scratch name, or the second device's name).
    session_name: str
    stage: KeyIntentStage = "started"
    # The API the new key is made with; ``None``: the default one.
    api_id: int | None = None
    new_hash: str | None = None
    old_hash: str | None = None
    # sha256 hex of the key the account's own file held when a re-key began: one-way.
    old_key_mark: str | None = None
    # The second device's ``DeviceFingerprint`` as JSON (no secrets in it).
    device_json: str | None = None
    started_at: str
    closed_at: str | None = None
    outcome: KeyIntentOutcome | None = None
