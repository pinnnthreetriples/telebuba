import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import '@/shared/i18n';

import type {
  UserParserAccountOption,
  UserParserBase,
  UserParserPreset,
  UserParserRun,
  UserParserUser,
} from '@/shared/api';

import { UserParserModal } from './UserParserModal';

const ACCOUNTS: UserParserAccountOption[] = [
  { account_id: 'acc-n', name: 'Plain', premium: false, busy_reason: null },
  { account_id: 'acc-p', name: 'Prem', premium: true, busy_reason: null },
  { account_id: 'acc-b', name: 'Busy', premium: false, busy_reason: 'account_busy' },
];

function runPayload(overrides: Partial<UserParserRun> = {}): UserParserRun {
  return {
    run_id: 'r1',
    name: 'Комментаторы · 09.10.2026',
    mode: 'comments',
    status: 'running',
    created_at: '2026-10-09T10:00:00Z',
    sources_total: 2,
    sources_done: 1,
    collected_raw: 40,
    kept: 0,
    sources: [
      { source: '@news', status: 'ok', count: 40, finished_at: '2026-10-09T10:01:00Z' },
      { source: '@private', status: 'pending', count: 0 },
    ],
    accounts: [{ account_id: 'acc-p', name: 'Prem', state: 'flooded', reads: 3 }],
    ...overrides,
  };
}

const USERS: UserParserUser[] = [
  {
    user_id: 7,
    first_name: 'Анна',
    username: 'anna',
    is_premium: true,
    last_seen: 'week',
    message_count: 3,
    first_at: '2026-10-01T00:00:00Z',
    last_at: '2026-10-05T00:00:00Z',
    sources: ['@news'],
  },
];

type Routes = {
  runs?: UserParserRun[];
  start?: { status: string; run_id?: string | null; refused_account_id?: string | null };
  presets?: UserParserPreset[];
  bases?: UserParserBase[];
  users?: UserParserUser[];
  total?: number;
};

function json(body: unknown, status = 200): Response {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function route(routes: Routes = {}) {
  const calls: { path: string; method: string; search: string; body: unknown }[] = [];
  const runs = routes.runs ?? [runPayload()];
  let runIndex = 0;
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const url = new URL(request.url);
    const text = request.method === 'GET' ? '' : await request.clone().text();
    calls.push({
      path: url.pathname,
      method: request.method,
      search: url.search,
      body: text ? JSON.parse(text) : null,
    });
    const path = url.pathname.replace('/api/v1/user-parser', '');
    if (path === '/accounts') return json({ items: ACCOUNTS });
    if (path === '/presets' && request.method === 'GET')
      return json({ items: routes.presets ?? [] });
    if (path === '/presets') {
      const body = JSON.parse(text) as { name: string; settings: object };
      return json({ preset_id: 'p-new', created_at: 'now', ...body });
    }
    if (path === '/bases') return json({ items: routes.bases ?? [] });
    if (path === '/runs' && request.method === 'POST') {
      return json(routes.start ?? { status: 'started', run_id: 'r1' }, 202);
    }
    if (path.endsWith('/stop')) return json(runPayload({ status: 'stopped', kept: 1 }));
    if (path.endsWith('/users')) {
      return json({
        items: routes.users ?? USERS,
        total: routes.total ?? (routes.users ?? USERS).length,
      });
    }
    if (path.startsWith('/runs/')) {
      const payload = runs[Math.min(runIndex, runs.length - 1)];
      runIndex += 1;
      return json(payload);
    }
    if (path.startsWith('/bases/') && request.method === 'DELETE') return json(null, 204);
    if (path.startsWith('/bases/')) {
      const body = JSON.parse(text) as { name: string };
      return json({ ...(routes.bases?.[0] ?? {}), name: body.name });
    }
    return json({ items: [] });
  });
  return calls;
}

function renderModal() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <UserParserModal
        campaignName="Promo"
        campaignChannels={['@news', '@private']}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

async function fillAndRun() {
  await userEvent.click(await screen.findByRole('button', { name: /Каналы кампании/ }));
  const submit = screen.getByRole('button', { name: 'Запустить' });
  await waitFor(() => {
    expect(submit).toBeEnabled();
  });
  await userEvent.click(submit);
}

describe('UserParserModal', () => {
  it('starts a run with the free accounts, Premium first, and follows it to the end', async () => {
    const calls = route({
      runs: [
        runPayload(),
        runPayload({
          status: 'done',
          sources_done: 2,
          kept: 1,
          finished_at: '2026-10-09T10:05:00Z',
        }),
      ],
    });
    renderModal();
    await fillAndRun();

    const start = calls.find((call) => call.path.endsWith('/runs') && call.method === 'POST');
    expect(start?.body).toMatchObject({
      mode: 'comments',
      sources: ['@news', '@private'],
      account_ids: ['acc-p', 'acc-n'],
      toggles: { skip_bots: true, exclude_admins: true },
      limits: { posts: 50, per_post: 200, min_length: 0 },
    });
    expect((start?.body as { name: string }).name).toContain('Комментаторы · ');

    expect(await screen.findByText('Читаю @news')).toBeInTheDocument();
    expect(screen.getByText('Собрано пользователей: 40')).toBeInTheDocument();
    expect(screen.getByText(/Prem: лимит Telegram/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Остановить' })).toBeInTheDocument();

    // A parser event refreshes the run; it is done now and its people are listed.
    const source = (
      globalThis.EventSource as unknown as { last: () => { emit: (p: unknown) => void } }
    ).last();
    source.emit({
      id: 1,
      created_at: 'now',
      level: 'INFO',
      status: 'success',
      event: 'user_parser_run_finished',
      extra: {},
    });
    expect(await screen.findByText('Анна')).toBeInTheDocument();
    expect(screen.getByText('Готово', { selector: 'span' })).toBeInTheDocument();
    expect(calls.some((call) => call.path === '/api/v1/user-parser/bases/r1/users')).toBe(true);
    expect(screen.getByRole('button', { name: /CSV/ })).toBeEnabled();
  });

  it('names the account a refusal is about and stays on the form', async () => {
    route({ start: { status: 'account_busy', refused_account_id: 'acc-p' } });
    renderModal();
    await fillAndRun();

    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('Аккаунт занят другой задачей — аккаунт Prem');
    expect(screen.getByRole('button', { name: 'Запустить' })).toBeInTheDocument();
  });

  it('stops a running run', async () => {
    const calls = route();
    renderModal();
    await fillAndRun();

    await userEvent.click(await screen.findByRole('button', { name: 'Остановить' }));

    await waitFor(() => {
      expect(calls.some((call) => call.path.endsWith('/runs/r1/stop'))).toBe(true);
    });
    expect(await screen.findByText('Остановлено — собранное сохранено')).toBeInTheDocument();
  });

  it('saves the form as a preset and loads one back', async () => {
    const preset: UserParserPreset = {
      preset_id: 'p1',
      name: 'Участники · 1',
      created_at: 'now',
      settings: { mode: 'members', sources: ['@saved'], account_ids: [], keywords: ['shop'] },
    };
    const calls = route({ presets: [preset] });
    renderModal();

    await userEvent.click(await screen.findByRole('button', { name: 'Сохранить' }));
    await waitFor(() => {
      expect(calls.some((call) => call.path.endsWith('/presets') && call.method === 'POST')).toBe(
        true,
      );
    });
    const saved = calls.find((call) => call.path.endsWith('/presets') && call.method === 'POST');
    expect(saved?.body).toMatchObject({
      name: 'Комментаторов · 2',
      settings: { mode: 'comments' },
    });

    await userEvent.click(screen.getByRole('combobox', { name: 'Заготовки' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Участники · 1' }));
    expect(await screen.findByText('@saved')).toBeInTheDocument();
  });

  it('shows the bases from the server, searches, renames and deletes', async () => {
    const base: UserParserBase = {
      run_id: 'b1',
      name: 'Крипта',
      mode: 'members',
      status: 'done',
      created_at: '2026-10-01T00:00:00Z',
      sources: ['@news'],
      kept: 1,
    };
    const calls = route({ bases: [base] });
    renderModal();

    await userEvent.click(await screen.findByRole('radio', { name: 'Базы · 1' }));
    expect(await screen.findByText('Анна')).toBeInTheDocument();

    await userEvent.type(screen.getByRole('textbox', { name: 'Имя, @username или ID' }), 'ann');
    await waitFor(() => {
      expect(calls.some((call) => call.search.includes('search=ann'))).toBe(true);
    });

    await userEvent.click(screen.getByRole('button', { name: 'Переименовать' }));
    const input = screen.getByRole('textbox', { name: 'Переименовать' });
    await userEvent.clear(input);
    await userEvent.type(input, 'Новое{Enter}');
    await waitFor(() => {
      expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({ name: 'Новое' });
    });

    await userEvent.click(screen.getByRole('button', { name: 'Удалить базу' }));
    const dialog = await screen.findByRole('dialog', { name: /Удалить «Крипта»/ });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Удалить' }));
    await waitFor(() => {
      expect(
        calls.some((call) => call.method === 'DELETE' && call.path.endsWith('/bases/b1')),
      ).toBe(true);
    });
  });
});
