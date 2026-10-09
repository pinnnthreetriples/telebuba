"""``/account-folders`` and the folder side of ``/accounts``, against the real service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schemas.accounts import AccountCreate
from services.accounts import add_account, remove_account
from tests.api.accounts_helpers import client

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

_BASE = "/api/v1/account-folders"


async def _folder(http: httpx.AsyncClient, name: str) -> str:
    created = await http.post(_BASE, json={"name": name})
    assert created.status_code == 200, created.text
    return created.json()["folder_id"]


async def _ids(http: httpx.AsyncClient, **params: str | int) -> list[str]:
    page = await http.get("/api/v1/accounts", params=params)
    assert page.status_code == 200, page.text
    return sorted(item["account_id"] for item in page.json()["items"])


@pytest.mark.asyncio
async def test_folders_are_created_renamed_listed_and_deleted(app: FastAPI) -> None:
    async with client(app) as http:
        created = await http.post(_BASE, json={"name": "  Основные  "})
        assert created.status_code == 200
        body = created.json()
        assert (body["name"], body["account_count"]) == ("Основные", 0)
        folder_id = body["folder_id"]
        second = await _folder(http, "Резерв")

        renamed = await http.patch(f"{_BASE}/{folder_id}", json={"name": "Главные"})
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Главные"
        # Renaming to its own name in another case is not a clash with itself.
        assert (
            await http.patch(f"{_BASE}/{folder_id}", json={"name": "ГЛАВНЫЕ"})
        ).status_code == 200

        listed = (await http.get(_BASE)).json()
        # Creation order: the tabs do not reshuffle on a rename.
        assert [f["folder_id"] for f in listed["items"]] == [folder_id, second]

        assert (await http.delete(f"{_BASE}/{folder_id}")).status_code == 204
        assert (await http.delete(f"{_BASE}/{folder_id}")).status_code == 404
        assert [f["name"] for f in (await http.get(_BASE)).json()["items"]] == ["Резерв"]


@pytest.mark.asyncio
async def test_names_are_unique_ignoring_case_cyrillic_too(app: FastAPI) -> None:
    async with client(app) as http:
        await _folder(http, "Основные")
        other = await _folder(http, "Резерв")

        taken = await http.post(_BASE, json={"name": "основные "})
        assert taken.status_code == 409
        assert taken.json()["error"]["message"] == "folder_name_taken"
        clash = await http.patch(f"{_BASE}/{other}", json={"name": "ОСНОВНЫЕ"})
        assert clash.status_code == 409

        assert (await http.post(_BASE, json={"name": "   "})).status_code == 422
        assert (await http.post(_BASE, json={"name": "x" * 41})).status_code == 422
        assert (await http.post(_BASE, json={"name": "x" * 40})).status_code == 200
        missing = await http.patch(f"{_BASE}/nope", json={"name": "Новая"})
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_membership_writes_report_only_what_changed(app: FastAPI) -> None:
    for account_id in ("a1", "a2", "a3"):
        await add_account(AccountCreate(account_id=account_id))
    async with client(app) as http:
        folder_id = await _folder(http, "Основные")
        url = f"{_BASE}/{folder_id}/accounts"

        first = await http.post(url, json={"account_ids": ["a1", "a2"]})
        assert first.json() == {"folder_id": folder_id, "account_ids": ["a1", "a2"]}
        # Idempotent: a1 is already in; a ghost id is skipped, not an error.
        again = await http.post(url, json={"account_ids": ["a1", "a3", "ghost"]})
        assert again.json()["account_ids"] == ["a3"]

        removed = await http.post(f"{url}/remove", json={"account_ids": ["a3", "a3", "a2"]})
        # Request order, each id once.
        assert removed.json()["account_ids"] == ["a3", "a2"]
        noop = await http.post(f"{url}/remove", json={"account_ids": ["a3"]})
        assert noop.json()["account_ids"] == []

        assert (await http.post(url, json={"account_ids": []})).status_code == 422
        gone = await http.post(f"{_BASE}/nope/accounts", json={"account_ids": ["a1"]})
        assert gone.status_code == 404
        gone_remove = await http.post(f"{_BASE}/nope/accounts/remove", json={"account_ids": ["a1"]})
        assert gone_remove.status_code == 404


@pytest.mark.asyncio
async def test_the_list_filters_by_folder_and_carries_folder_ids(app: FastAPI) -> None:
    for account_id in ("a1", "a2", "a3"):
        await add_account(AccountCreate(account_id=account_id))
    async with client(app) as http:
        main = await _folder(http, "Основные")
        spare = await _folder(http, "Резерв")
        await http.post(f"{_BASE}/{main}/accounts", json={"account_ids": ["a1", "a2"]})
        await http.post(f"{_BASE}/{spare}/accounts", json={"account_ids": ["a2"]})

        assert await _ids(http, folder=main) == ["a1", "a2"]
        assert await _ids(http, folder=spare) == ["a2"]
        assert await _ids(http, folder="unfiled") == ["a3"]
        assert await _ids(http, folder="no-such-folder") == []

        page = (await http.get("/api/v1/accounts")).json()
        assert page["total"] == 3
        folders_of = {item["account_id"]: item["folder_ids"] for item in page["items"]}
        assert folders_of == {"a1": [main], "a2": [main, spare], "a3": []}

        listed = (await http.get(_BASE)).json()
        assert [f["account_count"] for f in listed["items"]] == [2, 1]
        assert (listed["total_count"], listed["unfiled_count"]) == (3, 1)


@pytest.mark.asyncio
async def test_deleting_keeps_accounts_and_deleting_an_account_drops_memberships(
    app: FastAPI,
) -> None:
    for account_id in ("a1", "a2"):
        await add_account(AccountCreate(account_id=account_id))
    async with client(app) as http:
        main = await _folder(http, "Основные")
        spare = await _folder(http, "Резерв")
        for folder_id in (main, spare):
            await http.post(f"{_BASE}/{folder_id}/accounts", json={"account_ids": ["a1", "a2"]})

        assert (await http.delete(f"{_BASE}/{main}")).status_code == 204
        assert await _ids(http) == ["a1", "a2"]
        assert await _ids(http, folder=spare) == ["a1", "a2"]

        await remove_account("a1")
        listed = (await http.get(_BASE)).json()
        assert [f["account_count"] for f in listed["items"]] == [1]
        assert await _ids(http, folder=spare) == ["a2"]


@pytest.mark.asyncio
async def test_filter_options_and_query_validation(app: FastAPI) -> None:
    await add_account(AccountCreate(account_id="ru1", phone="+79991112233"))
    await add_account(AccountCreate(account_id="ru2", phone="79991112244"))
    await add_account(AccountCreate(account_id="de1", phone="+4915112345678"))
    await add_account(AccountCreate(account_id="nophone"))
    async with client(app) as http:
        options = (await http.get("/api/v1/accounts/filter-options")).json()
        assert options["phone_codes"] == [
            {"calling_code": 7, "country_code": "RU", "count": 2},
            {"calling_code": 49, "country_code": "DE", "count": 1},
        ]
        assert options["proxy_countries"] == []
        assert options["no_proxy_count"] == 4

        assert await _ids(http, phone_code=7) == ["ru1", "ru2"]
        assert await _ids(http, proxy_country="none") == ["de1", "nophone", "ru1", "ru2"]
        bad_country = await http.get("/api/v1/accounts", params={"proxy_country": "DEU"})
        assert bad_country.status_code == 422
        bad_trust = await http.get("/api/v1/accounts", params={"min_trust": 101})
        assert bad_trust.status_code == 422
