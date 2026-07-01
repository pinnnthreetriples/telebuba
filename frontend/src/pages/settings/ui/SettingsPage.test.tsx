import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
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
  quiet_hours_enabled: false,
  quiet_hours_start: 0,
  quiet_hours_end: 0,
  max_daily_actions: 0,
  has_gemini_key: true,
  gemini_model: 'gemini-2.5-flash',
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

test('quiet hours: enabling reveals the real saved hours, editing them persists on save', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/settings') {
      return Promise.resolve(jsonResponse(NEURO_SETTINGS));
    }
    return Promise.resolve(
      jsonResponse({ ...SETTINGS, quiet_hours_start: 23, quiet_hours_end: 8 }),
    );
  });
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  // disabled by default (per SETTINGS.quiet_hours_enabled) → no hour range shown yet
  expect(screen.queryByDisplayValue('23')).not.toBeInTheDocument();

  await userEvent.click(screen.getByLabelText('Тихие часы'));
  const fromInput = await screen.findByDisplayValue('23');
  const toInput = screen.getByDisplayValue('8');

  await userEvent.clear(fromInput);
  await userEvent.type(fromInput, '22');
  await userEvent.clear(toInput);
  await userEvent.type(toInput, '7');

  await userEvent.click(screen.getByText('Сохранить'));
  let putCall: [unknown, ...unknown[]] | undefined;
  await waitFor(() => {
    putCall = vi
      .mocked(fetch)
      .mock.calls.find(
        ([i]) =>
          (i as Request).url.endsWith('/warming/settings') && (i as Request).method === 'PUT',
      );
    expect(putCall).toBeDefined();
  });
  const body = (await (putCall![0] as Request).clone().json()) as {
    quiet_hours_enabled: boolean;
    quiet_hours_start: number;
    quiet_hours_end: number;
  };
  expect(body).toMatchObject({
    quiet_hours_enabled: true,
    quiet_hours_start: 22,
    quiet_hours_end: 7,
  });
});

test('warming limits are read-only; neuro limits edit and cancel resets', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  // warming limits are auto-managed → shown read-only
  expect(screen.getByLabelText('Подписок в день')).toHaveAttribute('readonly');

  // neuro limits are editable
  const cpd = screen.getByLabelText('Комментариев в день на канал');
  await userEvent.clear(cpd);
  await userEvent.type(cpd, '7');
  expect(cpd).toHaveValue('7');

  await userEvent.click(screen.getByText('Отмена'));
  // cancel resets the neuro field back to the loaded value
  expect(screen.getByLabelText('Комментариев в день на канал')).toHaveValue('3');
});
