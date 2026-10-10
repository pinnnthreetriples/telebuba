"""Saved chat lists: names are unique, a replace is whole, a delete is final."""

from __future__ import annotations

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast_collections import ChatCollection


@pytest.mark.asyncio
async def test_a_list_is_saved_listed_by_name_replaced_and_deleted() -> None:
    crypto = await repository.create_collection("Crypto", ["@alpha", "t.me/+AbC"])
    games = await repository.create_collection("Games", ["@gamma"])
    assert isinstance(crypto, ChatCollection)
    assert isinstance(games, ChatCollection)

    listed = await repository.list_collections()
    assert [(c.name, c.targets) for c in listed] == [
        ("Crypto", ["@alpha", "t.me/+AbC"]),
        ("Games", ["@gamma"]),
    ]

    replaced = await repository.replace_collection(crypto.collection_id, "Coins", ["@beta"])
    assert isinstance(replaced, ChatCollection)
    assert (replaced.name, replaced.targets) == ("Coins", ["@beta"])

    assert await repository.delete_collection(crypto.collection_id)
    assert not await repository.delete_collection(crypto.collection_id)
    assert [c.name for c in await repository.list_collections()] == ["Games"]


@pytest.mark.asyncio
async def test_a_name_in_use_and_a_missing_list_are_told_apart() -> None:
    first = await repository.create_collection("Crypto", [])
    second = await repository.create_collection("Games", [])
    assert isinstance(first, ChatCollection)
    assert isinstance(second, ChatCollection)

    assert await repository.create_collection("Crypto", ["@x"]) == "name_taken"
    assert await repository.replace_collection(second.collection_id, "Crypto", []) == "name_taken"
    assert await repository.replace_collection("missing", "Other", []) == "not_found"
