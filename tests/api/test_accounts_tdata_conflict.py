"""The tdata route answers an already-imported account with 409, like import-session."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from services.accounts import SessionAlreadyExistsError
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

    from schemas.tdata import TdataImportResult


@pytest.mark.asyncio
async def test_import_tdata_maps_an_existing_account_to_409(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _exists(request: object) -> TdataImportResult:  # noqa: ARG001
        msg = "tdata account '1' already exists. Delete it before importing."
        raise SessionAlreadyExistsError(msg)

    monkeypatch.setattr("services.accounts.import_account_tdata", _exists)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/import-tdata",
            files={"file": ("dup.zip", b"x", "application/zip")},
        )
    assert resp.status_code == 409
