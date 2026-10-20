import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { MobileOpenDialog, type MobileOutcome } from './MobileOpenDialog';

// The live screen is its own feature; here it only says what it was asked to show.
vi.mock('@/features/phone-screen', () => ({
  PhoneScreen: ({ phoneId, mode }: { phoneId: string; mode: string }) => (
    <div data-testid="screen">{`${phoneId}:${mode}`}</div>
  ),
  PhoneScreenWaiting: ({ label }: { label: string }) => <div data-testid="waiting">{label}</div>,
}));

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function serveStage(stage: string | null) {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(json({ account_id: 'acc-1', phone_id: 'telebuba-acc-1', stage })),
  );
}

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function dialog(outcome: MobileOutcome) {
  return wrap(
    <MobileOpenDialog accountId="acc-1" title="shangi" outcome={outcome} onClose={() => {}} />,
  );
}

test('before Android is up the screen waits in its frame', async () => {
  serveStage('pod');
  render(dialog({ phase: 'running' }));

  expect(await screen.findByTestId('waiting')).toHaveTextContent('Телефон запускается…');
  expect(screen.queryByTestId('screen')).not.toBeInTheDocument();
});

test('an earlier open’s ending is not this one’s stage', async () => {
  serveStage('failed');
  render(dialog({ phase: 'running' }));

  await waitFor(() => {
    expect(vi.mocked(fetch)).toHaveBeenCalled();
  });
  expect(screen.getByText('Поднимаю сеть телефона через прокси аккаунта…')).toBeInTheDocument();
});

test('once the app opens the screen is watched, and handed over when the open ends', async () => {
  serveStage('code');
  const { rerender } = render(dialog({ phase: 'running' }));

  expect(await screen.findByTestId('screen')).toHaveTextContent('telebuba-acc-1:view');
  expect(screen.getByText('Жду код входа в основной сессии аккаунта…')).toBeInTheDocument();

  rerender(
    dialog({
      phase: 'settled',
      result: { account_id: 'acc-1', status: 'started', signed_in: true },
    }),
  );

  expect(await screen.findByTestId('screen')).toHaveTextContent('telebuba-acc-1:control');
  expect(
    screen.getByText('Готово: Telegram на телефоне вошёл в аккаунт. Телефоном можно управлять.'),
  ).toBeInTheDocument();
});

test('a failed open shows no screen, only why', async () => {
  serveStage('failed');
  render(dialog({ phase: 'failed', message: 'Docker на сервере недоступен' }));

  expect(await screen.findByText('Телефон не запустился.')).toBeInTheDocument();
  expect(screen.getByText('Причина: Docker на сервере недоступен')).toBeInTheDocument();
  expect(screen.queryByTestId('screen')).not.toBeInTheDocument();
  expect(screen.queryByTestId('waiting')).not.toBeInTheDocument();
});
