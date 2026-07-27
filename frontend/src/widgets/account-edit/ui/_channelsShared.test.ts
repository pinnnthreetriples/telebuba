import { expect, test } from 'vitest';

import '@/shared/i18n';
import { i18n } from '@/shared/i18n';

import { channelErrorText } from './_channelsShared';

const t = i18n.t.bind(i18n);

function envelope(message: string, fields?: Record<string, unknown>): unknown {
  return { error: { code: 'bad_request', message, ...(fields ? { fields } : {}) } };
}

test('a channel-specific code resolves from the channel table', () => {
  expect(channelErrorText(envelope('channel_username_occupied'), t, 'fallback')).not.toBe(
    'channel_username_occupied',
  );
});

test('an account-wide rate-limit code falls back to the profile table', () => {
  // A slow-mode channel post is the real path here. Without the fallback the
  // operator read the raw `slow_mode_wait` inline, and the alternative — copying
  // the whole rate-limit family into a second namespace — is two sets of strings
  // that drift.
  expect(
    channelErrorText(envelope('slow_mode_wait', { retry_after_seconds: '42' }), t, 'fallback'),
  ).toBe('В канале включён медленный режим — повторите через 42 с');
});

test('the rate-limit duration comes from the envelope fields, and is "?" without one', () => {
  // The {{s}} slot was never given a value, so the one number the backend goes
  // out of its way to carry rendered as a literal placeholder on every channel
  // surface — including channel READ failures, which now carry it too.
  expect(channelErrorText(envelope('flood_wait', { retry_after_seconds: '300' }), t, 'x')).toBe(
    'Telegram ограничил действия — повторите через 300 с',
  );
  expect(channelErrorText(envelope('flood_wait'), t, 'x')).toBe(
    'Telegram ограничил действия — повторите через ? с',
  );
});

test('an unknown code still shows as-is, and no envelope uses the fallback', () => {
  expect(channelErrorText(envelope('not_a_real_code'), t, 'fallback')).toBe('not_a_real_code');
  expect(channelErrorText({}, t, 'fallback')).toBe('fallback');
});
