import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';
import type { CadencePreview, WarmingAccountState } from '@/shared/api';

import { cadenceHint } from '../lib/cadence';

import { VisitRow } from './WarmingCardRows';

function account(over: Partial<WarmingAccountState>): WarmingAccountState {
  return {
    account_id: 'acc-1',
    label: 'acc-1',
    state: 'active',
    health: 'ok',
    ...over,
  } as WarmingAccountState;
}

test('a running visit shows its kind and how far along its own order it is', () => {
  render(
    <VisitRow
      account={account({
        visit_kind: 'glance',
        cycle_plan: ['set_online', 'join', 'read', 'react'],
        last_action: 'read',
      })}
    />,
  );
  expect(screen.getAllByText('Заход: глянул')[0]).toBeVisible();
  const bar = screen.getByRole('progressbar');
  expect(bar).toHaveAttribute('aria-valuenow', '3');
  expect(bar).toHaveAttribute('aria-valuemax', '4');
});

test('no visit row between cycles', () => {
  const { container } = render(
    <VisitRow
      account={account({ state: 'sleeping', visit_kind: 'normal', cycle_plan: ['set_online'] })}
    />,
  );
  expect(container).toBeEmptyDOMElement();
});

test('the persona hint quotes the first and the last phase', () => {
  const rows: CadencePreview[] = [
    { persona: 'normal', phase: 'intro', visits_min: 5, visits_max: 6, actions_per_visit: 3 },
    { persona: 'normal', phase: 'warmed', visits_min: 10, visits_max: 16, actions_per_visit: 7 },
    { persona: 'calm', phase: 'intro', visits_min: 4, visits_max: 4, actions_per_visit: 4 },
  ];
  expect(cadenceHint(rows, 'normal')).toEqual({ first: '5–6', last: '10–16' });
  expect(cadenceHint(rows, 'calm')).toBeNull();
  expect(cadenceHint(undefined, 'normal')).toBeNull();
});
