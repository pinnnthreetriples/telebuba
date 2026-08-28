// Тестовые данные для снимков реальных экранов.
//
// Зачем вообще: типографика и ритм переехали в 2000+ мест, а проверял их каталог —
// то есть примитивы по одному. Каталог не показывает, что происходит с таблицей на 12
// колонок, с длинным именем канала в узкой колонке или с шапкой страницы над плотным
// списком. Ровно там перевёрстка и видна, и ровно там её никто не смотрел.
//
// Данные придуманные, но формы — из `openapi.json`: поля названы так, как их называет
// контракт, потому что фикстура с чужими именами полей рисует пустое состояние и снимок
// врёт, ничего при этом не ломая.
//
// Бэкенд не поднимается: запросы перехватываются в браузере. Живой стенд на :8080 при
// этом не задействован — снимать надо не его.

const NOW = '2026-08-28T12:00:00Z';

export const me = { id: 'u-1', username: 'operator', role: 'admin' };

export const health = { status: 'ok', version: '1.0.0' };

function account(i: number, over: Record<string, unknown> = {}) {
  return {
    account_id: `acc-${String(i)}`,
    label: null,
    session_name: `session_${String(i)}`,
    status: 'alive',
    user_id: 100000 + i,
    phone: `+7 900 ${String(100 + i)}-22-33`,
    username: `user_${String(i)}`,
    first_name: ['Иван', 'Мария', 'Пётр', 'Анна', 'Сергей'][i % 5],
    last_name: ['Петров', 'Смирнова', 'Кузнецов', 'Волкова', 'Орлов'][i % 5],
    avatar_etag: null,
    last_checked_at: NOW,
    created_at: NOW,
    updated_at: NOW,
    device_platform: 'android',
    device_model: 'Pixel 7',
    device_system_version: '14',
    device_app_version: '10.9.2',
    device_lang: 'ru',
    bio: 'Читаю про крипту и путешествия',
    proxy_id: `px-${String(i % 3)}`,
    proxy_type: 'socks5',
    proxy_host: `10.0.0.${String(i)}`,
    proxy_port: 1080,
    proxy_status: 'tcp_working',
    proxy_last_checked_at: NOW,
    proxy_last_error: null,
    proxy_exit_ip: `185.10.0.${String(i)}`,
    proxy_country_code: ['de', 'nl', 'fi'][i % 3],
    proxy_country_name: ['Германия', 'Нидерланды', 'Финляндия'][i % 3],
    trust_score: 90 - i * 7,
    trust_band: i < 2 ? 'high' : i < 4 ? 'medium' : 'low',
    spam_status: i === 4 ? 'restricted' : 'clean',
    spam_detail: i === 4 ? 'Ограничение до 30.08' : null,
    ...over,
  };
}

export const accounts = {
  items: [
    account(0),
    account(1),
    account(2, { status: 'unauthorized' }),
    account(3, { status: 'flood_wait', proxy_status: 'failed', proxy_last_error: 'Таймаут' }),
    account(4, { status: 'frozen' }),
  ],
  next_cursor: null,
};

export const accountStats = { total: 5, active: 2, idle: 1, needs_code: 1, problem: 1 };

export const proxies = {
  proxies: [0, 1, 2].map((i) => ({
    id: `px-${String(i)}`,
    proxy_type: 'socks5',
    host: `10.0.0.${String(i)}`,
    port: 1080,
    username: 'tb',
    has_password: true,
    status: i === 2 ? 'failed' : 'tcp_working',
    last_checked_at: NOW,
    last_error: i === 2 ? 'Соединение отклонено' : null,
    exit_ip: `185.10.0.${String(i)}`,
    country_code: ['de', 'nl', 'fi'][i],
    country_name: ['Германия', 'Нидерланды', 'Финляндия'][i],
    geo_status: i === 1 ? 'conflict' : 'ok',
    ipinfo_country_code: ['de', 'nl', 'fi'][i],
    maxmind_country_code: i === 1 ? 'de' : ['de', 'nl', 'fi'][i],
    asn: 'AS9009',
    is_datacenter: true,
    created_at: NOW,
    updated_at: NOW,
    used: [4, 1, 0][i] ?? 0,
    capacity: 5,
    free: [1, 4, 5][i] ?? 5,
  })),
};

function warmingState(i: number, over: Record<string, unknown> = {}) {
  return {
    account_id: `acc-${String(i)}`,
    label: `${['Иван', 'Мария', 'Пётр', 'Анна', 'Сергей'][i % 5] ?? 'Иван'} ${
      ['Петров', 'Смирнова', 'Кузнецов', 'Волкова', 'Орлов'][i % 5] ?? 'Петров'
    }`,
    state: 'active',
    health: 'ok',
    cycles_completed: 12 + i,
    last_event: 'joined_channel',
    last_cycle_at: NOW,
    next_run_at: '2026-08-28T13:40:00Z',
    updated_at: NOW,
    last_error: null,
    last_action: 'reaction',
    last_channel: '@crypto_daily',
    heartbeat_at: NOW,
    started_at: '2026-08-20T09:00:00Z',
    stopped_at: null,
    flood_wait_seconds: null,
    flood_wait_until: null,
    proxy_snapshot: 'socks5 10.0.0.1:1080',
    daily_actions: 18 + i,
    daily_count_date: '2026-08-28',
    quarantine_count: 0,
    trust_score: 88 - i * 6,
    trust_band: i < 2 ? 'high' : 'medium',
    trust_reasons: [],
    spam_status: 'clean',
    spam_detail: null,
    age_hours: 192 + i * 24,
    dm_allowed: true,
    phone_country: 'ru',
    proxy_country: 'de',
    phone: `+7 900 ${String(100 + i)}-22-33`,
    first_name: ['Иван', 'Мария', 'Пётр', 'Анна', 'Сергей'][i % 5],
    last_name: ['Петров', 'Смирнова', 'Кузнецов', 'Волкова', 'Орлов'][i % 5],
    avatar_etag: null,
    proxy_type: 'socks5',
    phase: 'settling',
    daily_cap: 40,
    progress_to_next: 0.6,
    days_to_next_phase: 4,
    warming_days: 8,
    target_days: 15,
    activity_persona: 'reader',
    readiness: null,
    promoted_to_nc: false,
    nc_handed_off: false,
    ...over,
  };
}

export const warmingSettings = {
  inter_account_chat: true,
  reactions_enabled: true,
  join_enabled: true,
  enforce_readiness: true,
  has_gemini_key: true,
  gemini_model: 'gemini-2.0-flash',
  gemini_max_retries: 3,
  gemini_min_interval_seconds: 1.5,
  has_openai_key: false,
  openai_model: 'gpt-4o-mini',
  captcha_llm_provider: 'gemini',
  updated_at: NOW,
};

export const warmingBoard = {
  idle: [warmingState(3, { state: 'idle', phase: null, warming_days: null })],
  warming: [
    warmingState(0),
    warmingState(1),
    warmingState(2, { state: 'flood_wait', health: 'warn', flood_wait_seconds: 420 }),
  ],
  channels: {},
  settings: warmingSettings,
  channel_count: 24,
  active_count: 3,
  summary: {},
  card_log_limit: 20,
  warmed: [warmingState(4, { state: 'idle', promoted_to_nc: true })],
};

export const neurocommentCampaigns = {
  campaigns: [0, 1].map((i) => ({
    campaign_id: `nc-${String(i)}`,
    name: ['Крипта', 'Путешествия'][i],
    prompt: 'Короткий дружелюбный комментарий по теме поста, без ссылок.',
    status: i === 0 ? 'active' : 'paused',
    created_at: NOW,
    updated_at: NOW,
    solver_enabled: true,
    channel_count: [4, 2][i] ?? 0,
    account_count: [3, 1][i] ?? 0,
  })),
};

export const neurocommentRuntime = {
  running: true,
  active_channels: 4,
  unwatched_channels: [],
  listener_account_id: 'acc-0',
  log_limit: 200,
  onboarding: false,
};

// Все поля контракта, включая числовые: без них экран настроек рисовал `undefined` в
// четырёх полях, и снимок-эталон закрепил бы это как норму.
export const neurocommentSettings = {
  max_comments_per_hour: 4,
  max_comments_per_channel_per_day: 12,
  reply_delay_min_seconds: 45,
  reply_delay_max_seconds: 180,
  min_trust_score: 60,
  comment_mode: 'first',
  reply_wait_minutes: 10,
  updated_at: NOW,
};

export const neuroshillingCampaigns = {
  campaigns: [0, 1].map((i) => ({
    campaign_id: `ns-${String(i)}`,
    name: ['Запуск токена', 'Обсуждение в чатах'][i],
    mode: i === 0 ? 'scenario' : 'chat',
    topic: 'Новый токен на Solana',
    targets_raw: '@solana_chat\n@defi_talks',
    unique_messages: true,
    use_chat_context: true,
    media_message_link: null,
    media_step_position: null,
    scenario_status: 'approved',
    run_mode: 'paced',
    pause_min_seconds: 40,
    pause_max_seconds: 180,
    messages_per_hour: 6,
    messages_per_chat_per_day: 12,
    total_per_account: 40,
    reserve_enabled: true,
    autoresponder: 'off',
    reply_to_humans: true,
    reply_activity: 'medium',
    listen_minutes: 30,
    status: i === 0 ? 'running' : 'idle',
    run_id: i === 0 ? 'run-1' : null,
    last_error: null,
    created_at: NOW,
    updated_at: NOW,
  })),
};

export const logs = {
  items: [0, 1, 2, 3].map((i) => ({
    id: 100 + i,
    created_at: NOW,
    level: i === 3 ? 'error' : 'info',
    status: i === 3 ? 'failed' : 'ok',
    account_id: `acc-${String(i % 3)}`,
    // Коды событий — из `logEvent.*` перевода, а не придуманные: приложение печатает
    // сам код, когда подписи нет, и в снимке остаётся `neurocomment.comment.posted`
    // вместо русской строки. Ровно та же ошибка, что была с перечислениями статусов.
    event: [
      'neurocomment_posted',
      'neurocomment_post_received',
      'neurocomment_comment_deleted',
      'neurocomment_post_failed',
    ][i],
    extra: i === 3 ? { error_type: 'SlowModeWaitError', channel: '@crypto_daily' } : {},
  })),
  next_cursor: null,
};

export const neurocommentBoard = {
  campaign_id: 'nc-0',
  campaign_name: 'Крипта',
  status: 'active',
  solver_enabled: true,
  accounts: [0, 1].map((i) => ({
    account_id: `acc-${String(i)}`,
    label: ['Иван Петров', 'Мария Смирнова'][i] ?? '',
    comments_last_hour: [2, 1][i] ?? 0,
    max_comments_per_hour: 4,
    comments_today: [9, 5][i] ?? 0,
    deleted_today: [1, 0][i] ?? 0,
    last_comment_at: NOW,
    last_comment_text: 'Интересный разбор, спасибо',
    last_comment_deleted: false,
    last_comment_channel: '@crypto_daily',
    pinned_channels: ['@crypto_daily'],
    readiness: [],
  })),
  channels: [
    {
      channel: '@crypto_daily',
      status: 'watching',
      ready_accounts: 2,
      total_accounts: 2,
      deleted_recent: 0,
    },
    {
      channel: '@defi_news',
      status: 'joining',
      ready_accounts: 1,
      total_accounts: 2,
      deleted_recent: 1,
    },
  ],
  comments: [0, 1].map((i) => ({
    channel: '@crypto_daily',
    post_id: 400 + i,
    campaign_id: 'nc-0',
    account_id: `acc-${String(i)}`,
    status: i === 1 ? 'deleted' : 'posted',
    comment_text: ['Интересный разбор, спасибо', 'А что с рисками?'][i] ?? '',
    comment_msg_id: 900 + i,
    created_at: NOW,
    updated_at: NOW,
    deleted_at: i === 1 ? NOW : null,
  })),
};

export const challenges = { rows: [] };

export const neuroshillingBoard = {
  campaign: neuroshillingCampaigns.campaigns[0],
  available: [0, 1, 2].map((i) => ({
    account_id: `acc-${String(i)}`,
    title: ['Иван Петров', 'Мария Смирнова', 'Пётр Кузнецов'][i] ?? '',
    assigned: i < 2,
    role_id: i < 2 ? `role-${String(i)}` : null,
    is_reserve: i === 2,
    state: 'ready',
    busy_owner: null,
    busy_campaign_name: null,
  })),
  targets: ['@solana_chat', '@defi_talks'],
  run: {
    status: 'running',
    run_id: 'run-1',
    sent: 34,
    total: 120,
    substitutions: 2,
    listening: true,
    chat_messages_seen: 512,
    human_replies_sent: 7,
    last_error_type: null,
    halted_accounts: [],
  },
};

export const neuroshillingScenario = {
  campaign_id: 'ns-0',
  scenario_status: 'approved',
  roles: [0, 1].map((i) => ({
    role_id: `role-${String(i)}`,
    name: ['Скептик', 'Энтузиаст'][i] ?? '',
    description: 'Задаёт вопросы по делу',
    created_at: NOW,
  })),
  steps: [0, 1, 2].map((i) => ({
    step_id: `step-${String(i)}`,
    position: i,
    kind: i === 2 ? 'reaction' : 'message',
    role_id: `role-${String(i % 2)}`,
    text: ['Кто-нибудь смотрел этот токен?', 'Смотрел, тесты уже прошли', ''][i] ?? '',
    reply_to_position: i === 1 ? 0 : null,
    target_position: i === 2 ? 1 : null,
    emoji: i === 2 ? '👍' : null,
    delay_min_seconds: 30,
    delay_max_seconds: 120,
  })),
};
