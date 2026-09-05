"""Run an injected script under Node against a stub of the scope it is written for.

The unit suite fakes the CDP session, so it can only ever check that a string was sent.
Nothing it asserts can see what the string DOES — and everything these scripts are
judged on (own keys, ``prototype``, constructibility, ``.name``, ``.toString()``, the
whole ``Date`` surface, what is frozen) is a property of the running code. So the
scripts are executed here on the same V8 the browser runs, against a stub whose
prototypes are laid out the way the real interfaces are: accessors on the prototype,
brand-checked, methods where the browser really keeps them.

The stub is deliberately thin. It is not a browser and is not trying to be: it exists so
the script under test can install itself, and so an assertion about the SHAPE of what it
installed has something real to run against. Anything the stub cannot answer (does real
Chrome freeze the brand entries? does ``window``'s ``devicePixelRatio`` getter brand-check?)
is settled against a real Chrome instead, never against this file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="Node.js is not on PATH")

# The operator's REAL machine zone for every probe run: nothing about a timezone shim can
# be proved on a host that already sits in the zone being claimed.
REAL_TZ = "Europe/Moscow"

_HELPERS = r"""
globalThis.__throws = (fn) => {
  try { fn(); return 'no throw'; } catch (e) { return e.name + ': ' + e.message; }
};
globalThis.__constructible = (f) => {
  try { Reflect.construct(Object, [], f); return true; } catch (e) { return false; }
};
globalThis.__shape = (f) => ({
  keys: Object.getOwnPropertyNames(f),
  prototype: typeof f.prototype,
  constructible: __constructible(f),
  name: f.name,
  length: f.length,
  source: Function.prototype.toString.call(f),
});
globalThis.__brand = (Ctor) => (val) => ({
  configurable: true,
  enumerable: true,
  get() {
    if (!(this instanceof Ctor)) { throw new TypeError('Illegal invocation'); }
    return val;
  },
});
// Node ships its own getter-only `navigator`, so a plain assignment silently loses.
globalThis.__global = (name, value) => {
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
};
"""

# ``navigator``/``screen``/``WebGLRenderingContext`` as Chrome lays them out: every
# attribute a brand-checked accessor on the PROTOTYPE, ``getParameter`` a prototype
# method, ``devicePixelRatio`` an own accessor of the global (Window is [Global]).
_PAGE_STUB = r"""
class Navigator {}
class Screen {}
const nav = __brand(Navigator);
const scr = __brand(Screen);
Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', nav(16));
Object.defineProperty(Navigator.prototype, 'deviceMemory', nav(8));
for (const [k, v] of [['width', 1920], ['height', 1080], ['availWidth', 1920],
    ['availHeight', 1040], ['availLeft', 0], ['availTop', 0], ['colorDepth', 30],
    ['pixelDepth', 30]]) {
  Object.defineProperty(Screen.prototype, k, scr(v));
}
__global('self', globalThis);
__global('Navigator', Navigator);
__global('Screen', Screen);
__global('navigator', new Navigator());
__global('screen', new Screen());
Object.defineProperty(globalThis, 'devicePixelRatio', {
  configurable: true,
  enumerable: true,
  get() {
    if (this !== globalThis) { throw new TypeError('Illegal invocation'); }
    return 1;
  },
});
class WebGLRenderingContext {
  getParameter(p) { return 'real-' + p; }
}
__global('WebGLRenderingContext', WebGLRenderingContext);
__global('__gl', new WebGLRenderingContext());
"""

# A worker scope: ``WorkerNavigator`` plus the real ``NavigatorUAData`` layout (accessors
# on the prototype, two methods on it), and ``self`` as the global.
_WORKER_STUB = r"""
class WorkerNavigator {}
class NavigatorUAData {
  getHighEntropyValues(hints) {
    return Promise.resolve({ architecture: 'real', bitness: 'real', model: 'real',
      platformVersion: 'real', uaFullVersion: 'kept', wow64: false,
      fullVersionList: [{ brand: 'real', version: '1' }], brands: [], mobile: true,
      platform: 'real' });
  }
  toJSON() { return { brands: [], mobile: true, platform: 'real' }; }
}
const wn = __brand(WorkerNavigator);
const ua = __brand(NavigatorUAData);
for (const [k, v] of [['userAgent', 'real-ua'], ['platform', 'real-platform'],
    ['language', 'xx-XX'], ['languages', Object.freeze(['xx-XX'])],
    ['hardwareConcurrency', 16], ['deviceMemory', 8]]) {
  Object.defineProperty(WorkerNavigator.prototype, k, wn(v));
}
Object.defineProperty(NavigatorUAData.prototype, 'brands',
  ua(Object.freeze([{ brand: 'Real', version: '1' }])));
Object.defineProperty(NavigatorUAData.prototype, 'mobile', ua(true));
Object.defineProperty(NavigatorUAData.prototype, 'platform', ua('Real'));
Object.defineProperty(WorkerNavigator.prototype, 'userAgentData',
  wn(new NavigatorUAData()));
__global('self', globalThis);
__global('WorkerNavigator', WorkerNavigator);
__global('NavigatorUAData', NavigatorUAData);
__global('navigator', new WorkerNavigator());
// Node 24 ships no Temporal and no WebGL, so both are stubbed at the SHAPE the browser
// exposes them with: `Temporal.Now`'s ISO readers take an optional zone (arity 0, hence
// the rest parameters) and answer the engine's own zone when given none, and
// `getParameter` is a prototype method of an interface `OffscreenCanvas` reaches. The
// VALUES here are the real browser's job (see the live Chrome run); what this settles is
// that the override lands, keeps the native shape, and forwards an explicit argument.
class WebGLRenderingContext {
  getParameter(p) { return 'real-' + p; }
}
__global('WebGLRenderingContext', WebGLRenderingContext);
__global('__gl', new WebGLRenderingContext());
__global('Temporal', {
  Now: {
    timeZoneId() { return 'real-zone'; },
    plainDateTimeISO(...a) { return 'PDT:' + (a[0] === undefined ? 'real-zone' : a[0]); },
    plainDateISO(...a) { return 'PD:' + (a[0] === undefined ? 'real-zone' : a[0]); },
    plainTimeISO(...a) { return 'PT:' + (a[0] === undefined ? 'real-zone' : a[0]); },
    zonedDateTimeISO(...a) { return 'ZDT:' + (a[0] === undefined ? 'real-zone' : a[0]); },
    instant() { return 'INSTANT'; },
  },
});
"""

# The message paths the QR hook attaches to: ``addEventListener`` /
# ``removeEventListener`` own to ``EventTarget.prototype`` and NOWHERE else,
# ``onmessage`` own to each of ``MessagePort.prototype`` and ``Worker.prototype``.
#
# Each one also enforces its WebIDL arity, which is not decoration: a wrapper that
# re-issues the call with its own full argument list swallows exactly this TypeError, so
# without the check the stub could not tell a faithful wrapper from one that made
# ``document.addEventListener()`` stop throwing on every frame.
_HOOK_STUB = r"""
const __arity = (name, on, need, got) => {
  if (got >= need) { return; }
  throw new TypeError("Failed to execute '" + name + "' on '" + on + "': " + need
    + " arguments required, but only " + got + " present.");
};
class EventTarget {
  addEventListener(type, fn, options) {
    __arity('addEventListener', 'EventTarget', 2, arguments.length);
    this.__args = { count: arguments.length, options };
    if (!this.__ls) { this.__ls = {}; }
    (this.__ls[type] = this.__ls[type] || []).push(fn);
  }
  removeEventListener(type, fn, options) {
    __arity('removeEventListener', 'EventTarget', 2, arguments.length);
    this.__args = { count: arguments.length, options };
    const list = this.__ls && this.__ls[type];
    if (!list) { return; }
    const at = list.indexOf(fn);
    if (at >= 0) { list.splice(at, 1); }
  }
  __fire(type, event) {
    for (const fn of ((this.__ls && this.__ls[type]) || []).slice()) {
      fn.call(this, event);
    }
    if (type === 'message' && this.__on) { this.__on.call(this, event); }
  }
}
class MessagePort extends EventTarget {
  postMessage(m, transfer) {
    __arity('postMessage', 'MessagePort', 1, arguments.length);
    this.__args = { count: arguments.length, options: transfer };
    this.__sent = m;
  }
}
class Worker extends EventTarget {}
const onmessage = {
  configurable: true,
  enumerable: true,
  get() { return this.__on || null; },
  set(fn) { this.__on = fn; },
};
Object.defineProperty(MessagePort.prototype, 'onmessage', onmessage);
Object.defineProperty(Worker.prototype, 'onmessage', onmessage);
__global('EventTarget', EventTarget);
__global('MessagePort', MessagePort);
__global('Worker', Worker);
__global('window', globalThis);
__global('btoa', (s) => Buffer.from(s, 'binary').toString('base64'));
"""

STUBS = {"page": _PAGE_STUB, "worker": _WORKER_STUB, "hook": _HOOK_STUB}


def run_probe(
    tmp_path: Path,
    kind: str,
    source: str,
    probes: dict[str, str],
    *,
    timezone: str = REAL_TZ,
) -> dict[str, Any]:
    """Install ``source`` in a stub ``kind`` scope, then evaluate each probe expression.

    Every probe is an expression whose value is JSON; one that raises comes back as the
    string ``"<probe> raised <Error>: <message>"`` so a broken probe cannot pass as a
    result.
    """
    body = "\n".join(
        [
            _HELPERS,
            STUBS[kind],
            source,
            "const __out = {};",
            *(
                f"try {{ __out[{json.dumps(key)}] = ({expr}); }}"
                f" catch (e) {{ __out[{json.dumps(key)}] ="
                f" 'raised ' + e.name + ': ' + e.message; }}"
                for key, expr in probes.items()
            ),
            "process.stdout.write(JSON.stringify(__out));",
        ]
    )
    script = tmp_path / "probe.js"
    script.write_text(body, encoding="utf-8")
    completed = subprocess.run(  # noqa: S603 - fixed argv, generated script, no shell
        [str(NODE), str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env={**os.environ, "TZ": timezone},
    )
    if completed.returncode != 0:
        msg = f"node exited {completed.returncode}\n{completed.stderr}"
        raise AssertionError(msg)
    return json.loads(completed.stdout)
