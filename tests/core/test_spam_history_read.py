"""``core.telegram_client.read_spambot_history``: read the old dialog, send nothing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.telegram_client import read_spambot_history
from core.telegram_client._pool import TelegramClientPoolError


class _FakeClient:
    """Records every call so a test can prove nothing was sent and nothing marked read."""

    def __init__(
        self, messages: list[object] | None = None, *, fail: Exception | None = None
    ) -> None:
        self._messages = messages or []
        self._fail = fail
        self.calls: list[str] = []

    async def get_input_entity(self, username: str) -> str:
        self.calls.append(f"resolve:{username}")
        return username

    async def get_messages(self, peer: object, limit: int) -> list[object]:
        self.calls.append(f"get_messages:{peer}:{limit}")
        if self._fail is not None:
            raise self._fail
        return self._messages

    async def send_message(self, *_args: object, **_kwargs: object) -> None:
        self.calls.append("send_message")

    async def send_read_acknowledge(self, *_args: object, **_kwargs: object) -> None:
        self.calls.append("send_read_acknowledge")

    async def get_me(self) -> object:
        self.calls.append("get_me")
        return SimpleNamespace(restricted=False)


def _patch(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    async def _get_client(_account_id: str, _device: str | None = None) -> object:
        return client

    monkeypatch.setattr("core.telegram_client._spam.get_client", _get_client)


def _message(text: str | None, *, out: bool = False) -> object:
    return SimpleNamespace(raw_text=text, out=out)


@pytest.mark.asyncio
async def test_it_returns_the_bots_messages_and_drops_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        [_message("Good news"), _message("/start", out=True), _message(""), _message(None)]
    )
    _patch(monkeypatch, client)
    history = await read_spambot_history("acc-1", 20)
    assert (history.account_id, history.texts, history.error) == ("acc-1", ["Good news"], None)


@pytest.mark.asyncio
async def test_it_only_reads_never_sends_or_marks_read(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient([_message("x")])
    _patch(monkeypatch, client)
    await read_spambot_history("acc-1", 7)
    assert client.calls == ["resolve:SpamBot", "get_messages:SpamBot:7"]


@pytest.mark.asyncio
async def test_an_empty_dialog_is_an_empty_list_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _FakeClient([]))
    history = await read_spambot_history("acc-1", 20)
    assert (history.texts, history.error) == ([], None)


@pytest.mark.asyncio
async def test_a_failure_is_a_class_name_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(fail=RuntimeError("secret proxy text")))
    history = await read_spambot_history("acc-1", 20)
    assert (history.texts, history.error) == ([], "RuntimeError")
    assert "secret" not in repr(history)


@pytest.mark.asyncio
async def test_a_pool_refusal_is_a_class_name_too(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _refuse(_account_id: str, _device: str | None = None) -> object:
        account, cause = "acc-1", OSError("down")
        raise TelegramClientPoolError(account, cause)

    monkeypatch.setattr("core.telegram_client._spam.get_client", _refuse)
    history = await read_spambot_history("acc-1", 20)
    assert history.error == "TelegramClientPoolError"
