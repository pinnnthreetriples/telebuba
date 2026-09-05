"""Manual check: does a real browser wear the account identity everywhere it must?

The unit suite fakes the CDP session, so it cannot see what a browser actually
does — twice during this feature's development a total login failure sat behind a
fully green suite. The V8 probes in ``tests/core/test_web_login_scripts_js.py``
close half of that (they execute the shipped scripts, and they DO run in CI), but
they cannot answer what only a browser knows: whether the identity reaches a
dedicated worker, a SHARED worker and the page alike.

This is that check, kept in the tree rather than rebuilt from scratch each time.
It is NOT a test and it is not wired into CI: it needs a real Chrome or Edge, and
this feature only runs where the operator's own desktop does.

    uv run python -m tools.web_login_browser_check

It drives the shipped ``launch_account_web`` against a LOCAL page — Telegram is
never contacted, no account and no proxy is touched, and the profile is a
throwaway. Run it after changing anything under ``core/web_login/``.

Exit code 0 means page, dedicated worker and shared worker agree on every probed
value; 1 means they disagree (each disagreement is printed) or the browser could
not be driven. A disagreement between page and worker is the failure mode this
whole area exists to prevent, and the one the fakes cannot show you.
"""

# ruff: noqa: T201 - the report IS this tool's output, like tools/mutation_report.py

from __future__ import annotations

import asyncio
import http.server
import json
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from core.web_login import browser as browser_module
from core.web_login.browser import BrowserNotFoundError, launch_account_web
from core.web_login.fingerprint import fingerprint_for

if TYPE_CHECKING:
    from core.web_login.browser import WebWindow

# Any account id: this never talks to Telegram, it only needs a fingerprint that is
# internally consistent and not the host's own. Two countries, on purpose — "IN" claims
# Asia/Kolkata, which ICU answers under its legacy alias Asia/Calcutta. A shim that
# hands back the name it was configured with instead of the one the engine canonicalises
# to disagrees with its own page in one expression, and that is exactly how the Temporal
# override was caught. A single-zone check would have passed.
_ACCOUNT = "browser-check"
_COUNTRIES = ("DE", "IN")
_SETTLE_SECONDS = 4.0

# Read in all three scopes and compared. Kept to what the identity actually
# claims — a value the host would answer differently is the point.
_READ = """() => ({
  userAgent: navigator.userAgent,
  platform: navigator.platform,
  language: navigator.language,
  languages: navigator.languages.join(','),
  hardwareConcurrency: navigator.hardwareConcurrency,
  deviceMemory: navigator.deviceMemory,
  uaPlatform: navigator.userAgentData ? navigator.userAgentData.platform : null,
  uaBrands: navigator.userAgentData
    ? navigator.userAgentData.brands.map(b => b.brand + '/' + b.version).join(',') : null,
  timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  offsetJan: new Date(Date.UTC(2026, 0, 1)).getTimezoneOffset(),
  offsetJul: new Date(Date.UTC(2026, 6, 1)).getTimezoneOffset(),
  dateString: new Date(Date.UTC(2026, 0, 1, 12)).toString(),
  parseZoned: Date.parse('Jul 1 2026 00:00:00 GMT-0400 (a) (b)'),
  parseBare: Date.parse('Jul 1 2026 00:00:00 (x)'),
  temporalZone: typeof Temporal === 'undefined' ? null : Temporal.Now.timeZoneId(),
  webgl: (() => {
    try {
      const canvas = typeof document !== 'undefined'
        ? document.createElement('canvas') : new OffscreenCanvas(2, 2);
      const gl = canvas.getContext('webgl');
      return gl ? String(gl.getParameter(37446)) : 'no-context';
    } catch (err) { return 'error: ' + err.message; }
  })(),
})"""

_PAGE_HTML = "<!doctype html><title>web-login check</title><body>probe</body>"
_WORKER_JS = (
    "const read = {read};"
    "self.onmessage = () => {{"
    " try {{ postMessage(JSON.stringify(read())); }}"
    " catch (err) {{ postMessage(JSON.stringify({{error: String(err)}})); }} }};"
)
# A shared worker must be served from the origin; a blob URL cannot back one.
_SHARED_JS = (
    "const read = {read};"
    "self.onconnect = (event) => {{ const port = event.ports[0];"
    " port.onmessage = () => {{"
    "  try {{ port.postMessage(JSON.stringify(read())); }}"
    "  catch (err) {{ port.postMessage(JSON.stringify({{error: String(err)}})); }} }}; }};"
)

_PROBE = """(async () => {
  const read = __READ__;
  const ask = (target, post) => new Promise((resolve) => {
    target.onmessage = (event) => resolve(JSON.parse(event.data));
    post();
  });
  const dedicated = new Worker('/worker.js');
  const fromDedicated = await ask(dedicated, () => dedicated.postMessage(1));
  dedicated.terminate();
  const shared = new SharedWorker('/shared.js');
  shared.port.start();
  const fromShared = await ask(shared.port, () => shared.port.postMessage(1));
  return JSON.stringify({page: read(), dedicated: fromDedicated, shared: fromShared});
})()"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _serve() -> int:
    """Serve the probe page and the two worker scripts on a loopback port."""
    bodies = {
        "/": (_PAGE_HTML.encode(), "text/html"),
        "/worker.js": (_WORKER_JS.format(read=_READ).encode(), "text/javascript"),
        "/shared.js": (_SHARED_JS.format(read=_READ).encode(), "text/javascript"),
    }
    port = _free_port()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body, kind = bodies.get(self.path, (b"", "text/plain"))
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", kind)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            """Stay quiet: the report below is the output."""

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return port


async def _read_scopes(window: WebWindow) -> dict[str, dict[str, object]]:
    response = await window.session.send_command(
        "Runtime.evaluate",
        {
            "expression": _PROBE.replace("__READ__", _READ),
            "returnByValue": True,
            "awaitPromise": True,
        },
        session_id=window.page,
    )
    value = response["result"]["result"].get("value")
    if not isinstance(value, str):
        msg = f"the probe returned no value: {response['result']['result']}"
        raise TypeError(msg)
    return json.loads(value)


def _report(scopes: dict[str, dict[str, object]], claimed: str) -> int:
    """Print every probed value and return the number of disagreements."""
    page = scopes["page"]
    disagreements = 0
    print(f"{'value':22} {'page':38} dedicated  shared")
    for key in sorted(page):
        expected = page[key]
        same = [scopes[scope].get(key) == expected for scope in ("dedicated", "shared")]
        marks = "  ".join("ok " if ok else "DIFF" for ok in same)
        print(f"{key:22} {str(expected)[:38]:38} {marks}")
        for scope, ok in zip(("dedicated", "shared"), same, strict=True):
            if not ok:
                disagreements += 1
                print(f"    {scope} answered {scopes[scope].get(key)!r}")
    print(f"\nclaimed timezone: {claimed}; page reports {page.get('timeZone')!r}")
    return disagreements


async def _check_one(country: str) -> int:
    profile = Path(tempfile.mkdtemp(prefix="web_login_check_"))
    fingerprint = fingerprint_for(_ACCOUNT, country)
    print(f"device: {fingerprint.device.name}  ua: {fingerprint.user_agent}")
    # A port nothing listens on: Chrome bypasses the proxy for loopback, so the
    # local page still loads and no traffic can leave the machine.
    window = await launch_account_web(
        _free_port(), profile_dir=profile, fingerprint=fingerprint, capture_tokens=False
    )
    try:
        await asyncio.sleep(_SETTLE_SECONDS)
        scopes = await _read_scopes(window)
    finally:
        await window.kill()
        await asyncio.sleep(1.0)
        shutil.rmtree(profile, ignore_errors=True)
    return _report(scopes, fingerprint.timezone)


async def _run() -> int:
    http_port = _serve()
    browser_module._WEBK_URL = f"http://127.0.0.1:{http_port}/"  # noqa: SLF001
    disagreements = 0
    for country in _COUNTRIES:
        print(f"\n=== claiming {country} ===")
        disagreements += await _check_one(country)
    if disagreements:
        print(f"\nFAIL: {disagreements} page/worker disagreement(s)")
        return 1
    print("\nOK: page, dedicated worker and shared worker agree")
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except BrowserNotFoundError as exc:
        print(f"FAIL: {exc}")
        return 1
    except (OSError, RuntimeError, TypeError) as exc:
        print(f"FAIL: could not drive the browser: {exc!r}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
