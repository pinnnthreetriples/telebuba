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

test('renders the design cards and animates the save', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });
  // the limit cards and the described toggles from the reference are present
  expect(screen.getByText('Лимиты прогрева')).toBeInTheDocument();
  expect(screen.getByText('Лимиты нейрокомментинга')).toBeInTheDocument();

  await userEvent.click(screen.getByLabelText('Автозапуск процессов'));
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(() => {
    const saved = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return request.url.endsWith('/warming/settings') && request.method === 'PUT';
    });
    expect(saved).toBe(true);
  });
  // save swaps to the animated "Сохранено" confirmation
  expect(await screen.findByText('Сохранено')).toBeInTheDocument();
});

test('edits the key and limits, toggles a flag, then cancel resets', async () => {
  routeSettings();
  renderWithClient(<SettingsPage />);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeInTheDocument();
  });

  await userEvent.type(screen.getByPlaceholderText(/Ключ задан/), 'secret');
  await userEvent.click(screen.getByRole('button', { name: 'Gemini API key' }));

  const sub = screen.getByLabelText('Подписок в день');
  await userEvent.clear(sub);
  await userEvent.type(sub, '20');
  expect(sub).toHaveValue('20');

  const cpd = screen.getByLabelText('Комментариев в день на аккаунт');
  await userEvent.clear(cpd);
  await userEvent.type(cpd, '30');
  expect(cpd).toHaveValue('30');

  // exercise the remaining limit handlers (number fields + range от/до inputs)
  for (const label of [
    'Чтений постов в день',
    'Реакций в день',
    'Параллельных аккаунтов',
    'Мин. trust-score для работы',
  ]) {
    await userEvent.type(screen.getByLabelText(label), '5');
  }
  for (const range of [...screen.getAllByLabelText('от'), ...screen.getAllByLabelText('до')]) {
    await userEvent.type(range, '9');
  }

  await userEvent.click(screen.getByLabelText('Анти-детект профили'));
  await userEvent.click(screen.getByText('Отмена'));
  expect(screen.getByLabelText('Подписок в день')).toHaveValue('15');
});
