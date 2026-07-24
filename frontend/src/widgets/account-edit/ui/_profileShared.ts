// Non-component helpers shared by the profile modal and its media tabs
// (PhotoTab / StoriesTab / MusicTab). Internal to the slice.
import type { CSSProperties } from 'react';

import type { ProfilePhotoView } from '@/shared/api';

import { envelopeMessage, type Translate } from './_channelsShared';

// Fallback tile background when a media item carries no thumbnail.
const TILE = 'linear-gradient(135deg,#cfd8ec,#e7dfd2)';

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

// Which text field a stable profile-save error code belongs under; null means
// "no specific field" — the general save-error box.
const PROFILE_ERROR_FIELDS: Record<string, 'username' | 'bio'> = {
  username_occupied: 'username',
  username_invalid: 'username',
  about_too_long: 'bio',
};

export function profileErrorField(err: unknown): 'username' | 'bio' | null {
  const message = envelopeMessage(err);
  return message ? (PROFILE_ERROR_FIELDS[message] ?? null) : null;
}

// A failed profile save rejects with the /api/v1 envelope whose `message` is a
// locale-neutral code (username_occupied, flood_wait, …) translated via
// accounts.profile.code.*; an unknown code shows as-is (same contract as
// channelErrorText). flood_wait interpolates fields.retry_after_seconds.
export function profileErrorText(err: unknown, t: Translate, fallback: string): string {
  const message = envelopeMessage(err);
  if (!message) return fallback;
  const fields = (err as { error?: { fields?: Record<string, unknown> } }).error?.fields;
  const seconds = fields?.retry_after_seconds;
  return t(`accounts.profile.code.${message}`, {
    defaultValue: message,
    s: typeof seconds === 'number' ? seconds : '?',
  });
}
