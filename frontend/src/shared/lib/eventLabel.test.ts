import { expect, test } from 'vitest';

import { eventLabel } from './eventLabel';

// A minimal stub of i18next's t: echoes the key so we can assert which branch ran.
const t = ((key: string) => `T:${key}`) as unknown as Parameters<typeof eventLabel>[0];

test('maps a known code to its logEvent.<code> key', () => {
  expect(eventLabel(t, 'neurocomment_posted')).toBe('T:logEvent.neurocomment_posted');
  expect(eventLabel(t, 'warming_started')).toBe('T:logEvent.warming_started');
});

test('falls back to the raw code for an unmapped event', () => {
  expect(eventLabel(t, 'totally_unknown_event')).toBe('totally_unknown_event');
  expect(eventLabel(t, '')).toBe('');
});
