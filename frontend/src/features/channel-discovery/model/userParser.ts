// Парсер пользователей — ПРОТОТИП: форма и демонстрационный прогон без бэкенда. Живёт
// рядом с автопоиском каналов, потому что открывается из той же строки «Каналы кампании»
// и собран из тех же строк формы и того же выбора аккаунтов.

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

export type ParsedUser = {
  id: number;
  name: string;
  username: string | null;
  premium: boolean;
  photo: boolean;
  stories: boolean;
  bot: boolean;
  deleted: boolean;
  admin: boolean;
  lastSeen: 'online' | 'recently' | 'week' | 'month' | 'long' | 'hidden';
  count: number;
  firstAt: string;
  lastAt: string;
  sources: string[];
};

// Имя и латинский хэндл: username в Telegram бывает только латиницей. Пара «магазинов» —
// чтобы стоп-слова в демо было на ком показать.
const NAMES: [string, string][] = [
  ['Алексей', 'alexey'],
  ['Мария', 'maria'],
  ['Dmitry K.', 'dmitry'],
  ['Анна', 'anna'],
  ['Crypto Shop', 'cryptoshop'],
  ['Олег', 'oleg'],
  ['Kate', 'kate'],
  ['Игорь', 'igor'],
  ['Liam', 'liam'],
  ['Наталья', 'natalia'],
  ['Promo Bot', 'promo_bot'],
  ['Elena V.', 'elena'],
  ['Павел', 'pavel'],
  ['Ксения', 'ksenia'],
  ['Mehmet', 'mehmet'],
  ['Юлия', 'yulia'],
];
const SEEN: ParsedUser['lastSeen'][] = [
  'online',
  'recently',
  'recently',
  'week',
  'month',
  'long',
  'hidden',
];

/** Детерминированный «сырой» улов: одинаковый на каждый запуск прототипа. */
export function mockUsers(sources: string[], total: number): ParsedUser[] {
  const now = Date.UTC(2026, 9, 4);
  const day = 86_400_000;
  return Array.from({ length: total }, (_, i) => {
    const [name, handle] = NAMES[i % NAMES.length] ?? ['User', 'user'];
    const span = (i * 7) % 29;
    // Каждый третий встречается сразу в нескольких источниках — для фильтра пересечения.
    const extra = i % 3 === 0 ? Math.min(sources.length, 1 + (i % 4)) : 1;
    const picked = Array.from(
      { length: extra },
      (_, k) => sources[(i + k) % Math.max(sources.length, 1)] ?? '',
    );
    return {
      id: 5_100_000_000 + i * 7919,
      name,
      username: i % 4 === 3 ? null : `${handle}_${String(i)}`,
      premium: i % 6 === 0,
      photo: i % 5 !== 4,
      stories: i % 3 === 1,
      bot: i % 11 === 10,
      deleted: i % 13 === 12,
      admin: i % 17 === 0,
      lastSeen: SEEN[i % SEEN.length] ?? 'long',
      count: 1 + ((i * 13) % 12),
      firstAt: new Date(now - (span + 1) * day).toISOString(),
      lastAt: new Date(now - (i % 3) * day).toISOString(),
      sources: [...new Set(picked)],
    };
  });
}

const SEEN_RANK: Record<ParsedUser['lastSeen'], number> = {
  online: 0,
  recently: 1,
  week: 2,
  month: 3,
  long: 4,
  hidden: 5,
};
const WANT_RANK: Record<LastSeen, number> = { any: 5, recently: 1, week: 2, month: 3 };

/** Те же фильтры, что обещает форма, — на демо-данных, чтобы было видно их действие. */
export function applyFilters(users: ParsedUser[], form: ParserForm): ParsedUser[] {
  const stop = parseWords(form.stopWords);
  const banned = new Set(
    parseWords(form.blacklist).map((entry) => entry.replace(/^@/, '').replace(/^t\.me\//, '')),
  );
  const counted = form.mode !== 'members';
  return users.filter((user) => {
    const handle = user.username?.toLowerCase() ?? '';
    if (form.toggles.skipBots && user.bot) return false;
    if (form.toggles.skipDeleted && user.deleted) return false;
    if (form.toggles.excludeAdmins && user.admin) return false;
    if (counted && user.count < form.minMessages) return false;
    if (user.sources.length < form.minSources) return false;
    if (SEEN_RANK[user.lastSeen] > WANT_RANK[form.lastSeen]) return false;
    if (form.toggles.withUsername && user.username === null) return false;
    if (form.toggles.withPhoto && !user.photo) return false;
    if (form.toggles.premiumOnly && !user.premium) return false;
    if (form.toggles.withStories && !user.stories) return false;
    const haystack = `${user.name} ${handle}`.toLowerCase();
    if (stop.some((word) => haystack.includes(word))) return false;
    if (banned.has(handle) || banned.has(String(user.id))) return false;
    return true;
  });
}

// Сохранённый сбор — «папка» во вкладке «Базы». Без автоудаления: удаляет только оператор.
export type ParsedBase = {
  id: string;
  name: string;
  mode: ParserMode;
  createdAt: string;
  sources: string[];
  users: ParsedUser[];
};

export function usersToCsv(users: ParsedUser[]): string {
  const head = 'id,name,username,premium,last_seen,count,first_at,last_at,sources';
  const rows = users.map((u) =>
    [
      u.id,
      JSON.stringify(u.name),
      u.username ?? '',
      u.premium,
      u.lastSeen,
      u.count,
      u.firstAt,
      u.lastAt,
      JSON.stringify(u.sources.join(' ')),
    ].join(','),
  );
  return [head, ...rows].join('\n');
}
