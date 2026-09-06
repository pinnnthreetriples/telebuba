import { expect, test } from 'vitest';

import type { DialogueFeedMessage } from '@/shared/api';

import { groupIntoPairs } from './pairs';

function message(overrides: Partial<DialogueFeedMessage> = {}): DialogueFeedMessage {
  return {
    from_account: 'a1',
    from_label: '+79051184490',
    to_account: 'a2',
    to_label: '+79161234567',
    text: 'Привет!',
    created_at: '2026-07-01T14:00:00Z',
    ...overrides,
  };
}

// The regression this grouping exists to prevent: keyed by direction, one
// exchange showed up as two rows of the list.
test('both directions between two accounts are ONE pair', () => {
  const pairs = groupIntoPairs([
    message({ from_account: 'a2', to_account: 'a1', text: 'ответ' }),
    message({ from_account: 'a1', to_account: 'a2', text: 'вопрос' }),
  ]);
  expect(pairs).toHaveLength(1);
  expect(pairs[0]?.messages.map((m) => m.text)).toEqual(['вопрос', 'ответ']);
});

test('different account pairs stay separate rows, freshest pair first', () => {
  const pairs = groupIntoPairs([
    message({ from_account: 'b1', to_account: 'b2', created_at: '2026-07-01T15:00:00Z' }),
    message({ from_account: 'a1', to_account: 'a2', created_at: '2026-07-01T14:00:00Z' }),
  ]);
  expect(pairs.map((pair) => pair.key)).toEqual(['b1|b2', 'a1|a2']);
});

// The side a name renders on has to survive the sliding 30-message window: the
// page's oldest line in a pair drops off as new ones arrive, so the sides cannot
// be derived from "who spoke first here".
test('the left-hand side is the same account whichever direction arrived last', () => {
  const forward = groupIntoPairs([message({ from_account: 'a1', to_account: 'a2' })]);
  const backward = groupIntoPairs([message({ from_account: 'a2', to_account: 'a1' })]);
  expect(forward[0]?.leftAccount).toBe(backward[0]?.leftAccount);
});

// Both sides keep their own fallback: the API resolves a label per side (name →
// phone → id) and only the named side may show a name.
test('names each side from the newest line, falling back to its label', () => {
  const pairs = groupIntoPairs([
    message({
      from_label: '527717224137',
      from_first_name: 'Polina',
      to_label: 'ghost-account',
      to_first_name: null,
      to_last_name: null,
    }),
  ]);
  expect([pairs[0]?.leftName, pairs[0]?.rightName]).toEqual(['Polina', 'ghost-account']);
});
