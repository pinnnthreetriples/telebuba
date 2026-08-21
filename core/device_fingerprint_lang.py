"""Country → language subtag for the synthetic device fingerprint's locale.

The country half of a fingerprint's ``system_lang_code`` is fixed by the
account's phone number, so only the language half is a choice. This module is
that choice and nothing else: one primary language subtag per region
``phonenumbers`` can return, from which ``language_pair`` assembles ``ll-CC``.
Assembling the tag rather than storing it is what makes
``phone_geo._lang_region(tag) == country`` true by construction, and therefore
``evaluate_geo(...).lang_matches`` true for every mapped country.

Provenance — every value comes from CLDR 48 (Unicode 16.0.0), not from
judgement. To re-verify, refetch the two source files and re-derive:

    https://raw.githubusercontent.com/unicode-org/cldr-json/main/
        cldr-json/cldr-core/supplemental/likelySubtags.json
        cldr-json/cldr-core/supplemental/territoryInfo.json

    for cc in sorted(phonenumbers.SUPPORTED_REGIONS):
        tag = likelySubtags.get(f"und-{cc}")          # rule 1
        lang = "en" if tag is None else tag.split("-")[0]   # rule 2
        if len(lang) != 2:                           # rule 3
            lang = highest _populationPercent 2-letter language in
                   territoryInfo[cc]["languagePopulation"]

Rule 1 — CLDR's likely subtag for a territory is its *most used* language, not
its de-jure official one. That is precisely the question this table asks, and
it is why KZ and BY come out ``ru`` rather than ``kk``/``be`` while UA comes out
``uk``: the arguable post-Soviet cases are answered by the data, so nothing here
is hand-picked. (territoryInfo's raw population ranking is the wrong source for
the same reason — it would make BE ``en`` at 59% second-language penetration.)

Rule 2 — CLDR omits ``und-CC`` when its add-likely-subtags algorithm already
resolves through ``und`` → ``en-Latn-US``, i.e. when the answer is English with
the region carried over. The 57 omitted regions are the anglophone ones (US, GB,
AU, CA, IE, NZ, SG, ZA, NG, and the small island territories); territoryInfo
independently ranks ``en`` top for all of them bar MW and ZM, where ``en`` is
still an official language.

Rule 3 — seven regions get a 3-letter CLDR subtag (BQ/CW ``pap``, PG ``tpi``,
PH ``fil``, PW ``pau``, TK ``tkl``, TV ``tvl``). Those have no ISO 639-1 code,
hence no Windows or macOS locale and no Telegram Desktop UI language, so a real
desktop there reports the territory's next-largest language instead: Dutch for
the two Caribbean municipalities, English for the five Pacific ones.
"""

from __future__ import annotations

from core.phone_geo import country_for_phone

FALLBACK_TAG = "en-US"

LANGUAGE_BY_COUNTRY: dict[str, str] = {
    "AC": "en",
    "AD": "ca",
    "AE": "ar",
    "AF": "fa",
    "AG": "en",
    "AI": "en",
    "AL": "sq",
    "AM": "hy",
    "AO": "pt",
    "AR": "es",
    "AS": "sm",
    "AT": "de",
    "AU": "en",
    "AW": "nl",
    "AX": "sv",
    "AZ": "az",
    "BA": "bs",
    "BB": "en",
    "BD": "bn",
    "BE": "nl",
    "BF": "fr",
    "BG": "bg",
    "BH": "ar",
    "BI": "rn",
    "BJ": "fr",
    "BL": "fr",
    "BM": "en",
    "BN": "ms",
    "BO": "es",
    "BQ": "nl",
    "BR": "pt",
    "BS": "en",
    "BT": "dz",
    "BW": "en",
    "BY": "ru",
    "BZ": "en",
    "CA": "en",
    "CC": "ms",
    "CD": "fr",
    "CF": "sg",
    "CG": "fr",
    "CH": "de",
    "CI": "fr",
    "CK": "en",
    "CL": "es",
    "CM": "fr",
    "CN": "zh",
    "CO": "es",
    "CR": "es",
    "CU": "es",
    "CV": "pt",
    "CW": "nl",
    "CX": "en",
    "CY": "el",
    "CZ": "cs",
    "DE": "de",
    "DJ": "fr",
    "DK": "da",
    "DM": "en",
    "DO": "es",
    "DZ": "ar",
    "EC": "es",
    "EE": "et",
    "EG": "ar",
    "EH": "ar",
    "ER": "ti",
    "ES": "es",
    "ET": "am",
    "FI": "fi",
    "FJ": "en",
    "FK": "en",
    "FM": "en",
    "FO": "fo",
    "FR": "fr",
    "GA": "fr",
    "GB": "en",
    "GD": "en",
    "GE": "ka",
    "GF": "fr",
    "GG": "en",
    "GH": "ak",
    "GI": "en",
    "GL": "kl",
    "GM": "en",
    "GN": "fr",
    "GP": "fr",
    "GQ": "es",
    "GR": "el",
    "GT": "es",
    "GU": "en",
    "GW": "pt",
    "GY": "en",
    "HK": "zh",
    "HN": "es",
    "HR": "hr",
    "HT": "ht",
    "HU": "hu",
    "ID": "id",
    "IE": "en",
    "IL": "he",
    "IM": "en",
    "IN": "hi",
    "IO": "en",
    "IQ": "ar",
    "IR": "fa",
    "IS": "is",
    "IT": "it",
    "JE": "en",
    "JM": "en",
    "JO": "ar",
    "JP": "ja",
    "KE": "sw",
    "KG": "ky",
    "KH": "km",
    "KI": "en",
    "KM": "ar",
    "KN": "en",
    "KP": "ko",
    "KR": "ko",
    "KW": "ar",
    "KY": "en",
    "KZ": "ru",
    "LA": "lo",
    "LB": "ar",
    "LC": "en",
    "LI": "de",
    "LK": "si",
    "LR": "en",
    "LS": "st",
    "LT": "lt",
    "LU": "fr",
    "LV": "lv",
    "LY": "ar",
    "MA": "ar",
    "MC": "fr",
    "MD": "ro",
    "ME": "sr",
    "MF": "fr",
    "MG": "mg",
    "MH": "en",
    "MK": "mk",
    "ML": "bm",
    "MM": "my",
    "MN": "mn",
    "MO": "zh",
    "MP": "en",
    "MQ": "fr",
    "MR": "ar",
    "MS": "en",
    "MT": "mt",
    "MU": "fr",
    "MV": "dv",
    "MW": "en",
    "MX": "es",
    "MY": "ms",
    "MZ": "pt",
    "NA": "af",
    "NC": "fr",
    "NE": "ha",
    "NF": "en",
    "NG": "en",
    "NI": "es",
    "NL": "nl",
    "NO": "nb",
    "NP": "ne",
    "NR": "en",
    "NU": "en",
    "NZ": "en",
    "OM": "ar",
    "PA": "es",
    "PE": "es",
    "PF": "fr",
    "PG": "en",
    "PH": "en",
    "PK": "ur",
    "PL": "pl",
    "PM": "fr",
    "PR": "es",
    "PS": "ar",
    "PT": "pt",
    "PW": "en",
    "PY": "gn",
    "QA": "ar",
    "RE": "fr",
    "RO": "ro",
    "RS": "sr",
    "RU": "ru",
    "RW": "rw",
    "SA": "ar",
    "SB": "en",
    "SC": "fr",
    "SD": "ar",
    "SE": "sv",
    "SG": "en",
    "SH": "en",
    "SI": "sl",
    "SJ": "nb",
    "SK": "sk",
    "SL": "en",
    "SM": "it",
    "SN": "wo",
    "SO": "so",
    "SR": "nl",
    "SS": "ar",
    "ST": "pt",
    "SV": "es",
    "SX": "en",
    "SY": "ar",
    "SZ": "en",
    "TA": "en",
    "TC": "en",
    "TD": "ar",
    "TG": "fr",
    "TH": "th",
    "TJ": "tg",
    "TK": "en",
    "TL": "pt",
    "TM": "tk",
    "TN": "ar",
    "TO": "to",
    "TR": "tr",
    "TT": "en",
    "TV": "en",
    "TW": "zh",
    "TZ": "sw",
    "UA": "uk",
    "UG": "sw",
    "US": "en",
    "UY": "es",
    "UZ": "uz",
    "VA": "it",
    "VC": "en",
    "VE": "es",
    "VG": "en",
    "VI": "en",
    "VN": "vi",
    "VU": "bi",
    "WF": "fr",
    "WS": "sm",
    "XK": "sq",
    "YE": "ar",
    "YT": "fr",
    "ZA": "en",
    "ZM": "en",
    "ZW": "sn",
}


def language_pair(phone: str | None) -> tuple[str, str]:
    """``(lang_code, system_lang_code)`` for a phone number's country.

    Telegram sees both fields on every connection. Drawing them independently
    let one account announce ``lang_code="en"`` beside
    ``system_lang_code="ko-KR"`` on a Russian number, which no real Telegram
    Desktop install does. Here the region follows the phone and the language is
    this table's entry for it, so the two cannot contradict each other.

    ``LANGUAGE_BY_COUNTRY`` covers every region ``phonenumbers`` can return, so
    ``FALLBACK_TAG`` now means "no phone yet" rather than "unmapped country":
    the import paths mint a fingerprint before any connection has told us the
    number (``core.repositories._accounts_session_check`` corrects the language
    once, on the first check that learns one). The other way in is a
    non-geographic number — ``+800``, ``+882`` — whose region code is ``001``,
    which is not a territory and has no locale to speak of.
    """
    country = country_for_phone(phone) or ""
    lang = LANGUAGE_BY_COUNTRY.get(country)
    return (lang, f"{lang}-{country}") if lang else ("en", FALLBACK_TAG)
