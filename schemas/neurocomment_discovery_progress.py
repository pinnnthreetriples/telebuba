"""The live progress model for a discovery stage.

Split from ``neurocomment_discovery`` (file-size cap). ``schemas/`` may not import
``core`` (enforced by ``tests/test_architecture.py``): the
mutable side (``WorkTracker``) that fills these in lives in
``services.neurocomment._discovery_state`` instead.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# One stream's lifecycle: an idle account never picked yet; waiting = picked and pacing
# before its next read; reading = the Telegram call is in flight; done = the stream ran
# out of work with nothing wrong; capped/flooded/cooling/dead/offline = why it stopped
# early. offline = the client pool could not connect the account at all.
DiscoveryStreamState = Literal[
    "idle",
    "waiting",
    "reading",
    "done",
    "capped",
    "flooded",
    "cooling",
    "dead",
    "offline",
]


class DiscoveryStream(BaseModel):
    """One account's stream, as the progress bar shows it."""

    account_id: str
    name: str
    premium: bool | None = None
    state: DiscoveryStreamState = "idle"
    reads: int = Field(default=0, ge=0)
    # Last failure on this stream, locale-neutral (``FloodWait(120s)``, …). ``None``
    # while the stream is healthy.
    error: str | None = None


class DiscoveryWork(BaseModel):
    """Progress of the stage that is running (or just ran)."""

    stage: Literal["searching", "qualifying"]
    done: int = Field(default=0, ge=0)
    # done + in-flight + queued + not-yet-enqueued (e.g. the recommendation wave held
    # back until the sweep seeds it): a ceiling that can shrink as holds resolve.
    planned: int = Field(default=0, ge=0)
    eta_seconds: int | None = None
    started_at: str
    streams: list[DiscoveryStream] = Field(default_factory=list)
