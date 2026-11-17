import { expect, test } from 'vitest';

import type { AccountRead } from '@/shared/api';

import { appealView, instantMs, isHeld, spamVerdictOf, upcomingMs } from './spamCard';

const BASE: AccountRead = {
  account_id: 'a1',
  status: 'alive',
  created_at: 'now',
  updated_at: 'now',
};

test('the verdict is the fold when sent, the stored reading otherwise, unknown last', () => {
  expect(spamVerdictOf({ ...BASE, spam_verdict: 'frozen', spam_status: 'clean' })).toBe('frozen');
  expect(spamVerdictOf({ ...BASE, spam_status: 'limited' })).toBe('limited');
  expect(spamVerdictOf(BASE)).toBe('unknown');
});

test('instants parse, and anything that is not one is nothing', () => {
  expect(instantMs('2026-07-01T00:00:00Z')).toBe(Date.parse('2026-07-01T00:00:00Z'));
  expect(instantMs(null)).toBeNull();
  expect(instantMs('')).toBeNull();
  expect(instantMs('not a date')).toBeNull();
});

test('only a moment still ahead is upcoming', () => {
  const now = Date.parse('2026-07-01T12:00:00Z');
  expect(upcomingMs('2026-07-01T13:00:00Z', now)).toBe(Date.parse('2026-07-01T13:00:00Z'));
  expect(upcomingMs('2026-07-01T11:00:00Z', now)).toBeNull();
  expect(upcomingMs('2026-07-01T12:00:00Z', now)).toBeNull();
  expect(upcomingMs(undefined, now)).toBeNull();
});

test('a check is held when it was postponed, refused or handed back from the window', () => {
  const answer = { account_id: 'a1', status: 'clean' as const, checked_at: 'now' };
  expect(isHeld({ ...answer, served_from: 'deferred' })).toBe(true);
  expect(isHeld({ ...answer, served_from: 'refused' })).toBe(true);
  expect(isHeld({ ...answer, served_from: 'coalesced' })).toBe(true);
  expect(isHeld({ ...answer, served_from: 'probe' })).toBe(false);
  expect(isHeld({ ...answer, served_from: 'cache' })).toBe(false);
  expect(isHeld(answer)).toBe(false);
});

test('the appeal block: hidden, available, filed or closed', () => {
  const limited: AccountRead = { ...BASE, spam_verdict: 'limited' };
  const clean: AccountRead = { ...BASE, spam_verdict: 'clean' };
  expect(appealView(clean, undefined)).toEqual({ kind: 'hidden' });
  expect(appealView(limited, undefined)).toEqual({ kind: 'available' });
  expect(appealView(limited, { status: 'none' })).toEqual({ kind: 'available' });

  expect(
    appealView(limited, {
      status: 'open',
      filed_at: '2026-07-01T00:00:00Z',
      filed_by: 'anna',
      recheck_at: '2026-07-02T00:00:00Z',
    }),
  ).toEqual({
    kind: 'filed',
    filedAt: '2026-07-01T00:00:00Z',
    filedBy: 'anna',
    recheckAt: '2026-07-02T00:00:00Z',
  });

  // The account row's own summary stands in until the appeal's read arrives.
  expect(
    appealView(
      {
        ...limited,
        spam_appeal_status: 'open',
        spam_appeal_filed_at: '2026-07-01T00:00:00Z',
        spam_appeal_filed_by: 'anna',
      },
      undefined,
    ),
  ).toEqual({
    kind: 'filed',
    filedAt: '2026-07-01T00:00:00Z',
    filedBy: 'anna',
    recheckAt: null,
  });

  expect(appealView({ ...clean, spam_appeal_status: 'closed' }, undefined)).toEqual({
    kind: 'closed',
    filedAt: null,
    filedBy: null,
  });
  // A new limit after a closed appeal is a fresh one to file, not history.
  expect(appealView({ ...limited, spam_appeal_status: 'closed' }, undefined)).toEqual({
    kind: 'available',
  });
});
