import { describe, expect, it } from 'vitest';

import { phoneActions, phoneBrand } from './status';

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

describe('phoneBrand', () => {
  it('is the model name first word', () => {
    expect(phoneBrand('Samsung Galaxy S23')).toBe('Samsung');
    expect(phoneBrand('  Pixel 7')).toBe('Pixel');
  });
});
