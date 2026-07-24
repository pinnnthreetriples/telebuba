// Shared constants + error-envelope helpers for the channel-management pieces
// (ChannelsTab / ChannelCreateModal / ChannelEditModal / ChannelPostsPanel)
// and the profile modal's photo gate. Internal to the slice (not re-exported
// from index).

// Client-side mirrors of the backend gates (schemas/telegram_actions_channels.py
// limits, services/accounts/_uploads.py suffix sets, settings.channels byte
// caps) so bad input is rejected up front instead of uploading fully and
// failing with an untranslated 400.
export const CHANNEL_TITLE_MAX = 128;
export const CHANNEL_ABOUT_MAX = 255;
export const POST_TEXT_MAX = 4096;
export const POST_CAPTION_MAX = 1024;
// Telegram public-handle rules: 5..32 chars, starts with a letter.
export const CHANNEL_USERNAME_RE = /^[A-Za-z][A-Za-z0-9_]{4,31}$/;
export const PHOTO_SUFFIXES = ['.jpg', '.jpeg', '.png', '.webp'];
export const PHOTO_MAX_BYTES = 10_000_000;
export const VIDEO_SUFFIXES = ['.mp4', '.mov'];
export const VIDEO_MAX_BYTES = 100_000_000;

// Field/label styling is the slice-wide one (single source in ./_styles).
export { FIELD, LABEL } from './_styles';

function hasSuffix(file: File, suffixes: string[]): boolean {
  const name = file.name.toLowerCase();
  return suffixes.some((suffix) => name.endsWith(suffix));
}

// Profile avatars and channel avatars share the same backend gate.
export function isUploadablePhoto(file: File): boolean {
  return file.size <= PHOTO_MAX_BYTES && hasSuffix(file, PHOTO_SUFFIXES);
}

// Post media: the suffix decides photo vs video (mirrors channel_posts.py's
// _derive_media_kind), each kind with its own byte cap.
export function isUploadablePostMedia(file: File): boolean {
  if (hasSuffix(file, PHOTO_SUFFIXES)) return file.size <= PHOTO_MAX_BYTES;
  if (hasSuffix(file, VIDEO_SUFFIXES)) return file.size <= VIDEO_MAX_BYTES;
  return false;
}

export type Translate = (key: string, opts?: Record<string, unknown>) => string;

// Pull the stable reason out of the /api/v1 error envelope
// ({error:{code,message,fields?}}): `message` carries the locale-neutral code
// (channel_username_occupied, username_occupied, …). Null when the rejection
// isn't our envelope.
export function envelopeMessage(err: unknown): string | null {
  const message = (err as { error?: { message?: unknown } } | null)?.error?.message;
  return typeof message === 'string' && message.trim() ? message : null;
}

// Codes translate via accounts.channel.code.*; anything unknown shows as-is
// (same contract as AddStoryModal's errorText).
export function channelErrorText(err: unknown, t: Translate, fallback: string): string {
  const message = envelopeMessage(err);
  if (!message) return fallback;
  return t(`accounts.channel.code.${message}`, { defaultValue: message });
}

// A create that failed AFTER the channel existed (the public-username step)
// rides the already-created channel's id in the envelope's fields — the UI
// must still refresh the list so the private channel shows up instead of
// being re-created.
export function errorChannelId(err: unknown): string | null {
  const fields = (err as { error?: { fields?: Record<string, unknown> } } | null)?.error?.fields;
  const value = fields?.channel_id;
  return typeof value === 'string' && value !== '' ? value : null;
}
