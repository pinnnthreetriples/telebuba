import { expect, test } from 'vitest';

import type { NeuroshillingCampaign, NeuroshillingScenario, NeuroshillingStep } from '@/shared/api';

import {
  asReaction,
  campaignFieldsOf,
  clampDelay,
  draftOf,
  mintKey,
  scenarioBody,
} from './scenarioDraft';

const CAMPAIGN: NeuroshillingCampaign = {
  campaign_id: 'c1',
  name: 'Промо',
  mode: 'campaign',
  topic: 'про сервис',
  created_at: 'now',
  updated_at: 'now',
};

const SCENARIO: NeuroshillingScenario = {
  campaign_id: 'c1',
  scenario_status: 'draft',
  roles: [{ role_id: 'r1', name: 'Скептик', description: 'сомневается', created_at: 'now' }],
  steps: [
    {
      step_id: 's1',
      position: 1,
      kind: 'message',
      role_id: 'r1',
      text: 'реплика',
      delay_min_seconds: 30,
      delay_max_seconds: 90,
    },
  ],
};

test('the draft takes the stored step id as its list key', () => {
  const draft = draftOf(CAMPAIGN, SCENARIO);

  expect(draft.campaignId).toBe('c1');
  expect(draft.steps[0]!.key).toBe('s1');
  expect(draft.steps[0]!.delayMinSeconds).toBe(30);
  expect(draft.roles[0]!.roleId).toBe('r1');
});

test('a sparse payload lands on the column defaults, not on undefined', () => {
  const draft = draftOf({ ...CAMPAIGN, topic: undefined }, { campaign_id: 'c1' });

  expect(draft).toMatchObject({
    topic: '',
    uniqueMessages: true,
    useChatContext: false,
    mediaMessageLink: '',
    mediaStepPosition: null,
    roles: [],
    steps: [],
  });
});

test('an emoji outside the picker set reads back but is not echoed', () => {
  // The stored column is free text so an older row still READS; it must not be
  // sent back into a Literal the server would refuse.
  expect(asReaction('🦄')).toBeNull();
  expect(asReaction(null)).toBeNull();
  expect(asReaction('🔥')).toBe('🔥');
});

test('a step carries only the link its kind owns', () => {
  const draft = draftOf(CAMPAIGN, SCENARIO);
  const body = scenarioBody({
    ...draft,
    steps: [
      { ...draft.steps[0]!, replyToPosition: 1, targetPosition: 2, emoji: '🔥' },
      { ...draft.steps[0]!, key: 's2', kind: 'reaction', replyToPosition: 1, targetPosition: 1 },
    ],
  });

  expect(body.steps![0]).toMatchObject({
    reply_to_position: 1,
    target_position: null,
    emoji: null,
  });
  expect(body.steps![1]).toMatchObject({ reply_to_position: null, target_position: 1 });
  // The list key never reaches the wire: array order IS the position.
  expect(body.steps![0]).not.toHaveProperty('key');
  expect(body.roles![0]).toEqual({ role_id: 'r1', name: 'Скептик', description: 'сомневается' });
});

test('the media step is cleared together with the link it points from', () => {
  const draft = { ...draftOf(CAMPAIGN, SCENARIO), mediaMessageLink: '  ', mediaStepPosition: 1 };

  expect(campaignFieldsOf(draft)).toMatchObject({
    media_message_link: null,
    media_step_position: null,
  });
  expect(campaignFieldsOf({ ...draft, mediaMessageLink: 'https://t.me/c/1' })).toMatchObject({
    media_message_link: 'https://t.me/c/1',
    media_step_position: 1,
  });
});

test('a stored media slot survives on a message step and is dropped on a reaction', () => {
  const reaction: NeuroshillingStep = {
    step_id: 's2',
    position: 2,
    kind: 'reaction',
    role_id: 'r1',
    emoji: '🔥',
    target_position: 1,
    delay_min_seconds: 30,
    delay_max_seconds: 90,
  };
  const scenario = { ...SCENARIO, steps: [...SCENARIO.steps!, reaction] };
  const linked = { ...CAMPAIGN, media_message_link: 'https://t.me/c/1/2' };

  // The card's picker offers message steps only, so a slot on the reaction matches
  // no option: the `<select>` shows "no media step" while the draft keeps writing 2
  // back, and approval refuses a field the operator reads as empty.
  expect(draftOf({ ...linked, media_step_position: 2 }, scenario).mediaStepPosition).toBeNull();
  expect(draftOf({ ...linked, media_step_position: 1 }, scenario).mediaStepPosition).toBe(1);
});

test('a minted key is unique and cannot look like a stored id', () => {
  expect(mintKey('role')).not.toBe(mintKey('role'));
  expect(mintKey('step')).toMatch(/^step-\d+$/);
});

test('a delay is trimmed to the column bounds', () => {
  expect(clampDelay(-5)).toBe(0);
  expect(clampDelay(99999)).toBe(3600);
  expect(clampDelay(Number.NaN)).toBe(0);
  expect(clampDelay(12.7)).toBe(12);
});
