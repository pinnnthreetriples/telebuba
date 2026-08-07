---
last_updated: 2026-08-06
---

# Add or Rename a Log Event

1. Name domain-feed events as domain + action; the feed is selected by prefix from the shared logs table.
2. Prefix by the domain that reaches the code. Shared Telegram actions bind their domain once in the service seam; genuinely cross-domain pool/infrastructure events stay unprefixed.
3. Do not reuse one event name for semantically different domains.
4. Add or verify the logEvent label in both frontend/src/shared/i18n/en.json and ru.json. The SPA may strip a domain prefix before lookup, so verify the actual lookup path before adding duplicate keys.
5. Colour is decided twice and the NAME wins: the SPA's severity map reads the event's suffix and overrides the level's colour. Pick the level for the operator's verdict — a dead end they must act on is ERROR, green reads as "noted, carry on" — then check the suffix does not overrule it. A drop event with no hint text leaves the operator nothing to act on.
6. `extra["reason"]` is a second vocabulary rendered beside the label. A bare ratio there already means "position in a daily budget", so putting anything else in it makes an unrelated event read as a spent budget. Carry the failing check's code in `reason` and counters in fields of their own.
7. User-controlled prefix filtering goes through the repository's escaped prefix clause; never hand-build SQL LIKE.
8. Keep extra bounded and secret-safe. Exception strings, session paths and proxy credentials belong in private application logs, not persisted/streamed event payloads.
9. Test at the caller's layer and assert the event name/payload contract, not only the log level.

tests/test_logevent_i18n_parity.py and tests/test_logevent_extra_bounds.py are the executable guards; read their current limits instead of copying implementation detail here.
