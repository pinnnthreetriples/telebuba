import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { BulkMessageJob } from '@/shared/api';

import { AccountsPage } from './AccountsPage';

function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function batch(jobId: string, status: BulkMessageJob['status']): BulkMessageJob {
  return {
    job_id: jobId,
    status,
    total: 1,
    completed: status === 'completed' ? 1 : 0,
    results: [],
  };
}

function routeApi(options: {
  userId: string;
  activeJob?: BulkMessageJob | null;
  jobs?: Record<string, BulkMessageJob>;
}) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const path = new URL(request.url).pathname;
    if (path === '/api/v1/auth/me') {
      return respond({ id: options.userId, username: options.userId });
    }
    if (path === '/api/v1/accounts/stats') {
      return respond({ total: 1, active: 1, idle: 0, needs_code: 0, problem: 0 });
    }
    if (path === '/api/v1/accounts') {
      return respond({
        items: [{ account_id: 'a1', status: 'alive', created_at: 'now', updated_at: 'now' }],
        next_cursor: null,
      });
    }
    if (path === '/api/v1/proxies') return respond({ proxies: [] });
    if (path === '/api/v1/accounts/bulk-messages/active') {
      return respond(options.activeJob ?? null);
    }
    if (path.startsWith('/api/v1/accounts/bulk-messages/')) {
      const jobId = path.split('/').at(-1) ?? '';
      const job = options.jobs?.[jobId];
      return job
        ? respond(job)
        : respond({ error: { code: 'not_found', message: 'bulk message job not found' } }, 404);
    }
    throw new Error(`Unexpected request: ${request.method} ${path}`);
  });
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AccountsPage />
    </QueryClientProvider>,
  );
}

async function openMessages() {
  const open = screen.getByRole('button', { name: 'Массовая отправка' });
  await waitFor(() => {
    expect(open).toBeEnabled();
  });
  await userEvent.click(open);
}

test('opens the bulk message composer when no batch is active', async () => {
  window.sessionStorage.clear();
  try {
    routeApi({ userId: 'operator' });
    renderPage();
    await openMessages();
    expect(screen.getByRole('dialog', { name: 'Массовая отправка сообщений' })).toBeInTheDocument();
    expect(screen.getByLabelText('Получатели')).toBeInTheDocument();
  } finally {
    window.sessionStorage.clear();
  }
});

test('finds a running batch through the server after the page remounts', async () => {
  window.sessionStorage.clear();
  try {
    const activeJob = batch('running-1', 'running');
    routeApi({ userId: 'operator', activeJob, jobs: { 'running-1': activeJob } });
    const first = renderPage();
    await openMessages();
    expect(await screen.findByText('0 из 1 отправок')).toBeInTheDocument();
    first.unmount();

    renderPage();
    await openMessages();
    expect(await screen.findByText('0 из 1 отправок')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Остановить' })).toBeInTheDocument();
  } finally {
    window.sessionStorage.clear();
  }
});

test('restores the last completed batch only for its owner', async () => {
  window.sessionStorage.clear();
  try {
    window.sessionStorage.setItem('telebuba:bulk-message-job:operator', 'finished-1');
    routeApi({
      userId: 'operator',
      jobs: { 'finished-1': batch('finished-1', 'completed') },
    });
    renderPage();
    await openMessages();
    expect(await screen.findByText('1 из 1 отправок')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Готово' })).toBeInTheDocument();
  } finally {
    window.sessionStorage.clear();
  }
});

test('ignores another user’s stored batch and clears the old unscoped key', async () => {
  window.sessionStorage.clear();
  try {
    window.sessionStorage.setItem('telebuba:bulk-message-job:operator-a', 'foreign-1');
    window.sessionStorage.setItem('telebuba:bulk-message-job-id', 'foreign-1');
    routeApi({ userId: 'operator-b' });
    renderPage();
    await openMessages();
    expect(screen.getByLabelText('Получатели')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('telebuba:bulk-message-job-id')).toBeNull();
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([input]) =>
          new URL((input as Request).url).pathname.endsWith('/foreign-1'),
        ),
    ).toBe(false);
  } finally {
    window.sessionStorage.clear();
  }
});

test('clears a stored batch that was lost after a server restart', async () => {
  window.sessionStorage.clear();
  try {
    window.sessionStorage.setItem('telebuba:bulk-message-job:operator', 'missing');
    routeApi({ userId: 'operator' });
    renderPage();
    await openMessages();
    expect(await screen.findByText('Задача больше недоступна')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Новая отправка' }));
    expect(screen.getByLabelText('Получатели')).toBeInTheDocument();
    await waitFor(() => {
      expect(window.sessionStorage.getItem('telebuba:bulk-message-job:operator')).toBeNull();
    });
  } finally {
    window.sessionStorage.clear();
  }
});
