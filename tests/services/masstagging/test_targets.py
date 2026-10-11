"""Who to tag: the typed forms, a list file, a parser base, and the saved list's checks."""

from __future__ import annotations

import pytest

from core.repositories import user_parser as user_parser_repository
from schemas.masstagging_records import TargetSeed
from schemas.telegram_actions_masstagging import MasstagUser
from schemas.user_parser import UserParserRequest
from schemas.user_parser_run import UserParserRun, UserParserUser
from services.masstagging import MasstaggingInvalidError
from services.masstagging.targets import (
    contact_seed,
    from_base,
    import_file,
    member_seed,
    parse_target,
    seeds_of,
    valid_targets,
)


@pytest.mark.parametrize(
    ("raw", "key", "shown"),
    [
        ("@Ivan_Petrov", "u:ivan_petrov", "@Ivan_Petrov"),
        ("ivan_petrov", "u:ivan_petrov", "@ivan_petrov"),
        ("https://t.me/ivan_petrov", "u:ivan_petrov", "@ivan_petrov"),
        ("t.me/moscow_chat/", "u:moscow_chat", "@moscow_chat"),
        ("+7 (900) 123-45-67", "p:79001234567", "+79001234567"),
    ],
)
def test_a_target_is_a_username_or_a_phone(raw: str, key: str, shown: str) -> None:
    seed = parse_target(raw)
    assert seed is not None
    assert (seed.target_key, seed.raw) == (key, shown)


@pytest.mark.parametrize(
    "raw", ["t.me/+AbCdEf", "t.me/joinchat/xyz", "t.me/c/123/4", "ab", "+12", "1bad", "@"]
)
def test_links_that_name_no_person_are_refused(raw: str) -> None:
    assert parse_target(raw) is None


def test_the_saved_list_is_deduplicated_and_checked() -> None:
    assert valid_targets(["@Ivan", "ivan", "t.me/ivan", "+79001234567"]) == [
        "@Ivan",
        "+79001234567",
    ]
    with pytest.raises(MasstaggingInvalidError, match="masstagging_invalid_target"):
        valid_targets(["@ivan", "t.me/+invite"])
    assert [seed.target_key for seed in seeds_of(["@ivan", "t.me/+x", "IVAN"])] == ["u:ivan"]


def test_a_list_file_is_split_and_counts_what_it_could_not_read() -> None:
    found = import_file(b"@alpha, beta\n+79001234567; t.me/+bad\n\n")

    assert found.targets == ["@alpha", "@beta", "+79001234567"]
    assert found.skipped == 1


def test_a_csv_with_a_username_column_reads_only_that_column() -> None:
    content = "﻿id,username,first_name\n1,alpha,Ivan\n2,,Olga\n3,beta,Petr\n".encode()

    found = import_file(content)

    assert found.targets == ["@alpha", "@beta"]


def test_a_file_that_is_not_text_is_refused() -> None:
    with pytest.raises(MasstaggingInvalidError, match="masstagging_import_invalid"):
        import_file(b"\xff\xfe\x00\x00binary")


@pytest.mark.asyncio
async def test_a_parser_base_is_copied_as_usernames() -> None:
    run = UserParserRun(
        run_id="base1", name="Moscow chats", mode="members", status="running", created_at="2026"
    )
    body = {"mode": "members", "sources": ["@group"], "account_ids": ["a1"]}
    await user_parser_repository.create_run(run, UserParserRequest.model_validate(body))
    await user_parser_repository.settle_run(
        run.model_copy(update={"status": "done"}),
        [
            UserParserUser(user_id=1, username="alpha"),
            UserParserUser(user_id=2, username=None),
            UserParserUser(user_id=3, username="robot", is_bot=True),
            UserParserUser(user_id=4, username="beta"),
        ],
    )

    found = await from_base("base1")

    assert found.targets == ["@alpha", "@beta"]
    assert found.skipped == 2
    assert found.name == "Moscow chats"
    with pytest.raises(MasstaggingInvalidError, match="masstagging_base_not_found"):
        await from_base("nope")


def test_a_contact_and_a_member_without_username_belong_to_their_account() -> None:
    user = MasstagUser(user_id=7, first_name="Olga")
    contact = contact_seed(user, "a1")
    member = member_seed(user, "a1")
    assert (contact.target_key, contact.kind, contact.assigned_account_id) == (
        "id:7",
        "contact",
        "a1",
    )
    assert (member.kind, member.assigned_account_id) == ("link", "a1")
    named = member_seed(MasstagUser(user_id=8, username="Petr"), "a1")
    assert named == TargetSeed(
        target_key="u:petr", raw="@Petr", kind="link", username="Petr", first_name=""
    )
