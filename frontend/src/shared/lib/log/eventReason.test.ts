import i18n from 'i18next';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import type { LogEntry } from '@/shared/api';
import ru from '@/shared/i18n/ru.json';

import { eventReason } from './eventReason';

// The real i18n instance, like eventLabel.test.ts: the key-ARRAY ladder and the
// defaultValue fallback are i18next behaviour, and a stub would prove neither.
const t = i18n.t.bind(i18n);

// Every label the ladder can reach, read from the bundle rather than listed here, so a
// code added tomorrow is covered the day it is written.
const codeLabels: Record<string, string> = {
  ...ru.logEventReason,
  ...ru.logEventTelegram.error,
  ...ru.accounts.profile.code,
  ...ru.accounts.channel.code,
  ...ru.accounts.addStory.code,
};

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

test('a status that is not a failure stays silent, unlike an unmapped one', () => {
  // A warming cycle that ended well writes `status: "ok"` — the raw fallback would hand
  // it a "· ok" tail on a healthy row, which is noise, not an explanation.
  expect(eventReason(t, entry({ status: 'ok', reads: 2 }))).toBe('');
  expect(eventReason(t, entry({ status: 'already_participant' }))).toBe('');
});

test('spells out an exception class and a gateway stable code from their own maps', () => {
  // Four maps, one ladder: a Telethon class name, a profile code, a channel code and a
  // story code. The story namespace was missing from the ladder, so `story_image_invalid`
  // — raised by the gateway's Pillow wrapper and logged as the failure row's `error_type`
  // — reached the operator raw while its Russian sentence sat unused.
  expect(eventReason(t, entry({ error_type: 'ChannelPrivateError' }))).toBe(
    'чат закрыт: не пускают или выгнали',
  );
  expect(eventReason(t, entry({ error_type: 'session_dead' }))).toBe(
    'Сессия аккаунта в Telegram недействительна',
  );
  expect(eventReason(t, entry({ error_type: 'chat_admin_required' }))).toBe(
    'Нужны права администратора канала',
  );
  expect(eventReason(t, entry({ error_type: 'story_image_invalid' }))).toBe(
    'Изображение не удалось прочитать',
  );
  expect(eventReason(t, entry({ error_type: 'story_collage_unknown_layout' }))).toBe(
    'Эта раскладка коллажа недоступна для такого числа фото',
  );
});

test('an unmapped value renders raw rather than blank, from EITHER field', () => {
  // Readable back is the floor: a blank would report a failure with no cause at all,
  // which is the defect this module exists to close. `reason` used to be resolved through
  // `logEventReason` alone with an empty default, so the sweep's wrapped cause — the very
  // thing added because 544 rows of "TelegramReadError" could not tell a flood-wait from
  // a lost peer — was dropped while the class name beside it showed raw.
  expect(eventReason(t, entry({ error_type: 'SomeUnmappedRpcError' }))).toBe(
    'SomeUnmappedRpcError',
  );
  expect(
    eventReason(t, entry({ reason: 'FloodWait(120s)', error_type: 'TelegramReadError' })),
  ).toBe('FloodWait(120s) · TelegramReadError');
  // A failed discovery run: a bare exception class in `reason` and no `error_type` at all.
  expect(eventReason(t, entry({ reason: 'TelegramReadError' }))).toBe('TelegramReadError');
});

test('a colon in a value is not read as an i18next namespace', () => {
  // i18next's default `nsSeparator` is ':', so `logEventReason.RPC: ChannelPrivateError`
  // parses as namespace `logEventReason.RPC` — a value that carries a colon could never
  // be looked up, and the gateway builds exactly those (`_read.py`).
  expect(eventReason(t, entry({ reason: 'RPC: ChannelPrivateError' }))).toBe(
    'RPC: ChannelPrivateError',
  );
  expect(eventReason(t, entry({ reason: 'unavailable: TelegramClientPoolError' }))).toBe(
    'unavailable: TelegramClientPoolError',
  );
});

test('the short log wording wins over the toast wording, from either field', () => {
  // These five live in both `logEventReason` and `accounts.profile.code`. Which one the
  // operator saw used to depend on which `extra` field the gateway happened to fill.
  const shared = {
    failed: 'Telegram отклонил',
    flood_wait: 'Telegram просит подождать',
    slow_mode_wait: 'медленный режим',
    premium_wait: 'нужен Premium',
    peer_flood: 'ограничение по спаму',
  };
  for (const [code, expected] of Object.entries(shared)) {
    expect(eventReason(t, entry({ reason: code }))).toBe(expected);
    expect(eventReason(t, entry({ error_type: code }))).toBe(expected);
  }
});

test('the toast tail is cut: a log row is history, not a prompt', () => {
  // "what happened — what to do" is toast copy; there is no form on screen and nothing
  // left to retry when the operator reads a row from Tuesday.
  expect(eventReason(t, entry({ error_type: 'validation_error' }))).toBe('Запрос отклонён');
  expect(eventReason(t, entry({ error_type: 'profile_photo_stale_reference' }))).toBe(
    'Фото изменилось на Telegram',
  );
  // The one string whose head was the CONSEQUENCE and whose tail was the failure; reworded
  // so a failure row leads with the failure and still names the private channel in the toast.
  expect(eventReason(t, entry({ error_type: 'channel_username_assign_failed' }))).toBe(
    'Не удалось присвоить имя пользователя',
  );
});

test('no label the log can reach leaks an uninterpolated placeholder', () => {
  // The log has no `retry_after_seconds` to pass, so a surviving `{{s}}` would render as
  // literal braces. Every timed code keeps its placeholder in the toast tail, which the
  // cut removes — this pins that, and any future `{{…}}` added to a head.
  for (const code of Object.keys(codeLabels)) {
    expect(eventReason(t, entry({ error_type: code }))).not.toContain('{{');
    expect(eventReason(t, entry({ reason: code }))).not.toContain('{{');
  }
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
