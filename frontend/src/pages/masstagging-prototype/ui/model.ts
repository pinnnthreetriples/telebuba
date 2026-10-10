// PROTOTYPE — throwaway. Mock data and the in-memory draft shared by the three
// layout variants, so flipping variants keeps what the operator typed.

export type Mode = 'stories' | 'priming';

export type MockAccount = {
  id: string;
  name: string;
  phone: string;
  username: string;
  country: string;
  premium: boolean;
  proxyOk: boolean;
  ageDays: number;
  spamblock: boolean;
  busy: boolean;
};

export const ACCOUNTS: MockAccount[] = [
  { id: 'a1', name: 'Артём', phone: '5563948135484', username: 'artemgs_y5x', country: 'BR', premium: true, proxyOk: true, ageDays: 8, spamblock: false, busy: false },
  { id: 'a2', name: 'Иван', phone: '992115542736', username: 'ivanhj_97x', country: 'TJ', premium: false, proxyOk: true, ageDays: 7, spamblock: false, busy: false },
  { id: 'a3', name: 'Carolina', phone: '998951950321', username: 'carolina1999_jbjw', country: 'US', premium: true, proxyOk: true, ageDays: 120, spamblock: false, busy: false },
  { id: 'a4', name: 'Игорь', phone: '998770796270', username: 'igor1997_fpwf', country: 'TR', premium: false, proxyOk: true, ageDays: 8, spamblock: false, busy: false },
  { id: 'a5', name: 'Gabriela', phone: '13528694517', username: 'gabriela_photo_ksax', country: 'US', premium: true, proxyOk: true, ageDays: 120, spamblock: false, busy: false },
  { id: 'a6', name: 'Júlia', phone: '56948100900', username: 'jname_fjy6d', country: 'US', premium: false, proxyOk: true, ageDays: 120, spamblock: false, busy: true },
  { id: 'a7', name: 'Миша', phone: '16072666691', username: 'mname_memzv', country: 'US', premium: false, proxyOk: true, ageDays: 30, spamblock: false, busy: false },
  { id: 'a8', name: 'Гена', phone: '16075550142', username: 'gena_rk20', country: 'US', premium: false, proxyOk: false, ageDays: 3, spamblock: false, busy: false },
  { id: 'a9', name: 'Софья', phone: '79210004411', username: 'sofya_lnt', country: 'KZ', premium: false, proxyOk: true, ageDays: 64, spamblock: true, busy: false },
];

export type Draft = {
  mode: Mode;
  accountIds: string[];
  targetSource: 'manual' | 'contacts';
  targets: string[];
  media: string[];
  caption: string;
  link: string;
  linkStyle: 'visible' | 'hidden';
  plateText: string;
  mentionsPerStory: number;
  storiesPerDay: number;
  storyGapMin: number;
  storyGapMax: number;
  dialogDelayMin: number;
  dialogDelayMax: number;
  dialogsPerDay: number;
  floodPause: number;
  floodsToQuarantine: number;
  volume: 'count' | 'time';
  maxTags: number;
  maxMinutes: number;
  skipTagged: boolean;
  lifetime: '6' | '12' | '24' | '48';
};

export const INITIAL_DRAFT: Draft = {
  mode: 'stories',
  accountIds: ['a1', 'a2', 'a3'],
  targetSource: 'manual',
  targets: ['@eduardafc_b7z', '@bivenmo', '+79001234567'],
  media: ['b0d36305-aa49.mp4'],
  caption: 'ПОСМОТРЕТЬ ОБУЧЕНИЕ',
  link: 'https://google.com',
  linkStyle: 'visible',
  plateText: 'Клик',
  mentionsPerStory: 2,
  storiesPerDay: 15,
  storyGapMin: 1,
  storyGapMax: 2,
  dialogDelayMin: 20,
  dialogDelayMax: 60,
  dialogsPerDay: 6,
  floodPause: 300,
  floodsToQuarantine: 3,
  volume: 'count',
  maxTags: 50,
  maxMinutes: 0,
  skipTagged: true,
  lifetime: '24',
};

// Telegram's own ceilings, per limits.tginfo.me (checked 2026-10). A free account
// gets ONE story a day and only a handful in its lifetime, no clickable link
// stickers, a 200-char caption, a 24h lifetime. Premium: 100/day, 6/12/24/48h.
export const FREE_STORIES_PER_DAY = 1;
export const FREE_MENTIONS_PER_STORY = 5;
export const PREMIUM_STORIES_PER_DAY = 100;
export const FREE_CAPTION_LIMIT = 200;
export const USERNAME_RESOLVES_PER_DAY = 200;

export type Patch = (patch: Partial<Draft>) => void;

export function selectedAccounts(draft: Draft): MockAccount[] {
  return ACCOUNTS.filter((account) => draft.accountIds.includes(account.id));
}

// How many targets one account can reach in a day under the draft's limits.
export function dailyReach(account: MockAccount, draft: Draft): number {
  if (draft.mode === 'priming') return Math.min(draft.dialogsPerDay, USERNAME_RESOLVES_PER_DAY);
  const stories = account.premium
    ? Math.min(draft.storiesPerDay, PREMIUM_STORIES_PER_DAY)
    : FREE_STORIES_PER_DAY;
  const perStory = account.premium
    ? draft.mentionsPerStory
    : Math.min(draft.mentionsPerStory, FREE_MENTIONS_PER_STORY);
  return Math.min(stories * perStory, USERNAME_RESOLVES_PER_DAY);
}

export function estimate(draft: Draft) {
  const accounts = selectedAccounts(draft);
  const perDay = accounts.reduce((sum, account) => sum + dailyReach(account, draft), 0);
  const free = accounts.filter((account) => !account.premium).length;
  const days = perDay === 0 ? null : Math.ceil(draft.targets.length / perDay);
  return { accounts, perDay, free, days };
}

// What blocks a launch, in the order the operator would fix it.
export function blockers(draft: Draft): string[] {
  const out: string[] = [];
  if (draft.accountIds.length === 0) out.push('Не выбрано ни одного аккаунта');
  if (draft.targets.length === 0) out.push('Список целей пуст');
  if (draft.mode === 'stories' && draft.media.length === 0) out.push('Пул медиа пуст');
  if (draft.mode === 'stories' && selectedAccounts(draft).every((a) => !a.premium) && draft.accountIds.length > 0)
    out.push('Все аккаунты без Premium — 1 сторис в сутки на аккаунт');
  return out;
}

export function parseTargets(raw: string): string[] {
  return raw
    .split(/[\s,;]+/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}
