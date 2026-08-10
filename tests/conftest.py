"""Fixtures every test gets, whatever the operator's ``.env`` happens to hold.

The root ``conftest.py`` stays free of pytest imports (deptry flags a dev dependency
reaching production code), so suite-wide fixtures live here instead — this file
covers everything under ``tests/`` and pytest applies it before any subpackage
conftest.
"""

from __future__ import annotations

import pytest

from core.config import settings


@pytest.fixture(autouse=True)
def _no_ambient_deepseek_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test as a deployment that has not configured DeepSeek.

    That key is the whole switch deciding who generates text, and it is read from the
    operator's ``.env`` — which made the suite depend on ambient config. With a key
    present, every test that stubs only ``_seams.generate_text`` routed to the
    UNSTUBBED ``generate_text_deepseek`` and issued live HTTPS calls to
    api.deepseek.com: 25 of them in one run, each waiting out a 30s timeout. CI has no
    key, so CI stayed green while local runs crawled and reached the network. The
    divergence is the defect here, not the slowness.

    Blanked centrally rather than patched into each stub helper, so no later test can
    reopen the hole by stubbing one provider and forgetting the other. A test that
    means to exercise DeepSeek sets the key itself and stubs both
    (``tests/services/neurocomment/test_llm_routing.py``).
    """
    monkeypatch.setattr(settings.deepseek, "api_key", "")
