"""Answer WebK's «Log in by passkey» with the account's vault key, inside its own window.

WebK asks the browser for a passkey with ``navigator.credentials.get`` (RP ID
``telegram.org``) and sends whatever comes back to Telegram. The browser has no such
credential — the key lives in the vault and never leaves it — so :data:`PASSKEY_HOOK`
takes that one call over: it queues the challenge and waits. :class:`PasskeyBridge`
polls the queue over the window's CDP socket, has the signer produce the assertion in the
vault, and hands the answer back to the waiting promise.

Polled, not pushed: ``Runtime.addBinding`` only reports calls once ``Runtime.enable`` has
been sent, and an enabled Runtime domain is the one DevTools tell a page can read. A
``Runtime.evaluate`` on a quiet window leaves nothing behind.

Only the window's own account is ever signed for, and only a request for RP ID
``telegram.org`` is taken over; every other WebAuthn call reaches the browser untouched.
A refusal or a vault failure rejects the promise as ``NotAllowedError`` — what a
cancelled system prompt looks like — so WebK shows its usual retry state.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from typing import TYPE_CHECKING, cast

from core.web_login._cdp import CdpError
from core.web_login._scripts import fill, once

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.web_login._cdp import CdpSession
    from schemas.passkey import WebPasskeyAssertion

# Lazily evaluated, so the names above are only needed by a type checker.
type PasskeySigner = Callable[[str], Awaitable[WebPasskeyAssertion]]

logger = logging.getLogger(__name__)

_POLL_SECONDS = 1.0
# A WebAuthn challenge is a handful of random bytes; the cap and the alphabet keep
# anything else the page might queue out of the vault and out of a JS expression.
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{1,256}")
_TAKE_EXPR = "window.__tbPk?window.__tbPk.take():null"

_HOOK_TEMPLATE = r"""
(() => {
__NATIVE__
  const container = self.CredentialsContainer && CredentialsContainer.prototype;
  if (!container || !container.get) return;
  const real = container.get;
  const queue = [];
  const waiting = new Map();
  let seq = 0;
  const toBytes = (text) => {
    const bin = atob(text.replace(/-/g, '+').replace(/_/g, '/'));
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  };
  const toText = (value) => {
    if (typeof value === 'string') return value;
    const view = value instanceof ArrayBuffer
      ? new Uint8Array(value)
      : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    let bin = '';
    for (let i = 0; i < view.length; i++) bin += String.fromCharCode(view[i]);
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  };
  const frozen = (target, props) => {
    for (const key of Object.keys(props)) {
      Object.defineProperty(target, key, { value: props[key], enumerable: true });
    }
    return target;
  };
  const build = (r) => {
    const response = frozen(
      Object.create(self.AuthenticatorAssertionResponse
        ? AuthenticatorAssertionResponse.prototype : Object.prototype),
      {
        clientDataJSON: toBytes(r.clientDataJson),
        authenticatorData: toBytes(r.authenticatorData),
        signature: toBytes(r.signature),
        userHandle: toBytes(r.userHandle),
      });
    // WebK reads the canonical JSON form (``toJSON``) where a platform returns one.
    const json = () => ({
      id: r.credentialId, rawId: r.credentialId, type: 'public-key',
      authenticatorAttachment: 'platform', clientExtensionResults: {},
      response: {
        clientDataJSON: r.clientDataJson, authenticatorData: r.authenticatorData,
        signature: r.signature, userHandle: r.userHandle,
      },
    });
    return frozen(
      Object.create(self.PublicKeyCredential ? PublicKeyCredential.prototype : Object.prototype),
      {
        id: r.credentialId, rawId: toBytes(r.credentialId), type: 'public-key',
        authenticatorAttachment: 'platform', response,
        getClientExtensionResults: () => ({}), toJSON: json,
      });
  };
  const wanted = (o) => !!(location.origin === 'https://web.telegram.org'
    && o && o.publicKey && o.publicKey.rpId === 'telegram.org'
    && o.publicKey.challenge);
  container.get = F({ f(options) {
    if (!wanted(options)) return real.apply(this, arguments);
    return new Promise((resolve, reject) => {
      // Already aborted before it was asked: nothing to sign for, and no listener would
      // ever fire for it (the event has come and gone).
      if (options.signal && options.signal.aborted) {
        reject(new DOMException('The operation was aborted.', 'AbortError'));
        return;
      }
      const id = ++seq;
      waiting.set(id, { resolve, reject });
      queue.push({ id, challenge: toText(options.publicKey.challenge) });
      if (options.signal) {
        options.signal.addEventListener('abort', () => {
          if (!waiting.delete(id)) return;
          const at = queue.findIndex((q) => q.id === id);
          if (at >= 0) queue.splice(at, 1);
          reject(new DOMException('The operation was aborted.', 'AbortError'));
        }, { once: true });
      }
    });
  } }, 'get', 0);
  Object.defineProperty(window, '__tbPk', { value: {
    // Only requests still waiting: an aborted one is never handed to the signer.
    take: () => {
      let next;
      while ((next = queue.shift())) { if (waiting.has(next.id)) return next; }
      return null;
    },
    done: (id, r) => {
      const w = waiting.get(id);
      if (w) { waiting.delete(id); w.resolve(build(r)); }
    },
    fail: (id) => {
      const w = waiting.get(id);
      if (w) {
        waiting.delete(id);
        w.reject(new DOMException('The operation either timed out or was not allowed.',
          'NotAllowedError'));
      }
    },
  } });
})();
"""

PASSKEY_HOOK = once("__tbPkInstalled", fill(_HOOK_TEMPLATE))


def _request(value: object) -> tuple[int, str] | None:
    """The ``(id, challenge)`` the page queued, or ``None`` for nothing / anything odd."""
    if not isinstance(value, dict):
        return None
    fields = cast("dict[str, object]", value)
    request_id, challenge = fields.get("id"), fields.get("challenge")
    if not isinstance(request_id, int) or isinstance(request_id, bool):
        return None
    if not isinstance(challenge, str) or _CHALLENGE.fullmatch(challenge) is None:
        return None
    return request_id, challenge


class PasskeyBridge:
    """Polls one window's page for a queued passkey request and answers it."""

    def __init__(self, session: CdpSession, page: str, sign: PasskeySigner) -> None:
        self._session = session
        self._page = page
        self._sign = sign
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        while not self._session.closed:
            request = await self._take()
            if request is None:
                await asyncio.sleep(_POLL_SECONDS)
                continue
            await self._answer(*request)

    async def _evaluate(self, expression: str) -> object:
        response = await self._session.send_command(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
            session_id=self._page,
        )
        try:
            return response["result"]["result"]["value"]
        except (KeyError, TypeError):
            return None

    async def _take(self) -> tuple[int, str] | None:
        try:
            return _request(await self._evaluate(_TAKE_EXPR))
        except (CdpError, OSError):
            return None

    async def _answer(self, request_id: int, challenge: str) -> None:
        try:
            assertion = await self._sign(challenge)
            payload = assertion.model_dump_json(by_alias=True)
            expression = f"window.__tbPk.done({request_id},{payload})"
        except Exception as exc:  # noqa: BLE001 - any signer failure is a refusal to the page
            logger.warning("Web passkey signing failed (%s).", type(exc).__name__)
            expression = f"window.__tbPk.fail({request_id})"
        with suppress(CdpError, OSError):
            await self._evaluate(expression)


__all__ = ["PASSKEY_HOOK", "PasskeyBridge", "PasskeySigner"]
