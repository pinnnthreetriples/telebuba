import { expect, test } from 'vitest';

import { eventLabel } from './eventLabel';

// A minimal stub of i18next's t: echoes the key so we can assert which branch ran.
const t = ((key: string) => `T:${key}`) as unknown as Parameters<typeof eventLabel>[0];

test('maps a known code to its logEvent.<code> key', () => {
  expect(eventLabel(t, 'neurocomment_posted')).toBe('T:logEvent.neurocomment_posted');
  expect(eventLabel(t, 'warming_started')).toBe('T:logEvent.warming_started');
});

test('localizes the tdata import/conversion events (not shown as raw codes)', () => {
  expect(eventLabel(t, 'tdata_convert_completed')).toBe('T:logEvent.tdata_convert_completed');
  expect(eventLabel(t, 'tdata_convert_started')).toBe('T:logEvent.tdata_convert_started');
  expect(eventLabel(t, 'tdata_no_accounts')).toBe('T:logEvent.tdata_no_accounts');
});

test('falls back to the raw code for an unmapped event', () => {
  expect(eventLabel(t, 'totally_unknown_event')).toBe('totally_unknown_event');
  expect(eventLabel(t, '')).toBe('');
});
