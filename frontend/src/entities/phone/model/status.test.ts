import { describe, expect, it } from 'vitest';

import { androidVersion, phoneActions } from './status';

describe('phoneActions', () => {
  it('starts only a sleeping phone and stops only an idle or hand-held one', () => {
    expect(phoneActions('asleep')).toEqual({
      start: true,
      stop: false,
      control: false,
      watch: false,
    });
    expect(phoneActions('free')).toEqual({ start: false, stop: true, control: true, watch: false });
    expect(phoneActions('manual')).toMatchObject({ stop: true, control: true });
  });

  it('only watches a warming phone and leaves a booting one alone', () => {
    expect(phoneActions('warming')).toEqual({
      start: false,
      stop: false,
      control: false,
      watch: true,
    });
    expect(phoneActions('starting')).toEqual({
      start: false,
      stop: false,
      control: false,
      watch: false,
    });
  });
});

describe('androidVersion', () => {
  it('names the Android release behind an SDK level', () => {
    expect(androidVersion('SDK 34')).toBe('Android 14');
    expect(androidVersion('SDK 33')).toBe('Android 13');
  });

  it('keeps anything it does not know as it is', () => {
    expect(androidVersion('SDK 99')).toBe('SDK 99');
    expect(androidVersion('Android 14')).toBe('Android 14');
  });
});
