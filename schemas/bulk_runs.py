"""Stored state of a persistent bulk-message run (rows of ``bulk_run*`` tables)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

RunStatus = Literal["running", "completed", "cancelled"]
# "manual": started from the send window; "lead_queue": the lead queue's own run.
RunKind = Literal["manual", "lead_queue"]
ItemStatus = Literal["pending", "sending", "ok", "failed", "skipped", "unconfirmed"]
# Why a running run is not sending right now.
WaitReason = Literal["schedule", "hours", "daily_cap", "pause", "series_break"]
TimeBasis = Literal["client", "operator"]


class BulkRunRecord(BaseModel):
    id: str
    owner_user_id: str
    status: RunStatus
    request_json: str
    cancel_requested: bool = False
    next_send_at: str | None = None
    wait_reason: WaitReason | None = None
    created_at: str
    finished_at: str | None = None
    kind: RunKind = "manual"


class BulkRunItem(BaseModel):
    id: int | None = None
    run_id: str
    ordinal: int
    account_id: str
    recipient: str
    peer: str
    tz_name: str
    time_basis: TimeBasis
    status: ItemStatus = "pending"
    error_code: str | None = None
    retry_after_seconds: int | None = None
    handed_over: bool = False
    finished_seq: int | None = None
    finished_at: str | None = None


class BulkRunAccount(BaseModel):
    run_id: str
    account_id: str
    stop_code: str | None = None
    retry_after_seconds: int | None = None
    ready_at: str | None = None
    series_count: int = 0
