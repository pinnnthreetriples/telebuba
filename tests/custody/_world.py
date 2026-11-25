"""A fake Telegram server, real session files and a failure injector for the custody matrix.

The server is a set of authorized auth keys; a key a flow creates is authorized by the fake
call that Telegram would act on (accept a login token, sign in, ...), ended by a logout. Every
session file is a real SQLite file in Telethon's schema, so what a file holds is read, never
assumed. Production code runs unmodified except for the seams the flows already have (the
client factory, the pool's ``get_client``) and thin pass-through wrappers that name a step.

A step is one awaited Telegram call, file operation or journal write. A :class:`Fault` picks
one step and one way for it to go wrong:

* ``raised``: the step raises (``ConnectionError`` for Telegram, ``OSError`` for files and the
  database). ``landed`` says whether the effect had taken place when the answer was lost.
* ``cancelled``: the flow's task is cancelled at the step, again before or after the effect.
* ``died``: the process died right after the step. The flow is let run on (it may do
  anything), and when it ends the world is put back exactly as it was at that instant: files,
  database rows and the server's authorizations. Nothing after the step happened.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal

from telethon import errors
from telethon.tl.functions.account import GetAuthorizationsRequest
from telethon.tl.functions.auth import AcceptLoginTokenRequest, LogOutRequest

from core.config import settings
from core.db import _get_engine
from tests.session_files import write_session_file

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pytest

Kind = Literal["raised", "cancelled", "died"]

USER_ID = 4242
PHONE = "+5215550001111"
OLD = b"old-auth-key"
_EPOCH = datetime(2026, 11, 7, 13, 0, tzinfo=UTC)
# Steps whose failure is a file or database fault, not a dropped connection.
_IO_PREFIXES = ("file.", "journal.", "db.", "svc.", "gateway.", "vault.")
# How long a worker thread waits for the loop to deliver the cancellation it asked for.
_CANCEL_WAIT_SECONDS = 30.0


@dataclass(frozen=True)
class Fault:
    """One way one step goes wrong; ``landed``: the effect happened before the answer was lost."""

    step: str
    kind: Kind
    landed: bool = False

    @property
    def label(self) -> str:
        effect = "died" if self.kind == "died" else ("answer_lost" if self.landed else "no_effect")
        return f"{self.step}/{effect}/{self.kind}"


@dataclass
class Server:
    """What Telegram holds: the authorized keys, and which of them a flow made."""

    live: set[bytes] = field(default_factory=set)
    created: list[bytes] = field(default_factory=list)
    hashes: dict[bytes, int] = field(default_factory=dict)
    born: dict[bytes, datetime] = field(default_factory=dict)
    # The API id a key was last signed in with (``None``: the default one).
    api_of: dict[bytes, int | None] = field(default_factory=dict)

    def seed(self, key: bytes, hash_: int) -> None:
        self.live.add(key)
        self.hashes[key] = hash_
        self.born[key] = _EPOCH

    def authorize(self, key: bytes) -> None:
        if key not in self.hashes:
            self.hashes[key] = 200 + len(self.hashes)
            self.born[key] = _EPOCH + timedelta(minutes=len(self.hashes))
        self.live.add(key)
        if key not in self.created:
            self.created.append(key)

    def copy(self) -> Server:
        return Server(
            set(self.live),
            list(self.created),
            dict(self.hashes),
            dict(self.born),
            dict(self.api_of),
        )


def read_key(path: Path) -> bytes | None:
    """The auth key a session file holds; ``None`` for a missing, keyless or unreadable file."""
    if not path.is_file():
        return None
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            row = db.execute("select auth_key from sessions").fetchone()
    except sqlite3.Error:
        return None
    return bytes(row[0]) if row and row[0] else None


def store_key(path: Path, key: bytes) -> None:
    """What Telethon does on connect: a key lands in the file's ``sessions`` table."""
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("delete from sessions")
        db.execute("insert into sessions values (2, '149.154.167.51', 443, ?, null)", (key,))


class Client:
    """A Telethon client of one role, bound to one session file (or none: a pooled approver)."""

    def __init__(
        self,
        world: World,
        path: Path | None,
        role: str,
        key: bytes | None = None,
        api_id: int | None = None,
    ) -> None:
        self.w = world
        self.api_id = api_id
        self.path = path
        self.role = role
        self.connected = False
        if path is not None and not path.exists():
            write_session_file(path, key=None)  # Telethon builds the file with the client
        found = key if path is None else read_key(path)
        self.session = SimpleNamespace(auth_key=SimpleNamespace(key=found))

    @property
    def key(self) -> bytes | None:
        return self.session.auth_key.key

    def is_connected(self) -> bool:
        return self.connected

    def _need_live_key(self) -> bytes:
        if self.key is None or self.key not in self.w.server.live:
            raise errors.AuthKeyUnregisteredError(request=None)
        return self.key

    async def connect(self) -> None:
        def _effect() -> None:
            self.connected = True
            if self.key is None:  # a keyless file: Telethon makes a fresh, unregistered key
                self.session.auth_key.key = self.w.mint_key()
                if self.path is not None:
                    store_key(self.path, self.session.auth_key.key)

        await self.w.step(f"{self.role}.connect", _effect)

    async def disconnect(self) -> None:
        await self.w.step(f"{self.role}.disconnect", lambda: setattr(self, "connected", False))

    async def get_me(self) -> SimpleNamespace:
        def _effect() -> SimpleNamespace:
            self._need_live_key()
            return SimpleNamespace(id=USER_ID, phone=PHONE[1:])

        return await self.w.step(f"{self.role}.get_me", _effect)

    async def log_out(self) -> bool:
        """Telethon's: ``False`` when Telegram refuses; on success the session file goes too."""

        def _effect() -> bool:
            if self.key is None or self.key not in self.w.server.live:
                return False
            self.w.server.live.discard(self.key)
            self.connected = False
            if self.path is not None:
                self.path.unlink(missing_ok=True)
            return True

        return await self.w.step(f"{self.role}.log_out", _effect)

    async def is_user_authorized(self) -> bool:
        return await self.w.step(
            f"{self.role}.is_authorized", lambda: self.key in self.w.server.live
        )

    async def send_code_request(self, _phone: str) -> SimpleNamespace:
        value = SimpleNamespace(phone_code_hash="hash-1", type=SimpleNamespace(length=5))
        return await self.w.step(f"{self.role}.send_code", lambda: value)

    async def sign_in(self, **_kwargs: object) -> None:
        def _effect() -> None:
            assert self.key is not None
            self.w.server.authorize(self.key)
            self.w.server.api_of[self.key] = self.api_id

        await self.w.step(f"{self.role}.sign_in", _effect)

    async def qr_login(self) -> SimpleNamespace:
        key = self.key

        async def wait(timeout: float) -> None:  # noqa: ASYNC109 - mirrors QRLogin.wait
            assert timeout > 0
            for _ in range(2000):
                if key in self.w.server.live:
                    return
                await asyncio.sleep(0)
            raise TimeoutError

        token = b"login:" + (key or b"")
        return await self.w.step(
            f"{self.role}.qr_login", lambda: SimpleNamespace(token=token, wait=wait)
        )

    def _listing(self) -> SimpleNamespace:
        me = self._need_live_key()
        server = self.w.server
        entries = [
            SimpleNamespace(
                hash=0 if key == me else server.hashes[key],
                current=key == me,
                date_created=server.born[key],
            )
            for key in sorted(server.live)
        ]
        return SimpleNamespace(authorization_ttl_days=180, authorizations=entries)

    def _accept(self, token: bytes) -> SimpleNamespace:
        self._need_live_key()
        key = token.removeprefix(b"login:")
        self.w.server.authorize(key)
        return SimpleNamespace(hash=self.w.server.hashes[key], unconfirmed=False)

    def _logout_request(self) -> bool:
        key = self._need_live_key()
        self.w.server.live.discard(key)
        return True

    async def __call__(self, request: object) -> object:
        if isinstance(request, GetAuthorizationsRequest):
            return await self.w.step(f"{self.role}.list", self._listing)
        if isinstance(request, AcceptLoginTokenRequest):
            return await self.w.step(
                f"{self.role}.accept_token", lambda: self._accept(request.token)
            )
        if isinstance(request, LogOutRequest):
            return await self.w.step(f"{self.role}.logout", self._logout_request)
        msg = f"the fake server does not know {type(request).__name__}"
        raise AssertionError(msg)


@dataclass
class _Frozen:
    dirs: dict[Path, Path | None]
    rows: dict[str, list[tuple[object, ...]]]
    server: Server


class World:
    """The fake server, the failure injector and the restart, for one account ``acc``."""

    def __init__(self, tmp_path: Path, roles: dict[str, str]) -> None:
        self.tmp = tmp_path
        self.roles = roles
        self.server = Server()
        self.faults: list[Fault] = []
        self.fired: list[Fault] = []
        self.trace: list[str] = []
        self.events: list[tuple[str, dict[str, object]]] = []
        self.phase = "setup"
        self.task: asyncio.Future[object] | None = None
        self.outcome: BaseException | None = None
        self.frozen: _Frozen | None = None
        self._minted = 0
        self._loop_thread = threading.get_ident()

    # --- keys, files, clients ---------------------------------------------------------------

    def mint_key(self) -> bytes:
        self._minted += 1
        return f"minted-auth-key-{self._minted}".encode()

    @property
    def sessions(self) -> Path:
        return settings.telegram.session_dir

    def file(self, name: str) -> Path:
        return self.sessions / f"{name}.session"

    def role_of(self, path: Path) -> str:
        if self.phase != "flow":
            return "leftover"
        return self.roles.get(path.name.removesuffix(".session"), "leftover")

    def factory(self, profile: object) -> Client:
        """``create_telegram_client`` for every module that builds one."""
        path = Path(f"{profile.session_path}.session")  # ty: ignore[unresolved-attribute]
        role = self.role_of(path)
        api_id = getattr(getattr(profile, "device", None), "api_id", None)
        return self.sync_step(f"{role}.build", lambda: Client(self, path, role, api_id=api_id))

    def pooled(self, role: str, name: str) -> Client:
        """A pool client: it holds the key its file held when it was borrowed."""
        return Client(self, None, role, key=read_key(self.file(name)))

    def disk_keys(self) -> dict[str, bytes]:
        """Every key on disk the repair can know of: sessions folder and quarantine."""
        found: dict[str, bytes] = {}
        for folder in (self.sessions, settings.session_vault.quarantine_dir):
            if folder.is_dir():
                for entry in sorted(folder.iterdir()):
                    key = read_key(entry)
                    if key is not None:
                        found[f"{folder.name}/{entry.name}"] = key
        return found

    # --- the injector -------------------------------------------------------------------------

    def _take(self, name: str) -> Fault | None:
        if self.phase == "repair" and not name.startswith("leftover."):
            return None
        if self.frozen is not None or self.phase not in {"flow", "repair"}:
            return None
        for fault in self.faults:
            if fault.step == name:
                self.faults.remove(fault)
                self.fired.append(fault)
                return fault
        return None

    def _error(self, name: str) -> Exception:
        return OSError(name) if name.startswith(_IO_PREFIXES) else ConnectionError(name)

    async def _interrupt(self, fault: Fault) -> None:
        if fault.kind == "raised":
            raise self._error(fault.step)
        task = self.task
        assert task is not None
        task.cancel()
        if asyncio.current_task() is task:
            await asyncio.sleep(0)
            msg = "the cancellation was not delivered"
            raise RuntimeError(msg)
        # A shielded task keeps going, as production's does: the flow alone is cancelled.

    async def step(self, name: str, effect: Callable[[], Any] | None = None) -> Any:
        """Run ``effect`` as the step ``name``, with whatever fault is armed for it."""
        fault = self._take(name)
        if fault is not None and fault.kind != "died" and not fault.landed:
            await self._interrupt(fault)
        result = effect() if effect is not None else None
        if isinstance(result, asyncio.Future) or asyncio.iscoroutine(result):
            result = await result
        if self.phase == "flow":
            self.trace.append(name)
        if fault is not None and fault.kind == "died":
            self.freeze()
        elif fault is not None:
            await self._interrupt(fault)
        return result

    def sync_step(self, name: str, effect: Callable[[], Any] | None = None) -> Any:
        """:meth:`step` for code that cannot await (a worker thread, or a plain factory)."""
        fault = self._take(name)
        on_loop = threading.get_ident() == self._loop_thread
        if fault is not None and fault.kind != "died" and not fault.landed:
            self._interrupt_sync(fault, on_loop=on_loop)
            return None
        result = effect() if effect is not None else None
        if self.phase == "flow":
            self.trace.append(name)
        if fault is not None and fault.kind == "died":
            self.freeze()
        elif fault is not None:
            self._interrupt_sync(fault, on_loop=on_loop)
        return result

    def _interrupt_sync(self, fault: Fault, *, on_loop: bool) -> None:
        if fault.kind == "raised":
            raise self._error(fault.step)
        if on_loop:
            raise asyncio.CancelledError
        # A worker thread cannot be cancelled: the file operation runs on, the awaiter is told.
        # The thread waits until the loop has cancelled the flow. A thread that returned first
        # could finish before the loop attached to its future: asyncio then completes the call
        # inside the awaiting step, the await never yields, and the flow runs on to its next
        # await as if this step had succeeded (after a copy that was never made).
        task = self.task
        assert task is not None
        told = threading.Event()

        def cancel() -> None:
            task.cancel()
            told.set()

        task.get_loop().call_soon_threadsafe(cancel)
        assert told.wait(_CANCEL_WAIT_SECONDS), "the cancellation was not delivered"

    # --- freezing the world at the instant of a death -------------------------------------------

    def _dirs(self) -> list[Path]:
        return [self.sessions, settings.session_vault.quarantine_dir, settings.session_vault.dir]

    def _tables(self, db: sqlite3.Connection) -> list[str]:
        rows = db.execute("select name from sqlite_master where type = 'table'").fetchall()
        return [name for (name,) in rows if name != "sqlite_stat1" and "_fts" not in name]

    def _db_path(self) -> str:
        return str(_get_engine().url.database)

    def freeze(self) -> None:
        if self.frozen is not None:
            return
        dirs: dict[Path, Path | None] = {}
        for index, folder in enumerate(self._dirs()):
            copy = self.tmp / "_frozen" / str(index) if folder.is_dir() else None
            if copy is not None:
                shutil.copytree(folder, copy)
            dirs[folder] = copy
        with closing(sqlite3.connect(self._db_path())) as db:
            rows = {
                t: db.execute(f'select * from "{t}"').fetchall()  # noqa: S608
                for t in self._tables(db)
            }
        self.frozen = _Frozen(dirs, rows, self.server.copy())

    def thaw(self) -> None:
        frozen = self.frozen
        assert frozen is not None
        for folder, copy in frozen.dirs.items():
            shutil.rmtree(folder, ignore_errors=True)
            if copy is not None:
                shutil.copytree(copy, folder)
        with closing(sqlite3.connect(self._db_path())) as db:
            for table, rows in frozen.rows.items():
                db.execute(f'delete from "{table}"')  # noqa: S608
                for row in rows:
                    marks = ",".join("?" * len(row))
                    db.execute(f'insert into "{table}" values ({marks})', row)  # noqa: S608
            db.commit()
        self.server = frozen.server
        self.frozen = None

    # --- running a flow, restarting, repairing ----------------------------------------------------

    async def run(self, flow: Callable[[], Awaitable[object]]) -> None:
        """Run ``flow`` with the armed faults; a death leaves the world as it was right then."""
        self._loop_thread = threading.get_ident()
        self.phase = "flow"
        self.task = asyncio.ensure_future(flow())
        await asyncio.wait({self.task})
        self.outcome = (
            asyncio.CancelledError() if self.task.cancelled() else self.task.exception() or None
        )
        self.phase = "repair"
        if self.frozen is not None:
            self.thaw()

    def log_stub(self) -> Callable[..., Awaitable[None]]:
        """A ``log_event`` that keeps what it is told, for the modules that write the journal."""

        async def _log(
            _level: str, event: str, account_id: str | None = None, extra: dict | None = None
        ) -> None:
            self.events.append((event, {"account_id": account_id, **(extra or {})}))

        return _log

    def event_names(self) -> list[str]:
        return [name for name, _ in self.events]

    def patch(self, monkeypatch: pytest.MonkeyPatch, module: object, **names: object) -> None:
        for attr, value in names.items():
            monkeypatch.setattr(module, attr, value)


def intent_rows() -> list[dict[str, object]]:
    """Every journal row, open or closed."""
    with _get_engine().connect() as connection:
        rows = connection.exec_driver_sql(
            "select * from account_key_intents order by id"
        ).mappings()
        return [dict(row) for row in rows]
