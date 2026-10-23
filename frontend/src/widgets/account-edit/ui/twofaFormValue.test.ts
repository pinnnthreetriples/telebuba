import { expect, test } from 'vitest';

import { TWOFA_HINT_MAX_LENGTH, twofaFormSchema, type TwofaFormValue } from './twofaFormValue';

// FormField shows the first issue's message as an i18n key; these pin the keys and the
// field each lands on, which a zod upgrade could otherwise shift unseen.
const twofaIssues = (value: TwofaFormValue) =>
  twofaFormSchema
    .safeParse(value)
    .error?.issues.map((issue) => [issue.path.join('.'), issue.message]) ?? [];

test.each<[TwofaFormValue, string[][]]>([
  [{ mode: 'generate', password: '', hint: '' }, []],
  [{ mode: 'custom', password: 'longenough', hint: 'my pet' }, []],
  [
    { mode: 'custom', password: '  short  ', hint: '' },
    [['password', 'accounts.edit.twofaErrShort']],
  ],
  [
    { mode: 'custom', password: 'longenough', hint: 'it is LongEnough' },
    [['hint', 'accounts.edit.twofaErrHintLeaks']],
  ],
  [
    { mode: 'custom', password: 'short', hint: 'short' },
    [
      ['password', 'accounts.edit.twofaErrShort'],
      ['hint', 'accounts.edit.twofaErrHintLeaks'],
    ],
  ],
])('2FA %j yields %j', (value, issues) => {
  expect(twofaIssues(value)).toEqual(issues);
});

test('a hint past the limit is rejected on the hint field', () => {
  const hint = 'x'.repeat(TWOFA_HINT_MAX_LENGTH + 1);
  const result = twofaFormSchema.safeParse({ mode: 'generate', password: '', hint });
  expect(result.error?.issues.map((issue) => issue.path)).toEqual([['hint']]);
});
