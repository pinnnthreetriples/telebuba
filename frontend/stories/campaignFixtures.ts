import type {
  NeuroshillingBoard,
  NeuroshillingCampaign,
  NeuroshillingScenario,
  NeuroshillingSettings,
} from '@/shared/api';

const created = '2026-08-28T12:00:00Z';

export const campaign: NeuroshillingCampaign = {
  campaign_id: 'ns-0',
  name: 'Обсуждение продукта с очень длинным названием для проверки узкого экрана',
  mode: 'campaign',
  topic: 'Какие вопросы возникают перед первым запуском?',
  targets_raw: '@solana_chat\n@defi_talks',
  scenario_status: 'approved',
  run_mode: 'sequential',
  status: 'running',
  unique_messages: true,
  use_chat_context: true,
  messages_per_hour: 6,
  messages_per_chat_per_day: 12,
  total_per_account: 40,
  reserve_enabled: true,
  pause_min_seconds: 40,
  pause_max_seconds: 180,
  autoresponder: 'off',
  reply_to_humans: true,
  reply_activity: 'medium',
  listen_minutes: 30,
  created_at: created,
  updated_at: created,
};

export const scenario: NeuroshillingScenario = {
  campaign_id: campaign.campaign_id,
  scenario_status: 'approved',
  roles: [
    { role_id: 'role-0', name: 'Скептик', description: 'Спрашивает о рисках', created_at: created },
    { role_id: 'role-1', name: 'Энтузиаст', description: 'Отвечает по делу', created_at: created },
  ],
  steps: [
    {
      step_id: 'step-0',
      position: 1,
      kind: 'message',
      role_id: 'role-0',
      text: 'Как проверить результат и не потерять настройки?',
      delay_min_seconds: 30,
      delay_max_seconds: 120,
    },
    {
      step_id: 'step-1',
      position: 2,
      kind: 'message',
      role_id: 'role-1',
      text: 'Сначала прочитайте сценарий целиком, затем сохраните черновик. Эта длинная реплика помогает проверить перенос строк на телефоне.',
      reply_to_position: 1,
      delay_min_seconds: 30,
      delay_max_seconds: 120,
    },
    {
      step_id: 'step-2',
      position: 3,
      kind: 'reaction',
      role_id: 'role-0',
      target_position: 2,
      emoji: '👍',
      delay_min_seconds: 30,
      delay_max_seconds: 120,
    },
  ],
};

export const board: NeuroshillingBoard = {
  campaign,
  available: [
    {
      account_id: 'acc-0',
      title: 'Иван Петров',
      assigned: true,
      role_id: 'role-0',
      state: 'active',
    },
    {
      account_id: 'acc-1',
      title: 'Мария Смирнова',
      assigned: true,
      role_id: 'role-1',
      state: 'active',
    },
    {
      account_id: 'acc-2',
      title: 'Пётр Кузнецов',
      assigned: false,
      is_reserve: true,
      state: 'active',
    },
  ],
  targets: ['@solana_chat', '@defi_talks'],
  run: {
    status: 'running',
    sent: 34,
    total: 120,
    substitutions: 2,
    listening: true,
    chat_messages_seen: 512,
    human_replies_sent: 7,
    halted_accounts: [],
  },
};

export const settings: NeuroshillingSettings = {
  campaign,
  scenario,
  accounts: (board.available ?? []).map(({ account_id, role_id, is_reserve }) => ({
    account_id,
    role_id,
    is_reserve,
  })),
};
