import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { ProfileModal } from './ProfileModal';

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  first_name: 'Иван',
  username: 'ivan',
  phone: '+79991234567',
  created_at: 'now',
  updated_at: 'now',
};

test('switches to the stories tab and opens the add-story modal above it', async () => {
  render(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  // header shows the account
  expect(screen.getByText('Иван')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Сторис'));
  await userEvent.click(screen.getByText('Добавить'));
  expect(screen.getByText('Новая сторис')).toBeInTheDocument();
});
