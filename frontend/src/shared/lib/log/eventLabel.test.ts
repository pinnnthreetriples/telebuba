import i18n from 'i18next';
import type { TFunction } from 'i18next';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { eventLabel } from './eventLabel';

// Use the real i18n instance so defaultValue fallback + plural/compositional
// resolution behave exactly as they do in the app.
const t = i18n.t.bind(i18n);

test('resolves an exact logEvent entry to its translation', () => {
  expect(eventLabel(t, 'neurocomment_posted')).toBe('Комментарий опубликован');
  expect(eventLabel(t, 'tdata_convert_completed')).toBe('Импорт tdata завершён');
  expect(eventLabel(t, 'app_started')).toBe('Приложение запущено');
});

test('composes dynamic telegram action codes from action + status', () => {
  expect(eventLabel(t, 'telegram_set_online')).toBe('Заход в сеть');
  expect(eventLabel(t, 'telegram_set_online_failed')).toBe('Заход в сеть — ошибка');
  expect(eventLabel(t, 'telegram_read_channel_flood_wait')).toBe(
    'Чтение канала — Telegram просит подождать',
  );
  expect(eventLabel(t, 'telegram_join_channel_already_participant')).toBe(
    'Подписка на канал — уже участник',
  );
});

test('a gateway domain prefix resolves to the same label as the bare form', () => {
  expect(eventLabel(t, 'warming_telegram_set_online')).toBe('Заход в сеть');
  expect(eventLabel(t, 'neurocomment_telegram_set_online_failed')).toBe('Заход в сеть — ошибка');
  expect(eventLabel(t, 'warming_telegram_read_channel_flood_wait')).toBe(
    'Чтение канала — Telegram просит подождать',
  );
  // The one fixed gateway name is prefixed too, and still hits its exact entry.
  expect(eventLabel(t, 'neurocomment_telegram_action_unavailable')).toBe(
    'Telegram временно недоступен',
  );
  // A multi-word domain strips too — the convention puts no shape constraint on a name.
  expect(eventLabel(t, 'spam_status_telegram_set_online')).toBe('Заход в сеть');
});

test('a domain-specific override key wins over the shared label when one exists', () => {
  // No such key ships today (labels are deliberately domain-independent), so stub one in:
  // the raw-code lookup must run BEFORE the prefix strip, or adding one could never work.
  // The code has to be one whose BARE form has its own exact entry, or both lookup orders
  // fall through to the same raw-code answer and the test discriminates nothing.
  const withOverride = ((key: string, opts?: { defaultValue?: string }) =>
    key === 'logEvent.neurocomment_telegram_action_unavailable'
      ? 'Telegram недоступен (нейрокомментинг)'
      : i18n.t(key, opts)) as unknown as TFunction;
  expect(eventLabel(withOverride, 'neurocomment_telegram_action_unavailable')).toBe(
    'Telegram недоступен (нейрокомментинг)',
  );
  // Bare-first ordering would return this instead, so the assertion above pins the ORDER,
  // not just the outcome. Every un-overridden domain still gets the one shared label.
  expect(eventLabel(withOverride, 'warming_telegram_action_unavailable')).toBe(
    'Telegram временно недоступен',
  );
});

test('a code that is already bare is never treated as domain-prefixed', () => {
  // The lazy prefix match would otherwise eat `telegram_relay_` and mislabel this as a
  // plain `telegram_join_channel`. Unresolvable is correct here; mislabelled is not.
  expect(eventLabel(t, 'telegram_relay_telegram_join_channel')).toBe(
    'telegram_relay_telegram_join_channel',
  );
});

test('falls back to the raw code for an unmapped event', () => {
  expect(eventLabel(t, 'totally_unknown_event')).toBe('totally_unknown_event');
  expect(eventLabel(t, 'telegram_no_such_action')).toBe('telegram_no_such_action');
  // The safety net returns the code as received, prefix included.
  expect(eventLabel(t, 'warming_telegram_no_such_action')).toBe('warming_telegram_no_such_action');
  expect(eventLabel(t, '')).toBe('');
});
