# A neutral app title: the API each session uses

Every session Telebuba creates shows the registered app's title in the account's session list ("Active sessions"). The app Telebuba used so far is titled "userbot01". A second app with a neutral title can be registered and used for new keys.

## The rule: a key keeps the API that made it

An existing key is never connected with another API. Telethon would allow it and Telegram accepts it, but people who work with this warn it gets accounts banned (opentele: "Don't use a same session with multiple different APIs, you might be banned"), and nothing documents it as safe. So the API is recorded per session (`device_fingerprints.api_id`, migration 115):

- `NULL`: every session from before this, and every import. These connect with `TELEGRAM__API_ID` (desktop) or `TELEGRAM__MOBILE_API_ID` (mobile), as always.
- The new app's id: these connect with `TELEGRAM__NEW_API_ID` / `TELEGRAM__NEW_API_HASH`, and only while both are set. A session whose API is not configured is refused (`ApiCredentialsMissingError`), never switched.

Only a new key gets the new app (`TelegramClientRequest.new_key`), and the id is recorded once the sign-in succeeds:

- a re-key's new session (the re-key is therefore also how an account moves to the new title: the new session is made with it, and the old one, on the old title, is closed by the operator later);
- a second device (desktop);
- a phone, QR or passkey sign-in over a key Telegram already ended (`unauthorized`, `session_error`) or a QR placeholder.

A sign-in on an account whose key may still work keeps the old app.

## Registering the app (the owner)

On https://my.telegram.org, signed in with a phone number that is NOT one of the fleet's accounts:

1. "API development tools".
2. App title: a neutral name, e.g. `Desktop Notes` (not "Telegram" unless preceded by "Unofficial", no "bot"/"userbot").
3. Short name: 5 to 32 letters/digits, e.g. `desknotes`.
4. Platform: Desktop. URL and description may stay empty.
5. Create, then put the shown `api_id` and `api_hash` into `.env` as `TELEGRAM__NEW_API_ID` and `TELEGRAM__NEW_API_HASH`, and restart Telebuba.

Keep the old `TELEGRAM__API_ID` / `TELEGRAM__API_HASH` as they are: every existing session still needs them. Never paste the hash into a chat or a commit.
