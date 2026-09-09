// Non-component helpers shared by the profile modal and its media tabs
// (PhotoTab / StoriesTab / MusicTab). Internal to the slice.
import type { CSSProperties } from 'react';

import type { ErrorDetail, ErrorEnvelope, ProfilePhotoView } from '@/shared/api';

import type { Translate } from './_channelsShared';

// Fallback tile background when a media item carries no thumbnail. The two stops are
// decorative and exist only to differ from each other — they carry no meaning any other
// element shares, so they are deliberately NOT tokens; naming them would put two
// single-use roles in the canon and imply the UI means something by them.
const TILE = 'linear-gradient(135deg,#cfd8ec,#e7dfd2)';

// Telegram's own profile-text limits, mirroring `PROFILE_NAME_MAX_LENGTH` and
// `PROFILE_BIO_MAX_LENGTH` in schemas/accounts.py. Here rather than inline in the
// single-account form because the bulk editor validates the same fields, and two
// copies of "70" would drift the moment one side learned a new limit.
export const PROFILE_NAME_MAX = 64;
export const PROFILE_BIO_MAX = 70;

/** The three profile-text fields a bulk save can write, and their own limits. */
export type TextFieldKey = 'first_name' | 'last_name' | 'bio';
export const TEXT_FIELDS = [
  'first_name',
  'last_name',
  'bio',
] as const satisfies readonly TextFieldKey[];
export const TEXT_MAX: Record<TextFieldKey, number> = {
  first_name: PROFILE_NAME_MAX,
  last_name: PROFILE_NAME_MAX,
  bio: PROFILE_BIO_MAX,
};

/** The three Telegram privacy keys the profile tab writes, and their three levels. */
export type PrivacyKey = 'profile_photo' | 'bio' | 'last_seen';
export type PrivacyLevel = 'everybody' | 'contacts' | 'nobody';
export const PRIVACY_KEYS = [
  'profile_photo',
  'bio',
  'last_seen',
] as const satisfies readonly PrivacyKey[];
export const PRIVACY_LEVELS = [
  'everybody',
  'contacts',
  'nobody',
] as const satisfies readonly PrivacyLevel[];

export function tileStyle(uri: string | null | undefined, ratio: string): CSSProperties {
  if (!uri) return { aspectRatio: ratio, background: TILE };
  return {
    aspectRatio: ratio,
    backgroundImage: `url(${uri})`,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
  };
}

// Defensive dedup by photo_id: Telegram can momentarily echo a duplicate id
// during a make-main promotion, and a repeated tile would misrender.
export function dedupeById(photos: ProfilePhotoView[]): ProfilePhotoView[] {
  const seen = new Set<string>();
  return photos.filter((photo) => {
    if (seen.has(photo.photo_id)) return false;
    seen.add(photo.photo_id);
    return true;
  });
}

// Every /api/v1 failure rejects with this envelope, and since api/errors.py
// declares it in the OpenAPI document its shape is the generated contract —
// no hand-written cast that a change to the wire shape could silently outdate.
// A rejection that is NOT ours (a transport error) simply has no `error`.
function errorDetail(err: unknown): ErrorDetail | undefined {
  return (err as Partial<ErrorEnvelope> | null | undefined)?.error;
}

function errorMessage(err: unknown): string | null {
  const message = errorDetail(err)?.message;
  return typeof message === 'string' && message.trim() ? message : null;
}

// Which text field a rejected save belongs under; null means "no specific field"
// — the general save-error box. Two kinds of key resolve here: a stable code,
// which names the field itself, and a 422's `fields` entry. A 422 carries the
// same `validation_error` code whatever was wrong, so its `body.<name>` keys
// (api/errors.py `_handle_validation_error`) are the ONLY thing saying which
// input the server refused — reachable whenever the zod schema and the Pydantic
// one disagree (bio counted trimmed here, untrimmed there).
const PROFILE_ERROR_FIELDS: Record<string, 'username' | 'bio'> = {
  username_occupied: 'username',
  username_invalid: 'username',
  about_too_long: 'bio',
  'body.username': 'username',
  'body.bio': 'bio',
};

export function profileErrorField(err: unknown): 'username' | 'bio' | null {
  const detail = errorDetail(err);
  for (const key of [detail?.message ?? '', ...Object.keys(detail?.fields ?? {})]) {
    const field = PROFILE_ERROR_FIELDS[key];
    if (field) return field;
  }
  return null;
}

// The three code tables in the order the global mutation toast walks them
// (shared/lib/query-client.ts). Same chain here so the inline message and the
// toast beside it can never word one failure differently: `unavailable` — which
// every account-editing action can return — has copy only under
// accounts.channel.code, and duplicating it into a second namespace is how two
// copies of one string start to drift.
const codeKeys = (message: string): string[] => [
  `accounts.profile.code.${message}`,
  `accounts.channel.code.${message}`,
  `accounts.addStory.code.${message}`,
];

// The backend serialises envelope fields as strings (api/errors.py), so parse
// rather than expect a number.
export function retryAfterSeconds(err: unknown): number | undefined {
  const seconds = Number(errorDetail(err)?.fields?.retry_after_seconds ?? NaN);
  return Number.isFinite(seconds) ? seconds : undefined;
}

// Render one locale-neutral code. Anything with no copy shows as-is — including
// a refused live READ, whose reason is a content-free label the gateway formats
// ("FloodWait(300s)", "RPC: AuthKeyUnregisteredError", "unavailable: …") rather
// than a code, so it falls through to itself instead of a generic sentence.
export function profileCodeText(message: string, t: Translate, seconds?: number): string {
  return t(codeKeys(message), { defaultValue: message, s: seconds ?? '?' });
}

// A failed profile save rejects with the envelope whose `message` is a stable
// code (username_occupied, flood_wait, …); an unknown code shows as-is, same
// contract as channelErrorText.
export function profileErrorText(err: unknown, t: Translate, fallback: string): string {
  const message = errorMessage(err);
  if (!message) return fallback;
  return profileCodeText(message, t, retryAfterSeconds(err));
}
