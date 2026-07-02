import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { WarmConfigModal } from './WarmConfigModal';

const SETTINGS = {
  inter_account_chat: false,
  reactions_enabled: true,
  join_enabled: true,
  enforce_readiness: true,
  max_daily_actions: 0,
  has_gemini_key: false,
  gemini_model: 'gemini-2.5-flash',
  updated_at: 'now',
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/warming/settings') {
      return Promise.resolve(jsonResponse(SETTINGS));
    }
    return Promise.resolve(jsonResponse({}));
  });
}

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

test('renders the design sections, toggles and scope tabs', async () => {
  routeApi();
  renderWithClient(<WarmConfigModal phone="+79991234567" onClose={vi.fn()} />);

  expect(screen.getByText('Настройки прогрева')).toBeInTheDocument();
  expect(screen.getByText('+79991234567')).toBeInTheDocument();
  // Both sections
  expect(screen.getByText('Поведение')).toBeInTheDocument();
  expect(screen.getByText('Лимиты и безопасность')).toBeInTheDocument();
  // Behaviour + limits toggles
  expect(screen.getByText('Реакции')).toBeInTheDocument();
  expect(screen.getByText('Взаимный чат')).toBeInTheDocument();
  expect(screen.getByText('Проверять готовность')).toBeInTheDocument();
  expect(screen.getByText('Локальное время')).toBeInTheDocument();
  // Scope tabs
  expect(screen.getByText('Только этот')).toBeInTheDocument();
  expect(screen.getByText('Все в прогреве')).toBeInTheDocument();
});

test('the local-time toggle reveals the quiet-hours picker', async () => {
  routeApi();
  renderWithClient(<WarmConfigModal phone="+79991234567" onClose={vi.fn()} />);

  expect(screen.queryByText('Тихие часы (сон)')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('switch', { name: 'Локальное время' }));
  expect(screen.getByText('Тихие часы (сон)')).toBeInTheDocument();
  expect(screen.getByLabelText('С')).toBeInTheDocument();
  expect(screen.getByLabelText('До')).toBeInTheDocument();
});

test('save writes the toggled global warming settings via the real mutation', async () => {
  routeApi();
  const onClose = vi.fn();
  renderWithClient(<WarmConfigModal phone="+79991234567" onClose={onClose} />);

  // Wait for the settings to seed the toggles.
  await waitFor(() => {
    expect(screen.getByText('Взаимный чат')).toBeInTheDocument();
  });
  // Flip "mutual chat" (was false → true).
  await userEvent.click(screen.getByRole('switch', { name: 'Взаимный чат' }));
  await userEvent.click(screen.getByText('Сохранить'));

  let saveCall: [unknown, ...unknown[]] | undefined;
  await waitFor(() => {
    saveCall = vi
      .mocked(fetch)
      .mock.calls.find(
        ([input]) =>
          new URL((input as Request).url).pathname === '/api/v1/warming/settings' &&
          (input as Request).method === 'PUT',
      );
    expect(saveCall).toBeDefined();
  });
  const body = (await (saveCall![0] as Request).clone().json()) as {
    inter_account_chat?: boolean;
    reactions_enabled?: boolean;
    enforce_readiness?: boolean;
  };
  expect(body.inter_account_chat).toBe(true);
  expect(body.reactions_enabled).toBe(true);
  expect(body.enforce_readiness).toBe(true);
  await waitFor(() => {
    expect(onClose).toHaveBeenCalled();
  });
});

test('the per-account scope is disabled (not yet persisted)', async () => {
  routeApi();
  renderWithClient(<WarmConfigModal phone="+79991234567" onClose={vi.fn()} />);

  await userEvent.click(screen.getByText('Только этот'));
  // Save is blocked while the un-persistable per-account scope is selected.
  expect(screen.getByText('Сохранить')).toBeDisabled();
});
