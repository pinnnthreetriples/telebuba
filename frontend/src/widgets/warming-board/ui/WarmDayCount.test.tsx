import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import { i18n } from '@/shared/i18n';

import type { WarmingAccountState } from '@/shared/api';

import { WarmDayCount } from './WarmDayCount';
import { WarmingBoard } from './WarmingBoard';

test('shows worked days against the target and the idle days that did not count', () => {
  render(<WarmDayCount days={5} target={7} idle={2} />);
  expect(screen.getByText('5 / 7 дней')).toBeInTheDocument();
  expect(screen.getByText('(2 дня простоя не засчитаны)')).toBeInTheDocument();
});

test('says nothing about idle days when every day counted', () => {
  render(<WarmDayCount days={5} target={7} idle={0} />);
  expect(screen.getByText('5 / 7 дней')).toBeInTheDocument();
  expect(screen.queryByText(/простоя/)).not.toBeInTheDocument();
});

test('pluralises the idle note in English', async () => {
  await i18n.changeLanguage('en');
  try {
    const { rerender } = render(<WarmDayCount days={1} target={7} idle={1} />);
    expect(screen.getByText('(1 idle day not counted)')).toBeInTheDocument();
    rerender(<WarmDayCount days={1} target={7} idle={3} />);
    expect(screen.getByText('(3 idle days not counted)')).toBeInTheDocument();
  } finally {
    await i18n.changeLanguage('ru');
  }
});

test('the card is not complete on calendar days alone', () => {
  // Seven calendar days in, five of them worked: the card keeps warming.
  const card: WarmingAccountState = {
    account_id: '79051184490',
    label: '79051184490',
    state: 'sleeping',
    health: 'ok',
    cycles_completed: 9,
    warming_days: 7,
    warmed_days: 5,
    idle_days: 2,
    target_days: 7,
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <WarmingBoard warming={[card]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={new Set()} />
    </QueryClientProvider>,
  );
  expect(screen.getByText('5 / 7 дней')).toBeInTheDocument();
  expect(screen.getByText('(2 дня простоя не засчитаны)')).toBeInTheDocument();
  expect(screen.queryByText('Отправить в прогретые')).not.toBeInTheDocument();
  expect(screen.getByText('Стоп')).toBeInTheDocument();
});
