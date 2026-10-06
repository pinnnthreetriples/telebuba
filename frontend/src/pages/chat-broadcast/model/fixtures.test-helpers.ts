// Fixtures shared by the chat-broadcast tests: a campaign, a board and its settings.
import type {
  ChatBroadcastBoard,
  ChatBroadcastBoardRow,
  ChatBroadcastCampaign,
  ChatBroadcastSettingsRead,
} from '@/shared/api';

export const CAMPAIGN: ChatBroadcastCampaign = {
  campaign_id: 'c1',
  name: 'Крипто-чаты',
  status: 'running',
  target_mode: 'list',
  account_count: 2,
  target_count: 3,
  round: 1,
  rest_until: null,
  last_error: null,
  created_at: '2026-10-06T10:00:00Z',
  updated_at: '2026-10-06T10:00:00+00:00',
};

export function row(over: Partial<ChatBroadcastBoardRow>): ChatBroadcastBoardRow {
  return {
    chat_key: 'alpha',
    raw: '@alpha',
    title: null,
    kind: 'public',
    account_id: 'a1',
    handed_from: null,
    state: 'queued',
    skip_reason: null,
    round: 1,
    sent_total: 0,
    planned_total: 6,
    next_action_at: null,
    requested_at: null,
    last_text: null,
    last_sent_at: null,
    message_deleted: false,
    active: true,
    history: [],
    ...over,
  };
}

export function board(over: Partial<ChatBroadcastBoard> = {}): ChatBroadcastBoard {
  return {
    campaign: CAMPAIGN,
    phase: 'running',
    chain_length: 2,
    started_at: '2026-10-06T10:00:00Z',
    resumed_at: null,
    finished_at: null,
    accounts: [
      { account_id: 'a1', state: 'active', halted_reason: null, busy_owner: null },
      { account_id: 'a2', state: 'active', halted_reason: null, busy_owner: null },
    ],
    counters: {
      accounts: 2,
      accounts_working: 2,
      chats: 3,
      joined: 2,
      pending_approval: 0,
      waiting: 1,
      sent: 4,
      skipped: 1,
      handed: 0,
      planned: 18,
      rounds: 3,
    },
    rows: [
      row({
        chat_key: 'alpha',
        raw: '@alpha',
        state: 'writing',
        last_sent_at: '2026-10-06T10:05:00Z',
        sent_total: 3,
      }),
      row({
        chat_key: 'beta',
        raw: '@beta',
        state: 'waiting',
        next_action_at: '2099-01-01T15:40:00Z',
      }),
      row({
        chat_key: 'gamma',
        raw: '@gamma',
        state: 'skipped',
        skip_reason: 'admin_only',
        active: false,
        history: [
          {
            at: '2026-10-06T10:03:00Z',
            account_id: 'a1',
            round: 1,
            kind: 'skipped',
            detail: 'admin_only',
          },
        ],
      }),
    ],
    ...over,
  };
}

export const SETTINGS_READ: ChatBroadcastSettingsRead = {
  campaign_id: 'c1',
  name: 'Крипто-чаты',
  status: 'draft',
  updated_at: '2026-10-06T10:00:00+00:00',
  account_ids: ['a1', 'a2'],
  settings: {
    targets: ['@alpha', 't.me/+AbCdEfGh123'],
    messages: [
      { kind: 'text', text: '{Привет|Здравствуйте}! Делаем ботов', photo: null, post: '' },
      { kind: 'post', text: '', photo: null, post: 't.me/mychannel/42' },
    ],
  },
};
