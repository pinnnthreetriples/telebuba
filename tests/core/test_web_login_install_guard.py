"""A retried dressing step must not install a page script twice.

``Page.addScriptToEvaluateOnNewDocument`` can be APPLIED by the browser and acknowledged too
late for the dressing deadline; the retry then registers the same source again. Every
document-start script therefore guards itself in the page (measured on V8 here), and the
fingerprint's sub-commands are retried one by one rather than replayed as a block.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.web_login import _targets
from core.web_login._passkey import PASSKEY_HOOK
from core.web_login._scripts import QR_CAPTURE_HOOK, once
from core.web_login.fingerprint import _page_init_script, fingerprint_for
from tests.core.test_web_login_targets import _driver, _Session, attached
from tests.core.web_login_js_probe import requires_node, run_probe

_SCRIPT = "Page.addScriptToEvaluateOnNewDocument"


class _LateAckSession(_Session):
    """Applies the first ``_SCRIPT`` command, then never answers it (a late ack)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._late = True

    async def send_command(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, object]:
        if method == _SCRIPT and self._late:
            self._late = False
            self.commands.append((method, params or {}, session_id))
            return await super().send_command("Never.answers", params, session_id=session_id)
        return await super().send_command(method, params, session_id=session_id)


@pytest.mark.asyncio
async def test_a_retry_resumes_after_the_acknowledged_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_targets, "_DRESS_TIMEOUT", 0.05)
    session = _LateAckSession(
        events=[attached("P1", "page")], hang_methods=frozenset({"Never.answers"})
    )

    await _driver(session, page_scripts=("window.__x=1;",)).first_page_session()

    # The late-acked command is the only one sent twice; nothing before it is replayed.
    assert len(session.sent("Emulation.setUserAgentOverride")) == 1
    assert len(session.sent("Emulation.setTimezoneOverride")) == 1
    assert len(session.sent("Emulation.setLocaleOverride")) == 1
    assert len(session.sent("Page.enable")) == 1
    assert len(session.sent(_SCRIPT)) == 3  # page init (twice: late ack), then the hook


def test_every_injected_document_script_is_guarded() -> None:
    page = _page_init_script(fingerprint_for("acct-1", "DE"))
    for source, key in (
        (page, "__tbFpInstalled"),
        (QR_CAPTURE_HOOK, "__tbCapInstalled"),
        (PASSKEY_HOOK, "__tbPkInstalled"),
    ):
        assert f"if (globalThis.{key}) return;" in source


@requires_node
def test_a_second_capture_hook_install_is_a_no_op(tmp_path: Any) -> None:
    probes = {
        "restored": "(window.__capOff(), EventTarget.prototype.addEventListener === __orig)",
    }
    source = "globalThis.__orig = EventTarget.prototype.addEventListener;\n" + (
        QR_CAPTURE_HOOK + QR_CAPTURE_HOOK
    )

    measured = run_probe(tmp_path, "hook", source, probes)

    assert measured["restored"] is True


@requires_node
def test_a_second_passkey_hook_install_does_not_throw(tmp_path: Any) -> None:
    prelude = (
        "globalThis.self = globalThis;\n"
        "class CredentialsContainer { get() { return 1; } }\n"
        "globalThis.CredentialsContainer = CredentialsContainer;\n"
    )
    probes = {"queue": "typeof window.__tbPk.take", "once": "typeof globalThis.__tbPkInstalled"}

    measured = run_probe(tmp_path, "hook", prelude + PASSKEY_HOOK + PASSKEY_HOOK, probes)

    assert measured == {"queue": "function", "once": "boolean"}


def test_once_runs_the_body_a_single_time() -> None:
    guarded = once("__k", "body();")

    assert guarded.count("body();") == 1
    assert "if (globalThis.__k) return;" in guarded
