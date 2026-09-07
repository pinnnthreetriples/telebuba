"""Config validation — cross-field min≤max bounds, plus the repr-secrecy guard."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from core.config import (
    ApiSettings,
    AuthSettings,
    NeurocommentSettings,
    NeuroshillingSettings,
    Settings,
    TelegramSettings,
    WarmingSettings,
    settings,
)
from schemas.telegram_actions_warming import (
    WarmConsumeMedia,
    WarmEmojiStatus,
    WarmInlineQuery,
    WarmSearchMessages,
    WarmSelfNote,
)

if TYPE_CHECKING:
    from typing import Any

    from pydantic_settings import BaseSettings


def test_reply_delay_min_must_not_exceed_max() -> None:
    with pytest.raises(ValidationError):
        NeurocommentSettings(reply_delay_min_seconds=10.0, reply_delay_max_seconds=3.0)


def test_join_delay_min_must_not_exceed_max() -> None:
    with pytest.raises(ValidationError):
        NeurocommentSettings(join_delay_min_seconds=60.0, join_delay_max_seconds=30.0)


def test_neuroshilling_join_delay_min_must_not_exceed_max() -> None:
    with pytest.raises(ValidationError):
        NeuroshillingSettings(join_delay_min_seconds=120.0, join_delay_max_seconds=30.0)


def test_neuroshilling_poll_interval_min_must_not_exceed_max() -> None:
    with pytest.raises(ValidationError):
        NeuroshillingSettings(poll_min_seconds=60.0, poll_max_seconds=30.0)


def test_neuroshilling_llm_output_cannot_exceed_the_gateway_ceiling() -> None:
    """The gateway request schema is the real ceiling.

    ``schemas.gemini`` caps ``max_output_tokens`` at 2048, so a larger value here
    would only fail later, inside a request the operator never sees.
    """
    assert NeuroshillingSettings().llm_max_output_tokens == 2048
    with pytest.raises(ValidationError):
        NeuroshillingSettings(llm_max_output_tokens=4096)


def test_inactive_channel_drop_days_rejects_a_fraction_of_a_day() -> None:
    """A sub-day window is a typo, never an intent, and this setting's mistake is not undoable.

    A cutoff minutes in the past makes every channel a suspect at once and unlinks the
    fleet, deleting per-account pins nothing restores. 0 stays valid — it disables the rule.
    """
    with pytest.raises(ValidationError):
        NeurocommentSettings(inactive_channel_drop_days=0.5)
    assert NeurocommentSettings(inactive_channel_drop_days=0.0).inactive_channel_drop_days == 0.0


def test_max_joins_per_account_per_day_defaults_to_conservative_cap() -> None:
    assert NeurocommentSettings().max_joins_per_account_per_day == 20


def test_max_joins_per_account_per_day_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        NeurocommentSettings(max_joins_per_account_per_day=-1)


def test_challenge_thinking_budget_must_leave_room_for_the_decision_json() -> None:
    """Thoughts are billed against the output cap.

    A budget that fills it truncates every decision and turns solvable captchas
    into give_up.
    """
    with pytest.raises(ValidationError):
        NeurocommentSettings(challenge_thinking_budget=1024, challenge_max_output_tokens=1024)


def test_auth_secret_must_be_at_least_32_bytes_when_set() -> None:
    with pytest.raises(ValidationError):
        AuthSettings(secret="too-short")


def test_auth_secret_empty_is_allowed() -> None:
    assert AuthSettings(secret="").secret == ""


def test_auth_secret_long_enough_is_accepted() -> None:
    secret = "x" * 32
    assert AuthSettings(secret=secret).secret == secret


def test_cors_wildcard_with_credentials_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ApiSettings(cors_origins=["*"], cors_allow_credentials=True)


def test_cors_explicit_origins_with_credentials_is_accepted() -> None:
    api = ApiSettings(cors_origins=["https://app.example"], cors_allow_credentials=True)
    assert api.cors_origins == ["https://app.example"]


def test_cors_wildcard_without_credentials_is_accepted() -> None:
    api = ApiSettings(cors_origins=["*"], cors_allow_credentials=False)
    assert api.cors_origins == ["*"]


def test_security_concurrency_limits_have_safe_bounded_defaults() -> None:
    assert ApiSettings().max_concurrent_uploads == 2
    assert AuthSettings().argon2_max_concurrency == 2
    with pytest.raises(ValidationError):
        ApiSettings(max_concurrent_uploads=0)
    with pytest.raises(ValidationError):
        ApiSettings(max_concurrent_uploads=17)
    with pytest.raises(ValidationError):
        AuthSettings(argon2_max_concurrency=0)
    with pytest.raises(ValidationError):
        AuthSettings(argon2_max_concurrency=9)


def test_upload_staging_has_private_cleanup_defaults_and_bounded_ttl() -> None:
    api = ApiSettings()
    # Compared as a Path, not a string: the default renders with a backslash on
    # Windows, which is the operator's platform and says nothing about the setting.
    assert api.upload_staging_dir == Path("runtime/uploads")
    assert api.upload_staging_ttl_seconds == 86_400
    with pytest.raises(ValidationError):
        ApiSettings(upload_staging_ttl_seconds=59)


def test_session_cookie_defaults_to_strict_same_site() -> None:
    assert AuthSettings().cookie_samesite == "strict"


def test_phase_daily_cap_rejects_a_cap_of_zero() -> None:
    """A cap of 0 switches off the daily gate AND the pre-cycle reservation (#208)."""
    with pytest.raises(ValidationError):
        WarmingSettings(phase_daily_cap={"intro": 0})


def test_extras_inline_bots_must_stay_within_the_whitelist() -> None:
    """Any bot outside the schema whitelist is a third party that would see the query."""
    with pytest.raises(ValidationError):
        WarmingSettings(extras_inline_bots=["gif", "somebot"])
    with pytest.raises(ValidationError):
        WarmingSettings(extras_inline_bots=[])
    assert WarmingSettings(extras_inline_bots=["gif"]).extras_inline_bots == ["gif"]


@pytest.mark.parametrize(
    "bad",
    [
        {"extras_leave_min_age_days": 0},
        {"extras_leave_min_interval_days": 0},
        {"extras_rejoin_cooldown_days": 0},
        {"extras_mute_hours": []},
        {"extras_mute_hours": [-1.0]},
        {"extras_mute_hours": [24 * 365 + 1]},
        {"extras_archive_probability": 1.5},
    ],
)
def test_extras_chat_settings_are_bounded(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WarmingSettings(**bad)


def test_extras_mute_hours_edges_match_the_mute_action_schema() -> None:
    assert WarmingSettings(extras_mute_hours=[0, 24 * 365]).extras_mute_hours == [0.0, 8760.0]


@pytest.mark.parametrize("word", ["a", "x" * 33], ids=["one_char", "thirty_three_chars"])
def test_extras_search_queries_fit_the_action_schemas_they_are_typed_into(word: str) -> None:
    """A word outside 2..32 chars would fail the search-fallback / inline-query schema at draw."""
    with pytest.raises(ValidationError):
        WarmingSettings(extras_search_queries=[word])


@pytest.mark.parametrize(
    "window",
    [(0.5, 10.0), (10.0, 5.0), (1.0, 721.0)],
    ids=["sub_hour_lo", "lo_above_hi", "past_cap"],
)
def test_extras_scheduled_delay_window_is_ordered_and_within_the_schedule_cap(
    window: tuple[float, float],
) -> None:
    """``rng.uniform`` would silently invert lo > hi; a delay past 720h fails the note schema."""
    with pytest.raises(ValidationError):
        WarmingSettings(extras_scheduled_delay_hours=window)
    edge = (1.0, 720.0)
    assert WarmingSettings(extras_scheduled_delay_hours=edge).extras_scheduled_delay_hours == edge


@pytest.mark.parametrize(
    "bad",
    [
        {"extras_media_bytes_per_cycle": -1},
        {"extras_media_bytes_per_item": 65_535},
        {"extras_media_bytes_per_item": 50_000_001},
        {"extras_media_pause_seconds": (-1.0, 5.0)},
        {"extras_media_pause_seconds": (10.0, 5.0)},
        {"extras_media_pause_seconds": (5.0, 301.0)},
        {"extras_emoji_until_hours": (0.0, 1.0)},
        {"extras_emoji_until_hours": (0.001, 1.0)},
        {"extras_emoji_until_hours": (5.0, 2.0)},
        {"extras_emoji_until_hours": (1.0, 169.0)},
        {"extras_emoji_clear_probability": 1.1},
    ],
    ids=[
        "negative_cycle_bytes",
        "item_below_floor",
        "item_above_cap",
        "negative_pause",
        "pause_lo_above_hi",
        "pause_past_five_minutes",
        "zero_lo",
        "sub_minute_lo",
        "lo_above_hi",
        "past_week",
        "probability_above_one",
    ],
)
def test_extras_media_and_emoji_settings_are_bounded(bad: dict[str, Any]) -> None:
    """Per-item bytes mirror ``WarmConsumeMedia.max_bytes``; the windows, ``rng.uniform``'s.

    The emoji floor is a minute: core floors ``until`` to the minute, so a shorter lifetime
    could resolve to a time at or before now.
    """
    with pytest.raises(ValidationError):
        WarmingSettings(**bad)


def test_extras_media_and_emoji_edges_match_their_action_schemas() -> None:
    edge = WarmingSettings(
        extras_media_bytes_per_cycle=0,
        extras_media_bytes_per_item=65_536,
        extras_media_pause_seconds=(0.0, 300.0),
        extras_emoji_until_hours=(1 / 60, 168.0),
    )
    assert edge.extras_media_bytes_per_cycle == 0  # 0 = video/voice disabled, still valid
    assert edge.extras_media_pause_seconds == (0.0, 300.0)
    WarmConsumeMedia(
        channel="c", message_ids=[1], kind="video", max_bytes=edge.extras_media_bytes_per_item
    )
    WarmEmojiStatus(until_hours=edge.extras_emoji_until_hours[1])


@pytest.mark.parametrize("texts", [[], ["a"], ["x" * 61]], ids=["empty", "one_char", "sixty_one"])
def test_extras_note_texts_fit_the_self_note_schema(texts: list[str]) -> None:
    """A note outside 2..60 chars would fail ``WarmSelfNote`` at draw, not at boot."""
    with pytest.raises(ValidationError):
        WarmingSettings(extras_note_texts=texts)


def test_default_note_texts_and_search_queries_are_accepted_by_their_action_models() -> None:
    """The config bounds mirror the schemas; this pins them together so neither drifts alone."""
    defaults = WarmingSettings()
    for text in defaults.extras_note_texts:
        WarmSelfNote(text=text)
    for word in defaults.extras_search_queries:
        WarmSearchMessages(channel="c", message_ids=[1], fallback_query=word)
        WarmInlineQuery(bot="gif", query=word)


# --- repr secrecy -----------------------------------------------------------
# pytest's assertion rewriting dumps a model repr whenever an assertion touches a
# ``settings`` attribute chain. The values come from the developer's own ``.env``
# (which python-dotenv finds by walking UP, so a git worktree picks up the parent
# checkout's file), so a failing test prints live credentials into a local log —
# and into whatever an operator then pastes into an issue or a PR. Every
# secret-bearing field is therefore declared ``Field(repr=False)``.
#
# The guard matches field NAMES rather than a hand-listed set of fields, so a
# newly added secret field is caught rather than silently unprotected. Substrings,
# not suffixes: a suffix rule let ``private_key_pem``, ``basic_auth`` and
# ``session_string`` through. Measured over all 245 (namespace, field) pairs:
# catches all 13 protected fields with zero false positives.
#
# The matching is NOT complete, and no single number describes how complete it is —
# any "N of M names covered" figure only holds against the list of names whoever
# wrote it happened to think of. These are the gaps we knowingly accept, with what
# closing each would cost in false positives across those 245 pairs:
#   ``session``  5 (session_ttl_minutes, session_dir, session_max_bytes,
#                expected_actions_per_session, persona_sessions) — so the
#                ``session_*`` family beyond ``session_str`` slips: ``session_data``,
#                ``session_b64``, ``takeout_session``. Accepted reluctantly: a
#                Telethon session IS a whole account (see core/config.py's own note
#                on ``.session`` files), but 5 false positives is too high a price.
#   ``_url``     5 (all five ``*_base_url`` fields) — so ``sentry_url``, a Sentry DSN
#                under another name, slips even though ``dsn`` is a pattern.
#   ``iv``       8, ``sk`` 2, ``tdata`` 1 — bare crypto abbreviations and
#                ``tdata_bundle`` slip. ``iv`` matches NINE field names but costs
#                eight: ``warming.active_hours_enabled`` is a ``bool``, so the rule
#                below drops it before its name is ever tested. Every cost here is a
#                predicate outcome, not a name-match count.
# ``pk``, ``nonce`` and ``kdf`` would cost nothing but are not patterns either: no
# field here is named that way and this codebase spells things out
# (``admin_password``, not ``pw``). Add them the day a field needs them.
#
# The field pin below is the backstop for all of this: it does not care why a field
# stopped being flagged.

_SECRET_NAME_SUFFIXES = ("_id", "_key", "_keys", "_hash")
_SECRET_NAME_SUBSTRINGS = (
    "secret",
    "pass",
    "pw",
    "cred",
    "token",
    "session_str",
    "private",
    "seed",
    "mnemonic",
    "recovery",
    "otp",
    "2fa",
    # A Python identifier cannot start with a digit, so the spelled-out form is the
    # likelier one to actually appear as a field name.
    "twofa",
    "bearer",
    "signing",
    "cert",
    "keyfile",
    "apikey",
    "license",
    "dsn",
    "salt",
    "user",
    "jwt",
    "hmac",
    "webhook",
    "database_url",
    "service_account",
    "auth",
)
# One representative name per pattern. ``test_every_secret_name_pattern_is_load_bearing``
# proves each pattern is individually necessary to detect its own example, so a
# typo in any single pattern turns red instead of hiding behind another pattern.
_PATTERN_EXAMPLES = {
    "_id": "api_id",
    "_key": "encryption_key",
    "_keys": "api_keys",
    "_hash": "api_hash",
    "secret": "secret_key_base",
    "pass": "master_pass",
    "pw": "pw",
    "cred": "credentials",
    "token": "refresh_token",
    "session_str": "session_string",
    "private": "private_key_pem",
    "seed": "seed_phrase",
    "mnemonic": "mnemonic",
    "recovery": "recovery_code",
    "otp": "otp",
    "2fa": "tg_2fa",
    "twofa": "twofa",
    "bearer": "bearer",
    "signing": "signing",
    "cert": "cert",
    "keyfile": "keyfile",
    "apikey": "apikey",
    "license": "license",
    "dsn": "dsn",
    "salt": "salt",
    "user": "proxy_user",
    "jwt": "jwt",
    "hmac": "hmac",
    "webhook": "webhook_url",
    "database_url": "database_url",
    "service_account": "service_account_json",
    "auth": "basic_auth",
}
# Fields whose name matches but that provably carry no credential. A MAPPING, not
# a set: an entry cannot be added without writing the reason, and the reason lands
# in the diff where a reviewer sees it. Empty today — both structural exceptions
# (a bool, and a plural integer token budget) are expressed as rules in
# ``_is_secret_field`` instead, so nobody has to grow this to stay green.
#
# The length floor below buys nothing on its own: one plausible sentence would
# silently unguard any field here. Review is the actual control; the floor only
# stops a bare "n/a" from passing for a justification.
_NOT_SECRET: dict[str, str] = {}
_MIN_EXEMPTION_REASON_CHARS = 40


def _matches_secret_name(name: str, *, without: str = "") -> bool:
    """Whether ``name`` looks credential-bearing, optionally ignoring one pattern.

    Case-folded so ``API_KEY`` and ``apiKey`` match too; every pattern is lowercase.
    """
    folded = name.casefold()
    suffixes = tuple(s for s in _SECRET_NAME_SUFFIXES if s != without)
    return folded.endswith(suffixes) or any(
        s in folded for s in _SECRET_NAME_SUBSTRINGS if s != without
    )


def _is_secret_field(model: type[BaseSettings], name: str) -> bool:
    if name in _NOT_SECRET:
        return False
    annotation = model.model_fields[name].annotation
    # A bool holds one bit: it can *name* a credential (``has_gemini_key``) but
    # cannot carry one. A plural integer ``*_tokens`` is an LLM output budget, not
    # a token; a credential list would be ``list[str]`` and still matches.
    if annotation is bool:
        return False
    if name.endswith("_tokens") and annotation is int:
        return False
    return _matches_secret_name(name)


def _secret_field_names(model: type[BaseSettings]) -> list[str]:
    return [name for name in model.model_fields if _is_secret_field(model, name)]


def _fake_value(model: type[BaseSettings], name: str) -> object:
    """A recognisable non-secret stand-in, typed to the field and long enough.

    40 ``z``s clear ``AuthSettings``' 32-byte HMAC floor.
    """
    annotation = model.model_fields[name].annotation
    if annotation is int:
        return 987654321
    fake = f"FAKE-{name}-{'z' * 40}"
    # ``*_keys`` anticipates a ``list[str]`` of provider keys; wrap so validation
    # accepts it instead of raising and losing the report.
    return [fake] if annotation == list[str] else fake


_SECRET_NAMESPACES = [
    (namespace, model)
    for namespace, model in ((n, type(getattr(settings, n))) for n in Settings.model_fields)
    if _secret_field_names(model)
]


def test_every_secret_name_pattern_has_an_example() -> None:
    """A new pattern without an example would not be covered by the test below."""
    assert set(_PATTERN_EXAMPLES) == {*_SECRET_NAME_SUFFIXES, *_SECRET_NAME_SUBSTRINGS}


@pytest.mark.parametrize(("pattern", "example"), sorted(_PATTERN_EXAMPLES.items()))
def test_every_secret_name_pattern_is_load_bearing(pattern: str, example: str) -> None:
    """Each pattern must be the *only* reason its own example is detected.

    Without this, typo'ing one pattern stays green because another pattern happens
    to match the same example, and the fields resting on the typo'd pattern go
    unprotected in silence.
    """
    assert _matches_secret_name(example), (
        f"pattern {pattern!r} no longer detects {example!r} — the pattern is broken"
    )
    assert not _matches_secret_name(example, without=pattern), (
        f"pattern {pattern!r} is not load-bearing: {example!r} is still detected "
        f"without it, so a typo in {pattern!r} would go unnoticed"
    )


def test_the_protected_field_set_is_pinned() -> None:
    """Pin the FIELDS the sweep covers, not just the namespaces.

    Deleting a pattern *together with its example* is otherwise invisible — and
    ``test_every_secret_name_pattern_has_an_example`` actively pushes a contributor
    pruning a pattern to delete its example in the same edit, which is exactly the
    motion that hid the loss. This pin makes a field falling out of the swept set
    red no matter what happened to the patterns.

    A new secret field therefore lands here deliberately, next to its
    ``repr=False`` and its ``.env.example`` key.
    """
    swept = sorted(
        (namespace, name)
        for namespace, model in _SECRET_NAMESPACES
        for name in _secret_field_names(model)
    )
    assert swept == [
        ("auth", "admin_password"),
        ("auth", "admin_username"),
        ("auth", "secret"),
        ("deepseek", "api_key"),
        ("gemini", "api_key"),
        ("logging", "sentry_dsn"),
        ("openai", "api_key"),
        ("proxy", "ipinfo_token"),
        ("proxy", "maxmind_account_id"),
        ("proxy", "maxmind_license_key"),
        ("telegram", "api_hash"),
        ("telegram", "api_id"),
        ("warming", "fleet_hash_salt"),
    ]


def test_a_not_secret_exemption_must_name_a_real_field_and_justify_itself() -> None:
    """``_NOT_SECRET`` is the escape hatch; make using it cost a written reason."""
    real_fields = {
        name
        for namespace in Settings.model_fields
        for name in type(getattr(settings, namespace)).model_fields
    }
    for name, reason in _NOT_SECRET.items():
        assert name in real_fields, f"_NOT_SECRET[{name!r}] exempts a field that does not exist"
        assert len(reason) >= _MIN_EXEMPTION_REASON_CHARS, (
            f"_NOT_SECRET[{name!r}] needs a real justification, not {reason!r}"
        )


@pytest.mark.parametrize(("namespace", "model"), _SECRET_NAMESPACES)
def test_secret_fields_never_reach_a_repr(namespace: str, model: type[BaseSettings]) -> None:
    """Neither the sub-model's own repr nor the aggregate ``Settings`` repr shows them."""
    fakes = {name: _fake_value(model, name) for name in _secret_field_names(model)}
    # model_validate, not the constructor: it validates this dict alone, so the
    # assertions never depend on whatever the developer's own .env happens to hold.
    nested = model.model_validate(fakes)
    aggregate = repr(Settings.model_validate({namespace: nested}))
    own = repr(nested)
    for name, value in fakes.items():
        assert model.model_fields[name].repr is False, (
            f"{namespace}.{name} looks secret-bearing but is not declared Field(repr=False)"
        )
        assert str(value) not in own, f"{namespace}.{name} leaks into repr({namespace})"
        assert str(value) not in aggregate, f"{namespace}.{name} leaks into repr(settings)"


def test_hiding_a_secret_from_repr_leaves_the_value_usable() -> None:
    """``repr=False`` hides only the repr — reads and dumps still carry the value."""
    telegram = TelegramSettings(api_hash="FAKE-hash", api_id=987654321)
    assert telegram.api_hash == "FAKE-hash"
    assert telegram.model_dump()["api_hash"] == "FAKE-hash"
    assert "session_dir" in repr(telegram)
