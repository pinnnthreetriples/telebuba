import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
        jsonResponse({
          account_id: 'acc-1',
          status: 'alive',
          created_at: 'now',
          updated_at: 'now',
        }),
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

test('proxy: manual creates+assigns, pool select assigns', async () => {
  const proxy = (over: Record<string, unknown> = {}) => ({
    id: 'newp',
    proxy_type: 'socks5',
    host: '1.2.3.4',
    port: 1080,
    has_password: false,
    status: 'tcp_working',
    created_at: 'now',
    updated_at: 'now',
    used: 0,
    capacity: 3,
    free: 3,
    ...over,
  });
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/proxies' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ proxies: [proxy({ id: 'pool-1', host: '9.9.9.9' })] }));
    }
    if (pathname === '/api/v1/proxies') return Promise.resolve(jsonResponse(proxy()));
    if (pathname.endsWith('/assign')) return Promise.resolve(jsonResponse(proxy()));
    if (pathname.endsWith('/check')) return Promise.resolve(jsonResponse(proxy()));
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  renderWithClient(<AccountEdit account={ACCOUNT} onBack={vi.fn()} />);
  await userEvent.click(screen.getByText('Прокси'));

  // fill every manual field (covers each controlled onChange) then create+assign
  await userEvent.type(screen.getByLabelText('Host'), '1.2.3.4');
  await userEvent.type(screen.getByLabelText('Порт'), '1080');
  await userEvent.type(screen.getByLabelText('Логин'), 'u');
  await userEvent.type(screen.getAllByLabelText('Пароль')[0]!, 'p');
  await userEvent.selectOptions(screen.getByLabelText('Тип'), 'https');
  await userEvent.click(screen.getAllByText('Проверить')[0]!);
  await waitFor(() => {
    const created = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return new URL(request.url).pathname === '/api/v1/proxies' && request.method === 'POST';
    });
    expect(created).toBe(true);
  });

  // pool mode: selecting a free proxy assigns it
  await userEvent.click(screen.getByText('Из пула'));
  await waitFor(() => {
    expect(screen.getByRole('option', { name: '9.9.9.9:1080' })).toBeInTheDocument();
  });
  await userEvent.selectOptions(screen.getByRole('combobox'), 'pool-1');
  await waitFor(() => {
    const assigned = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/proxies/pool-1/assign'));
    expect(assigned).toBe(true);
  });
});

test('the import dropzone uploads a .session file then dismisses the card', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/accounts/import-session') {
      return Promise.resolve(
        jsonResponse({ account_id: 'new', status: 'new', created_at: 'n', updated_at: 'n' }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  renderWithClient(<AccountEdit account={ACCOUNT} onBack={vi.fn()} />);
  const input = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, {
    target: { files: [new File(['x'], 'acc.session', { type: 'application/octet-stream' })] },
  });
  await waitFor(() => {
    const imported = vi
      .mocked(fetch)
      .mock.calls.some(([i]) => (i as Request).url.includes('/accounts/import-session'));
    expect(imported).toBe(true);
  });
  await screen.findByText('готово');
  await userEvent.click(screen.getByLabelText('Удалить файл'));
  expect(screen.queryByText('acc.session')).not.toBeInTheDocument();
});

test('a failed tdata import shows the error state', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/accounts/import-tdata') {
      return Promise.reject(new Error('boom'));
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  renderWithClient(<AccountEdit account={ACCOUNT} onBack={vi.fn()} />);
  await userEvent.click(screen.getByText('tdata.zip'));
  const input = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, {
    target: { files: [new File(['x'], 'b.zip', { type: 'application/zip' })] },
  });
  await screen.findByText('ошибка');
});

test('the delete-account action confirms, deletes, and returns to the list', async () => {
  vi.mocked(fetch).mockImplementation(() => Promise.resolve(jsonResponse({})));
  const onBack = vi.fn();
  renderWithClient(<AccountEdit account={ACCOUNT} onBack={onBack} />);
  await userEvent.click(screen.getByRole('button', { name: 'Удалить аккаунт' }));
  await userEvent.click(await screen.findByText('Удалить'));
  await waitFor(() => {
    const deleted = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return request.url.endsWith('/accounts/acc-1') && request.method === 'DELETE';
    });
    expect(deleted).toBe(true);
  });
  await waitFor(() => {
    expect(onBack).toHaveBeenCalled();
  });
});

test('an unauthorized account shows the non-active session state (not a green "active")', () => {
  renderWithClient(
    <AccountEdit account={{ ...ACCOUNT, status: 'unauthorized' }} onBack={vi.fn()} />,
  );
  // The session row now reflects the real state, not a hardcoded "active".
  expect(screen.queryByText('Сессия активна')).not.toBeInTheDocument();
  expect(screen.getByText('Сессия неактивна · нужен повторный вход')).toBeInTheDocument();
});

test('a proxyless account shows the unassigned state and no detach control', () => {
  renderWithClient(<AccountEdit account={{ ...ACCOUNT, proxy_id: undefined }} onBack={vi.fn()} />);
  expect(screen.getByText('Прокси не назначен')).toBeInTheDocument();
  expect(screen.queryByText('Отвязать прокси')).not.toBeInTheDocument();
});

test('a proxy check renders the real returned fields, not a fabricated "12ms"', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname.endsWith('/check')) {
      return Promise.resolve(
        jsonResponse({ status: 'tcp_working', country_code: 'de', exit_ip: '5.6.7.8' }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<AccountEdit account={ACCOUNT} onBack={vi.fn()} />);
  await userEvent.click(screen.getByText('Прокси'));
  await userEvent.click(screen.getByText('Из пула'));
  await userEvent.click(screen.getAllByText('Проверить')[0]!);
  // Real country + exit IP surface; the invented latency is gone.
  await screen.findByText('DE · 5.6.7.8');
  expect(screen.queryByText(/12ms/)).not.toBeInTheDocument();
});

test('the detach-proxy control unassigns the account and refreshes', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/proxies/unassign') {
      return Promise.resolve(jsonResponse({}));
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<AccountEdit account={ACCOUNT} onBack={vi.fn()} />);
  await userEvent.click(screen.getByText('Прокси'));
  await userEvent.click(screen.getByText('Отвязать прокси'));
  await waitFor(() => {
    const unassigned = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return (
        new URL(request.url).pathname === '/api/v1/proxies/unassign' && request.method === 'POST'
      );
    });
    expect(unassigned).toBe(true);
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
