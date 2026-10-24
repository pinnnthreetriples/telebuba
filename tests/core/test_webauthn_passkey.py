"""WebAuthn bytes for a vault-held passkey: CBOR, the attestation object, the assertion."""

from __future__ import annotations

import hashlib
import json
import struct

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from core.webauthn_passkey import (
    ORIGIN,
    RP_ID,
    b64url,
    b64url_decode,
    build_assertion,
    build_registration,
    cbor_encode,
    credential_id_for,
)


def _pem(key: ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


@pytest.mark.parametrize(
    ("value", "encoded"),
    [
        (0, b"\x00"),
        (23, b"\x17"),
        (24, b"\x18\x18"),
        (255, b"\x18\xff"),
        (256, b"\x19\x01\x00"),
        (-1, b"\x20"),
        (-7, b"\x26"),
        (b"\x01\x02", b"\x42\x01\x02"),
        ("fmt", b"\x63fmt"),
        ({}, b"\xa0"),
        ({1: 2}, b"\xa1\x01\x02"),
    ],
)
def test_cbor_encodes_the_webauthn_item_kinds(value: object, encoded: bytes) -> None:
    assert cbor_encode(value) == encoded  # ty: ignore[invalid-argument-type]


def test_cbor_refuses_an_item_longer_than_two_length_bytes() -> None:
    with pytest.raises(ValueError, match="too long"):
        cbor_encode(bytes(0x10000))


def test_base64url_round_trips_without_padding() -> None:
    raw = b"\xfb\xff\x00telebuba"
    text = b64url(raw)
    assert "=" not in text
    assert "+" not in text
    assert b64url_decode(text) == raw


def test_a_credential_id_is_sixteen_bytes_fixed_by_the_public_key() -> None:
    key, other = (
        _pem(ec.generate_private_key(ec.SECP256R1())),
        _pem(ec.generate_private_key(ec.SECP256R1())),
    )
    assert len(b64url_decode(credential_id_for(key))) == 16
    assert credential_id_for(key) == credential_id_for(key)
    assert credential_id_for(key) != credential_id_for(other)


def test_the_registration_carries_the_public_key_and_credential_id() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    credential_id = credential_id_for(_pem(key))
    registration = build_registration("Y2hhbGxlbmdl", _pem(key), credential_id)

    client_data = json.loads(registration.client_data_json)
    assert client_data == {
        "type": "webauthn.create",
        "challenge": "Y2hhbGxlbmdl",
        "origin": ORIGIN,
        "crossOrigin": False,
    }
    # {"fmt": "none", "attStmt": {}, "authData": <bytes>} — authData is the tail.
    attestation = registration.attestation_object
    assert attestation.startswith(b"\xa3\x63fmt\x64none\x67attStmt\xa0\x68authData")
    head = attestation.index(b"authData") + len(b"authData")
    assert attestation[head] == 0x58  # a byte string with a one-byte length
    auth_data = attestation[head + 2 :]
    assert len(auth_data) == attestation[head + 1]
    assert auth_data[:32] == hashlib.sha256(RP_ID.encode()).digest()
    assert auth_data[32] == 0x45  # user present + verified + attested credential data
    assert auth_data[33:37] == b"\x00\x00\x00\x00"
    assert auth_data[37:53] == bytes(16)
    (length,) = struct.unpack(">H", auth_data[53:55])
    assert auth_data[55 : 55 + length] == b64url_decode(credential_id)
    numbers = key.public_key().public_numbers()
    cose = auth_data[55 + length :]
    assert numbers.x.to_bytes(32, "big") in cose
    assert numbers.y.to_bytes(32, "big") in cose
    assert cose.startswith(b"\xa5\x01\x02\x03\x26\x20\x01")


def test_a_registration_refuses_a_key_that_is_not_p256() -> None:
    with pytest.raises(TypeError, match="P-256"):
        build_registration("c", _pem(ed25519.Ed25519PrivateKey.generate()), "Y3JlZA")
    with pytest.raises(TypeError, match="P-256"):
        build_registration("c", _pem(ec.generate_private_key(ec.SECP384R1())), "Y3JlZA")


def test_the_assertion_payload_is_what_a_verifier_checks() -> None:
    assertion = build_assertion("bG9naW4")
    client_data = json.loads(assertion.client_data_json)
    assert client_data["type"] == "webauthn.get"
    assert client_data["challenge"] == "bG9naW4"
    assert client_data["origin"] == ORIGIN
    auth_data = assertion.authenticator_data
    assert auth_data == hashlib.sha256(RP_ID.encode()).digest() + b"\x05" + bytes(4)
    assert assertion.signed_payload == (
        auth_data + hashlib.sha256(assertion.client_data_json.encode()).digest()
    )

    key = ec.generate_private_key(ec.SECP256R1())
    signature = key.sign(assertion.signed_payload, ec.ECDSA(hashes.SHA256()))
    # What Telegram does with the stored public key: verify over the same bytes.
    key.public_key().verify(signature, assertion.signed_payload, ec.ECDSA(hashes.SHA256()))
    with pytest.raises(InvalidSignature):
        key.public_key().verify(signature, auth_data, ec.ECDSA(hashes.SHA256()))
