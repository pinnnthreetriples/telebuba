"""Local fixtures for neurocomment service tests."""

from __future__ import annotations

from tests.services.neurocomment.challenge_support import isolate_challenge
from tests.services.neurocomment.engine_support import isolate_engine
from tests.services.neurocomment.onboarding_support import isolate_onboarding
from tests.services.neurocomment.runtime_support import isolate_runtime

__all__ = (
    "isolate_challenge",
    "isolate_engine",
    "isolate_onboarding",
    "isolate_runtime",
)
