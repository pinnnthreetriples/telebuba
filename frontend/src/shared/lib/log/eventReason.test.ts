import i18n from 'i18next';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import type { LogEntry } from '@/shared/api';

import { eventReason } from './eventReason';

// The real i18n instance, like eventLabel.test.ts: the key-ARRAY ladder and the
// defaultValue fallback are i18next behaviour, and a stub would prove neither.
const t = i18n.t.bind(i18n);

function entry(extra: LogEntry['extra']): LogEntry {
  return {
    id: 1,
    created_at: '2026-08-03T10:00:00+00:00',
    level: 'ERROR',
    status: 'error',
    account_id: 'acc-1',
    event: 'neurocomment_telegram_join_channel_failed',
    extra,
  };
}

test('a row with neither field says nothing, so a caller can render its own placeholder', () => {
  expect(eventReason(t, entry({}))).toBe('');
  expect(eventReason(t, entry({ channel: '@a' }))).toBe('');
});

test('resolves a reason, and falls back to status when there is no reason', () => {
  expect(eventReason(t, entry({ reason: 'rejoin_exhausted' }))).toBe(
    'попытки вернуться в чат закончились',
  );
  expect(eventReason(t, entry({ status: 'failed' }))).toBe('Telegram отклонил');
  // `reason` wins: a row carrying both is a failure whose specific cause is known.
  expect(eventReason(t, entry({ reason: 'quota', status: 'failed' }))).toBe('лимит исчерпан');
});

test('spells out an exception class and a gateway stable code from their own maps', () => {
  // Three maps, one ladder: a Telethon class name, a profile code and a channel code.
  expect(eventReason(t, entry({ error_type: 'ChannelPrivateError' }))).toBe(
    'чат закрыт: не пускают или выгнали',
  );
  expect(eventReason(t, entry({ error_type: 'session_dead' }))).toBe(
    'Сессия аккаунта в Telegram недействительна — войдите заново',
  );
  expect(eventReason(t, entry({ error_type: 'chat_admin_required' }))).toBe(
    'Нужны права администратора канала',
  );
});

test('an unmapped error type renders raw rather than blank', () => {
  // Readable back is the floor: a blank would report a failure with no cause at all,
  // which is the defect this module exists to close.
  expect(eventReason(t, entry({ error_type: 'SomeUnmappedRpcError' }))).toBe(
    'SomeUnmappedRpcError',
  );
});

test('reason and error type sit side by side, not one behind the other', () => {
  // `status: "failed"` always translates, so an error type placed BEHIND the reason as a
  // fallback would never render and the row would keep saying a post failed without why.
  expect(eventReason(t, entry({ status: 'failed', error_type: 'ChannelPrivateError' }))).toBe(
    'Telegram отклонил · чат закрыт: не пускают или выгнали',
  );
});

test('a non-string extra value is ignored rather than stringified', () => {
  // `extra` is free-form JSON: a numeric or object value under one of these keys must not
  // become the operator's explanation.
  expect(eventReason(t, entry({ reason: 42, error_type: null }))).toBe('');
});
