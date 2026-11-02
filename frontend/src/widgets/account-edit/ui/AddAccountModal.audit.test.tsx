import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AddAccountModal } from './AddAccountModal';
import { auditedAudit, auditRoutes } from './AddAccountModal.test-helpers';

// The wizard's order once a proxy is assigned: check each account, audit its sessions, and
// only then the cloud password. An account somebody else also holds is held out of the
// later steps while the rest go on.

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const POOL_PROXY = {
  id: 'pool-1',
  proxy_type: 'socks5',
  host: 'nl-1.proxyhub.net',
  port: 1080,
  has_password: false,
  status: 'tcp_working',
  country_code: 'nl',
  created_at: 'now',
  updated_at: 'now',
  used: 0,
  capacity: 3,
  free: 3,
};

// Every request, in order, as "METHOD path". `b` has two foreign sessions.
function route(): string[] {
  const seen: string[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    seen.push(`${request.method} ${pathname}`);
    const audited = auditRoutes(request, ['a', 'b'], { b: 2 });
    if (audited) return audited;
    if (pathname === '/api/v1/proxies' && request.method === 'GET') {
      return json({ proxies: [POOL_PROXY] });
    }
    if (pathname.endsWith('/assign')) return json(POOL_PROXY);
    if (pathname === '/api/v1/accounts/import-session') {
      const file = (await request.formData()).get('file') as File;
      return json({
        account_id: file.name.replace('.session', ''),
        status: 'new',
        created_at: 'n',
        updated_at: 'n',
      });
    }
    const closing = /^\/api\/v1\/accounts\/([^/]+)\/sessions\/close-foreign$/.exec(pathname);
    if (closing?.[1]) {
      return json({
        account_id: closing[1],
        outcomes: [{ hash: '100', status: 'closed' }],
        closed_count: 1,
        audit: auditedAudit(closing[1], 0),
      });
    }
    return json({});
  });
  return seen;
}

async function reachAuditStep(): Promise<void> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByText('Файл .session'));
}

async function importTwoAndAssignAProxy(): Promise<void> {
  fireEvent.change(document.body.querySelector('input[type="file"]') as HTMLInputElement, {
    target: {
      files: ['a.session', 'b.session'].map(
        (name) => new File(['x'], name, { type: 'application/octet-stream' }),
      ),
    },
  });
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Далее'));
  await userEvent.click(screen.getByText('Выбрать из пула'));
  await userEvent.click(await screen.findByText('Распределить автоматически'));
  await waitFor(() => {
    expect(screen.getByText('Назначено: 2 из 2')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Готово'));
}

test('the audit comes before the cloud password, and a held account is left out of it', async () => {
  const seen = route();
  await reachAuditStep();
  await importTwoAndAssignAProxy();

  expect(await screen.findByText(/чужих сессий: 2 — закройте их или продолжайте/)).toBeVisible();
  await waitFor(() => {
    expect(screen.getByText('Далее').closest('button')).toBeEnabled();
  });
  // Nothing about a cloud password has been asked yet.
  expect(seen.filter((line) => line.includes('/2fa'))).toEqual([]);
  expect(screen.queryByText('Шаг 4 · облачный пароль')).toBeNull();

  await userEvent.click(screen.getByText('Далее'));

  expect(await screen.findByText('Шаг 4 · облачный пароль')).toBeVisible();
  // `a` is offered a password; `b` waits for the decision it never got.
  expect(screen.getByText('a.session')).toBeInTheDocument();
  expect(screen.queryByText('b.session')).toBeNull();
  const check = seen.indexOf('POST /api/v1/accounts/check');
  const read = seen.indexOf('GET /api/v1/accounts/a/sessions');
  expect(check).toBeGreaterThan(-1);
  expect(read).toBeGreaterThan(check);
});

test('the import option closes foreign sessions without a decision', async () => {
  const seen = route();
  await reachAuditStep();
  await userEvent.click(
    screen.getByRole('checkbox', { name: /Закрывать чужие сессии автоматически/ }),
  );
  await importTwoAndAssignAProxy();

  await waitFor(() => {
    expect(seen).toContain('POST /api/v1/accounts/b/sessions/close-foreign');
  });
  await waitFor(() => {
    expect(screen.getByText('Далее').closest('button')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Далее'));

  // Both accounts go on: `b` was closed, not held.
  expect(await screen.findByText('Шаг 4 · облачный пароль')).toBeVisible();
  expect(screen.getByText('b.session')).toBeInTheDocument();
});

test('the option is off until the operator turns it on', async () => {
  route();
  await reachAuditStep();

  expect(
    screen.getByRole('checkbox', { name: /Закрывать чужие сессии автоматически/ }),
  ).not.toBeChecked();
});
