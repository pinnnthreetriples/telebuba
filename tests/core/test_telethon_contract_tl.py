"""Telethon contract: TL request/type fields, helper mirrors and scans of our source."""

from __future__ import annotations

import ast
import importlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from telethon import TelegramClient, helpers, password, utils
from telethon.tl import functions, types

from core.telegram_client._read_post_image import _still_byte_count
from tests.core._telethon_contract_support import ROOT, open_clients, session_of, signature

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Callable[..., TelegramClient]]:
    yield from open_clients(tmp_path)


# ---------------------------------------------------------------------------------------
# TL requests and types built or read in custody flows
# ---------------------------------------------------------------------------------------

_REQUESTS: tuple[tuple[Callable[..., Any], dict[str, Any]], ...] = (
    (functions.auth.AcceptLoginTokenRequest, {"token": b"t"}),
    (functions.account.ChangeAuthorizationSettingsRequest, {"hash": 5, "confirmed": True}),
    (functions.account.ResetAuthorizationRequest, {"hash": 5}),
    (functions.auth.LogOutRequest, {}),
    (functions.account.GetAuthorizationsRequest, {}),
    (functions.updates.GetStateRequest, {}),
    (functions.help.GetAppConfigRequest, {"hash": 0}),
    (functions.account.GetPasswordRequest, {}),
    (functions.account.GetPasswordSettingsRequest, {"password": types.InputCheckPasswordEmpty()}),
    (functions.account.ConfirmPasswordEmailRequest, {"code": "123456"}),
    (functions.account.ResendPasswordEmailRequest, {}),
    (functions.account.CancelPasswordEmailRequest, {}),
    (
        functions.account.UpdatePasswordSettingsRequest,
        {
            "password": types.InputCheckPasswordEmpty(),
            "new_settings": types.account.PasswordInputSettings(email="a@example.org"),
        },
    ),
    (
        types.account.PasswordInputSettings,
        {
            "new_algo": types.PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow(
                salt1=b"a", salt2=b"b", g=3, p=b"p"
            ),
            "new_password_hash": b"h",
            "hint": "h",
        },
    ),
    (
        functions.InitConnectionRequest,
        {
            "api_id": 1,
            "device_model": "m",
            "system_version": "s",
            "app_version": "a",
            "system_lang_code": "en",
            "lang_pack": "android",
            "lang_code": "en",
            "query": functions.help.GetConfigRequest(),
        },
    ),
)


@pytest.mark.parametrize(("cls", "kwargs"), _REQUESTS, ids=[cls.__name__ for cls, _ in _REQUESTS])
def test_custody_requests_accept_the_keywords_we_pass(
    cls: Callable[..., Any], kwargs: dict[str, Any]
) -> None:
    built = cls(**kwargs)
    assert isinstance(bytes(built), bytes)
    for name, value in kwargs.items():
        assert getattr(built, name) == value
    assert set(kwargs) <= set(signature(cls.__init__)) or not kwargs


def test_the_authorization_attributes_the_audit_reads() -> None:
    read = {
        "hash", "current", "official_app", "unconfirmed", "password_pending", "device_model",
        "platform", "system_version", "api_id", "app_name", "app_version", "date_created",
        "date_active", "ip", "country", "region",
    }  # fmt: skip
    assert read <= set(signature(types.Authorization.__init__))
    created = datetime(2026, 1, 2, tzinfo=UTC)
    authorization = types.Authorization(
        hash=77, device_model="d", platform="p", system_version="s", api_id=1, app_name="n",
        app_version="v", date_created=created, date_active=created, ip="1.2.3.4",
        country="c", region="r", current=True, official_app=True, unconfirmed=True,
    )  # fmt: skip
    assert (authorization.hash, authorization.unconfirmed, authorization.date_created) == (
        77,
        True,
        created,
    )
    listing = types.account.Authorizations(
        authorization_ttl_days=180, authorizations=[authorization]
    )
    assert (listing.authorization_ttl_days, listing.authorizations) == (180, [authorization])


def _password_state(**fields: Any) -> types.account.Password:
    return types.account.Password(new_algo=None, new_secure_algo=None, secure_random=b"", **fields)  # ty: ignore[invalid-argument-type]


def test_the_password_types_and_srp_helpers() -> None:
    read = {
        "has_password",
        "hint",
        "has_recovery",
        "pending_reset_date",
        "email_unconfirmed_pattern",
    }
    assert read | {"current_algo", "new_algo", "srp_B", "srp_id"} <= set(
        signature(types.account.Password.__init__)
    )
    reset = datetime(2026, 1, 2, tzinfo=UTC)
    state = _password_state(
        has_password=True, hint="h", pending_reset_date=reset, email_unconfirmed_pattern="a***@b.c"
    )
    assert (state.has_password, state.hint, state.pending_reset_date) == (True, "h", reset)
    algo_class = types.PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow
    assert signature(algo_class.__init__)[1:] == ["salt1", "salt2", "g", "p"]
    algo = algo_class(salt1=b"a", salt2=b"b", g=3, p=b"p")
    algo.salt1 += b"more"  # what the password change does to extend the salt
    assert algo.salt1 == b"amore"
    assert signature(password.compute_check) == ["request", "password"]
    assert signature(password.compute_digest) == ["algo", "password"]
    # ``compute_check`` opens with ``request.current_algo`` and raises AttributeError when
    # the optional field is missing entirely: ``_srp`` turns that into a stable code.
    assert _password_state().current_algo is None


def test_the_passkey_types() -> None:
    data = types.DataJSON(data="{}")
    login = types.InputPasskeyResponseLogin(
        client_data=data, authenticator_data=b"a", signature=b"s", user_handle="2:1"
    )
    register = types.InputPasskeyResponseRegister(client_data=data, attestation_data=b"x")
    for response in (login, register):
        credential = types.InputPasskeyCredentialPublicKey(id="i", raw_id="i", response=response)
        assert bytes(functions.account.RegisterPasskeyRequest(credential=credential))
    assert bytes(functions.auth.InitPasskeyLoginRequest(api_id=1, api_hash="h"))
    credential = types.InputPasskeyCredentialPublicKey(id="i", raw_id="i", response=login)
    assert bytes(functions.auth.FinishPasskeyLoginRequest(credential=credential))
    assert bytes(
        functions.auth.FinishPasskeyLoginRequest(
            credential=credential, from_dc_id=2, from_auth_key_id=-5
        )
    )
    assert bytes(functions.account.DeletePasskeyRequest(id="i"))
    assert bytes(functions.account.GetPasskeysRequest())
    assert bytes(functions.account.InitPasskeyRegistrationRequest())
    assert {"id"} <= set(signature(types.Passkey.__init__))
    assert signature(types.account.Passkeys.__init__)[1:] == ["passkeys"]
    assert signature(types.account.PasskeyRegistrationOptions.__init__)[1:] == ["options"]
    assert signature(types.auth.PasskeyLoginOptions.__init__)[1:] == ["options"]


def test_the_photo_size_weight_matches_telethons_private_one() -> None:
    sizes = (
        types.PhotoSize("x", 1, 1, 123),
        types.PhotoCachedSize("x", 1, 1, b"abcd"),
        types.PhotoStrippedSize("x", b"ab"),
        types.PhotoSizeProgressive("x", 1, 1, [1, 5, 3]),
    )
    for size in sizes:
        assert _still_byte_count(size) == utils._photo_size_byte_count(size)
    assert _still_byte_count(types.PhotoPathSize("x", b"abc")) is None


def test_the_surrogate_helpers_count_utf16_units() -> None:
    text = "a\U0001f600b"
    surrogate = helpers.add_surrogate(text)
    assert len(surrogate) == 4
    assert helpers.del_surrogate(surrogate[1:3]) == "\U0001f600"


# ---------------------------------------------------------------------------------------
# scans of the production source: the list above cannot silently grow
# ---------------------------------------------------------------------------------------

_SOURCE_DIRS = ("api", "core", "services", "tools")
# What is watched for private (single leading underscore) attributes, by two rules:
# 1. a name Telethon's ``TelegramClient`` or its ``SQLiteSession`` has (``dir``/``vars`` of a
#    real client and session), read on ANY receiver but our own ``self``/``cls``;
# 2. ANY ``._name`` read on a receiver that is a Telethon object our code handles: a variable
#    called like one (``_TELETHON_RECEIVERS``) or assigned from one (simple assignment and
#    ``for`` tracking, see ``_telethon_names``). ``self._x``/``cls._x`` is our own state.
# Not watched: objects reached through a call chain or subscript (``f()._x``,
# ``items[0]._x``), private names on a variable the tracking cannot trace, ``getattr`` with a
# built name. Files: api/ core/ services/ tools/.
_PRIVATE_ALLOWED = {
    ("core/telegram_client/_client.py", "_init_request"),
    ("core/telegram_client/_guarded_client.py", "_call"),
    ("core/telegram_client/_guarded_client.py", "_updates_error"),
    ("core/telegram_client/_passkey_login.py", "_switch_dc"),
    ("core/telegram_client/_pool_live.py", "_authorized"),
}
# Modules imported from telethon that are neither the top package, ``errors``, ``events``
# nor a TL namespace. Each is a deliberate deep import listed in the inventory.
_DEEP_IMPORTS_ALLOWED = {"telethon.password", "telethon.helpers", "telethon.tl.tlobject"}
_TL_PREFIXES = ("telethon.tl.functions", "telethon.tl.types")
# Rule 2 hits that are our own objects: ``CdpSession`` (not a Telethon session) is built in a
# classmethod as ``session = cls(...)`` and its own private pump is set on it.
_NOT_TELETHON = {
    ("core/web_login/_cdp.py", "_pump"),
    ("core/web_login/_cdp.py", "_route"),
}
_TELETHON_RECEIVERS = {
    "sender",
    "message",
    "msg",
    "event",
    "authorization",
    "authorizations",
    "session",
    "client",
    "tg_client",
}
_PRIVATE_PACKAGES = {"client", "network", "sessions", "crypto", "extensions"}


def _production_sources() -> Iterator[tuple[str, ast.Module]]:
    for directory in _SOURCE_DIRS:
        for path in sorted((ROOT / directory).rglob("*.py")):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            yield path.relative_to(ROOT).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _private_telethon_names(client: TelegramClient) -> set[str]:
    session = session_of(client)
    names = set(dir(TelegramClient)) | set(vars(client)) | set(dir(session)) | set(vars(session))
    return {n for n in names if n.startswith("_") and not n.startswith("__")}


def _is_deep_import(module: str) -> bool:
    """A ``telethon`` module that is not the package, ``errors``, ``events`` or a TL namespace."""
    if module.split(".", maxsplit=1)[0] != "telethon":
        return False
    public = {"telethon", "telethon.errors", "telethon.events", "telethon.tl"}
    return module not in public and not module.startswith(_TL_PREFIXES)


def _root(node: ast.expr) -> ast.expr:
    """The expression a chain of attributes, calls, awaits and subscripts starts from."""
    while isinstance(node, ast.Attribute | ast.Call | ast.Await | ast.Subscript):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node


def _telethon_names(tree: ast.Module) -> set[str]:
    """Names called like a Telethon object, plus names assigned (or looped) from one."""
    names = set(_TELETHON_RECEIVERS)
    grew = True
    while grew:
        grew = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                source = node.value
            elif isinstance(node, ast.For | ast.AsyncFor):
                targets, source = [node.target], node.iter
            else:
                continue
            root = _root(source)
            if isinstance(root, ast.Name) and root.id in names:
                fresh = {t.id for t in targets if isinstance(t, ast.Name)} - names
                names |= fresh
                grew = grew or bool(fresh)
    return names


def _receiver_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else None


def _private_uses(rel: str, tree: ast.Module, private: set[str]) -> set[tuple[str, str]]:
    """``(file, name)`` for every private Telethon name touched, by the two rules above."""
    subclass_nodes = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any("TelegramClient" in ast.unparse(base) for base in node.bases)
        for inner in ast.walk(node)
    }
    handled = _telethon_names(tree)
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("_")
            and not node.attr.startswith("__")
        ):
            own = ast.unparse(node.value) in {"self", "cls"} and id(node) not in subclass_nodes
            on_telethon_object = _receiver_name(node.value) in handled
            if (
                not own
                and (node.attr in private or on_telethon_object)
                and (rel, node.attr) not in _NOT_TELETHON
            ):
                found.add((rel, node.attr))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "setattr", "hasattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in private
        ):
            found.add((rel, str(node.args[1].value)))
    return found


def _deep_imports(tree: ast.Module) -> set[str]:
    """Deep ``telethon.x`` modules imported by ``from`` or plain ``import`` (aliased or not)."""
    deep: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            deep |= {a.name for a in node.names if _is_deep_import(a.name)}
        elif isinstance(node, ast.ImportFrom) and node.module:
            if _is_deep_import(node.module):
                deep.add(node.module)
            # ``from telethon import client`` (a mixin package) or ``_x`` is as deep as a path.
            if node.module == "telethon":
                deep |= {
                    f"telethon.{a.name}"
                    for a in node.names
                    if a.name.startswith("_") or a.name in _PRIVATE_PACKAGES
                }
    return deep


def test_no_new_private_telethon_attribute_is_used(
    make_client: Callable[..., TelegramClient],
) -> None:
    private = _private_telethon_names(make_client())
    found: set[tuple[str, str]] = set()
    deep: set[str] = set()
    for rel, tree in _production_sources():
        found |= _private_uses(rel, tree, private)
        deep |= _deep_imports(tree)
    assert found == _PRIVATE_ALLOWED, "a Telethon-private attribute was added or dropped"
    assert deep == _DEEP_IMPORTS_ALLOWED, "a private or deep Telethon import changed"


_ALIAS_SOURCES = {
    "telethon": {"errors", "events", "hints", "utils", "helpers", "password"},
    "telethon.tl": {"functions", "types"},
}


def _import_aliases(rel: str, tree: ast.Module, unresolved: list[str]) -> dict[str, Any]:
    """Resolve every ``from telethon... import X``; the namespaces usable as ``X.name``."""
    aliases: dict[str, Any] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.module):
            continue
        if node.module.split(".")[0] != "telethon":
            continue
        try:
            module = importlib.import_module(node.module)
        except ImportError:
            unresolved.append(f"{rel}: import {node.module}")
            continue
        for imported in node.names:
            try:
                value = getattr(module, imported.name, None) or importlib.import_module(
                    f"{node.module}.{imported.name}"
                )
            except ImportError:
                unresolved.append(f"{rel}: {node.module}.{imported.name}")
                continue
            if imported.name in _ALIAS_SOURCES.get(node.module, ()):
                aliases[imported.asname or imported.name] = value
    return aliases


def _unresolved_chains(rel: str, tree: ast.Module, aliases: dict[str, Any]) -> list[str]:
    """``errors.X`` / ``functions.a.B`` style references that name nothing in Telethon."""
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain = [node.attr]
        base = node.value
        while isinstance(base, ast.Attribute):
            chain.append(base.attr)
            base = base.value
        if not (isinstance(base, ast.Name) and base.id in aliases):
            continue
        value = aliases[base.id]
        for part in reversed(chain):
            if not hasattr(value, part):
                missing.append(f"{rel}: {base.id}.{'.'.join(reversed(chain))}")
                break
            value = getattr(value, part)
    return missing


def test_every_telethon_name_the_code_uses_resolves() -> None:
    unresolved: list[str] = []
    for rel, tree in _production_sources():
        aliases = _import_aliases(rel, tree, unresolved)
        unresolved += _unresolved_chains(rel, tree, aliases)
    assert not unresolved, sorted(set(unresolved))
