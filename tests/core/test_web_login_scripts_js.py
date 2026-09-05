"""What the injected scripts DO, measured by running them on V8 instead of reading them.

Every other test of this surface asserts that a substring reached a fake CDP session,
which cannot see a single thing a page actually checks. These execute the real generated
scripts under Node and read the result back: own keys, ``prototype``,
constructibility, ``.name``/``.length``, ``.toString()``, brand checks, what is frozen,
where a hook landed, and the whole ``Date`` surface across a DST boundary.

The clock is judged against a CONTROL: the same expressions evaluated by an unpatched V8
whose ``TZ`` really is the claimed zone. A shimmed worker on a Moscow machine has to
answer exactly what a real engine in New York answers — every formatter, every reader,
every writer, the component constructor and ``Date.parse`` — which is a stronger and far
less brittle claim than any literal string this file could spell out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from core.web_login import fingerprint as fp_module
from core.web_login._scripts import QR_CAPTURE_HOOK
from core.web_login.fingerprint import _page_init_script, fingerprint_for, worker_init_script
from tests.core.web_login_js_probe import REAL_TZ, requires_node, run_probe

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = requires_node

# "US" resolves to America/New_York, which observes DST while the operator's claimed
# real machine (REAL_TZ) does not — so a stuck offset cannot pass as a shifted one.
CLAIMED_TZ = "America/New_York"
_ACCOUNT = "acct-js-probe"
_BUILD = "148.0.7778.217"


@pytest.fixture(autouse=True)
def _pinned_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fp_module, "_observed", {"build": _BUILD, "edge": ""})


def _brands() -> list[Any]:
    """The brand list the worker script is told to publish, straight from the builder."""
    data: Any = fp_module._ua_data(fingerprint_for(_ACCOUNT, "US"))
    return list(data["brands"])


# --------------------------------------------------------------------------- probes

_SHAPE_KEYS = ("keys", "prototype", "constructible")
# What a native method reports, measured on V8: `Object.keys(Object.keys)` is
# ["length", "name"], it has no `prototype`, and `new` on it throws.
_NATIVE_METHOD = {"keys": ["length", "name"], "prototype": "undefined", "constructible": False}

_WORKER_PROBES = {
    "getHighEntropyValues": "__shape(navigator.userAgentData.getHighEntropyValues)",
    "toJSON": "__shape(navigator.userAgentData.toJSON)",
    "getTimezoneOffset_shape": "__shape(Date.prototype.getTimezoneOffset)",
    "getHours_shape": "__shape(Date.prototype.getHours)",
    "setHours_shape": "__shape(Date.prototype.setHours)",
    "toLocaleString_shape": "__shape(Date.prototype.toLocaleString)",
    "userAgent_getter": "__shape(Object.getOwnPropertyDescriptor("
    "WorkerNavigator.prototype, 'userAgent').get)",
    "illegal_hardware": "__throws(() => Object.getOwnPropertyDescriptor("
    "WorkerNavigator.prototype, 'hardwareConcurrency').get.call({}))",
    "illegal_brands": "__throws(() => Object.getOwnPropertyDescriptor("
    "NavigatorUAData.prototype, 'brands').get.call({}))",
    "hardwareConcurrency": "navigator.hardwareConcurrency",
    "userAgent": "navigator.userAgent",
    "brands": "navigator.userAgentData.brands",
    "brands_frozen": "Object.isFrozen(navigator.userAgentData.brands)",
    "brand_entry_frozen": "Object.isFrozen(navigator.userAgentData.brands[0])",
    "dtf_shape": "__shape(Intl.DateTimeFormat)",
    "dtf_constructor": "Intl.DateTimeFormat.prototype.constructor === Intl.DateTimeFormat",
    "dtf_prototype_writable": "Object.getOwnPropertyDescriptor("
    "Intl.DateTimeFormat, 'prototype').writable",
    "dtf_subclass": "(() => { class X extends Intl.DateTimeFormat {}; const x = new X();"
    " return [Object.getPrototypeOf(x) === X.prototype,"
    " x.resolvedOptions().timeZone]; })()",
    "dtf_no_new": "Intl.DateTimeFormat().resolvedOptions().timeZone",
    "date_shape": "__shape(Date)",
    "date_constructor": "Date.prototype.constructor === Date",
    "date_tag": "Object.prototype.toString.call(new Date())",
    "temporal_zone": "Temporal.Now.timeZoneId()",
    "temporal_plain_date_time": "Temporal.Now.plainDateTimeISO()",
    "temporal_plain_date": "Temporal.Now.plainDateISO()",
    "temporal_plain_time": "Temporal.Now.plainTimeISO()",
    "temporal_zoned": "Temporal.Now.zonedDateTimeISO()",
    "temporal_explicit_zone": "Temporal.Now.plainDateTimeISO('Asia/Tokyo')",
    "temporal_instant": "Temporal.Now.instant()",
    "temporal_zone_shape": "__shape(Temporal.Now.timeZoneId)",
    "temporal_reader_shape": "__shape(Temporal.Now.plainDateTimeISO)",
    "gl_shape": "__shape(WebGLRenderingContext.prototype.getParameter)",
    "gl_vendor": "__gl.getParameter(37445)",
    "gl_renderer": "__gl.getParameter(37446)",
    "gl_passthrough": "__gl.getParameter(1)",
}

# Evaluated twice: once against the shim on a machine in REAL_TZ, once against a bare V8
# whose TZ really is CLAIMED_TZ. Every answer has to match.
_CLOCK_PROBES = {
    "offset_winter": "new Date(Date.UTC(2026, 0, 15)).getTimezoneOffset()",
    "offset_summer": "new Date(Date.UTC(2026, 6, 15)).getTimezoneOffset()",
    "getHours": "new Date(Date.UTC(2026, 0, 1, 5)).getHours()",
    "getDay": "new Date(Date.UTC(2026, 0, 1, 5)).getDay()",
    "toString": "new Date(Date.UTC(2026, 0, 1, 5)).toString()",
    "toString_dst": "new Date(Date.UTC(2026, 6, 1, 4)).toString()",
    "toDateString": "new Date(Date.UTC(2026, 0, 1, 5)).toDateString()",
    "toTimeString": "new Date(Date.UTC(2026, 0, 1, 5)).toTimeString()",
    "toUTCString": "new Date(Date.UTC(2026, 0, 1, 5)).toUTCString()",
    "toLocaleString": "new Date(Date.UTC(2026, 0, 1, 5)).toLocaleString('en-US')",
    "toLocaleDateString": "new Date(Date.UTC(2026, 0, 1, 5)).toLocaleDateString('en-US')",
    "toLocaleTimeString": "new Date(Date.UTC(2026, 0, 1, 5)).toLocaleTimeString('en-US')",
    "toLocaleString_opts": "new Date(Date.UTC(2026, 0, 1, 5))"
    ".toLocaleString('en-US', { hour: '2-digit', minute: '2-digit' })",
    "toLocaleDateString_opts": "new Date(Date.UTC(2026, 0, 1, 5))"
    ".toLocaleDateString('en-US', { weekday: 'long' })",
    "getYear": "new Date(Date.UTC(2026, 0, 1, 5)).getYear()",
    "invalid": "new Date(NaN).toString()",
    "components": "new Date(2026, 0, 1).toISOString()",
    "components_dst": "new Date(2026, 6, 1, 12, 30).toISOString()",
    "components_two_digit_year": "new Date(99, 0, 1).toISOString()",
    "copy_construct": "new Date(new Date(Date.UTC(2026, 0, 1))).toISOString()",
    "construct_number": "new Date(1782864000000).toISOString()",
    "construct_string_local": "new Date('2026-07-01T00:00:00').toISOString()",
    "construct_string_zoned": "new Date('2026-07-01T00:00:00Z').toISOString()",
    "construct_invalid": "new Date('not a date').toString()",
    "date_as_function": "Date().slice(8) === new Date().toString().slice(8)",
    "parse_local": "Date.parse('2026-07-01T00:00:00')",
    "parse_zulu": "Date.parse('2026-07-01T00:00:00Z')",
    "parse_date_only": "Date.parse('2026-07-01')",
    "parse_offset": "Date.parse('2026-07-01T00:00:00+02:00')",
    # V8's legacy keyword table: these eight already FIX an offset, so a shim that
    # unwound them again would answer a number computed from the host's own offset.
    "parse_cst": "Date.parse('Jul 1 2026 00:00:00 CST')",
    "parse_cdt": "Date.parse('Jul 1 2026 00:00:00 CDT')",
    "parse_est": "Date.parse('Jul 1 2026 00:00:00 EST')",
    "parse_edt": "Date.parse('Jul 1 2026 00:00:00 EDT')",
    "parse_mst": "Date.parse('Jul 1 2026 00:00:00 MST')",
    "parse_mdt": "Date.parse('Jul 1 2026 00:00:00 MDT')",
    "parse_pst": "Date.parse('Jul 1 2026 00:00:00 PST')",
    "parse_pdt": "Date.parse('Jul 1 2026 00:00:00 PDT')",
    "parse_ut": "Date.parse('Jul 1 2026 00:00:00 UT')",
    # Not in the table (NaN on V8), so the widened class must not have started
    # swallowing a tail that merely ENDS in one of them.
    "parse_bst": "Date.parse('Jul 1 2026 00:00:00 BST')",
    "parse_west": "Date.parse('Jul 1 2026 00:00:00 WEST')",
    "parse_round_trip": "Date.parse(new Date(Date.UTC(2026, 6, 1, 4)).toString())",
    # V8 reads parentheses as a comment, not as a zone. A zone-less local string that
    # merely ENDS in one must still be unwound: taking it for an instant answers the
    # HOST's offset, and the summer/winter pair below names the operator's real zone.
    "parse_paren_summer": "Date.parse('Jul 1 2026 00:00:00 (x)')",
    "parse_paren_winter": "Date.parse('Jan 1 2026 00:00:00 (x)')",
    # V8 also accepts several comments in a row after a zone. Anchoring only one would
    # double-unwind these, leaking the host offset from the opposite side.
    "parse_zone_two_parens": "Date.parse('Jul 1 2026 00:00:00 GMT-0400 (a) (b)')",
    "set_hours": "(() => { const d = new Date(Date.UTC(2026, 6, 15, 10));"
    " d.setHours(0, 0, 0, 0); return [d.toISOString(), d.getHours()]; })()",
    "set_full_year": "(() => { const d = new Date(Date.UTC(2026, 6, 15, 10));"
    " d.setFullYear(2030); return d.toISOString(); })()",
    "set_month_across_dst": "(() => { const d = new Date(Date.UTC(2026, 0, 15, 10));"
    " d.setMonth(6); return [d.toISOString(), d.getHours()]; })()",
    "set_year": "(() => { const d = new Date(Date.UTC(2026, 6, 15, 10));"
    " d.setYear(99); return d.getFullYear(); })()",
    "intl_zone": "new Intl.DateTimeFormat('en-US').resolvedOptions().timeZone",
    "intl_format": "new Intl.DateTimeFormat('en-US',"
    " { dateStyle: 'full', timeStyle: 'long' }).format(new Date(Date.UTC(2026, 0, 1, 5)))",
}

_PAGE_PROBES = {
    "getParameter": "__shape(WebGLRenderingContext.prototype.getParameter)",
    "vendor": "__gl.getParameter(37445)",
    "renderer": "__gl.getParameter(37446)",
    "passthrough": "__gl.getParameter(1)",
    "hardware_getter": "__shape(Object.getOwnPropertyDescriptor("
    "Navigator.prototype, 'hardwareConcurrency').get)",
    "illegal_hardware": "__throws(() => Object.getOwnPropertyDescriptor("
    "Navigator.prototype, 'hardwareConcurrency').get.call({}))",
    "illegal_screen": "__throws(() => Object.getOwnPropertyDescriptor("
    "Screen.prototype, 'availTop').get.call({}))",
    "illegal_dpr": "__throws(() => Object.getOwnPropertyDescriptor("
    "globalThis, 'devicePixelRatio').get.call({}))",
    "hardwareConcurrency": "navigator.hardwareConcurrency",
    "screen_width": "screen.width",
    "devicePixelRatio": "devicePixelRatio",
    "hardware_enumerable": "Object.getOwnPropertyDescriptor("
    "Navigator.prototype, 'hardwareConcurrency').enumerable",
    "navigator_own_names": "Object.getOwnPropertyNames(navigator)",
}

_HOOK_PROBES = {
    "port_own": "MessagePort.prototype.hasOwnProperty('addEventListener')",
    "worker_own": "Worker.prototype.hasOwnProperty('addEventListener')",
    "eventtarget_own": "EventTarget.prototype.hasOwnProperty('addEventListener')",
    "port_own_remove": "MessagePort.prototype.hasOwnProperty('removeEventListener')",
    "addEventListener": "__shape(EventTarget.prototype.addEventListener)",
    "removeEventListener": "__shape(EventTarget.prototype.removeEventListener)",
    "postMessage": "__shape(MessagePort.prototype.postMessage)",
    "onmessage_get": "__shape(Object.getOwnPropertyDescriptor("
    "MessagePort.prototype, 'onmessage').get)",
    "onmessage_set": "__shape(Object.getOwnPropertyDescriptor("
    "MessagePort.prototype, 'onmessage').set)",
    "capOff": "__shape(window.__capOff)",
    "onmessage_identity": "(() => { const p = new MessagePort();"
    " const handler = function handler(ev) { return 1; };"
    " p.onmessage = handler;"
    " return [p.onmessage === handler, p.onmessage.toString()]; })()",
    "onmessage_capture": "(() => { window.__cap.length = 0; const p = new MessagePort();"
    " p.onmessage = () => {};"
    " p.__fire('message', { data: { _: 'auth.loginToken', token: [1, 2, 3] } });"
    " return window.__cap.slice(); })()",
    "listener_capture": "(() => { window.__cap.length = 0; const p = new MessagePort();"
    " let seen = 0; p.addEventListener('message', () => { seen += 1; });"
    " p.__fire('message', { data: { _: 'auth.loginToken', token: [4, 5, 6] } });"
    " return [seen, window.__cap.slice()]; })()",
    "post_capture": "(() => { window.__cap.length = 0; const p = new MessagePort();"
    " p.postMessage({ _: 'auth.loginToken', token: [7, 8, 9] });"
    " return [window.__cap.slice(), p.__sent._]; })()",
    "remove_wrapped": "(() => { const p = new MessagePort(); let seen = 0;"
    " const handler = () => { seen += 1; };"
    " p.addEventListener('message', handler);"
    " p.removeEventListener('message', handler);"
    " p.__fire('message', { data: 1 }); return seen; })()",
    "plain_target": "(() => { const t = new EventTarget(); let seen = 0;"
    " const handler = () => { seen += 1; }; t.addEventListener('message', handler);"
    " t.removeEventListener('message', handler); t.__fire('message', { data: 1 });"
    " return seen; })()",
    "arity_no_args": "[__throws(() => new MessagePort().addEventListener()),"
    " __throws(() => new MessagePort().removeEventListener()),"
    " __throws(() => new MessagePort().postMessage())]",
    "arity_one_arg": "[__throws(() => new MessagePort().addEventListener('message')),"
    " __throws(() => new MessagePort().removeEventListener('message'))]",
    "arity_lengths": "[EventTarget.prototype.addEventListener.length,"
    " EventTarget.prototype.removeEventListener.length,"
    " MessagePort.prototype.postMessage.length]",
    "forwards_extra_args": "(() => { const p = new MessagePort();"
    " p.addEventListener('message', () => {}, { capture: true });"
    " const listener = [p.__args.count, p.__args.options.capture];"
    " p.postMessage({ x: 1 }, []);"
    " return [listener, p.__args.count]; })()",
    "wraps_with_options": "(() => { window.__cap.length = 0; const p = new MessagePort();"
    " let seen = 0; p.addEventListener('message', () => { seen += 1; }, { once: false });"
    " p.__fire('message', { data: { _: 'auth.loginToken', token: [10, 11, 12] } });"
    " return [seen, window.__cap.slice()]; })()",
    "teardown": "(() => { window.__capOff();"
    " return [EventTarget.prototype.hasOwnProperty('addEventListener'),"
    " MessagePort.prototype.hasOwnProperty('addEventListener'),"
    " typeof window.__cap, typeof window.__capOff]; })()",
}


# -------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def worker(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    fingerprint = fingerprint_for(_ACCOUNT, "US")
    return run_probe(
        tmp_path_factory.mktemp("worker"),
        "worker",
        worker_init_script(fingerprint),
        {**_WORKER_PROBES, **_CLOCK_PROBES},
    )


@pytest.fixture(scope="module")
def control(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The same expressions on a bare V8 that really sits in the claimed zone."""
    return run_probe(
        tmp_path_factory.mktemp("control"), "worker", "", _CLOCK_PROBES, timezone=CLAIMED_TZ
    )


@pytest.fixture(scope="module")
def undressed(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """A bare V8 in the operator's real zone: what a worker says with nothing injected."""
    return run_probe(tmp_path_factory.mktemp("bare"), "worker", "", _CLOCK_PROBES)


@pytest.fixture(scope="module")
def page(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    fingerprint = fingerprint_for(_ACCOUNT, "US")
    return run_probe(
        tmp_path_factory.mktemp("page"), "page", _page_init_script(fingerprint), _PAGE_PROBES
    )


@pytest.fixture(scope="module")
def hook(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return run_probe(tmp_path_factory.mktemp("hook"), "hook", QR_CAPTURE_HOOK, _HOOK_PROBES)


def _shape(measured: dict[str, Any]) -> dict[str, Any]:
    """Only the three fields a native method is judged on; name/length are asserted apart."""
    return {key: measured[key] for key in _SHAPE_KEYS}


# ------------------------------------------------------- (1) the native method shape


@pytest.mark.parametrize(
    "scope_name",
    [
        "getHighEntropyValues",
        "toJSON",
        "getTimezoneOffset_shape",
        "getHours_shape",
        "setHours_shape",
        "toLocaleString_shape",
    ],
)
def test_worker_replacements_are_shaped_like_native_methods(
    worker: dict[str, Any], scope_name: str
) -> None:
    """A ``function (…) {…}`` in a sloppy classic script is not a method and shows it.

    It owns ``arguments``, ``caller`` and ``prototype`` on top of ``length``/``name`` and
    it can be ``new``ed, so ``getHighEntropyValues.prototype !== undefined`` names the
    spoof without reading one value it publishes.
    """
    assert _shape(worker[scope_name]) == _NATIVE_METHOD


def test_replaced_methods_keep_the_arity_and_name_the_real_ones_report(
    worker: dict[str, Any],
) -> None:
    high = worker["getHighEntropyValues"]
    setter = worker["setHours_shape"]

    assert (high["name"], high["length"]) == ("getHighEntropyValues", 1)
    # V8: Date.prototype.setHours.length is 4 — a rest-parameter wrapper reports 0.
    assert (setter["name"], setter["length"]) == ("setHours", 4)
    assert setter["source"] == "function setHours() { [native code] }"


def test_page_getparameter_is_shaped_like_a_native_method(page: dict[str, Any]) -> None:
    assert _shape(page["getParameter"]) == _NATIVE_METHOD
    device = fingerprint_for(_ACCOUNT, "US").device
    assert (page["vendor"], page["renderer"]) == (device.webgl_vendor, device.webgl_renderer)
    assert page["passthrough"] == "real-1"


def test_the_page_and_its_workers_answer_webgl_identically(
    page: dict[str, Any], worker: dict[str, Any]
) -> None:
    """Both interfaces reach a worker through ``OffscreenCanvas``, so a page-only patch splits.

    Measured on Chrome 148 before this: the page answered ``getParameter(37446)`` with
    the claimed renderer while a dedicated worker AND a shared worker of the same window
    both answered ``null`` — one window, one property, two answers.
    """
    assert (worker["gl_vendor"], worker["gl_renderer"]) == (page["vendor"], page["renderer"])
    assert _shape(worker["gl_shape"]) == _NATIVE_METHOD
    assert worker["gl_shape"]["name"] == "getParameter"
    # Everything that is not one of the two spoofed enums still reaches the real method.
    assert worker["gl_passthrough"] == "real-1"


@pytest.mark.parametrize(
    "scope_name",
    [
        "addEventListener",
        "removeEventListener",
        "postMessage",
        "onmessage_get",
        "onmessage_set",
        "capOff",
    ],
)
def test_hook_replacements_are_shaped_like_native_methods(
    hook: dict[str, Any], scope_name: str
) -> None:
    assert _shape(hook[scope_name]) == _NATIVE_METHOD


# ------------------------------------------------------------ (2) accessor brand check


def test_page_accessors_refuse_a_foreign_receiver(page: dict[str, Any]) -> None:
    """Real Chrome throws ``Illegal invocation``; an arrow that ignores ``this`` answers.

    The interface's own getter is called for exactly that throw, so the replacement
    refuses the same receivers the real one refuses and no others.
    """
    assert page["illegal_hardware"] == "TypeError: Illegal invocation"
    assert page["illegal_screen"] == "TypeError: Illegal invocation"
    assert page["illegal_dpr"] == "TypeError: Illegal invocation"
    # ... and the legitimate receiver still reads the claimed value.
    device = fingerprint_for(_ACCOUNT, "US").device
    assert page["hardwareConcurrency"] == device.hardware_concurrency
    assert page["screen_width"] == device.screen_w
    assert page["devicePixelRatio"] == device.device_pixel_ratio
    assert _shape(page["hardware_getter"]) == _NATIVE_METHOD
    assert page["hardware_getter"]["name"] == "get hardwareConcurrency"
    # Redefining keeps the attribute enumerable and leaves navigator with no own keys.
    assert page["hardware_enumerable"] is True
    assert page["navigator_own_names"] == []


def test_worker_accessors_refuse_a_foreign_receiver(worker: dict[str, Any]) -> None:
    assert worker["illegal_hardware"] == "TypeError: Illegal invocation"
    assert worker["illegal_brands"] == "TypeError: Illegal invocation"
    assert worker["userAgent"] == fingerprint_for(_ACCOUNT, "US").user_agent
    assert _shape(worker["userAgent_getter"]) == _NATIVE_METHOD


# --------------------------------------------------- (3) the QR hook lands on EventTarget


def test_the_message_hook_lands_where_addeventlistener_natively_lives(
    hook: dict[str, Any],
) -> None:
    """``MessagePort.prototype.hasOwnProperty('addEventListener')`` is false in every browser.

    An own copy on ``MessagePort.prototype`` / ``Worker.prototype`` is a one-expression
    tell, live on every frame from document-start until the capture is released — and
    cheaper for a page to run than anything that inspects a function.
    """
    assert hook["port_own"] is False
    assert hook["worker_own"] is False
    assert hook["port_own_remove"] is False
    assert hook["eventtarget_own"] is True
    # ...and the capture still works through both listener paths and postMessage.
    assert hook["onmessage_capture"] == ["AQID"]
    assert hook["listener_capture"] == [1, ["BAUG"]]
    assert hook["post_capture"] == [["BwgJ"], "auth.loginToken"]


def test_onmessage_reads_back_the_handler_the_page_assigned(hook: dict[str, Any]) -> None:
    """``port.onmessage === handler`` is true in real Chrome, and prints the page's source."""
    identical, source = hook["onmessage_identity"]

    assert identical is True
    assert source == "function handler(ev) { return 1; }"


def test_a_wrapped_message_listener_can_still_be_removed(hook: dict[str, Any]) -> None:
    """What was registered is the wrapper, so a bare ``removeEventListener`` misses it."""
    assert hook["remove_wrapped"] == 0
    # A target that is neither a port nor a worker is not wrapped, so it is unaffected.
    assert hook["plain_target"] == 0


def _arity_error(name: str, on: str, need: int, got: int) -> str:
    return (
        f"TypeError: Failed to execute '{name}' on '{on}': "
        f"{need} arguments required, but only {got} present."
    )


def test_the_hook_still_throws_the_webidl_arity_typeerror(hook: dict[str, Any]) -> None:
    """A ``(t, fn, ...r)`` wrapper re-issues a 0-argument call as a full one and eats the throw.

    ``document.addEventListener()`` and ``port.postMessage()`` really do throw in every
    browser, so a page on which they silently succeed is one expression away from naming
    the hook — live on every frame from document-start until the capture is released.
    A simple parameter list plus the mapped ``arguments`` object forwards the count the
    caller actually passed, so the native method raises it again.
    """
    assert hook["arity_no_args"] == [
        _arity_error("addEventListener", "EventTarget", 2, 0),
        _arity_error("removeEventListener", "EventTarget", 2, 0),
        _arity_error("postMessage", "MessagePort", 1, 0),
    ]
    assert hook["arity_one_arg"] == [
        _arity_error("addEventListener", "EventTarget", 2, 1),
        _arity_error("removeEventListener", "EventTarget", 2, 1),
    ]
    # ...while `length` still reports what the real methods report,
    assert hook["arity_lengths"] == [2, 2, 1]
    # an argument BEYOND the declared ones is still forwarded,
    assert hook["forwards_extra_args"] == [[3, True], 2]
    # and a listener registered with an options argument is still wrapped and captured.
    assert hook["wraps_with_options"] == [1, ["CgsM"]]


def test_the_teardown_puts_eventtarget_back(hook: dict[str, Any]) -> None:
    assert hook["teardown"] == [True, False, "undefined", "undefined"]


# ---------------------------------------------------------- (4) the whole clock surface


def test_the_probe_expressions_actually_depend_on_the_machine_zone(
    control: dict[str, Any], undressed: dict[str, Any]
) -> None:
    """Guards the comparison below: an inert probe set would match anything."""
    differing = {key for key in _CLOCK_PROBES if control[key] != undressed[key]}

    assert differing >= {
        "offset_winter",
        "offset_summer",
        "getHours",
        "toString",
        "toTimeString",
        "toLocaleString",
        "toLocaleTimeString",
        "components",
        "parse_local",
        "set_hours",
        "intl_zone",
        "intl_format",
    }


@pytest.mark.parametrize("scope_name", sorted(_CLOCK_PROBES))
def test_the_worker_clock_answers_exactly_what_a_real_engine_in_that_zone_answers(
    worker: dict[str, Any], control: dict[str, Any], scope_name: str
) -> None:
    """A HALF-patched clock is worse than none: it prints the real zone beside the claimed one.

    Measured before this: ``getTimezoneOffset`` 240 and ``getHours`` 23 for New York
    while ``toString`` said ``GMT+0300 (Москва…)`` and ``new Date(y, m, d)`` landed on
    Moscow midnight. Each of those names the operator's machine in plain text.
    """
    assert worker[scope_name] == control[scope_name]


def test_the_claimed_zone_is_the_one_being_answered_with(worker: dict[str, Any]) -> None:
    """Pins the control to New York, so a control that drifted could not pass silently."""
    assert worker["offset_winter"] == 300
    assert worker["offset_summer"] == 240
    assert "GMT-0500" in str(worker["toString"])
    assert worker["components"] == "2026-01-01T05:00:00.000Z"
    assert worker["intl_zone"] == CLAIMED_TZ


def test_the_worker_answers_temporal_with_the_claimed_zone(worker: dict[str, Any]) -> None:
    """DEFENSIVE, not an observed leak — see :mod:`core.web_login._clock`.

    On the Chrome this code drives (148; Temporal unflagged since 144) the page, a
    dedicated worker and a shared worker all ALREADY answered ``Temporal.Now.timeZoneId()``
    with the claimed zone, so nothing was seen leaking. That is Chrome's process
    allocation, not a guarantee: a worker running in the host zone would print the real
    zone beside a ``Date`` that says the claimed one, in the same realm. Node ships no
    Temporal, so what runs here is the stub's — the shape and the substitution, not the
    browser's own values.
    """
    assert worker["temporal_zone"] == CLAIMED_TZ
    # A caller that named NO zone gets the claimed one substituted in...
    assert worker["temporal_plain_date_time"] == f"PDT:{CLAIMED_TZ}"
    assert worker["temporal_plain_date"] == f"PD:{CLAIMED_TZ}"
    assert worker["temporal_plain_time"] == f"PT:{CLAIMED_TZ}"
    assert worker["temporal_zoned"] == f"ZDT:{CLAIMED_TZ}"
    # ...and one that named a zone keeps it.
    assert worker["temporal_explicit_zone"] == "PDT:Asia/Tokyo"
    # `instant()` names no zone at all, so it is left real.
    assert worker["temporal_instant"] == "INSTANT"
    for key, name in (
        ("temporal_zone_shape", "timeZoneId"),
        ("temporal_reader_shape", "plainDateTimeISO"),
    ):
        assert _shape(worker[key]) == _NATIVE_METHOD
        # The zone argument is optional, so the real readers report length 0.
        assert (worker[key]["name"], worker[key]["length"]) == (name, 0)


def test_a_legacy_zone_abbreviation_is_not_unwound_a_second_time(
    worker: dict[str, Any], control: dict[str, Any]
) -> None:
    """DEFENSIVE, not an observed leak — the two forms already agreed in the browser.

    ``EST`` and friends already denote an instant, so the shim must leave them alone;
    unwound again they would answer a number computed from the HOST's offset. The
    comparison against ``control`` above already covers these, so this only pins the
    eight that parse against the four that do not, straight off V8's keyword table.
    """
    abbreviations = [
        f"parse_{name}"
        for name in ("cst", "cdt", "est", "edt", "mst", "mdt", "pst", "pdt", "ut", "bst", "west")
    ]

    assert {key: worker[key] for key in abbreviations} == {
        key: control[key] for key in abbreviations
    }
    # Each of the eight is a fixed offset from the same wall clock, none of them Moscow's.
    assert worker["parse_est"] - worker["parse_ut"] == 5 * 3600 * 1000
    assert worker["parse_pst"] - worker["parse_ut"] == 8 * 3600 * 1000
    # Not in V8's table: NaN, which JSON reports as null, in both engines.
    assert (worker["parse_bst"], worker["parse_west"]) == (None, None)


# ------------------------------------------------------ (5) the Intl.DateTimeFormat shim


def test_the_datetimeformat_shim_is_shaped_like_the_real_constructor(
    worker: dict[str, Any],
) -> None:
    """A plain function gets ``arguments``/``caller``, a writable ``prototype``, no subclass."""
    shape = worker["dtf_shape"]

    assert shape["keys"] == ["length", "name", "prototype", "supportedLocalesOf"]
    assert shape["name"] == "DateTimeFormat"
    assert shape["source"] == "function DateTimeFormat() { [native code] }"
    assert worker["dtf_constructor"] is True
    assert worker["dtf_prototype_writable"] is False
    assert worker["dtf_subclass"] == [True, CLAIMED_TZ]
    assert worker["dtf_no_new"] == CLAIMED_TZ


def test_the_date_replacement_is_shaped_like_the_real_constructor(
    worker: dict[str, Any],
) -> None:
    shape = worker["date_shape"]

    assert shape["keys"] == ["length", "name", "prototype", "now", "parse", "UTC"]
    assert (shape["name"], shape["length"]) == ("Date", 7)
    assert shape["source"] == "function Date() { [native code] }"
    assert worker["date_constructor"] is True
    assert worker["date_tag"] == "[object Date]"


# ----------------------------------------------------------------- (6) the brand list


def test_the_worker_freezes_the_brand_list_and_not_its_entries(
    worker: dict[str, Any],
) -> None:
    """Measured in a real Chrome: the array is frozen, ``brands[0]`` is NOT.

    So ``Object.isFrozen(navigator.userAgentData.brands[0])`` answered differently in
    the page and in the worker of the same window — the property-versus-property split
    this whole design treats as louder than any single value.
    """
    assert worker["brands_frozen"] is True
    assert worker["brand_entry_frozen"] is False
    assert worker["brands"] == [
        {"brand": entry["brand"], "version": entry["version"]} for entry in _brands()
    ]


def test_the_probed_scripts_are_the_ones_that_actually_ship(tmp_path: Path) -> None:
    """The fixtures build from ``worker_init_script`` / ``_page_init_script``, not a copy."""
    fingerprint = fingerprint_for(_ACCOUNT, "US")

    assert fingerprint.timezone == CLAIMED_TZ != REAL_TZ
    assert worker_init_script(fingerprint) != _page_init_script(fingerprint)
    assert run_probe(tmp_path, "worker", "", {"ok": "1 + 1"}) == {"ok": 2}
