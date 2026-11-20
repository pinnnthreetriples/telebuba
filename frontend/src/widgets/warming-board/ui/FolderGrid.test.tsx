import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState } from '@/shared/api';

import { newFolder } from '../model/folders';

import { FolderGrid } from './FolderGrid';

function account(
  id: string,
  warmedDays: number,
  state: WarmingAccountState['state'] = 'active',
): WarmingAccountState {
  return {
    account_id: id,
    label: id,
    state,
    health: 'ok',
    cycles_completed: 1,
    warmed_days: warmedDays,
    target_days: 14,
  };
}

function renderFolder(accounts: WarmingAccountState[]) {
  const folder = { ...newFolder(14), id: 'f1', name: 'Пачка' };
  render(
    <FolderGrid
      folders={[folder]}
      membersOf={() => accounts}
      unfiled={[]}
      onOpen={vi.fn()}
      onCreate={vi.fn()}
    />,
  );
  return screen.getByRole('button', { name: /Пачка/ });
}

test('a folder whose every account is warmed reads as done', () => {
  const card = renderFolder([account('a', 14), account('b', 15)]);
  expect(card).toHaveClass('border-success-line');
  expect(card).toHaveTextContent('все догреты');
});

test('a folder with an account that needs a look gets the warning frame and the count', () => {
  const card = renderFolder([account('a', 3), account('b', 14, 'error')]);
  expect(card).toHaveClass('border-warning-line');
  expect(card).toHaveTextContent('догрет 1');
});

test('a calm folder stays plain', () => {
  const card = renderFolder([account('a', 3), account('b', 5)]);
  expect(card).toHaveClass('border-line');
});
