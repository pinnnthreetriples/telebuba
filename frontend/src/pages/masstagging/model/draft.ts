// The settings dialog's ONE draft: every section edits it and Save writes it. Server shape
// in, server shape out — the backend's defaults are mirrored so a never-saved campaign
// opens with the numbers agreed on the mockup rather than with empty fields.
import type {
  MasstaggingPace,
  MasstaggingSettings,
  MasstaggingSettingsRead,
  MasstaggingSettingsUpdate,
} from '@/shared/api';

export type Settings = Required<MasstaggingSettings>;

export type Draft = {
  name: string;
  accountIds: string[];
  settings: Settings;
};

export const DEFAULT_SETTINGS: Settings = {
  mode: 'stories',
  target_source: 'manual',
  targets: [],
  base_name: '',
  media: [],
  caption: '',
  link: '',
  link_text: '',
  visibility: 'all',
  lifetime_hours: 48,
  prime_first: true,
  mentions_per_story: 5,
  stories_per_day: 15,
  story_gap_minutes: { min: 1, max: 2 },
  dialogs_per_day: 6,
  dialog_gap_seconds: { min: 20, max: 60 },
  ramp_up: true,
  skip_tagged: true,
  stop_mode: 'count',
  stop_tags: 50,
  stop_minutes: 0,
  flood_pause_seconds: 500,
  floods_to_rest: 3,
};

// Telegram's own ceilings the dialog shows: a free account's caption, and the most
// mentions a story takes.
export const FREE_CAPTION_LIMIT = 200;
export const PREMIUM_CAPTION_LIMIT = 2048;
export const MAX_MENTIONS = 10;

const PACE_KEYS = [
  'mentions_per_story',
  'stories_per_day',
  'story_gap_minutes',
  'dialogs_per_day',
  'dialog_gap_seconds',
  'ramp_up',
  'skip_tagged',
  'stop_mode',
  'stop_tags',
  'stop_minutes',
  'flood_pause_seconds',
  'floods_to_rest',
] as const;

export type Pace = Required<MasstaggingPace>;

export function draftOf(read: MasstaggingSettingsRead): Draft {
  return {
    name: read.name,
    accountIds: [...read.account_ids],
    settings: { ...DEFAULT_SETTINGS, ...read.settings } as Settings,
  };
}

export function bodyOf(draft: Draft, expectedUpdatedAt: string): MasstaggingSettingsUpdate {
  return {
    expected_updated_at: expectedUpdatedAt,
    name: draft.name.trim() || draft.name,
    account_ids: draft.accountIds,
    settings: draft.settings,
  };
}

export function sameDraft(a: Draft, b: Draft): boolean {
  return JSON.stringify(bodyOf(a, '')) === JSON.stringify(bodyOf(b, ''));
}

// «Темп» alone — what the board's gear edits and saves while the campaign runs.
export function paceOf(settings: Settings): Pace {
  const pace: Partial<Pace> = {};
  for (const key of PACE_KEYS) Object.assign(pace, { [key]: settings[key] });
  return pace as Pace;
}

export function samePace(a: Pace, b: Pace): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

const USERNAME = /^[A-Za-z][A-Za-z0-9_]{3,31}$/;
const LINK = /^(?:https?:\/\/)?(?:www\.)?(?:t|telegram)\.me\/(.+)$/i;
const PHONE_NOISE = /[\s\-()]/g;
const SPLIT = /[\s,;]+/;

// One typed target the way the server reads it: a username (also from a t.me link) or a
// phone. `key` makes two spellings of one person one target; `shown` is what is saved.
export function parseTarget(raw: string): { key: string; shown: string } | null {
  let text = raw.trim();
  if (text.startsWith('+')) {
    const digits = text.slice(1).replace(PHONE_NOISE, '');
    return /^\d{7,15}$/.test(digits) ? { key: `p:${digits}`, shown: `+${digits}` } : null;
  }
  const link = LINK.exec(text);
  if (link !== null) {
    text = (link[1] ?? '').split('?')[0]?.replace(/\/+$/, '') ?? '';
    if (text.includes('/')) return null;
  }
  const name = text.startsWith('@') ? text.slice(1) : text;
  return USERNAME.test(name) ? { key: `u:${name.toLowerCase()}`, shown: `@${name}` } : null;
}

// A pasted blob against the list it joins: what is new, and how many items were unusable.
export function parseTargets(
  value: string,
  existing: string[],
): { added: string[]; invalid: number } {
  const known = new Set(existing.map((item) => parseTarget(item)?.key).filter(Boolean));
  const added: string[] = [];
  let invalid = 0;
  for (const item of value.split(SPLIT).filter((part) => part !== '')) {
    const parsed = parseTarget(item);
    if (parsed === null) {
      invalid += 1;
    } else if (!known.has(parsed.key)) {
      known.add(parsed.key);
      added.push(parsed.shown);
    }
  }
  return { added, invalid };
}

// The same list with another one appended, one entry per person.
export function mergeTargets(existing: string[], incoming: string[]): string[] {
  return [...existing, ...parseTargets(incoming.join('\n'), existing).added];
}
