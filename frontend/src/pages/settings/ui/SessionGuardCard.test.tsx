import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { SessionGuardCard } from './SessionGuardCard';

function serve(body: unknown): void {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } }),
    ),
  );
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SessionGuardCard />
    </QueryClientProvider>,
  );
}

test('a working guard says when it last did something', async () => {
  serve({ running: true, last_activity_at: '2026-11-16T10:00:00+00:00', stalled: false });
  renderCard();

  expect(await screen.findByRole('status')).toHaveTextContent('Работает · последнее действие');
});

test('a silent guard is an alert', async () => {
  serve({
    running: true,
    last_activity_at: '2026-11-16T10:00:00+00:00',
    stalled: true,
    reason: 'silent',
  });
  renderCard();

  expect(await screen.findByRole('alert')).toHaveTextContent('Молчит с');
});

test('a guard that stopped says it is restarting', async () => {
  serve({ running: false, last_activity_at: null, stalled: true, reason: 'stopped' });
  renderCard();

  expect(await screen.findByRole('alert')).toHaveTextContent('Остановился и перезапускается');
});

const HEALTHY = {
  running: true,
  last_activity_at: '2026-11-16T10:00:00+00:00',
  stalled: false,
  backups: {
    session_vault_enabled: true,
    mirror_configured: true,
    accounts_total: 8,
    accounts_without_copy: 0,
    last_session_copy_at: '2026-11-16T09:00:00+00:00',
    db_backup_enabled: true,
    last_db_backup_at: '2026-11-16T03:00:00+00:00',
    db_backup_overdue: false,
    passkey_vault: 'ok',
  },
};

function withBackups(patch: Record<string, unknown>) {
  return { ...HEALTHY, backups: { ...HEALTHY.backups, ...patch } };
}

test('healthy copies and vault read as quiet rows', async () => {
  serve(HEALTHY);
  renderCard();

  const block = await screen.findByTestId('session-guard-backups');
  expect(block).toHaveTextContent('Копии и сейф');
  expect(block).toHaveTextContent('Копии ключей аккаунтов включены');
  expect(block).toHaveTextContent('Последняя копия:');
  expect(block).toHaveTextContent('Второе место для копий настроено');
  expect(block).toHaveTextContent('Копия базы:');
  expect(block).toHaveTextContent('Сейф ключей доступа отвечает');
  expect(block).not.toHaveTextContent('без копии');
  expect(block.querySelector('.text-warning-deep, .text-danger-deep')).toBeNull();
});

test('accounts without a copy are counted as a warning', async () => {
  serve(withBackups({ accounts_without_copy: 3 }));
  renderCard();

  expect(await screen.findByText('3 из 8 аккаунтов без копии')).toHaveClass('text-warning-deep');
});

test('switched-off copies are a warning row', async () => {
  serve(withBackups({ session_vault_enabled: false }));
  renderCard();

  expect(await screen.findByText('Копии ключей аккаунтов выключены')).toHaveClass(
    'text-warning-deep',
  );
});

test('an overdue database backup is a warning', async () => {
  serve(withBackups({ db_backup_overdue: true }));
  renderCard();

  expect(await screen.findByText('Копия базы давно не обновлялась')).toHaveClass(
    'text-warning-deep',
  );
});

test('a vault that does not answer is a danger row', async () => {
  serve(withBackups({ passkey_vault: 'unreachable' }));
  renderCard();

  expect(await screen.findByText('Сейф ключей доступа не отвечает')).toHaveClass(
    'text-danger-deep',
  );
});
