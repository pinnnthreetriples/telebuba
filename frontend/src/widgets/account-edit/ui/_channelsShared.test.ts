import { expect, test } from 'vitest';

import '@/shared/i18n';
import { i18n } from '@/shared/i18n';

import { channelErrorText } from './_channelsShared';

const t = i18n.t.bind(i18n);

function envelope(message: string): unknown {
  return { error: { code: 'bad_request', message } };
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
  expect(channelErrorText(envelope('slow_mode_wait'), t, 'fallback')).toBe(
    'В канале включён медленный режим — повторите через {{s}} с',
  );
});

test('an unknown code still shows as-is, and no envelope uses the fallback', () => {
  expect(channelErrorText(envelope('not_a_real_code'), t, 'fallback')).toBe('not_a_real_code');
  expect(channelErrorText({}, t, 'fallback')).toBe('fallback');
});
