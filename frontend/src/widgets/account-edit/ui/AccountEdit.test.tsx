import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { AccountEdit } from './AccountEdit';

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  label: 'Main',
  status: 'alive',
  username: 'mainuser',
  phone: '+79051184490',
  proxy_country_code: 'nl',
  last_checked_at: '2026-06-28',
  created_at: 'now',
  updated_at: 'now',
};

test('renders the hero and every section header', () => {
  render(<AccountEdit account={ACCOUNT} onBack={vi.fn()} />);
  expect(screen.getByText('+79051184490')).toBeInTheDocument();
  expect(screen.getByText('76/100')).toBeInTheDocument();
  for (const title of ['Сессия', 'Прокси', 'Device fingerprint', 'Спам/бан-сигналы', 'Действия']) {
    expect(screen.getByText(title)).toBeInTheDocument();
  }
  // the locked device fingerprint shows the mock profile
  expect(screen.getByDisplayValue('iPhone 13')).toBeInTheDocument();
});

test('section toggles, import tabs and proxy mode drive the handlers', async () => {
  const onBack = vi.fn();
  render(<AccountEdit account={ACCOUNT} onBack={onBack} />);

  // expand accordions — covers both Section header layouts (plain + right-slot)
  await userEvent.click(screen.getByText('Сессия'));
  await userEvent.click(screen.getByText('Спам/бан-сигналы'));

  // import segmented control
  await userEvent.click(screen.getByText('tdata.zip'));
  await userEvent.click(screen.getByText('.session'));

  // proxy: manual → pool → manual
  expect(screen.getByText('Host')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Из пула'));
  expect(screen.getByText('Прокси-пул')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Вручную'));
  expect(screen.getByText('Host')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Назад к списку'));
  expect(onBack).toHaveBeenCalled();
});
