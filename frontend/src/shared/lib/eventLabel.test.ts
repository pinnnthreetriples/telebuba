import { expect, test } from 'vitest';

import { i18n } from '@/shared/i18n';

import { eventLabel } from './eventLabel';

test('maps a known code to its translation', () => {
  expect(eventLabel(i18n.t, 'neurocomment_posted')).toBe('Комментарий опубликован');
  expect(eventLabel(i18n.t, 'warming_started')).toBe('Прогрев запущен');
});

test('localizes the tdata import/conversion events (not raw codes)', () => {
  expect(eventLabel(i18n.t, 'tdata_convert_completed')).toBe('Импорт tdata завершён');
  expect(eventLabel(i18n.t, 'tdata_no_accounts')).toBe('В архиве нет аккаунтов');
});

test('falls back to the raw code for an unmapped event', () => {
  expect(eventLabel(i18n.t, 'totally_unknown_event')).toBe('totally_unknown_event');
  expect(eventLabel(i18n.t, '')).toBe('');
});
