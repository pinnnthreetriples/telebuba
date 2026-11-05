"""Credential redaction across Sentry, the console sink and the value registry."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from core import logging as logging_module
from core.config import settings
from core.logging import reset_logging_for_tests, setup_logging

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_URL = "socks5://leakuser:LEAKPASS123@10.9.8.7:1080"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()
    logging_module._SECRETS.clear()


def test_sentry_init_registers_redacting_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.logging, "sentry_dsn", "https://fake@sentry.example/0")
    reset_logging_for_tests()
    with patch("core.logging.sentry_sdk.init") as mock_init:
        setup_logging()
    kwargs = mock_init.call_args.kwargs
    event = {
        "message": f"boom {_URL}",
        "logentry": {"message": "x %s", "formatted": f"x {_URL}", "params": [_URL]},
        "exception": {"values": [{"type": "OSError", "value": f"proxy {_URL} refused"}]},
        "breadcrumbs": {"values": [{"message": f"connect {_URL}", "data": {"u": _URL}}]},
    }
    dumped = json.dumps(kwargs["before_send"](event, {}))
    assert "LEAKPASS123" not in dumped
    assert "leakuser" not in dumped
    assert "refused" in dumped
    crumb = kwargs["before_breadcrumb"]({"message": f"via {_URL}"}, {})
    assert "LEAKPASS123" not in crumb["message"]


def test_console_sink_redacts_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        msg = f"proxy {_URL} refused"
        raise OSError(msg)  # noqa: TRY301
    except OSError:
        logging.getLogger("core.x").exception("connect failed")
    err = capsys.readouterr().err
    assert "LEAKPASS123" not in err
    assert "connect failed" in err


def test_register_log_secrets_ignores_short_values_and_replaces_on_rotation() -> None:
    logging_module.register_log_secrets("acc", "short", "longuser1", "oldpass123")
    assert logging_module.redact_secrets("short longuser1 oldpass123") == "short *** ***"
    logging_module.register_log_secrets("acc", "newuser12", "newpass123")
    assert logging_module.redact_secrets("oldpass123 newpass123") == "oldpass123 ***"
    for i in range(logging_module._MAX_SECRET_OWNERS + 20):
        logging_module.register_log_secrets(f"o{i}", f"secret-{i:04d}")
    assert len(logging_module._SECRETS) == logging_module._MAX_SECRET_OWNERS


def test_redact_secrets_is_linear_on_colon_heavy_lines() -> None:
    line = ("a:" * 50_000) + "b"
    started = time.perf_counter()
    assert logging_module.redact_secrets(line) == line
    assert time.perf_counter() - started < 1.0
    assert logging_module.redact_secrets("socks5://us%40er:p%3Aw%2Fd@h:1") == "socks5://***@h:1"
