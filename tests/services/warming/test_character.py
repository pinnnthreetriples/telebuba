"""Account character (item 4.2): code-picked basics, AI-filled job and interests, anti-repeat."""

from __future__ import annotations

import json

import pytest

from core.config import settings
from core.db import create_account, delete_account, load_warming_settings
from core.repositories.warming_character import (
    fetch_account_character,
    store_account_character,
)
from schemas.accounts import AccountCreate
from schemas.gemini import GeminiResult
from services.warming import _seams
from services.warming._character import (
    _parse_profile,
    account_character,
    base_character,
    interests_overlap,
    is_distinct,
)
from services.warming._character_data import CITIES_BY_COUNTRY

_MX = "+525512345678"
_RU = "+79161234567"


@pytest.fixture(autouse=True)
def _characters_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "character_enabled", True)


def _profile(occupation: str, interests: list[str]) -> GeminiResult:
    return GeminiResult(
        status="ok", text=json.dumps({"occupation": occupation, "interests": interests})
    )


def test_the_basics_follow_the_phone_and_never_drift() -> None:
    first = base_character("acc-1", _MX)
    assert first == base_character("acc-1", _MX)
    assert first.language == "es"
    assert first.country == "MX"
    assert first.city in CITIES_BY_COUNTRY["MX"]
    lo, hi = settings.warming.character_age_range
    assert lo <= first.age <= hi
    assert base_character("acc-2", _RU).language == "ru"


def test_the_fleet_spreads_across_ages_genders_and_cities() -> None:
    basics = [base_character(f"acc-{i}", _MX) for i in range(40)]
    assert len({b.gender for b in basics}) == 2
    assert len({b.city for b in basics}) > 2
    assert len({b.age for b in basics}) > 5


def test_no_phone_keeps_the_legacy_language_and_no_city() -> None:
    basics = base_character("acc-1", None)
    assert basics.language == "ru"
    assert basics.city is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not json",
        "[1, 2]",
        '{"occupation": "x", "interests": ["a", "b", "c"]}',
        '{"occupation": "chef", "interests": ["a", "a", "b"]}',
        '{"occupation": "chef", "interests": "cooking"}',
    ],
)
def test_an_unusable_profile_is_refused(raw: str | None) -> None:
    assert _parse_profile(raw) is None


def test_a_profile_is_cleaned_and_capped() -> None:
    raw = json.dumps(
        {"occupation": " Chef ", "interests": ["Fútbol", "cine", "café", "a", "b", "c"]}
    )
    assert _parse_profile(raw) == ("Chef", ["fútbol", "cine", "café", "a", "b"])


def test_anti_repeat_needs_a_unique_job_and_low_overlap() -> None:
    other = base_character("acc-2", _MX).model_copy(
        update={"occupation": "Chef", "interests": ["fútbol", "cine", "café"]}
    )
    mine = base_character("acc-1", _MX)
    same_job = mine.model_copy(
        update={"occupation": "chef", "interests": ["yoga", "ajedrez", "pan"]}
    )
    close = mine.model_copy(
        update={"occupation": "Taxista", "interests": ["fútbol", "cine", "pan"]}
    )
    apart = mine.model_copy(
        update={"occupation": "Taxista", "interests": ["fútbol", "yoga", "pan"]}
    )
    assert interests_overlap(["a", "b", "c"], ["A", "b", "d"]) == pytest.approx(0.5)
    assert not is_distinct(same_job, [other])
    assert not is_distinct(close, [other])
    assert is_distinct(apart, [other])


@pytest.mark.asyncio
async def test_the_character_is_made_once_in_json_mode_and_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-1", phone=_MX))
    requests: list[object] = []

    async def gen(request: object) -> GeminiResult:
        requests.append(request)
        return _profile("Diseñadora", ["fútbol", "cine", "cocina"])

    monkeypatch.setattr(_seams, "generate_text", gen)
    secret = await load_warming_settings()
    made = await account_character("acc-1", secret)
    again = await account_character("acc-1", secret)

    assert made is not None
    assert made == again
    assert made.occupation == "Diseñadora"
    assert len(requests) == 1
    request = requests[0]
    assert getattr(request, "response_json_object", False) is True
    assert "Spanish" in getattr(request, "prompt", "")
    assert await fetch_account_character("acc-1") == made


@pytest.mark.asyncio
async def test_a_repeated_profile_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1", phone=_MX))
    taken = base_character("acc-9", _MX).model_copy(
        update={"occupation": "Chef", "interests": ["fútbol", "cine", "café"]}
    )
    await store_account_character(taken)
    answers = iter(
        [_profile("chef", ["yoga", "pan", "mar"]), _profile("Taxista", ["yoga", "pan", "mar"])]
    )

    async def gen(_request: object) -> GeminiResult:
        return next(answers)

    monkeypatch.setattr(_seams, "generate_text", gen)
    made = await account_character("acc-1", await load_warming_settings())
    assert made is not None
    assert made.occupation == "Taxista"


@pytest.mark.asyncio
async def test_a_profile_that_never_comes_is_not_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1", phone=_MX))
    calls = 0

    async def gen(_request: object) -> GeminiResult:
        nonlocal calls
        calls += 1
        return GeminiResult(status="error", error="boom")

    monkeypatch.setattr(_seams, "generate_text", gen)
    assert await account_character("acc-1", await load_warming_settings()) is None
    assert calls == settings.warming.character_max_attempts
    assert await fetch_account_character("acc-1") is None


@pytest.mark.asyncio
async def test_switched_off_means_no_character(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "character_enabled", False)
    assert await account_character("acc-1", await load_warming_settings()) is None


@pytest.mark.asyncio
async def test_deleting_the_account_drops_its_character() -> None:
    await create_account(AccountCreate(account_id="acc-1", phone=_MX))
    character = base_character("acc-1", _MX).model_copy(
        update={"occupation": "Chef", "interests": ["fútbol", "cine", "café"]}
    )
    await store_account_character(character)
    await delete_account("acc-1")
    assert await fetch_account_character("acc-1") is None


@pytest.mark.asyncio
async def test_a_character_from_an_old_number_is_made_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-1", phone=_MX))
    old = base_character("acc-1", _RU).model_copy(
        update={"occupation": "Повар", "interests": ["кино", "футбол", "чай"]}
    )
    await store_account_character(old)

    async def gen(_request: object) -> GeminiResult:
        return _profile("Diseñadora", ["fútbol", "cine", "cocina"])

    monkeypatch.setattr(_seams, "generate_text", gen)
    made = await account_character("acc-1", await load_warming_settings())
    assert made is not None
    assert made.language == "es"
    stored = await fetch_account_character("acc-1")
    assert stored is not None
    assert stored.occupation == "Diseñadora"


@pytest.mark.asyncio
async def test_a_racing_writer_gets_the_stored_character() -> None:
    await create_account(AccountCreate(account_id="acc-1", phone=_MX))
    first = base_character("acc-1", _MX).model_copy(
        update={"occupation": "Chef", "interests": ["fútbol", "cine", "café"]}
    )
    second = first.model_copy(update={"occupation": "Taxista"})
    assert await store_account_character(first) == first
    assert await store_account_character(second) == first
