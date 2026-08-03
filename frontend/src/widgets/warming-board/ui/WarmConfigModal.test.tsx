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
  has_gemini_key: false,
  gemini_model: 'gemini-2.5-flash',
  // Operator-set on the settings page. A PUT is a full replacement and these two
  // columns have no keep-semantics on the write path, so whatever this modal omits
  // is reset to the schema defaults (1 and 0.0).
  gemini_max_retries: 4,
  gemini_min_interval_seconds: 2.5,
  updated_at: 'now',
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeApi(settings: Record<string, unknown> = SETTINGS, putStatus = 200) {
  let reads = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/warming/settings') {
      if (request.method === 'PUT' && putStatus !== 200) {
        // With a `message` — an envelope without one falls back to the generic
        // shell.mutationError, which is the very branch that hid whether the alert
        // reports the specific reason at all.
        return Promise.resolve(
          jsonResponse(
            { error: { code: 'internal_error', message: 'settings_row_locked' } },
            putStatus,
          ),
        );
      }
      // Every GET carries a fresh `updated_at`, exactly as the real singleton row
      // does — it is rewritten on every save, and a refetch happens on a
      // reconnect too. A byte-identical row would let React Query's structural
      // sharing keep the object identity, which hides anything keyed on it.
      reads += 1;
      return Promise.resolve(jsonResponse({ ...settings, updated_at: `read-${String(reads)}` }));
    }
    return Promise.resolve(jsonResponse({}));
  });
}

// How many times the modal has READ the settings row.
function settingsReads(): number {
  return vi
    .mocked(fetch)
    .mock.calls.filter(
      ([input]) =>
        new URL((input as Request).url).pathname === '/api/v1/warming/settings' &&
        (input as Request).method !== 'PUT',
    ).length;
}

// The body of the PUT the modal sent.
async function savedBody(): Promise<Record<string, unknown>> {
  let call: [unknown, ...unknown[]] | undefined;
  await waitFor(() => {
    call = vi
      .mocked(fetch)
      .mock.calls.find(
        ([input]) =>
          new URL((input as Request).url).pathname === '/api/v1/warming/settings' &&
          (input as Request).method === 'PUT',
      );
    expect(call).toBeDefined();
  });
  return (await (call![0] as Request).clone().json()) as Record<string, unknown>;
}

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

test('renders the design sections, toggles and scope tabs', async () => {
  routeApi();
  renderWithClient(<WarmConfigModal phone="+79991234567" onClose={vi.fn()} />);

  // The dialog's accessible name, not just the visible heading.
  expect(screen.getByRole('dialog', { name: 'Настройки прогрева' })).toBeInTheDocument();
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

test('saving invalidates the settings and the board, not the whole cache', async () => {
  routeApi();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  render(
    <QueryClientProvider client={queryClient}>
      <WarmConfigModal phone="+79991234567" onClose={vi.fn()} />
    </QueryClientProvider>,
  );

  await userEvent.click(screen.getByRole('switch', { name: 'Взаимный чат' }));
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(invalidate).toHaveBeenCalled();
  });
  // An unfiltered invalidateQueries() also refetched the accounts table, the
  // proxies, the neurocomment campaigns and every open profile snapshot.
  for (const [filters] of invalidate.mock.calls) {
    expect(filters?.queryKey).toBeDefined();
  }
});

test('save leaves the settings-page Gemini fields out of the body entirely', async () => {
  routeApi();
  renderWithClient(<WarmConfigModal phone="+79991234567" onClose={vi.fn()} />);

  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Сохранить'));

  // The write path keeps all three on an omitted field, so absence is what preserves
  // them. Echoing the query's values instead wrote whatever this modal last read —
  // and it never refetches on window focus, so a tab left open overwrote the
  // settings page with a stale row.
  const body = await savedBody();
  expect(body).not.toHaveProperty('gemini_max_retries');
  expect(body).not.toHaveProperty('gemini_min_interval_seconds');
  expect(body).not.toHaveProperty('gemini_model');
});

test('a cold cache saves the stored toggles, not the hardcoded fallbacks', async () => {
  // Reactions off + mutual chat on is the exact inverse of the fallbacks the modal
  // renders before the row arrives, so a state left on those cannot pass by accident.
  routeApi({ ...SETTINGS, reactions_enabled: false, inter_account_chat: true });
  renderWithClient(<WarmConfigModal phone="+79991234567" onClose={vi.fn()} />);

  await waitFor(() => {
    expect(screen.getByRole('switch', { name: 'Реакции' })).toHaveAttribute(
      'aria-checked',
      'false',
    );
  });
  await userEvent.click(screen.getByText('Сохранить'));

  const body = await savedBody();
  expect(body.reactions_enabled).toBe(false);
  expect(body.inter_account_chat).toBe(true);
});

test('a rejected save keeps the dialog open with the operator input', async () => {
  routeApi(SETTINGS, 500);
  const onClose = vi.fn();
  renderWithClient(<WarmConfigModal phone="+79991234567" onClose={onClose} />);

  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeEnabled();
  });
  await userEvent.click(screen.getByRole('switch', { name: 'Взаимный чат' }));
  await userEvent.click(screen.getByText('Сохранить'));

  // onSettled fires on failure too, so closing there threw away the whole form
  // over a failed PUT. The alert reports the envelope's OWN reason, not the generic
  // copy: this modal stays open over the failure, so it is the in-context report.
  await waitFor(() => {
    expect(screen.getByRole('alert')).toHaveTextContent('settings_row_locked');
  });
  // onSettled also invalidates the settings key regardless of outcome, so a GET
  // follows the rejected PUT. Wait for that row to land: re-seeding the toggles
  // from it reverted the flip while the error was still on screen.
  await waitFor(() => {
    expect(settingsReads()).toBeGreaterThan(1);
  });
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole('switch', { name: 'Взаимный чат' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
});

test('the per-account scope is disabled (not yet persisted)', async () => {
  routeApi();
  renderWithClient(<WarmConfigModal phone="+79991234567" onClose={vi.fn()} />);

  await userEvent.click(screen.getByText('Только этот'));
  // Save is blocked while the un-persistable per-account scope is selected.
  expect(screen.getByText('Сохранить')).toBeDisabled();
});
