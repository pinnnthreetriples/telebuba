import i18n from 'i18next';
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
  expect(eventLabel(t, 'telegram_read_channel_flood_wait')).toBe('Чтение канала — флуд-контроль');
  expect(eventLabel(t, 'telegram_join_channel_already_participant')).toBe(
    'Подписка на канал — уже участник',
  );
});

test('falls back to the raw code for an unmapped event', () => {
  expect(eventLabel(t, 'totally_unknown_event')).toBe('totally_unknown_event');
  expect(eventLabel(t, 'telegram_no_such_action')).toBe('telegram_no_such_action');
  expect(eventLabel(t, '')).toBe('');
});
