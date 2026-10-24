"""The WebAuthn bytes a Telegram passkey ceremony needs, built without a browser.

Pure functions, no I/O. Telegram's passkey API (``core.telegram.org/api/passkeys``)
takes the same structures a browser's ``navigator.credentials`` returns; here they
are assembled by hand for a key that lives in the vault:

* registration — ``clientDataJSON`` and an attestation object with format ``none``
  (no attestation signature, so only the public key is needed);
* login — ``authenticatorData`` plus ``clientDataJSON``, and the bytes the vault must
  sign: ``authenticatorData || SHA-256(clientDataJSON)``.

Telegram accepts only RP ID ``telegram.org``; the origin is the one its web app uses.
The flags claim user presence and verification, as a platform authenticator would.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from typing import NamedTuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

RP_ID = "telegram.org"
ORIGIN = "https://telegram.org"
_FLAG_USER_PRESENT = 0x01
_FLAG_USER_VERIFIED = 0x04
_FLAG_ATTESTED_DATA = 0x40
_CREDENTIAL_ID_BYTES = 16
_P256_COORDINATE_BYTES = 32
# CBOR major types (RFC 8949 §3.1).
_CBOR_UINT, _CBOR_NEGINT, _CBOR_BYTES, _CBOR_TEXT, _CBOR_MAP = 0, 1, 2, 3, 5
_CBOR_INLINE_MAX = 23
_CBOR_ONE_BYTE_MAX = 0xFF
_CBOR_TWO_BYTE_MAX = 0xFFFF

type CborValue = int | bytes | str | dict[int | str, CborValue]


class PasskeyRegistration(NamedTuple):
    client_data_json: str
    attestation_object: bytes


class PasskeyAssertion(NamedTuple):
    client_data_json: str
    authenticator_data: bytes
    # What the vault signs; the signature is the assertion's last field.
    signed_payload: bytes


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def credential_id_for(public_key_pem: str) -> str:
    """The credential id of a public key, base64url as Telegram's ``id``/``raw_id`` take it.

    Derived, not random: a create retried after Telegram's answer was lost reaches the
    same id, so it can find the passkey the first attempt registered instead of adding
    a second one nothing tracks.
    """
    public = serialization.load_pem_public_key(public_key_pem.encode())
    der = public.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return b64url(hashlib.sha256(der).digest()[:_CREDENTIAL_ID_BYTES])


def _cbor_head(major: int, length: int) -> bytes:
    if length <= _CBOR_INLINE_MAX:
        return bytes([major << 5 | length])
    if length <= _CBOR_ONE_BYTE_MAX:
        return bytes([major << 5 | 24, length])
    if length <= _CBOR_TWO_BYTE_MAX:
        return bytes([major << 5 | 25]) + struct.pack(">H", length)
    msg = "CBOR item too long for a WebAuthn structure"
    raise ValueError(msg)


def cbor_encode(value: CborValue) -> bytes:
    """Canonical CBOR for the four item kinds WebAuthn uses (ints, bytes, text, maps)."""
    if isinstance(value, int):
        return _cbor_head(_CBOR_UINT, value) if value >= 0 else _cbor_head(_CBOR_NEGINT, -1 - value)
    if isinstance(value, bytes):
        return _cbor_head(_CBOR_BYTES, len(value)) + value
    if isinstance(value, str):
        raw = value.encode()
        return _cbor_head(_CBOR_TEXT, len(raw)) + raw
    items = b"".join(cbor_encode(k) + cbor_encode(v) for k, v in value.items())
    return _cbor_head(_CBOR_MAP, len(value)) + items


def _cose_public_key(public_key_pem: str) -> bytes:
    key = serialization.load_pem_public_key(public_key_pem.encode())
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        msg = "passkey public key must be EC P-256"
        raise TypeError(msg)
    numbers = key.public_numbers()
    # COSE_Key: kty=EC2(2), alg=ES256(-7), crv=P-256(1), x, y.
    return cbor_encode(
        {
            1: 2,
            3: -7,
            -1: 1,
            -2: numbers.x.to_bytes(_P256_COORDINATE_BYTES, "big"),
            -3: numbers.y.to_bytes(_P256_COORDINATE_BYTES, "big"),
        }
    )


def _client_data(ceremony: str, challenge: str) -> str:
    return json.dumps(
        {"type": ceremony, "challenge": challenge, "origin": ORIGIN, "crossOrigin": False},
        separators=(",", ":"),
    )


def _rp_id_hash() -> bytes:
    return hashlib.sha256(RP_ID.encode()).digest()


def build_registration(
    challenge: str, public_key_pem: str, credential_id: str
) -> PasskeyRegistration:
    """``clientDataJSON`` + a ``none`` attestation object for ``account.registerPasskey``."""
    raw_id = b64url_decode(credential_id)
    auth_data = (
        _rp_id_hash()
        + bytes([_FLAG_USER_PRESENT | _FLAG_USER_VERIFIED | _FLAG_ATTESTED_DATA])
        + struct.pack(">I", 0)
        + bytes(16)  # AAGUID: none, like any authenticator that does not attest
        + struct.pack(">H", len(raw_id))
        + raw_id
        + _cose_public_key(public_key_pem)
    )
    attestation = cbor_encode({"fmt": "none", "attStmt": {}, "authData": auth_data})
    return PasskeyRegistration(_client_data("webauthn.create", challenge), attestation)


def build_assertion(challenge: str) -> PasskeyAssertion:
    """``authenticatorData`` + ``clientDataJSON`` for a login, and the payload to sign.

    The signature counter stays 0: the vault keeps no counter, and WebAuthn lets an
    authenticator that does not count report 0 every time.
    """
    auth_data = _rp_id_hash() + bytes([_FLAG_USER_PRESENT | _FLAG_USER_VERIFIED]) + bytes(4)
    client_data = _client_data("webauthn.get", challenge)
    signed = auth_data + hashlib.sha256(client_data.encode()).digest()
    return PasskeyAssertion(client_data, auth_data, signed)
