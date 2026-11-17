"""``launch_account_web(start_url=...)``: the window's first navigation, nothing else changes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from core.web_login import browser, spambot_dialog_url
from core.web_login.browser import launch_account_web
from tests.core.web_login_helpers import (
    FINGERPRINT,
    PAGE,
    RecordingSession,
    attached,
    wire_launch,
)

if TYPE_CHECKING:
    from pathlib import Path


async def _launch(
    monkeypatch: pytest.MonkeyPatch, profile: Path, **options: Any
) -> RecordingSession:
    session = RecordingSession(events=[attached(PAGE, "page")])
    wire_launch(monkeypatch, {}, session)
    window = await launch_account_web(
        41000, profile_dir=profile, fingerprint=FINGERPRINT, capture_tokens=True, **options
    )
    await window.driver.aclose()
    return session


@pytest.mark.asyncio
async def test_without_a_start_url_the_window_opens_on_webk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session = await _launch(monkeypatch, tmp_path / "a")

    assert session.commands[-1] == ("Page.navigate", {"url": browser._WEBK_URL}, PAGE)


@pytest.mark.asyncio
async def test_a_start_url_replaces_only_the_address_of_the_first_navigation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plain = await _launch(monkeypatch, tmp_path / "plain")
    url = spambot_dialog_url("hash")

    pointed = await _launch(monkeypatch, tmp_path / "pointed", start_url=url)

    assert pointed.commands[-1] == ("Page.navigate", {"url": url}, PAGE)
    assert pointed.commands[:-1] == plain.commands[:-1]  # identity, scripts, hooks: unchanged
    # Still dressed BEFORE it navigates, as ever.
    assert pointed.methods.index("Page.addScriptToEvaluateOnNewDocument") < pointed.methods.index(
        "Page.navigate"
    )
