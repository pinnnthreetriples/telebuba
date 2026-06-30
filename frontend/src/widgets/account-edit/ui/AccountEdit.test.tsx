import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
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
  proxy_id: 'p1',
  proxy_country_code: 'nl',
  last_checked_at: '2026-06-28',
  trust_score: 82,
  trust_band: 'good',
  spam_status: 'limited',
  spam_detail: 'до 2026-07-01',
  device_model: 'Pixel 7',
  device_system_version: 'Android 14',
  device_lang: 'ru-RU',
  created_at: 'now',
  updated_at: 'now',
};

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

test('renders the hero and every section header', () => {
  renderWithClient(<AccountEdit account={ACCOUNT} onBack={vi.fn()} />);
  expect(screen.getByText('+79051184490')).toBeInTheDocument();
  // trust comes from the backend-computed score
  expect(screen.getByText('82/100')).toBeInTheDocument();
  for (const title of ['Сессия', 'Прокси', 'Device fingerprint', 'Спам/бан-сигналы', 'Действия']) {
    expect(screen.getByText(title)).toBeInTheDocument();
  }
  // the locked device fingerprint shows the real fingerprint fields
  expect(screen.getByDisplayValue('Pixel 7')).toBeInTheDocument();
  expect(screen.getByDisplayValue('Android 14')).toBeInTheDocument();
  // the real spam verdict surfaces in the signals section
  expect(screen.getByText('Ограничен')).toBeInTheDocument();
  expect(screen.getByText('до 2026-07-01')).toBeInTheDocument();
});

test('section toggles, import tabs and proxy mode drive the handlers', async () => {
  const onBack = vi.fn();
  renderWithClient(<AccountEdit account={ACCOUNT} onBack={onBack} />);

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

  await userEvent.click(screen.getByText(/Назад к списку/));
  expect(onBack).toHaveBeenCalled();
});

test('login-by-code requests a code then confirms sign-in', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/acc-1/request-code') {
      return Promise.resolve(jsonResponse({ account_id: 'acc-1', phone: '+79051184490' }));
    }
    if (pathname === '/api/v1/accounts/acc-1/submit-code') {
      return Promise.resolve(
        jsonResponse({ account_id: 'acc-1', status: 'alive', created_at: 'now', updated_at: 'now' }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  renderWithClient(<AccountEdit account={ACCOUNT} onBack={vi.fn()} />);
  await userEvent.click(screen.getByText('Сессия'));
  await userEvent.click(screen.getByText('Отправить код'));
  await waitFor(() => {
    expect(screen.getByText(/Код отправлен/)).toBeInTheDocument();
  });

  await userEvent.type(screen.getByPlaceholderText('1 2 3 4 5'), '12345');
  await userEvent.click(screen.getByText('Подтвердить вход'));
  await waitFor(() => {
    const submitted = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/submit-code'));
    expect(submitted).toBe(true);
  });
});

test('the @SpamBot check fires the real spam-check endpoint', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/accounts/acc-1/spam-check') {
      return Promise.resolve(
        jsonResponse({ account_id: 'acc-1', status: 'clean', checked_at: 'now' }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  renderWithClient(<AccountEdit account={ACCOUNT} onBack={vi.fn()} />);
  await userEvent.click(screen.getByText('Спам/бан-сигналы'));
  // both the proxy form and the signals header carry a «Проверить»; the signals
  // one is rendered last (proxy section comes first in the layout).
  const checks = screen.getAllByText('Проверить');
  await userEvent.click(checks[checks.length - 1]!);

  await waitFor(() => {
    const probed = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/spam-check'));
    expect(probed).toBe(true);
  });
});
