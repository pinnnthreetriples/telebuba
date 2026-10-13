// The "pace and limits" block's numbers in one place: slider ranges, the zones their
// hints are coloured by, the built-in presets and the forecast arithmetic.
//
// The zone thresholds and presets follow CRM vendor documentation (Salebot, Whatcrm):
// about 15 new contacts per account per day spread over the day, faster imports
// shrink the next day's allowance. They are not official Telegram limits.

import type { OutreachLimits } from '@/shared/api';

export type PacingKey = 'pause' | 'messages' | 'contacts' | 'batch';
export type PacingValues = Record<PacingKey, number>;
export type Zone = 'ok' | 'warn' | 'risk';

type Slider = {
  min: number;
  max: number;
  step: number;
  // Inclusive bounds of the green and amber zones; past amber is red.
  ok: number;
  warn: number;
  // Pause is safer the larger it is; the rest are safer the smaller they are.
  higherIsSafer: boolean;
};

export const SLIDERS: Record<PacingKey, Slider> = {
  pause: { min: 10, max: 600, step: 10, ok: 60, warn: 30, higherIsSafer: true },
  messages: { min: 5, max: 60, step: 1, ok: 25, warn: 40, higherIsSafer: false },
  contacts: { min: 3, max: 40, step: 1, ok: 15, warn: 20, higherIsSafer: false },
  batch: { min: 1, max: 20, step: 1, ok: 5, warn: 10, higherIsSafer: false },
};

export const PACING_KEYS: PacingKey[] = ['pause', 'messages', 'contacts', 'batch'];

export type BuiltInPreset = { id: 'careful' | 'normal' | 'fast'; values: PacingValues };

export const BUILT_IN_PRESETS: BuiltInPreset[] = [
  { id: 'careful', values: { pause: 180, messages: 20, contacts: 12, batch: 2 } },
  { id: 'normal', values: { pause: 120, messages: 25, contacts: 15, batch: 3 } },
  { id: 'fast', values: { pause: 60, messages: 35, contacts: 20, batch: 5 } },
];

export function zoneOf(key: PacingKey, value: number): Zone {
  const slider = SLIDERS[key];
  if (slider.higherIsSafer) {
    if (value >= slider.ok) return 'ok';
    return value >= slider.warn ? 'warn' : 'risk';
  }
  if (value <= slider.ok) return 'ok';
  return value <= slider.warn ? 'warn' : 'risk';
}

export function worstZone(zones: Zone[]): Zone {
  if (zones.includes('risk')) return 'risk';
  return zones.includes('warn') ? 'warn' : 'ok';
}

// The run waits a uniform pause in ±50% of the slider's centre.
export function pauseRange(pause: number): [number, number] {
  return [Math.round(pause * 0.5), Math.round(pause * 1.5)];
}

export function clampTo(key: PacingKey, value: number): number {
  const { min, max } = SLIDERS[key];
  return Math.min(max, Math.max(min, Math.round(value)));
}

export function samePacing(a: PacingValues, b: PacingValues): boolean {
  return PACING_KEYS.every((key) => a[key] === b[key]);
}

export type SendForecast = {
  sends: number;
  // Working days the run spans, today included.
  days: number;
  // Within one day: roughly how long the sends take.
  seconds: number;
};

export function forecastSends({
  mode,
  recipients,
  left,
  cap,
  pause,
  concurrency,
  windowMinutes,
}: {
  mode: 'each' | 'split';
  recipients: number;
  // First messages each selected account may still send today.
  left: number[];
  // The daily cap per account; null when there is none.
  cap: number | null;
  pause: number;
  concurrency: number;
  windowMinutes: number;
}): SendForecast {
  const accounts = left.length;
  const sends = mode === 'each' ? recipients * accounts : recipients;
  if (accounts === 0 || sends === 0) return { sends, days: 0, seconds: 0 };
  // An account sends at most this many a day: its cap, or as many pauses as fit the window.
  const byWindow = Math.max(Math.floor((windowMinutes * 60) / Math.max(pause, 1)), 1);
  const perDay = cap === null ? byWindow : Math.min(cap, byWindow);
  const shares = left.map((_, index) =>
    mode === 'each'
      ? recipients
      : Math.floor(recipients / accounts) + (index < recipients % accounts ? 1 : 0),
  );
  const days = Math.max(
    ...shares.map((share, index) => {
      const today = Math.min(Math.max(left[index] ?? 0, 0), perDay);
      return share <= today ? 1 : 1 + Math.ceil((share - today) / Math.max(perDay, 1));
    }),
  );
  const longest = Math.max(...shares) * pause;
  const parallel = (sends * pause) / Math.max(Math.min(concurrency, accounts), 1);
  return { sends, days, seconds: Math.max(longest, parallel) - pause };
}

// Minutes the working window stays open a day ("09:00"–"20:00" → 660).
export function windowMinutes(start: string, end: string): number {
  const minutes = (clock: string) => {
    const [hours = 0, mins = 0] = clock.split(':').map(Number);
    return hours * 60 + mins;
  };
  return Math.max(minutes(end) - minutes(start), 1);
}

// Numbers no selected account has room for today.
export function lookupOverflow(numbers: number, left: number[]): number {
  return Math.max(numbers - left.reduce((sum, room) => sum + Math.max(room, 0), 0), 0);
}

// The saved outreach limits as slider positions; the "normal" preset until they load
// (and for any field the server left at its default).
export function pacingFromLimits(limits: OutreachLimits | undefined): PacingValues {
  const normal: PacingValues = { pause: 120, messages: 25, contacts: 15, batch: 3 };
  if (limits === undefined) return normal;
  const low = limits.bulk_min_delay_seconds ?? normal.pause * 0.5;
  const high = limits.bulk_max_delay_seconds ?? normal.pause * 1.5;
  return {
    pause: clampTo('pause', (low + high) / 2),
    messages: clampTo('messages', limits.first_messages_per_day ?? normal.messages),
    contacts: clampTo('contacts', limits.contact_imports_per_day ?? normal.contacts),
    batch: clampTo('batch', limits.lookup_batch_size ?? normal.batch),
  };
}
