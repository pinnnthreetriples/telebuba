import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { SettingsPage } from './SettingsPage';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

const SETTINGS = {
  inter_account_chat: false,
  reactions_enabled: true,
  join_enabled: true,
  enforce_readiness: true,
  has_gemini_key: true,
  gemini_model: 'gemini-2.5-flash',
  gemini_max_retries: 2,
  gemini_min_interval_seconds: 1.5,
  updated_at: 'now',
};

const NEURO_SETTINGS = {
  max_comments_per_hour: 10,
  max_comments_per_channel_per_day: 3,
  reply_delay_min_seconds: 3,
  reply_delay_max_seconds: 10,
  min_trust_score: 45,
  updated_at: 'now',
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeSettings() {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/neurocomment/settings') {
      return Promise.resolve(jsonResponse(NEURO_SETTINGS));
    }
    return Promise.resolve(jsonResponse(SETTINGS));
  });
}

async function warmingPutBody(): Promise<Record<string, unknown>> {
  const calls = vi.mocked(fetch).mock.calls.map(([i]) => i as Request);
  const puts = calls.filter((r) => r.url.endsWith('/warming/settings') && r.method === 'PUT');
  const put = puts[puts.length - 1];
  if (!put) throw new Error('no warming PUT');
  return JSON.parse(await put.clone().text());
}

test('saves both warming toggles and neuro limits, then confirms', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });
  expect(screen.getByText('Лимиты прогрева')).toBeInTheDocument();
  expect(screen.getByText('Лимиты нейрокомментинга')).toBeInTheDocument();
  // neuro limits are loaded from the API
  expect(screen.getByLabelText('Мин. trust-score для работы')).toHaveValue('45');

  // a real warming toggle + save fires both the warming and neuro PUTs
  await userEvent.click(screen.getByLabelText('Реакции в прогреве'));
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(() => {
    const calls = vi.mocked(fetch).mock.calls.map(([i]) => i as Request);
    const warmPut = calls.some((r) => r.url.endsWith('/warming/settings') && r.method === 'PUT');
    const neuroPut = calls.some(
      (r) => r.url.endsWith('/neurocomment/settings') && r.method === 'PUT',
    );
    expect(warmPut && neuroPut).toBe(true);
  });
  expect(await screen.findByText('Сохранено')).toBeInTheDocument();
});

test('a failed save shows the error state instead of silently doing nothing', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (request.method === 'PUT' && url.pathname === '/api/v1/warming/settings') {
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'internal', message: 'boom' } }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    }
    if (url.pathname === '/api/v1/neurocomment/settings') {
      return Promise.resolve(jsonResponse(NEURO_SETTINGS));
    }
    return Promise.resolve(jsonResponse(SETTINGS));
  });
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByText('Сохранить'));
  expect(await screen.findByText('Не удалось сохранить')).toBeInTheDocument();
});

test('the warming-limits block is an engine-derived note, not editable fake constants', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });
  // The informational note is present, and no invented constants (15/80/25) are
  // rendered as data in disabled inputs.
  expect(
    screen.getByText(/подбирается движком автоматически/, { exact: false }),
  ).toBeInTheDocument();
  expect(screen.queryByLabelText('Подписок в день')).not.toBeInTheDocument();
  expect(screen.queryByDisplayValue('15')).not.toBeInTheDocument();
  expect(screen.queryByDisplayValue('80')).not.toBeInTheDocument();
});

test('invalid neuro input is blocked with a field error, not silently sent', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  const cpd = screen.getByLabelText('Комментариев в день на канал');
  // Type then clear → a touched, empty (invalid) field. Empty used to be sent as
  // 0 (a real limit of zero); now it is a validation error instead.
  await userEvent.type(cpd, '9');
  await userEvent.clear(cpd);
  // the field-level error surfaces
  expect(await screen.findByText('Введите целое число от 1 до 100')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Сохранить'));
  // no neuro PUT is sent for the invalid form
  await waitFor(() => {
    const neuroPut = vi.mocked(fetch).mock.calls.some(([i]) => {
      const r = i as Request;
      return r.url.endsWith('/neurocomment/settings') && r.method === 'PUT';
    });
    expect(neuroPut).toBe(false);
  });
});

test('cancel resets an edited neuro field back to the loaded value', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  const cpd = screen.getByLabelText('Комментариев в день на канал');
  await userEvent.clear(cpd);
  await userEvent.type(cpd, '7');
  expect(cpd).toHaveValue('7');

  await userEvent.click(screen.getByText('Отмена'));
  expect(screen.getByLabelText('Комментариев в день на канал')).toHaveValue('3');
});

test('the clear-key action sends clear_gemini_key: true', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByText('Очистить ключ'));
  // the placeholder reflects the pending clear
  expect(screen.getByPlaceholderText('Ключ будет удалён при сохранении')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(async () => {
    expect((await warmingPutBody()).clear_gemini_key).toBe(true);
  });
});

test('Gemini tuning fields load, show help hints, and are sent in the warming PUT', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  const retries = screen.getByLabelText('Повторные попытки Gemini');
  const interval = screen.getByLabelText('Пауза между генерациями (сек)');
  // loaded from the settings row
  expect(retries).toHaveValue(2);
  expect(interval).toHaveValue(1.5);
  // each field carries a "?" help hint with a plain-language explanation
  expect(
    screen.getByText(/упереться в лимит запросов в минуту/, { exact: false }),
  ).toBeInTheDocument();

  await userEvent.clear(retries);
  await userEvent.type(retries, '3');
  await userEvent.clear(interval);
  await userEvent.type(interval, '4.5');
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(async () => {
    const body = await warmingPutBody();
    expect(body.gemini_max_retries).toBe(3);
    expect(body.gemini_min_interval_seconds).toBe(4.5);
  });
});

test('an out-of-range Gemini retry value is clamped before the PUT', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  const retries = screen.getByLabelText('Повторные попытки Gemini');
  // Set an over-max value directly (a number input rejects out-of-range typing).
  fireEvent.change(retries, { target: { value: '99' } });
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(async () => {
    expect((await warmingPutBody()).gemini_max_retries).toBe(5); // clamped to max
  });
});

test('by default the save sends clear_gemini_key: false (key preserved)', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(async () => {
    const body = await warmingPutBody();
    expect(body.clear_gemini_key).toBe(false);
    expect(body.gemini_api_key).toBeNull();
  });
});
