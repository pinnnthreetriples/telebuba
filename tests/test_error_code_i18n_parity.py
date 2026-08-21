"""Drift guard: every action-failure code the API can return must be translatable.

The API is locale-neutral: a refused action surfaces a stable snake_case code and
the SPA owns the wording. When a code has no entry, the operator is shown the raw
identifier — ``story_video_ffmpeg_missing`` instead of "ffmpeg is missing on the
server". That is not hypothetical: four ``StoryVideoErrorCode`` members and the
whole non-``flood_wait`` rate-limit family shipped untranslated and were only found
by reading the tables by hand.

The codes come from enumerable sources — a ``Literal`` in the gateway, the
``ActionStatus`` union, and the gateway's Telethon-error → code maps — so unlike
the free-form ``log_event`` codes guarded by ``test_logevent_i18n_parity``, they can
be checked exhaustively.

The error maps were added to the enumeration after the fact: while only
``StoryVideoErrorCode | ActionStatus`` were checked, an entire refusal family could
be (and was) mapped to codes with no copy in either locale, and nothing failed.

Resolution mirrors the SPA: the global mutation toast
(``frontend/src/shared/lib/query-client.ts``) is the surface every failed mutation
reaches, and it tries ``shell.code.*`` → ``accounts.profile.code.*`` →
``accounts.channel.code.*`` → ``accounts.addStory.code.*``. A code present in any
of the four is therefore visible to the operator; a code in none of them is not.
Which namespace a code lives in is a UI decision this test deliberately does not
police.

``shell.code`` sits at the TOP level of the locale file, not under ``accounts``,
so it is read separately rather than added to ``_CODE_NAMESPACES`` — that tuple
also feeds the log-ladder check below, whose keys really are ``accounts.*``. It
had been outside this guard entirely, which meant the domain refusal codes that
live there (``schemas.neuroshilling.NeuroshillingRefusalCode``) were checked by
nothing at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from core.telegram_client._channels import _TELETHON_ERROR_CODES
from core.telegram_client._media import _MEDIA_ERROR_CODES, MusicSaveErrorCode
from core.telegram_client._profile import _DEAD_SESSION_ERROR_CODES, _PROFILE_ERROR_CODES
from core.telegram_client._twofa import _TWOFA_ERROR_CODES
from core.telegram_client._video import StoryVideoErrorCode
from schemas.neurocomment import NeurocommentRefusalCode
from schemas.neuroshilling import NeuroshillingRefusalCode
from schemas.telegram_actions import ActionStatus
from schemas.twofa import TwoFactorRefusalCode
from schemas.warming import WarmingRefusalCode

_ROOT = Path(__file__).resolve().parents[1]
# Statuses that are not failures, so they never reach a code table.
_NON_FAILURE_STATUSES = frozenset({"ok", "already_participant"})
# The ``accounts.*`` namespaces the mutation toast walks, in order.
_CODE_NAMESPACES = ("profile", "channel", "addStory")


def _i18n(locale: str) -> dict:
    path = _ROOT / "frontend" / "src" / "shared" / "i18n" / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _translatable_codes(locale: str) -> set[str]:
    table = _i18n(locale)
    accounts = table["accounts"]
    shell = set(table["shell"]["code"])
    return shell | {code for namespace in _CODE_NAMESPACES for code in accounts[namespace]["code"]}


def _mapped_codes() -> set[str]:
    """Every code the gateway's Telethon-error → code ladders can produce."""
    return {
        code
        for family in (
            _PROFILE_ERROR_CODES,
            _TELETHON_ERROR_CODES,
            _MEDIA_ERROR_CODES,
            _DEAD_SESSION_ERROR_CODES,
            _TWOFA_ERROR_CODES,
        )
        for _error_cls, code in family
    }


def _expected_codes() -> set[str]:
    # ``MusicSaveErrorCode`` covers the codes ``_media`` raises by hand rather than
    # through a ladder. The ladders were the only media source enumerated, so a
    # hand-raised code could ship untranslated — and one did: the add path's
    # refusal was reusing the remove path's ``profile_music_stale_reference``,
    # which was translated, so nothing here noticed the wrong copy either.
    #
    # ``NeuroshillingRefusalCode`` is the first refusal vocabulary that is neither a
    # gateway ladder nor an ``ActionStatus``: the domain raises it straight out of
    # policy and the route puts it in the envelope's ``message``. Without it here,
    # nothing in the suite could see a refusal code shipping untranslated.
    #
    # ``WarmingRefusalCode`` is the same shape one layer over: warming's start refusals
    # (``services.warming._exclusion``) reach the operator as the 409's ``detail``. That
    # family was in no source here at all, so all three of its codes — including the
    # neuroshilling exclusion added on top of them — were translated by luck, not proof.
    #
    # ``TwoFactorRefusalCode`` is the 2FA domain's whole vocabulary, and it exists
    # because most of its codes are raised BY HAND rather than through a ladder —
    # ``twofa_not_changed``, ``twofa_password_not_set``, ``twofa_removal_unconfirmed``
    # in the gateway, ``twofa_password_not_stored`` and ``twofa_rollback_failed`` in
    # ``services/``, and two more in the extracted SRP sibling. Enumerating only
    # ``_TWOFA_ERROR_CODES`` left every one of them checked by nothing.
    #
    # ``NeurocommentRefusalCode`` is the same family for the listener's own start,
    # which answers the 409 ``detail`` with a code the way the two above do. It exists
    # so a refusal added there is enumerable here instead of reaching the operator the
    # way that route's warming refusal still does, as an English sentence.
    return (
        set(get_args(StoryVideoErrorCode))
        | set(get_args(MusicSaveErrorCode))
        | set(get_args(NeurocommentRefusalCode))
        | set(get_args(NeuroshillingRefusalCode))
        | set(get_args(WarmingRefusalCode))
        | set(get_args(TwoFactorRefusalCode))
        | (set(get_args(ActionStatus)) - _NON_FAILURE_STATUSES)
        | _mapped_codes()
    )


def test_every_action_failure_code_has_ru_and_en_copy() -> None:
    expected = _expected_codes()
    assert expected, "code enumeration broke — the Literals moved or were renamed"
    for locale in ("ru", "en"):
        missing = sorted(expected - _translatable_codes(locale))
        assert not missing, f"{locale}.json has no copy for: {missing}"


def test_the_log_reason_ladder_walks_every_code_namespace() -> None:
    """The failure LOG must reach the same namespaces the toast does.

    ``core.telegram_client._action_results._generic_error`` writes a gateway stable
    code into the failure row's ``extra.error_type``, so every namespace above is
    log-visible too — and ``frontend/src/shared/lib/log/eventReason.ts`` walks its
    own ladder to translate it. That ladder shipped without ``addStory``: ten
    labels, ``story_image_invalid`` among them, were translated for the toast and
    rendered raw in the log, and the test above passed the whole time because it
    only asks whether copy EXISTS.

    A text scan is the honest limit here. It proves the three namespace strings are
    present in the module; it cannot prove they are in the ladder array rather than
    a comment, that i18next is handed them in that order, or that the lookup
    succeeds. ``eventReason.test.ts`` resolves real codes through the real i18n
    instance and is what proves those. This guards the one failure mode a
    TypeScript-side test cannot: a namespace added to ``_CODE_NAMESPACES`` here and
    silently never wired into the SPA.
    """
    source = (_ROOT / "frontend" / "src" / "shared" / "lib" / "log" / "eventReason.ts").read_text(
        encoding="utf-8",
    )
    missing = [ns for ns in _CODE_NAMESPACES if f"accounts.{ns}.code" not in source]
    assert not missing, f"eventReason.ts never looks up: {missing}"


def test_the_rate_limit_family_that_carries_seconds_interpolates_them() -> None:
    """``{{s}}`` is fed from ``retry_after_seconds``; a wait code without it reads oddly.

    ``peer_flood`` is excluded on purpose: Telegram sends no duration with it
    (``_flood_action_result(..., seconds=None)``), so a "retry in ? s" would be a
    worse message than none.
    """
    timed = ("flood_wait", "slow_mode_wait", "premium_wait")
    for locale in ("ru", "en"):
        accounts = _i18n(locale)["accounts"]
        for code in timed:
            copies = [
                accounts[namespace]["code"][code]
                for namespace in _CODE_NAMESPACES
                if code in accounts[namespace]["code"]
            ]
            assert copies, f"{locale}: {code} has no copy at all"
            for copy in copies:
                assert "{{s}}" in copy, f"{locale}: {code} drops the retry-after seconds"
