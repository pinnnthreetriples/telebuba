"""The seller's session JSON parser — real key set, FAKE values only."""

from __future__ import annotations

import json

import pytest

from core.session_json import SessionJsonError, fingerprint_from_meta, parse_seller_json

FAKE_PASSWORD = "fake-pass-1234567"


def _doc(**overrides: object) -> bytes:
    data: dict[str, object] = {
        "twoFA": FAKE_PASSWORD,
        "device": "Samsung Galaxy S23",
        "sdk": "Android 14",
        "app_version": "10.9.1",
        "lang_code": "ru",
        "system_lang_code": "ru-RU",
        "phone": "79990001122",
        "id": 1234567,
        "unused_key": "ignored",
    }
    data.update(overrides)
    return json.dumps(data).encode()


def test_real_seller_keys_are_read() -> None:
    meta = parse_seller_json(_doc())

    assert meta.password == FAKE_PASSWORD
    assert meta.device_model == "Samsung Galaxy S23"
    assert meta.system_version == "Android 14"
    assert meta.app_version == "10.9.1"
    assert (meta.lang_code, meta.system_lang_code) == ("ru", "ru-RU")
    assert meta.phone == "79990001122"
    assert meta.user_id == 1234567
    assert meta.is_android is True


def test_the_password_is_not_in_repr() -> None:
    meta = parse_seller_json(_doc())

    assert FAKE_PASSWORD not in repr(meta)
    assert FAKE_PASSWORD not in str(meta)


@pytest.mark.parametrize("key", ["password", "2fa", "twofa"])
def test_password_synonyms(key: str) -> None:
    meta = parse_seller_json(_doc(twoFA=None, **{key: FAKE_PASSWORD}))

    assert meta.password == FAKE_PASSWORD


def test_other_synonyms_and_phone_normalised_to_digits() -> None:
    raw = json.dumps(
        {
            "device_model": "Pixel 8",
            "system_version": "Android 14",
            "app_version": "10.9.1",
            "lang_pack": "en",
            "system_lang_pack": "en-US",
            "phone": "+7 (999) 000-11-22",
            "id": "42",
        },
    ).encode()

    meta = parse_seller_json(raw)

    assert meta.device_model == "Pixel 8"
    assert meta.system_version == "Android 14"
    assert (meta.lang_code, meta.system_lang_code) == ("en", "en-US")
    assert meta.phone == "79990001122"
    assert meta.user_id == 42
    assert meta.password is None


def test_unknown_keys_and_blank_values_are_ignored() -> None:
    meta = parse_seller_json(_doc(twoFA="  ", device="", id=True))

    assert meta.password is None
    assert meta.device_model is None
    assert meta.user_id is None


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"not json", id="garbage"),
        pytest.param(b"[1, 2]", id="array"),
        pytest.param(b'"text"', id="string"),
        pytest.param(bytes([0xFF, 0xFE, 0x00]), id="not-utf8"),
        pytest.param(b"x" * (65 * 1024), id="oversized"),
    ],
)
def test_bad_json_is_refused_with_the_code(content: bytes) -> None:
    with pytest.raises(SessionJsonError) as excinfo:
        parse_seller_json(content)

    assert str(excinfo.value) == "session_json_invalid"


def test_a_utf8_bom_is_tolerated() -> None:
    assert parse_seller_json(b"\xef\xbb\xbf" + _doc()).password == FAKE_PASSWORD


def test_an_android_seller_becomes_a_verbatim_mobile_fingerprint() -> None:
    meta = parse_seller_json(_doc())

    fingerprint = fingerprint_from_meta("79990001122", meta)

    assert fingerprint is not None
    assert fingerprint.platform == "android"
    assert fingerprint.device_type == "mobile"
    assert (fingerprint.device_model, fingerprint.system_version) == (
        "Samsung Galaxy S23",
        "Android 14",
    )
    assert fingerprint.app_version == "10.9.1"
    assert (fingerprint.lang_code, fingerprint.system_lang_code) == ("ru", "ru-RU")


def test_missing_language_falls_back_to_the_phone_country() -> None:
    meta = parse_seller_json(_doc(lang_code=None, system_lang_code=None))

    fingerprint = fingerprint_from_meta("79990001122", meta)

    assert fingerprint is not None
    assert fingerprint.system_lang_code.endswith("RU")


@pytest.mark.parametrize(
    "overrides",
    [{"sdk": "iOS 17"}, {"sdk": None}, {"device": None}, {"app_version": None}],
)
def test_no_fingerprint_unless_android_and_complete(overrides: dict[str, object]) -> None:
    assert fingerprint_from_meta("1", parse_seller_json(_doc(**overrides))) is None
