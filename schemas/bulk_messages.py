"""Contracts for an operator-started bulk Telegram message run."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from schemas.accounts import _ACCOUNT_ID_PATTERN

_MAX_SENDS = 500
_MAX_RECIPIENT_LENGTH = 200


class BulkMessageRequest(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=50)
    recipients: list[str] = Field(min_length=1, max_length=50)
    text: str = Field(min_length=1, max_length=4096)
    min_delay_seconds: float = Field(default=0, ge=0, le=300)
    max_delay_seconds: float = Field(default=0, ge=0, le=300)

    @model_validator(mode="after")
    def validate_batch(self) -> BulkMessageRequest:
        if self.max_delay_seconds < self.min_delay_seconds:
            msg = "max_delay_seconds must be at least min_delay_seconds"
            raise ValueError(msg)
        if len(self.account_ids) * len(self.recipients) > _MAX_SENDS:
            msg = "bulk message batch exceeds 500 sends"
            raise ValueError(msg)
        if len(self.account_ids) != len(set(self.account_ids)):
            msg = "duplicate account_ids"
            raise ValueError(msg)
        if any(
            not re.fullmatch(_ACCOUNT_ID_PATTERN, account_id) for account_id in self.account_ids
        ):
            msg = "invalid account_id"
            raise ValueError(msg)
        if any(
            not recipient.strip() or len(recipient) > _MAX_RECIPIENT_LENGTH
            for recipient in self.recipients
        ):
            msg = "invalid recipient"
            raise ValueError(msg)
        if not self.text.strip():
            msg = "message text is empty"
            raise ValueError(msg)
        return self


class BulkMessageOutcome(BaseModel):
    account_id: str
    recipient: str
    status: Literal["ok", "failed", "skipped", "unconfirmed"]
    error_code: str | None = None
    retry_after_seconds: int | None = None


class BulkMessageJob(BaseModel):
    job_id: str
    status: Literal["running", "completed", "cancelled"]
    total: int
    completed: int
    results: list[BulkMessageOutcome]


class BulkMessageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


class BulkMessageGenerated(BaseModel):
    text: str
    provider: Literal["deepseek", "gemini"]
