"""The worker half of the timezone override: the WHOLE local-time surface, not part of it.

``Emulation.setTimezoneOverride`` reaches page sessions only, so a browser-level worker
keeps answering with the operator's own Windows zone while the page one frame away
claims the account's. This fragment closes that gap inside the worker — and it has to
close all of it, because a HALF-patched clock is louder than an unpatched one.

Measured on a Moscow machine claiming ``America/New_York`` with only
``getTimezoneOffset`` and the seven ``get*`` readers shifted::

    getTimezoneOffset() ->  240          (New York, correct)
    getHours()          ->   23          (New York, correct)
    toString()          -> "... GMT+0300 (Москва, стандартное время)"
    new Date(2026, 0, 1) -> 2025-12-31T21:00:00Z   (Moscow midnight)

Any one of those three lines next to ``getHours()`` in the same realm names the real
machine's zone in plain text, which is worse than a worker that merely disagrees with
its page. So everything that reads or writes LOCAL time is rebuilt here from the real
``Intl`` machinery captured before the shim goes in:

* the readers and the writers (``setHours`` and friends land on the claimed zone's wall
  clock, so ``d.setHours(0,0,0,0); d.getHours()`` is still ``0``);
* the three ``to*String`` formatters, whose GMT offset and long zone name come from
  ``formatToParts``, and the three ``toLocale*`` ones, which go through the shimmed
  ``Intl.DateTimeFormat``;
* the ``Date(y, m, d, …)`` constructor and zone-less ``Date.parse``, which name a wall
  clock rather than an instant and so have to be resolved against the claimed zone.

``fromWall`` resolves a wall clock twice — once with the offset at the naive reading,
then with the offset at that guess — which is what puts a time near a DST boundary on
the right side of it.

``Temporal`` is covered for the same reason and NOT because anything was seen leaking.
Measured on the Chrome this code drives (148, Temporal unflagged since 144), the page,
a dedicated worker and a shared worker all already answered ``Temporal.Now.timeZoneId()``
with the CLAIMED zone, so today there is nothing to close. That is a property of how
Chrome happened to allocate the process, not something this module guarantees — and a
worker that ever ran in the host's zone would print the real zone next to a ``Date`` that
says the claimed one, in the same realm, which is exactly the half-patched clock above.
``Temporal.Now.instant()`` names no zone and is left alone.

The ``Date`` and ``Intl.DateTimeFormat`` replacements are proxies rather than plain
functions on purpose: a proxy over the real constructor inherits its own-key set, its
non-writable ``prototype`` and its ``[[Construct]]`` behaviour (so ``class X extends
Intl.DateTimeFormat {}`` still builds a real instance), none of which a ``function``
expression in a sloppy classic script can imitate. Only ``prototype.constructor`` has
to be re-pointed by hand.
"""

from __future__ import annotations

CLOCK = r"""
  const RealDate = Date;
  const RealFormat = Intl.DateTimeFormat;
  const getTime = RealDate.prototype.getTime;
  const setTime = RealDate.prototype.setTime;
  const realOffset = RealDate.prototype.getTimezoneOffset;
  const realParse = RealDate.parse;
  const zoned = (args) => {
    const options = Object.assign({}, args[1]);
    if (options.timeZone === undefined) options.timeZone = C.timezone;
    return [args[0] === undefined ? C.locale : args[0], options];
  };
  const ZonedFormat = new Proxy(RealFormat, {
    apply: (target, self, args) => Reflect.apply(target, self, zoned(args)),
    construct: (target, args, kind) => Reflect.construct(target, zoned(args), kind),
  });
  // A proxy keeps the real own keys, the non-writable `prototype` and subclassing;
  // `prototype.constructor` is the one identity it cannot carry over by itself.
  Object.defineProperty(RealFormat.prototype, 'constructor',
    { value: ZonedFormat, writable: true, configurable: true });
  Intl.DateTimeFormat = N(ZonedFormat, 'DateTimeFormat');
  // ``en-US`` everywhere the result is read back as a number or has to come out in V8's
  // own fixed English (``Thu Jan 01 2026``); the browser's own locale only where V8
  // itself localises, which is the parenthesised zone name and nothing else.
  const SHAPES = {
    offset: ['en-US', { hourCycle: 'h23', year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }],
    date: ['en-US', { weekday: 'short', month: 'short', day: '2-digit', year: 'numeric' }],
    time: ['en-US', { hourCycle: 'h23', hour: '2-digit', minute: '2-digit',
      second: '2-digit' }],
    zone: [undefined, { timeZoneName: 'long' }],
  };
  // One formatter per shape for the worker's whole life: building an Intl.DateTimeFormat
  // costs more than everything else on this path together (measured on V8: `getHours`
  // 69 us -> 3 us, `new Date(y, m, d)` 132 us -> 7 us, `toString` 272 us -> 9 us).
  const made = {};
  const parts = (ms, kind) => {
    made[kind] ||= new RealFormat(SHAPES[kind][0],
      Object.assign({ timeZone: C.timezone }, SHAPES[kind][1]));
    const out = {};
    for (const part of made[kind].formatToParts(new RealDate(ms))) out[part.type] = part.value;
    return out;
  };
  const offsetAt = (ms) => {
    try {
      const p = parts(ms, 'offset');
      const wall = RealDate.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24,
        +p.minute, +p.second);
      return Math.round((ms - wall) / 60000);
    } catch (e) { return 0; }
  };
  // The claimed zone's wall clock packed as if it were UTC, and back again.
  const wallOf = (ms) => ms - offsetAt(ms) * 60000;
  const fromWall = (wall) =>
    (isFinite(wall) ? wall + offsetAt(wall + offsetAt(wall) * 60000) * 60000 : NaN);
  const pad = (n) => (n < 10 ? '0' : '') + n;
  const dateText = (ms) => {
    const p = parts(ms, 'date');
    return p.weekday + ' ' + p.month + ' ' + p.day + ' ' + p.year;
  };
  const timeText = (ms) => {
    const p = parts(ms, 'time');
    const off = offsetAt(ms);
    const abs = Math.abs(off);
    return p.hour + ':' + p.minute + ':' + p.second + ' GMT' + (off > 0 ? '-' : '+')
      + pad(Math.floor(abs / 60)) + pad(abs % 60)
      + ' (' + parts(ms, 'zone').timeZoneName + ')';
  };
  // `getTime` is the brand check every method below opens with, so a foreign `this`
  // throws rather than answering. The MESSAGE matches Chrome's only for the readers,
  // which is where Chrome also routes through getTime; the formatters and setters say
  // "this is not a Date object." where Chrome says "Method Date.prototype.<x> called on
  // incompatible receiver". Reproducing that per method costs ~14 wrappers to change a
  // string that leaks nothing, so the throw is matched and the wording is not.
  RealDate.prototype.getTimezoneOffset =
    F({ f() { return offsetAt(getTime.call(this)); } }, 'getTimezoneOffset');
  for (const unit of ['FullYear', 'Month', 'Date', 'Day', 'Hours', 'Minutes', 'Seconds']) {
    const utc = RealDate.prototype['getUTC' + unit];
    RealDate.prototype['get' + unit] = F({ f() {
      return utc.call(new RealDate(wallOf(getTime.call(this))));
    } }, 'get' + unit);
  }
  for (const unit of ['FullYear', 'Month', 'Date', 'Hours', 'Minutes', 'Seconds',
      'Milliseconds']) {
    const utc = RealDate.prototype['setUTC' + unit];
    RealDate.prototype['set' + unit] = F({ f(...args) {
      const wall = new RealDate(wallOf(getTime.call(this)));
      utc.apply(wall, args);
      return setTime.call(this, fromWall(getTime.call(wall)));
    } }, 'set' + unit, utc.length);
  }
  RealDate.prototype.getYear = F({ f() {
    return new RealDate(wallOf(getTime.call(this))).getUTCFullYear() - 1900;
  } }, 'getYear');
  RealDate.prototype.setYear = F({ f(y) {
    const n = Math.trunc(Number(y));
    return RealDate.prototype.setFullYear.call(this, n >= 0 && n <= 99 ? 1900 + n : Number(y));
  } }, 'setYear');
  const INVALID = 'Invalid Date';
  RealDate.prototype.toString = F({ f() {
    const t = getTime.call(this);
    return isNaN(t) ? INVALID : dateText(t) + ' ' + timeText(t);
  } }, 'toString');
  RealDate.prototype.toDateString = F({ f() {
    const t = getTime.call(this);
    return isNaN(t) ? INVALID : dateText(t);
  } }, 'toDateString');
  RealDate.prototype.toTimeString = F({ f() {
    const t = getTime.call(this);
    return isNaN(t) ? INVALID : timeText(t);
  } }, 'toTimeString');
  // ToDateTimeOptions: a caller that named any component keeps exactly what it asked
  // for; one that named none gets the numeric defaults, as the real methods do.
  const REQUIRED = { date: ['weekday', 'year', 'month', 'day'],
    time: ['dayPeriod', 'hour', 'minute', 'second', 'fractionalSecondDigits'] };
  const shape = (options, kinds) => {
    const out = Object.assign({}, options);
    let named = out.dateStyle !== undefined || out.timeStyle !== undefined;
    for (const kind of kinds) {
      for (const key of REQUIRED[kind]) if (out[key] !== undefined) named = true;
    }
    if (named) return out;
    if (kinds.indexOf('date') >= 0) {
      out.year = 'numeric'; out.month = 'numeric'; out.day = 'numeric';
    }
    if (kinds.indexOf('time') >= 0) {
      out.hour = 'numeric'; out.minute = 'numeric'; out.second = 'numeric';
    }
    return out;
  };
  for (const pair of [['toLocaleString', ['date', 'time']],
      ['toLocaleDateString', ['date']], ['toLocaleTimeString', ['time']]]) {
    const kinds = pair[1];
    RealDate.prototype[pair[0]] = F({ f(...args) {
      const t = getTime.call(this);
      return isNaN(t) ? INVALID : new ZonedFormat(args[0], shape(args[1], kinds)).format(t);
    } }, pair[0], 0);
  }
  // A string that names its own zone (or is an ISO date, which the spec reads as UTC)
  // already denotes an instant; anything else denoted a wall clock in the machine's
  // real zone, so it is unwound with the REAL offset and re-read in the claimed one.
  // ``[CEMP][SD]T`` is the rest of V8's legacy keyword table — exactly CDT, CST, EDT,
  // EST, MDT, MST, PDT, PST parse (BST, CET, AEST, JST are NaN) — and each of those
  // already fixes an offset, so unwinding one a second time would answer a number that
  // depends on the HOST's offset. Defensive, like the Temporal block: in the browser
  // configuration measured, the abbreviated and the numeric forms already agreed.
  // The parenthetical is a SUFFIX of a real zone token, never a token of its own:
  // V8 reads parentheses as a comment, so treating them as a zone would classify any
  // zone-less local string merely ending in one ("Jul 1 2026 00:00:00 (x)") as an
  // instant, skip the unwind, and answer a number built from the HOST's offset —
  // evaluated in January and in July that names the operator's real zone outright.
  // It has to be here at all so ``new Date(d.toString())`` round-trips, since our own
  // toString ends in "GMT-0500 (Eastern Standard Time)".
  const ZONE_TAIL = /(?:Z|[+-]\d{2}:?\d{2}|GMT|UTC|UT|[CEMP][SD]T)\s*(?:\([^)]*\))?\s*$/i;
  const DATE_ONLY = /^[+-]?\d{4,6}(-\d{2}(-\d{2})?)?$/;
  RealDate.parse = F({ f(text) {
    const s = String(text);
    const t = realParse.call(RealDate, s);
    if (isNaN(t) || DATE_ONLY.test(s.trim()) || ZONE_TAIL.test(s.trim())) return t;
    return fromWall(t - realOffset.call(new RealDate(t)) * 60000);
  } }, 'parse');
  const primitive = (v) => {
    if (v === null || typeof v !== 'object') return v;
    const hint = v[Symbol.toPrimitive];
    const p = hint ? hint.call(v, 'default') : v.valueOf();
    return p !== null && typeof p === 'object' ? String(v) : p;
  };
  const fromParts = (args) => {
    const y = Math.trunc(Number(args[0]));
    const at = (i, dflt) => (args.length > i ? Number(args[i]) : dflt);
    return fromWall(RealDate.UTC(y >= 0 && y <= 99 ? 1900 + y : Number(args[0]),
      at(1, 0), at(2, 1), at(3, 0), at(4, 0), at(5, 0), at(6, 0)));
  };
  self.Date = N(new Proxy(RealDate, {
    apply() {
      const now = RealDate.now();
      return dateText(now) + ' ' + timeText(now);
    },
    construct(target, args, kind) {
      let t = RealDate.now();
      if (args.length === 1) {
        const v = args[0] instanceof RealDate ? getTime.call(args[0]) : primitive(args[0]);
        t = typeof v === 'string' ? RealDate.parse(v) : getTime.call(new RealDate(v));
      } else if (args.length > 1) t = fromParts(args);
      return Reflect.construct(target, [t], kind);
    },
  }), 'Date');
  Object.defineProperty(RealDate.prototype, 'constructor',
    { value: self.Date, writable: true, configurable: true });
  // Same realm, same clock: `Temporal.Now` names a zone in plain text, so it answers the
  // claimed one too. `instant()` is zone-free and stays real; the four ISO readers take
  // an OPTIONAL zone, so only a caller that named none is substituted for.
  if (self.Temporal) {
    const Now = Temporal.Now;
    Now.timeZoneId = F({ f() { return C.timezone; } }, 'timeZoneId');
    for (const name of ['plainDateTimeISO', 'plainDateISO', 'plainTimeISO',
        'zonedDateTimeISO']) {
      const real = Now[name];
      Now[name] = F({ f(zone) {
        return real.call(this, zone === undefined ? C.timezone : zone);
      } }, name, real.length);
    }
  }
"""
