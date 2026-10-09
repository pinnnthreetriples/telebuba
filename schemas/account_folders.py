"""Pydantic contracts for account folders and the Accounts page's list filters.

A folder is an operator-named list of accounts; an account may sit in several. The
"all accounts" and «Без папки» tabs are views, not folders, so nothing here stores
them. No behaviour, no I/O.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from schemas.accounts import _ACCOUNT_ID_PATTERN

# The «Без папки» view's value of the list's ``folder`` filter.
UNFILED = "unfiled"
# The «Без прокси» value of the list's ``proxy_country`` filter.
NO_PROXY = "none"

_MAX_NAME = 40
# One drag carries the selection; the fleet is far below this.
_MAX_ACCOUNTS = 1000

FolderName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=_MAX_NAME)
]
AccountId = Annotated[str, StringConstraints(min_length=1, pattern=_ACCOUNT_ID_PATTERN)]


class AccountFolder(BaseModel):
    folder_id: str
    name: str
    account_count: int = Field(ge=0)
    created_at: str


class AccountFolders(BaseModel):
    """Folders in creation order, plus the counts of the two virtual views."""

    items: list[AccountFolder]
    total_count: int = Field(ge=0)
    unfiled_count: int = Field(ge=0)


class AccountFolderWrite(BaseModel):
    """Create or rename: the name is trimmed; uniqueness ignores case (Cyrillic too)."""

    model_config = ConfigDict(extra="forbid")

    name: FolderName


class AccountFolderAccounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_ids: list[AccountId] = Field(min_length=1, max_length=_MAX_ACCOUNTS)


class AccountFolderChange(BaseModel):
    """The accounts a membership write actually changed — what an undo must revert.

    Adding an account already in the folder, or removing one not in it, changes
    nothing and is left out; so is an id with no account behind it.
    """

    folder_id: str
    account_ids: list[str]


class AccountListFilters(BaseModel):
    """The SQL-side filters of the accounts list (the service resolves the rest)."""

    query: str = ""
    # ``None`` = any status.
    statuses: frozenset[str] | None = None
    # A folder id, ``UNFILED``, or ``None`` for every account.
    folder: str | None = None
    # An upper-case ISO alpha-2 code, ``NO_PROXY``, or ``None`` for any.
    proxy_country: str | None = None


class PhoneCodeOption(BaseModel):
    """A calling code present in the fleet's phone numbers."""

    calling_code: int
    # The code's main region (``RU`` for +7) for the flag; ``None`` for a non-geo code.
    country_code: str | None = None
    count: int = Field(ge=0)


class ProxyCountryOption(BaseModel):
    country_code: str
    country_name: str | None = None
    count: int = Field(ge=0)


class AccountFilterOptions(BaseModel):
    """What the Accounts page's filter pills can offer: only values present in data."""

    phone_codes: list[PhoneCodeOption]
    proxy_countries: list[ProxyCountryOption]
    no_proxy_count: int = Field(ge=0)
