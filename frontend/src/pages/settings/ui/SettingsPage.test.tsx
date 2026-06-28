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

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeSettings() {
  vi.mocked(fetch).mockImplementation(() => Promise.resolve(jsonResponse(SETTINGS)));
}

test('loads the settings and saves a change', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByLabelText('Реакции'));
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(() => {
    const saved = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return request.url.endsWith('/warming/settings') && request.method === 'PUT';
    });
    expect(saved).toBe(true);
  });
});

test('shows a validation error when the Gemini model is cleared', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByLabelText('Модель Gemini')).toBeInTheDocument();
  });
  await userEvent.clear(screen.getByLabelText('Модель Gemini'));
  await waitFor(() => {
    expect(screen.getByText('Обязательное поле')).toBeInTheDocument();
  });
});
