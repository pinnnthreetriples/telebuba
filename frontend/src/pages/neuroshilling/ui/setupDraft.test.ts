import { expect, test } from 'vitest';

import type { NeuroshillingCampaign } from '@/shared/api';

import {
  advancedChangeCount,
  clampInt,
  countTargets,
  setupDraftOf,
  setupFieldsOf,
} from './setupDraft';

const CAMPAIGN: NeuroshillingCampaign = {
  campaign_id: 'c1',
  name: 'Промо',
  mode: 'campaign',
  created_at: 'now',
  updated_at: 'now',
};

test('a campaign carrying only its required fields still yields a complete draft', () => {
  // The board omits every field left at its schema default, so the draft has to
  // restore the SAME defaults — otherwise a save writes NaN or an empty string.
  expect(setupDraftOf(CAMPAIGN)).toEqual({
    campaignId: 'c1',
    targetsRaw: '',
    runMode: 'sequential',
    pauseMinSeconds: 10,
    pauseMaxSeconds: 20,
    messagesPerHour: 10,
    messagesPerChatPerDay: 3,
    totalPerAccount: null,
    reserveEnabled: false,
  });
});

test('the body carries only the columns this card owns', () => {
  const body = setupFieldsOf(
    setupDraftOf({ ...CAMPAIGN, targets_raw: '@a', reserve_enabled: true }),
  );

  expect(body).toEqual({
    targets_raw: '@a',
    run_mode: 'sequential',
    pause_min_seconds: 10,
    pause_max_seconds: 20,
    messages_per_hour: 10,
    messages_per_chat_per_day: 3,
    total_per_account: null,
    reserve_enabled: true,
  });
  // The stage-6 columns are absent BY NAME: the card shows them disabled, and the
  // page's echo of the stored campaign is what carries them through a save. A key
  // here would let a control nothing reads yet write the column.
  expect(Object.keys(body)).not.toContain('autoresponder');
  expect(Object.keys(body)).not.toContain('reply_to_humans');
  expect(Object.keys(body)).not.toContain('reply_activity');
  expect(Object.keys(body)).not.toContain('listen_minutes');
});

test('a zero total means no ceiling only when it arrives as null', () => {
  // `total_per_account` is `ge=1` on the wire, so 0 is not "unlimited" there and
  // must never be produced from an emptied box.
  expect(setupDraftOf({ ...CAMPAIGN, total_per_account: 250 }).totalPerAccount).toBe(250);
  expect(setupDraftOf(CAMPAIGN).totalPerAccount).toBeNull();
});

test('targets are counted across lines, commas and stray whitespace', () => {
  expect(countTargets('')).toBe(0);
  expect(countTargets('   \n  ')).toBe(0);
  expect(countTargets('@a\n@b\n\n@c')).toBe(3);
  expect(countTargets('@a, @b; https://t.me/c')).toBe(3);
});

test('clampInt keeps a value inside the wire bounds and floors the unparseable', () => {
  expect(clampInt(42, 1, 60)).toBe(42);
  expect(clampInt(900, 1, 60)).toBe(60);
  expect(clampInt(-5, 0, 60)).toBe(0);
  expect(clampInt(7.9, 0, 60)).toBe(7);
  // An emptied number input reads back as NaN.
  expect(clampInt(Number.NaN, 1, 60)).toBe(1);
});

test('the advanced badge counts what was changed, not how many controls there are', () => {
  const base = setupDraftOf(CAMPAIGN);
  expect(advancedChangeCount(base)).toBe(0);
  expect(advancedChangeCount({ ...base, messagesPerHour: 4 })).toBe(1);
  expect(
    advancedChangeCount({
      ...base,
      messagesPerHour: 4,
      messagesPerChatPerDay: 1,
      totalPerAccount: 100,
      reserveEnabled: true,
    }),
  ).toBe(4);
});
