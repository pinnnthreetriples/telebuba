// Shared constants + error-envelope helpers for the channel-management pieces
// (ChannelsTab / ChannelCreateModal / ChannelEditModal / ChannelPostsPanel).
// Internal to the slice (not re-exported from index).

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

// Same field/label styling as the profile modal's text tab.
export const FIELD =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
export const LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';

function hasSuffix(file: File, suffixes: string[]): boolean {
  const name = file.name.toLowerCase();
  return suffixes.some((suffix) => name.endsWith(suffix));
}

export function isUploadableChannelPhoto(file: File): boolean {
  return file.size <= PHOTO_MAX_BYTES && hasSuffix(file, PHOTO_SUFFIXES);
}

// Post media: the suffix decides photo vs video (mirrors channel_posts.py's
// _derive_media_kind), each kind with its own byte cap.
export function isUploadablePostMedia(file: File): boolean {
  if (hasSuffix(file, PHOTO_SUFFIXES)) return file.size <= PHOTO_MAX_BYTES;
  if (hasSuffix(file, VIDEO_SUFFIXES)) return file.size <= VIDEO_MAX_BYTES;
  return false;
}

type Translate = (key: string, opts?: Record<string, unknown>) => string;

// Pull the stable reason out of the /api/v1 error envelope
// ({error:{code,message,fields?}}) a failed channel action rejects with:
// `message` carries the locale-neutral code (channel_username_occupied, …)
// which translates via accounts.channel.code.*; anything unknown shows as-is
// (same contract as AddStoryModal's errorText).
export function channelErrorText(err: unknown, t: Translate, fallback: string): string {
  const message = (err as { error?: { message?: unknown } } | null)?.error?.message;
  if (typeof message !== 'string' || !message.trim()) return fallback;
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
