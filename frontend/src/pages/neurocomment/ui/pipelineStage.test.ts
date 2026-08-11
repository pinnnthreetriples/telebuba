import { expect, test } from 'vitest';

import { pipelineStage, STAGE_FRESH_MS } from './pipelineStage';

const NOW = Date.parse('2026-08-11T12:00:00+00:00');

function line(event: string, agoMs = 0) {
  return { event, created_at: new Date(NOW - agoMs).toISOString() };
}

test('a stopped engine lights no stage at all', () => {
  expect(pipelineStage([line('neurocomment_posted')], false, NOW)).toEqual({
    stage: -1,
    staleAt: null,
  });
});

test('each stage of a post’s life maps to its own step', () => {
  const cases: [string, number][] = [
    ['neurocomment_post_received', 1],
    ['neurocomment_post_skipped', 2],
    ['neurocomment_channel_cooled', 2],
    ['neurocomment_no_account_available', 2],
    ['neurocomment_generation_started', 3],
    ['neurocomment_generation_retry', 3],
    ['neurocomment_claim_lost_before_send', 3],
    ['neurocomment_posted', 5],
    ['neurocomment_post_failed', 5],
  ];
  for (const [event, stage] of cases) {
    expect(pipelineStage([line(event)], true, NOW).stage, event).toBe(stage);
  }
});

// `_outcomes._classify_post` writes exactly one terminal row per attempt. Miss one and
// that outcome falls through to the post's own `generation_started`, parking the rail on
// «Генерация» for a minute after the post already died at the comment step.
test('every post-only branch of the outcome ladder lands on «Комментарий»', () => {
  const terminal = [
    'neurocomment_posted',
    'neurocomment_posted_after_reclaim',
    'neurocomment_posted_row_missing',
    'neurocomment_post_commit_failed',
    'neurocomment_post_failed',
    'neurocomment_post_gated',
    'neurocomment_post_access_lost',
    'neurocomment_post_cooldown',
    'neurocomment_post_unavailable',
    'neurocomment_post_ban_unconfirmed',
  ];
  for (const event of terminal) {
    expect(pipelineStage([line(event)], true, NOW).stage, event).toBe(5);
  }
});

// Codes that are NOT on the per-post path must never move the rail: the card green-checks
// every step below the active one, so a stray match asserts a comment was generated and
// sent when no post exists at all. Pressing Start used to light «Капча» this way.
test('work that is not a post never claims a pipeline stage', () => {
  const offPath = [
    // onboarding join (`_classify.solve_if_present`) and the deletion sweep's captcha pass
    'neurocomment_challenge_attempt',
    'neurocomment_challenge_result',
    'neurocomment_captcha_retry',
    'neurocomment_captcha_gave_up',
    // written ABOVE post_received, for a post on a channel with no active campaign
    'neurocomment_no_campaign',
    // the outcome ladder's one non-post-only branch: onboarding writes it too
    'neurocomment_account_banned',
    // background sweeps, discovery, re-join
    'neurocomment_comment_deleted',
    'neurocomment_listener_started',
  ];
  for (const event of offPath) {
    expect(pipelineStage([line(event)], true, NOW), event).toEqual({ stage: 0, staleAt: null });
  }
});

// The pair that would collapse under a shared `neurocomment_post_` prefix: arrival is
// a detection, a skip is the filter's verdict, and the rail must tell them apart.
test('post_received and post_skipped are different stages', () => {
  expect(pipelineStage([line('neurocomment_post_received')], true, NOW).stage).toBe(1);
  expect(pipelineStage([line('neurocomment_post_skipped')], true, NOW).stage).toBe(2);
});

test('the newest stage-bearing line wins, and lines with no stage are skipped', () => {
  const lines = [
    line('neurocomment_listener_started'),
    line('neurocomment_posted', 1000),
    line('neurocomment_post_received', 2000),
  ];
  expect(pipelineStage(lines, true, NOW).stage).toBe(5);
});

// The reported bug was a rail frozen at one step. A finished post must not leave it
// parked there: between posts the engine is listening, which is step 0.
test('a stage older than the freshness window falls back to «Слушатель»', () => {
  const stale = pipelineStage([line('neurocomment_posted', STAGE_FRESH_MS + 1)], true, NOW);
  expect(stale.stage).toBe(0);
  expect(stale.staleAt).toBeLessThan(NOW);
});

test('a running engine with nothing logged yet sits on «Слушатель», with nothing to expire', () => {
  expect(pipelineStage([], true, NOW)).toEqual({ stage: 0, staleAt: null });
});

test('staleAt is when the current stage expires, so the caller can re-render once', () => {
  const { stage, staleAt } = pipelineStage([line('neurocomment_generation_started')], true, NOW);
  expect(stage).toBe(3);
  expect(staleAt).toBe(NOW + STAGE_FRESH_MS);
});
