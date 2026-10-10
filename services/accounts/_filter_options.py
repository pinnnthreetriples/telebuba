"""The Accounts page's filter options: only values the fleet actually has."""

from __future__ import annotations

from collections import Counter

from core.db import list_accounts
from core.phone_geo import calling_code_for_phone, region_for_calling_code
from schemas.account_folders import AccountFilterOptions, PhoneCodeOption, ProxyCountryOption


async def account_filter_options() -> AccountFilterOptions:
    """Phone calling codes and proxy countries in use, most common first.

    A calling code is the phone filter's unit, not a country: +7 covers Russia and
    Kazakhstan alike, so the pill carries the code's main region for its flag only.
    """
    accounts = (await list_accounts()).accounts
    codes = Counter(
        code for code in (calling_code_for_phone(a.phone) for a in accounts) if code is not None
    )
    countries: Counter[str] = Counter()
    names: dict[str, str | None] = {}
    no_proxy = 0
    for account in accounts:
        if account.proxy_id is None:
            no_proxy += 1
        elif account.proxy_country_code:
            country = account.proxy_country_code.upper()
            countries[country] += 1
            names.setdefault(country, account.proxy_country_name)
    return AccountFilterOptions(
        phone_codes=[
            PhoneCodeOption(
                calling_code=code, country_code=region_for_calling_code(code), count=count
            )
            for code, count in sorted(codes.items(), key=lambda item: (-item[1], item[0]))
        ],
        proxy_countries=[
            ProxyCountryOption(country_code=country, country_name=names[country], count=count)
            for country, count in sorted(countries.items(), key=lambda item: (-item[1], item[0]))
        ],
        no_proxy_count=no_proxy,
    )
