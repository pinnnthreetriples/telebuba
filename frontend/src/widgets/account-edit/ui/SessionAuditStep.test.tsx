import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { auditedAudit, auditRoutes } from './AddAccountModal.test-helpers';
import { SessionAuditStep } from './SessionAuditStep';

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

type Setup = {
  foreign?: Record<string, number>;
  // accounts whose check answers this status instead of alive
  checkStatus?: Record<string, string>;
  // what close-foreign leaves standing, per account
  leftAfterClose?: Record<string, number>;
};

// Every request the step makes, in order, as "METHOD path".
function route(setup: Setup = {}): string[] {
  const seen: string[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    seen.push(`${request.method} ${pathname}`);
    if (pathname === '/api/v1/accounts/check') {
      const { account_id: id } = (await request.clone().json()) as { account_id: string };
      return json({
        account_id: id,
        status: setup.checkStatus?.[id] ?? 'alive',
        created_at: 'n',
        updated_at: 'n',
      });
    }
    const closing = /^\/api\/v1\/accounts\/([^/]+)\/sessions\/close-foreign$/.exec(pathname);
    if (closing?.[1]) {
      const id = closing[1];
      const left = setup.leftAfterClose?.[id] ?? 0;
      return json({
        account_id: id,
        outcomes: [
          left === 0
            ? { hash: '100', status: 'closed' }
            : { hash: '100', status: 'too_new', retry_after_seconds: 7200 },
        ],
        closed_count: left === 0 ? 1 : 0,
        audit: auditedAudit(id, left),
      });
    }
    const audited = auditRoutes(request, ['a', 'b'], setup.foreign);
    if (audited) return audited;
    return json({});
  });
  return seen;
}

function renderStep(props: { autoClose?: boolean } = {}) {
  const onDone = vi.fn();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SessionAuditStep
        accountIds={['a', 'b']}
        sources={{ a: 'a.session', b: 'b.session' }}
        autoClose={props.autoClose ?? false}
        onDone={onDone}
        onImported={vi.fn()}
      />
    </QueryClientProvider>,
  );
  return { onDone };
}

const next = () => screen.getByRole('button', { name: 'Далее' });

test('each account is checked before its sessions are read, and a clean batch goes on whole', async () => {
  const seen = route();
  const { onDone } = renderStep();

  await waitFor(() => {
    expect(next()).toBeEnabled();
  });
  await userEvent.click(next());

  expect(onDone).toHaveBeenCalledWith(['a', 'b']);
  // The fleet read (proxies), then per account the check and only then its sessions.
  expect(seen).toEqual([
    'GET /api/v1/accounts',
    'POST /api/v1/accounts/check',
    'GET /api/v1/accounts/a/sessions',
    'POST /api/v1/accounts/check',
    'GET /api/v1/accounts/b/sessions',
  ]);
});

test('an account with foreign sessions stops at a decision while the rest go on', async () => {
  route({ foreign: { b: 2 } });
  const { onDone } = renderStep();

  expect(await screen.findByText(/чужих сессий: 2 — закройте их или продолжайте/)).toBeVisible();
  expect(screen.getByText(/Аккаунтов, задержанных до вашего решения: 1/)).toBeVisible();
  await userEvent.click(next());

  // `b` is held back from the cloud password and the passkey; `a` is not.
  expect(onDone).toHaveBeenCalledWith(['a']);
});

test('closing the foreign sessions lets the held account through', async () => {
  route({ foreign: { b: 1 } });
  const { onDone } = renderStep();

  await userEvent.click(await screen.findByRole('button', { name: 'Закрыть чужие сессии' }));

  expect(await screen.findAllByText('чужие сессии закрыты')).toHaveLength(1);
  await userEvent.click(next());
  expect(onDone).toHaveBeenCalledWith(['a', 'b']);
});

test('a close Telegram refuses leaves the account held, with the reason', async () => {
  route({ foreign: { b: 1 }, leftAfterClose: { b: 1 } });
  const { onDone } = renderStep();

  await userEvent.click(await screen.findByRole('button', { name: 'Закрыть чужие сессии' }));

  expect(await screen.findByText(/повторите примерно через 2 ч/)).toBeVisible();
  await userEvent.click(next());
  expect(onDone).toHaveBeenCalledWith(['a']);
});

test('continuing anyway is the operator accepting the foreign sessions', async () => {
  route({ foreign: { b: 1 } });
  const { onDone } = renderStep();

  await userEvent.click(await screen.findByRole('button', { name: 'Всё равно продолжить' }));
  await userEvent.click(next());

  expect(onDone).toHaveBeenCalledWith(['a', 'b']);
});

test('with the import option on, foreign sessions are closed without asking', async () => {
  const seen = route({ foreign: { b: 2 } });
  const { onDone } = renderStep({ autoClose: true });

  await waitFor(() => {
    expect(next()).toBeEnabled();
  });
  expect(seen).toContain('POST /api/v1/accounts/b/sessions/close-foreign');
  expect(seen).not.toContain('POST /api/v1/accounts/a/sessions/close-foreign');
  expect(screen.queryByRole('button', { name: 'Закрыть чужие сессии' })).toBeNull();
  await userEvent.click(next());
  expect(onDone).toHaveBeenCalledWith(['a', 'b']);
});

test('without the option nothing is closed unasked', async () => {
  const seen = route({ foreign: { b: 2 } });
  renderStep();

  await screen.findByText(/чужих сессий: 2/);
  expect(seen.filter((line) => line.includes('close-foreign'))).toEqual([]);
});

test('an account that is not alive is held with its status and can be retried', async () => {
  route({ checkStatus: { a: 'unauthorized' } });
  const { onDone } = renderStep();

  expect(await screen.findByText(/не удалось проверить — /)).toBeVisible();
  route();
  await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  await waitFor(() => {
    expect(screen.getAllByText('чужих сессий нет')).toHaveLength(2);
  });
  await userEvent.click(next());

  expect(onDone).toHaveBeenCalledWith(['a', 'b']);
});

test('an account without a proxy is never contacted', async () => {
  const seen: string[] = [];
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    seen.push(`${request.method} ${pathname}`);
    if (pathname === '/api/v1/accounts') {
      return Promise.resolve(
        json({
          items: [
            { account_id: 'a', status: 'new', created_at: 'n', updated_at: 'n' },
            { account_id: 'b', status: 'new', created_at: 'n', updated_at: 'n' },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(json({}));
  });
  const { onDone } = renderStep();

  await waitFor(() => {
    expect(next()).toBeEnabled();
  });
  expect(seen).toEqual(['GET /api/v1/accounts']);
  await userEvent.click(next());
  // They go on as before: the later steps refuse them on their own.
  expect(onDone).toHaveBeenCalledWith(['a', 'b']);
});
