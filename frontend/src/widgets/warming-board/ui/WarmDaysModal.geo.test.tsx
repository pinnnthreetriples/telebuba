import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { WarmDaysModal } from './WarmDaysModal';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  // The delay field previews the start; any answer will do here.
  vi.mocked(fetch).mockResolvedValue(
    new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
});

test('a single start warns about the geo pair and needs the tick to start', async () => {
  const onConfirm = vi.fn();
  renderWithClient(
    <WarmDaysModal
      accountId="a1"
      phone="+525512345678"
      geo={{ phoneCountry: 'MX', proxyCountry: 'US' }}
      onClose={vi.fn()}
      onConfirm={onConfirm}
    />,
  );

  const region = new Intl.DisplayNames(['ru'], { type: 'region' });
  expect(
    screen.getByText(
      `Прокси: ${String(region.of('US'))}, номер: ${String(region.of('MX'))}. Telegram может счесть вход подозрительным`,
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Сменить прокси' })).toHaveAttribute('href', '/');
  const start = screen.getByRole('button', { name: 'Запустить прогрев' });
  expect(start).toBeDisabled();

  await userEvent.click(screen.getByLabelText('Запустить всё равно'));
  expect(start).toBeEnabled();
  await userEvent.click(start);

  expect(onConfirm).toHaveBeenCalledWith(
    expect.objectContaining({ acceptGeo: true, onlyGeoMatching: false }),
  );
});

test('the pool asks once: start all, only the matching ones, or nothing', async () => {
  const onConfirm = vi.fn();
  const onClose = vi.fn();
  const { unmount } = renderWithClient(
    <WarmDaysModal
      poolAccountIds={['a', 'b', 'c']}
      poolGeoMismatch={2}
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );

  expect(screen.getByText('У 2 из 3 гео не совпадает')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Запустить пул' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Запустить все' }));
  expect(onConfirm).toHaveBeenLastCalledWith(
    expect.objectContaining({ acceptGeo: true, onlyGeoMatching: false }),
  );
  unmount();

  renderWithClient(
    <WarmDaysModal
      poolAccountIds={['a', 'b', 'c']}
      poolGeoMismatch={2}
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: 'Только совпадающие' }));
  expect(onConfirm).toHaveBeenLastCalledWith(
    expect.objectContaining({ acceptGeo: false, onlyGeoMatching: true }),
  );
});

test('a pool where nothing matches cannot start only the matching ones', () => {
  renderWithClient(
    <WarmDaysModal
      poolAccountIds={['a', 'b']}
      poolGeoMismatch={2}
      onClose={vi.fn()}
      onConfirm={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Только совпадающие' })).toBeDisabled();
});

test('«Вернуть в прогрев» is the start dialog itself, naming the wave it opens', async () => {
  const onConfirm = vi.fn();
  renderWithClient(
    <WarmDaysModal
      accountId="a1"
      phone="+79991234567"
      nextWave={2}
      defaultDays={14}
      onClose={vi.fn()}
      onConfirm={onConfirm}
    />,
  );

  expect(screen.getByRole('dialog', { name: 'Прогрев аккаунта' })).toBeInTheDocument();
  expect(screen.getByText('Волна 2: прогрев начнётся с нуля')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Запустить прогрев' }));
  // The default length, not one past worked days: the new wave counts from zero.
  expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ days: 14, acceptGeo: false }));
});
