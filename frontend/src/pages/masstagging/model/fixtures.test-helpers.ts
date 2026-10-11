// Fixtures shared by the masstagging tests: a campaign, a board and its settings.
import type {
  MasstaggingBoard,
  MasstaggingBoardRow,
  MasstaggingCampaign,
  MasstaggingSettingsRead,
} from '@/shared/api';

export const CAMPAIGN: MasstaggingCampaign = {
  campaign_id: 'c1',
  name: 'Обучение — март',
  mode: 'stories',
  status: 'ready',
  target_count: 2,
  account_count: 2,
  missing: [],
  rest_until: null,
  last_error: null,
  created_at: '2026-10-11T10:00:00Z',
  updated_at: '2026-10-11T10:00:00+00:00',
};

export function campaign(over: Partial<MasstaggingCampaign> = {}): MasstaggingCampaign {
  return { ...CAMPAIGN, ...over };
}

export function row(over: Partial<MasstaggingBoardRow> = {}): MasstaggingBoardRow {
  return {
    target_key: 'u:alpha',
    target: '@alpha',
    state: 'queued',
    reason: null,
    when: null,
    account_id: null,
    active: true,
    history: [],
    ...over,
  };
}

export function board(over: Partial<MasstaggingBoard> = {}): MasstaggingBoard {
  return {
    campaign: CAMPAIGN,
    next_action_at: null,
    accounts: [
      { account_id: 'a1', state: 'working', premium: true, busy_owner: null, until: null },
      { account_id: 'a2', state: 'working', premium: false, busy_owner: null, until: null },
    ],
    counters: {
      total: 2,
      tagged: 0,
      left: 2,
      failed: 0,
      skipped: 0,
      per_day: 80,
      days_left: 1,
      working_accounts: 2,
      excluded_accounts: 0,
      free_accounts: 1,
    },
    rows: [row(), row({ target_key: 'u:beta', target: '@beta' })],
    ...over,
  };
}

export const SETTINGS_READ: MasstaggingSettingsRead = {
  campaign_id: 'c1',
  name: 'Обучение — март',
  status: 'ready',
  updated_at: '2026-10-11T10:00:00+00:00',
  account_ids: ['a1', 'a2'],
  settings: {
    mode: 'stories',
    targets: ['@alpha', '@beta'],
    media: [{ media_id: `${'a'.repeat(64)}.mp4`, name: 'lesson.mp4' }],
  },
};
