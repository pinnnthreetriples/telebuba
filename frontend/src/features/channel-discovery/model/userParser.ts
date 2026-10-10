// Парсер пользователей: форма и её перевод в запрос сервера. Живёт рядом с автопоиском
// каналов, потому что открывается из той же строки «Каналы кампании» и собран из тех же
// строк формы и того же выбора аккаунтов.
import type { UserParserRequest, UserParserSettings } from '@/shared/api';

export type ParserMode = 'members' | 'messages' | 'comments';

export const PARSER_MODES: readonly ParserMode[] = ['members', 'messages', 'comments'];

export type ParserToggle =
  | 'skipBots'
  | 'skipDeleted'
  | 'skipScam'
  | 'withUsername'
  | 'withPhoto'
  | 'premiumOnly'
  | 'withStories'
  | 'includeReplies'
  | 'includeForwards'
  | 'excludeOwn'
  | 'excludeAdmins'
  | 'excludeCollected';

// Telegram отдаёт статус «был в сети» только корзинами — точного времени нет, а у кого
// время скрыто настройками приватности, тот в корзину не попадает вовсе.
export type LastSeen = 'any' | 'recently' | 'week' | 'month';
export const LAST_SEEN: readonly LastSeen[] = ['any', 'recently', 'week', 'month'];

export type LimitSpec = { key: string; min: number; max: number; initial: number };

// Лимит участников не задокументирован Telegram: около 10 000 — это наблюдение, поэтому
// интерфейс говорит «может быть неполным», а не обещает весь список.
export const MODE_LIMITS: Record<ParserMode, LimitSpec[]> = {
  members: [{ key: 'members', min: 1, max: 10000, initial: 5000 }],
  messages: [
    { key: 'messages', min: 1, max: 50000, initial: 1000 },
    { key: 'days', min: 1, max: 365, initial: 30 },
  ],
  comments: [
    { key: 'posts', min: 1, max: 1000, initial: 50 },
    { key: 'perPost', min: 1, max: 5000, initial: 200 },
    { key: 'minLength', min: 0, max: 500, initial: 0 },
  ],
};

export const SKIP_TOGGLES: readonly ParserToggle[] = ['skipBots', 'skipDeleted', 'skipScam'];
export const PROFILE_TOGGLES: readonly ParserToggle[] = [
  'withUsername',
  'withPhoto',
  'premiumOnly',
  'withStories',
];
export const MESSAGE_TOGGLES: readonly ParserToggle[] = ['includeReplies', 'includeForwards'];
export const EXCLUDE_TOGGLES: readonly ParserToggle[] = [
  'excludeOwn',
  'excludeAdmins',
  'excludeCollected',
];

export type ParserForm = {
  mode: ParserMode;
  sources: string;
  keywords: string;
  // null — оператор не трогал выбор, берутся аккаунты по умолчанию (как в автопоиске).
  accountIds: string[] | null;
  limits: Record<string, number>;
  // Сколько раз человек должен написать (в режимах по сообщениям и комментариям).
  minMessages: number;
  // В скольких источниках из списка он должен встретиться.
  minSources: number;
  lastSeen: LastSeen;
  stopWords: string;
  blacklist: string;
  toggles: Record<ParserToggle, boolean>;
  protect: boolean;
  fast: boolean;
  chatDelay: number;
  requestDelay: number;
};

const ALL_LIMITS = Object.fromEntries(
  Object.values(MODE_LIMITS)
    .flat()
    .map((l) => [l.key, l.initial]),
);

export const EMPTY_PARSER_FORM: ParserForm = {
  mode: 'comments',
  sources: '',
  keywords: '',
  accountIds: null,
  limits: ALL_LIMITS,
  minMessages: 1,
  minSources: 1,
  lastSeen: 'any',
  stopWords: '',
  blacklist: '',
  toggles: {
    skipBots: true,
    skipDeleted: true,
    skipScam: true,
    withUsername: false,
    withPhoto: false,
    premiumOnly: false,
    withStories: false,
    includeReplies: true,
    includeForwards: false,
    excludeOwn: true,
    excludeAdmins: true,
    excludeCollected: false,
  },
  protect: true,
  fast: false,
  chatDelay: 5,
  requestDelay: 1,
};

/** Источники по одному на строку; пустые строки и повторы не считаются. */
export function parseSources(text: string): string[] {
  return [
    ...new Set(
      text
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean),
    ),
  ];
}

/** «shop, bot» или по строке — список слов в нижнем регистре. */
export function parseWords(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((word) => word.trim().toLowerCase())
    .filter(Boolean);
}

/** Ссылка на тему форума: t.me/chat/123 — номер темы в конце. */
export function topicOf(source: string): string | null {
  return /t\.me\/[\w+]+\/(\d+)\/?$/.exec(source)?.[1] ?? null;
}

// Ключи лимитов в форме — camelCase, на сервере — snake_case.
const LIMIT_KEYS = {
  members: 'members',
  messages: 'messages',
  days: 'days',
  posts: 'posts',
  perPost: 'per_post',
  minLength: 'min_length',
} as const;

const TOGGLE_KEYS = {
  skipBots: 'skip_bots',
  skipDeleted: 'skip_deleted',
  skipScam: 'skip_scam',
  withUsername: 'with_username',
  withPhoto: 'with_photo',
  premiumOnly: 'premium_only',
  withStories: 'with_stories',
  includeReplies: 'include_replies',
  includeForwards: 'include_forwards',
  excludeOwn: 'exclude_own',
  excludeAdmins: 'exclude_admins',
  excludeCollected: 'exclude_collected',
} as const satisfies Record<ParserToggle, string>;

type ServerLimits = NonNullable<UserParserSettings['limits']>;
type ServerToggles = NonNullable<UserParserSettings['toggles']>;

/** Форма как заготовка: может быть заполнена наполовину. */
export function formToSettings(form: ParserForm): UserParserSettings {
  const limits = Object.fromEntries(
    Object.entries(LIMIT_KEYS).map(([key, server]) => [
      server,
      form.limits[key] ?? ALL_LIMITS[key],
    ]),
  ) as ServerLimits;
  const toggles = Object.fromEntries(
    (Object.keys(TOGGLE_KEYS) as ParserToggle[]).map((key) => [
      TOGGLE_KEYS[key],
      form.toggles[key],
    ]),
  ) as ServerToggles;
  return {
    mode: form.mode,
    sources: parseSources(form.sources),
    keywords: parseWords(form.keywords),
    account_ids: form.accountIds ?? [],
    limits,
    min_messages: form.minMessages,
    min_sources: form.minSources,
    last_seen: form.lastSeen,
    stop_words: parseWords(form.stopWords),
    blacklist: parseWords(form.blacklist),
    toggles,
    protect: form.protect,
    fast: form.fast,
    chat_delay: form.chatDelay,
    request_delay: form.requestDelay,
  };
}

/** Запуск: аккаунты — те, что реально выбраны (свои или по умолчанию), имя — папки в «Базах». */
export function formToRequest(
  form: ParserForm,
  accountIds: string[],
  name: string,
): UserParserRequest {
  return {
    ...formToSettings(form),
    sources: parseSources(form.sources),
    account_ids: accountIds,
    name,
  };
}

/** Заготовка обратно в форму. Пустой список аккаунтов — «как по умолчанию». */
export function settingsToForm(settings: UserParserSettings): ParserForm {
  const base = EMPTY_PARSER_FORM;
  const limits = settings.limits ?? {};
  const toggles = settings.toggles ?? {};
  return {
    mode: settings.mode ?? base.mode,
    sources: (settings.sources ?? []).join('\n'),
    keywords: (settings.keywords ?? []).join(', '),
    accountIds: settings.account_ids?.length ? settings.account_ids : null,
    limits: Object.fromEntries(
      Object.entries(LIMIT_KEYS).map(([key, server]) => [
        key,
        limits[server] ?? ALL_LIMITS[key] ?? 0,
      ]),
    ),
    minMessages: settings.min_messages ?? base.minMessages,
    minSources: settings.min_sources ?? base.minSources,
    lastSeen: settings.last_seen ?? base.lastSeen,
    stopWords: (settings.stop_words ?? []).join(', '),
    blacklist: (settings.blacklist ?? []).join(', '),
    toggles: Object.fromEntries(
      (Object.keys(TOGGLE_KEYS) as ParserToggle[]).map((key) => [
        key,
        toggles[TOGGLE_KEYS[key]] ?? base.toggles[key],
      ]),
    ) as Record<ParserToggle, boolean>,
    protect: settings.protect ?? base.protect,
    fast: settings.fast ?? base.fast,
    chatDelay: settings.chat_delay ?? base.chatDelay,
    requestDelay: settings.request_delay ?? base.requestDelay,
  };
}
