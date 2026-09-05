"""The JavaScript injected into an account's window: page, worker, and the QR hook.

Split out of :mod:`core.web_login.fingerprint` and :mod:`core.web_login.browser` so
one copy of the native-function disguise is shared by all three scripts (and so both
modules stay inside the file-size budget). The Python side builds these with
:func:`fill`; the ``__FP__`` placeholder becomes an injected JSON literal and
``__NATIVE__`` becomes :data:`_NATIVE`.

Every override here has to survive the cheapest checks a page runs on a patched
function, which are not about the VALUE at all:

* ``fn.toString()`` — a real accessor or method reports ``[native code]``; a
  replacement reports its own source, and ``() => val`` is not something any browser
  ships. :data:`_NATIVE` installs one ``Function.prototype.toString`` proxy that
  reports ``[native code]`` for every function handed to ``N`` (and for itself).
* ``fn.name`` — a native getter is named ``get hardwareConcurrency``, not ``get``;
  ``WebGLRenderingContext.prototype.getParameter`` is named ``getParameter``, while a
  function assigned to a member expression gets no name at all (NamedEvaluation does
  not apply), so ``.name === ""`` alone flags the WebGL spoof. ``N`` names each one.
* the function's SHAPE. These are sloppy-mode classic scripts, so a ``function (…) {…}``
  expression owns ``arguments``, ``caller`` and ``prototype`` and is constructible,
  where a native method owns only ``length`` and ``name`` and throws on ``new``. One
  expression — ``navigator.userAgentData.getHighEntropyValues.prototype !== undefined``
  — reads that difference without touching a single value. Object-literal methods
  (:data:`_NATIVE`'s ``F``/``G``/``S``) have the native shape and keep dynamic ``this``;
  the two replacements that genuinely need ``[[Construct]]`` are proxies over the real
  constructor instead.
* a native accessor's brand check: ``Object.getOwnPropertyDescriptor(Navigator
  .prototype, 'hardwareConcurrency').get.call({})`` throws ``Illegal invocation``.
  :data:`_NATIVE`'s ``own`` calls the interface's REAL getter for that throw.
"""

from __future__ import annotations

import json

from core.web_login._clock import CLOCK

# Installed first in every scope, and idempotent under a second install: the outer
# proxy simply forwards an unknown function to the inner one, which recognises it.
_NATIVE = r"""
  const N = (() => {
    const spoofed = new WeakSet();
    const real = Function.prototype.toString;
    const shim = new Proxy(real, {
      apply(target, self, args) {
        return spoofed.has(self)
          ? 'function ' + self.name + '() { [native code] }'
          : Reflect.apply(target, self, args);
      },
    });
    spoofed.add(shim);
    Function.prototype.toString = shim;
    return (fn, name, len) => {
      try {
        Object.defineProperty(fn, 'name', { value: name, configurable: true });
      } catch (e) {}
      if (len !== undefined) {
        try {
          Object.defineProperty(fn, 'length', { value: len, configurable: true });
        } catch (e) {}
      }
      spoofed.add(fn);
      return fn;
    };
  })();
  // Every replacement is written as an object-literal method or accessor: same own keys
  // as a native one (``length``, ``name``), no ``prototype``, not constructible — and,
  // unlike an arrow, still bound to its receiver.
  const F = (holder, name, len) => N(holder.f, name, len);
  const G = (holder, name) => N(Object.getOwnPropertyDescriptor(holder, 'f').get, name);
  const S = (holder, name) => N(Object.getOwnPropertyDescriptor(holder, 'f').set, name);
  // The interface's own getter is the only thing that knows how to brand-check its
  // receiver, so it is called for its throw and its answer thrown away.
  const own = (holder, prop, val) => {
    const prior = Object.getOwnPropertyDescriptor(holder, prop);
    const brand = prior && prior.get;
    Object.defineProperty(holder, prop, {
      get: G({ get f() { if (brand) brand.call(this); return val; } }, 'get ' + prop),
      configurable: true,
    });
  };
  const def = (obj, prop, val) => {
    try { own(Object.getPrototypeOf(obj), prop, val); } catch (e) {}
  };
"""

# Document-start script: hardens what the Emulation domain does NOT already cover.
# ``setUserAgentOverride`` alone settles the page's userAgent, platform and languages,
# so this only adds the surface it leaves real. Config arrives as an injected ``__FP__``
# literal, and every override is wrapped so a failure can never break page load. Each
# accessor is defined on the PROTOTYPE, where real Chrome keeps it: on a stock browser
# ``Object.getOwnPropertyNames(navigator)`` is empty, so an own property is a tell. (No
# canvas patch: Telegram does not fingerprint canvas for the session record, and WebK
# draws its QR to a canvas.)
#
# The screen numbers are a set, not six independent values: ``availHeight == height``
# is impossible on macOS (the menu bar alone reserves rows) and unusual on Windows, and
# a ``devicePixelRatio`` that does not fit the claimed panel is the same kind of
# internal disagreement. Both come from the device row.
_PAGE_HEAD = r"""
(() => {
  const C = __FP__;
  __NATIVE__
  def(navigator, 'hardwareConcurrency', C.hardwareConcurrency);
  def(navigator, 'deviceMemory', C.deviceMemory);
  def(screen, 'width', C.screenW);
  def(screen, 'height', C.screenH);
  def(screen, 'availWidth', C.availW);
  def(screen, 'availHeight', C.availH);
  def(screen, 'availLeft', 0);
  def(screen, 'availTop', C.availTop);
  def(screen, 'colorDepth', 24);
  def(screen, 'pixelDepth', 24);
  try {
    // NOT on the prototype, unlike every accessor above: Window is a WebIDL [Global]
    // interface, so its members are own properties of the window object and one
    // defined on Window.prototype would simply stay shadowed by the real thing.
    own(self, 'devicePixelRatio', C.dpr);
  } catch (e) {}
"""

# Installed in BOTH scopes, because both interfaces reach a worker through
# ``OffscreenCanvas``. Page-only, they disagreed — measured on Chrome 148: the page
# answered ``getParameter(37446)`` with the claimed renderer while a dedicated worker and
# a shared worker of the same window both answered ``null``, and a property that answers
# differently in two scopes of one window is louder than any value either one reports.
#
# Both scopes answer the CLAIMED strings unconditionally rather than both honouring the
# ``WEBGL_debug_renderer_info`` gate real Chrome applies. Gating would mean delegating to
# the real ``getParameter`` whenever the extension was not requested — which is a branch
# that can return the HOST's GPU, on any build that answers 37446 un-gated, in a module
# whose whole point is that it never can. Every check that reads the renderer asks for
# the extension first, so the two designs answer identically where it counts.
#
# ``patchGL`` no-ops when the interface is absent, so the same fragment is safe in a
# worker that has no ``OffscreenCanvas`` at all.
_GL = r"""
  const patchGL = (proto) => {
    if (!proto || !proto.getParameter) return;
    try {
      const orig = proto.getParameter;
      proto.getParameter = F({ f(p) {
        if (p === 37445) return C.webglVendor;
        if (p === 37446) return C.webglRenderer;
        return orig.call(this, p);
      } }, 'getParameter');
    } catch (e) {}
  };
  patchGL(self.WebGLRenderingContext && WebGLRenderingContext.prototype);
  patchGL(self.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
"""

PAGE_TEMPLATE = _PAGE_HEAD + _GL + "})();\n"


# Worker-scope overrides. A worker has no ``Page`` domain and no window, so this is the
# ``navigator`` surface plus the clock — and it carries ``userAgent`` too, because a
# browser-level worker does not inherit the page's UA override. Evaluated on the
# worker's own session while it is paused on start, so WebK's ``initConnection`` reads
# these and never the real machine's values.
#
# ``navigator.userAgentData`` is dressed here too, and NOT over CDP. ``Emulation`` has
# no worker target, and ``Network.setUserAgentOverride`` sent to one never answers: a
# live run measured it burning the whole 30 s command timeout per worker WHILE that
# worker sat paused on start, which alone ate most of the login budget and cost the
# operator the session. The objection to doing it in script was the prototype — so the
# REAL object is kept and only its accessors and its two methods are redefined over it,
# leaving Chrome's own prototype, constructor and internal slots in place. What stays
# real is the worker's ``Sec-CH-UA`` request headers, which no CDP domain can reach
# from here; ``--user-agent`` still settles the ``User-Agent`` header itself.
#
# The WebGL half is :data:`_GL`, the same fragment the page gets: both interfaces reach
# a worker through ``OffscreenCanvas``, and page-only they disagreed (see :data:`_GL`).
#
# The clock half is :data:`core.web_login._clock.CLOCK`, appended below: it covers the
# WHOLE local-time surface (readers, writers, every formatter, the component
# constructor and zone-less ``Date.parse``), because a half-shifted clock prints the
# operator's real zone next to the claimed one's hours.
_WORKER_HEAD = r"""
(() => {
  const C = __FP__;
  __NATIVE__
  def(navigator, 'userAgent', C.userAgent);
  def(navigator, 'platform', C.navPlatform);
  def(navigator, 'language', C.locale);
  def(navigator, 'languages', Object.freeze(C.languages.slice()));
  def(navigator, 'hardwareConcurrency', C.hardwareConcurrency);
  def(navigator, 'deviceMemory', C.deviceMemory);
  const uad = navigator.userAgentData;
  if (uad) {
    const U = C.uaData;
    const copy = (v) => (Array.isArray(v) ? v.map((x) => Object.assign({}, x)) : v);
    // Chrome freezes the low-entropy brand LIST and not its entries: measured in a real
    // Chrome, `Object.isFrozen(navigator.userAgentData.brands)` is true while
    // `Object.isFrozen(brands[0])` is false. Freezing the entries here would make that
    // one expression answer differently in the page and in its own worker.
    def(uad, 'brands', Object.freeze(U.brands.map((b) => Object.assign({}, b))));
    def(uad, 'mobile', U.mobile);
    def(uad, 'platform', U.platform);
    const proto = Object.getPrototypeOf(uad);
    const high = proto.getHighEntropyValues;
    // Declared arity 1, as the real method reports; the argument is forwarded whole.
    proto.getHighEntropyValues = F({ f(hints) {
      // Delegate to the real method, then overwrite what we publish. The object keeps
      // Chrome's own prototype, constructor and internal slots; a hint we do NOT
      // publish (uaFullVersion, formFactors) keeps the browser's real answer — the
      // same answer the page gives — and an invalid hint still rejects as it really does.
      return Reflect.apply(high, this, arguments).then((out) => {
        out.brands = copy(U.brands);
        out.mobile = U.mobile;
        out.platform = U.platform;
        for (const k of Object.keys(U.high)) {
          if (k in out) out[k] = copy(U.high[k]);
        }
        return out;
      });
    } }, 'getHighEntropyValues');
    proto.toJSON = F({ f() {
      return { brands: copy(U.brands), mobile: this.mobile, platform: this.platform };
    } }, 'toJSON');
  }
"""

WORKER_TEMPLATE = _WORKER_HEAD + _GL + CLOCK + "})();\n"


# Injected at document-start on every frame of the FIRST open: hooks the Web Worker /
# MessagePort message paths and captures WebK's own QR ``auth.loginToken`` (base64url).
# Pure page script — no MTProto. The capture half is byte-for-byte the build that was
# validated live (WebK build 675); only the disguise and the teardown are new.
#
# The teardown matters because the array holds live login tokens: once the login has
# completed the capture has done its job, so the tokens are dropped and every prototype
# this hook replaced is put back. What cannot be undone are the wrappers already
# installed on live listeners.
#
# It is exposed as ``window.__capOff`` and fired from Python (:func:`release_capture`)
# once ``page_state`` actually reads ``logged_in``. An in-page timer watching for WebK's
# chat shell cannot tell that moment: WebK ships ``#column-center`` in its document from
# the first paint, merely zero-sized behind the auth screen, which is exactly why
# ``_PAGE_STATE_EXPR`` filters those markers on visibility. A bare ``querySelector``
# matched on the QR screen, so the timer deleted ``window.__cap`` seconds after load and
# no login token was ever captured — measured live: QR at 42 s, no token, no login.
#
# ``addEventListener`` is hooked on ``EventTarget.prototype``, which is the only place it
# natively lives. Installing an OWN copy on ``MessagePort.prototype`` and
# ``Worker.prototype`` made ``MessagePort.prototype.hasOwnProperty('addEventListener')``
# answer ``true``, which no browser does — a one-expression tell, live on every frame
# from document-start until the capture is released, and far cheaper to run than
# anything that inspects a function. The wrapper is applied only to the two receivers
# that matter, so nothing else on the page is touched.
#
# Two consequences of wrapping are undone rather than left visible: ``onmessage`` reads
# back the handler the page assigned (``port.onmessage === handler``, and its
# ``toString()`` is the page's own source) rather than the wrapper, and
# ``removeEventListener`` looks the wrapper up so a listener can still be removed by the
# function that registered it.
_HOOK_TEMPLATE = (
    "(() => {\n"
    "__NATIVE__\n"
    "window.__cap=[];\n"
    "function u8(x){return x instanceof Uint8Array?x:Array.isArray(x)?"
    "Uint8Array.from(x):(x&&x.buffer?new Uint8Array(x.buffer):null);}\n"
    "function b(u){return btoa(String.fromCharCode.apply(null,u))"
    r".replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}"
    "\n"
    "function scan(o,d,s){if(o==null||d>6||typeof o!=='object'||s.has(o))"
    "return;s.add(o);\n"
    " try{if(o._==='auth.loginToken'&&o.token!=null){const v=u8(o.token);"
    "if(v)window.__cap.push(b(v));}}catch(e){}\n"
    " for(const k in o){try{scan(o[k],d+1,s);}catch(e){}}}\n"
    "const wrap=new WeakMap();const plain=new WeakMap();\n"
    "function w(fn){let x=wrap.get(fn);if(!x){"
    "x=F({f(ev){try{scan(ev&&ev.data,0,new WeakSet());}catch(e){}"
    "return fn.apply(this,arguments);}},fn.name,fn.length);"
    "wrap.set(fn,x);plain.set(x,fn);}return x;}\n"
    "const tap=(o,t)=>t==='message'&&(o instanceof MessagePort||o instanceof Worker);\n"
    "const undo=[];\n"
    "const ael=EventTarget.prototype.addEventListener;\n"
    "const rel=EventTarget.prototype.removeEventListener;\n"
    # A simple parameter list plus the MAPPED `arguments` object, never `(t,fn,...r)`:
    # a rest wrapper re-issues a 0- or 1-argument call as a full one, so the WebIDL
    # "2 arguments required" TypeError real Chrome throws never happens. Forwarding
    # `arguments` keeps the count the caller actually passed (and any options argument),
    # while `length` stays the 2 the real method reports.
    "EventTarget.prototype.addEventListener=F({f(t,fn){const a=arguments;"
    "if(typeof fn==='function'&&tap(this,t))a[1]=w(fn);return ael.apply(this,a);}},"
    "'addEventListener');\n"
    "EventTarget.prototype.removeEventListener=F({f(t,fn){const a=arguments;"
    "if(typeof fn==='function'&&tap(this,t))a[1]=wrap.get(fn)||fn;"
    "return rel.apply(this,a);}},'removeEventListener');\n"
    "undo.push(()=>{EventTarget.prototype.addEventListener=ael;"
    "EventTarget.prototype.removeEventListener=rel;});\n"
    "for(const P of [MessagePort.prototype,Worker.prototype]){"
    "const dd=Object.getOwnPropertyDescriptor(P,'onmessage');\n"
    " if(dd&&dd.set){Object.defineProperty(P,'onmessage',{configurable:true,"
    "get:G({get f(){const cur=dd.get.call(this);return plain.get(cur)||cur;}},"
    "'get onmessage'),"
    "set:S({set f(fn){dd.set.call(this,"
    "typeof fn==='function'?w(fn):fn);}},'set onmessage')});\n"
    "  undo.push(()=>{Object.defineProperty(P,'onmessage',dd);});}}\n"
    "const mp=MessagePort.prototype.postMessage;"
    "MessagePort.prototype.postMessage=F({f(m){const a=arguments;"
    "try{scan(m,0,new WeakSet());}"
    "catch(e){}return mp.apply(this,a);}},'postMessage');\n"
    "undo.push(()=>{MessagePort.prototype.postMessage=mp;});\n"
    "window.__capOff=F({f(){window.__cap.length=0;delete window.__cap;"
    "delete window.__capOff;\n"
    " for(const f of undo){try{f();}catch(e){}}}},'__capOff');\n"
    "})();\n"
)


def fill(template: str, config: dict[str, object] | None = None) -> str:
    """Resolve a template's ``__NATIVE__`` block and its injected ``__FP__`` literal."""
    filled = template.replace("__NATIVE__", _NATIVE)
    return filled if config is None else filled.replace("__FP__", json.dumps(config))


QR_CAPTURE_HOOK = fill(_HOOK_TEMPLATE)
