import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type {
  StartWarmingRequest,
  WarmedAccount,
  WarmingAccountState,
  WarmingBoardState,
} from '@/shared/api';

import { account, BOARD, jsonResponse, renderWithClient } from './WarmingPage.testHelpers';

const { WarmingPage } = await import('./WarmingPage');

// «Прогрев пачками» on the page: who a folder start sends a geo consent for, when an
// account is written into a folder, one start per account across every surface, the
// folder's progress once its accounts graduate, and «Новая папка» from «В папку».

const STORAGE_KEY = 'tb.warmingFolders.prototype';

function folder(id: string, name: string) {
  return {
    id,
    name,
    days: 14,
    mix: { calm: 0, normal: 100, active: 0 },
    device: 'primary',
    delayHours: 3,
    spread: true,
    pace: 'inherit',
    finish: 'keep',
  };
}

function seedFolders(assignment: Record<string, string> = {}, folders = [folder('f1', 'Пачка')]) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ folders, assignment }));
}

function storedAssignment(): Record<string, string> {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  return raw ? (JSON.parse(raw) as { assignment: Record<string, string> }).assignment : {};
}

function mismatched(id: string, proxy = 'US'): WarmingAccountState {
  return {
    ...account(id, 'idle'),
    phone_country: 'MX',
    proxy_country: proxy,
    geo_status: 'mismatch',
    geo_accepted: false,
  };
}

function warmed(id: string): WarmedAccount {
  return { account_id: id, label: id, warming_days: 14, warmed_days: 14, target_days: 14 };
}

let board: WarmingBoardState;
// account_id → the status /start answers with; a missing id answers 200, `hang` never.
let startStatus: Record<string, number | 'hang'>;

function serve() {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/warming/board') return jsonResponse(board);
    if (url.pathname === '/api/v1/warming/start') {
      const body = (await request.clone().json()) as StartWarmingRequest;
      const status = startStatus[body.account_id] ?? 200;
      if (status === 'hang') return new Promise<Response>(() => undefined);
      return status === 200
        ? jsonResponse(account(body.account_id, 'active'))
        : jsonResponse({ detail: 'already_running' }, status);
    }
    return jsonResponse({});
  });
}

function startBodies(): Promise<StartWarmingRequest[]> {
  return Promise.all(
    vi
      .mocked(fetch)
      .mock.calls.map(([input]) => input as Request)
      .filter((request) => new URL(request.url).pathname === '/api/v1/warming/start')
      .map((request) => request.clone().json() as Promise<StartWarmingRequest>),
  );
}

function renderWithOwnClient() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <WarmingPage />
    </QueryClientProvider>,
  );
  return queryClient;
}

// The test runtime's own localStorage has no methods; the folders keep to an in-memory one.
function memoryStorage(): Storage {
  const items = new Map<string, string>();
  return {
    get length() {
      return items.size;
    },
    clear: () => {
      items.clear();
    },
    getItem: (key) => items.get(key) ?? null,
    key: (index) => [...items.keys()][index] ?? null,
    removeItem: (key) => {
      items.delete(key);
    },
    setItem: (key, value) => {
      items.set(key, value);
    },
  };
}

beforeEach(() => {
  vi.stubGlobal('localStorage', memoryStorage());
  board = { ...BOARD, idle: [], warming: [], warmed: [] };
  startStatus = {};
  serve();
});

async function addToFolder(count: number) {
  await userEvent.click(await screen.findByRole('button', { name: 'В папку' }));
  const dialog = await screen.findByRole('dialog');
  await userEvent.click(
    within(dialog).getByRole('button', { name: `Добавить и запустить ${String(count)}` }),
  );
}

test('a folder start consents only for the account whose mismatch the dialog showed', async () => {
  seedFolders();
  board.idle = [mismatched('mx-1'), account('ok-1', 'idle')];
  renderWithClient(<WarmingPage />);

  await userEvent.click(await screen.findByRole('button', { name: 'В папку' }));
  const dialog = await screen.findByRole('dialog');
  // The dialog names the pair the consent will cover.
  expect(within(dialog).getByText(/Согласие запишется только для этих пар/)).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole('button', { name: 'Добавить и запустить 2' }));

  await waitFor(async () => {
    expect(await startBodies()).toHaveLength(2);
  });
  const bodies = await startBodies();
  expect(bodies.find((b) => b.account_id === 'mx-1')).toMatchObject({
    accept_geo_mismatch: true,
    accepted_geo_pair: 'MX>US',
  });
  const plain = bodies.find((b) => b.account_id === 'ok-1');
  expect(plain?.accept_geo_mismatch).toBe(false);
  expect(plain?.accepted_geo_pair).toBeUndefined();
});

test('«Только совпадающие» in a folder sends no consent and leaves the mismatch out', async () => {
  seedFolders();
  board.idle = [mismatched('mx-1'), account('ok-1', 'idle')];
  renderWithClient(<WarmingPage />);

  await userEvent.click(await screen.findByRole('button', { name: 'В папку' }));
  const dialog = await screen.findByRole('dialog');
  await userEvent.click(within(dialog).getByRole('button', { name: 'Только совпадающие' }));

  await waitFor(async () => {
    expect(await startBodies()).toHaveLength(1);
  });
  expect((await startBodies())[0]).toMatchObject({
    account_id: 'ok-1',
    accept_geo_mismatch: false,
  });
});

test('a proxy changed while the folder dialog was open: the consent still names the shown pair', async () => {
  seedFolders();
  board.idle = [mismatched('mx-1', 'US'), account('ok-1', 'idle')];
  const queryClient = renderWithOwnClient();

  await userEvent.click(await screen.findByRole('button', { name: 'В папку' }));
  const dialog = await screen.findByRole('dialog');
  // Both proxies move to Germany while the operator reads the dialog.
  board = { ...board, idle: [mismatched('mx-1', 'DE'), mismatched('ok-1', 'DE')] };
  await act(async () => {
    await queryClient.refetchQueries();
  });
  await userEvent.click(within(dialog).getByRole('button', { name: 'Добавить и запустить 2' }));

  await waitFor(async () => {
    expect(await startBodies()).toHaveLength(2);
  });
  const bodies = await startBodies();
  // The server compares this pair with the account's current one and stores nothing.
  expect(bodies.find((b) => b.account_id === 'mx-1')?.accepted_geo_pair).toBe('MX>US');
  // ok-1 showed no warning, so it consents to nothing.
  expect(bodies.find((b) => b.account_id === 'ok-1')?.accept_geo_mismatch).toBe(false);
});

test('a refused start keeps the previous folder; only the started account moves', async () => {
  seedFolders({ 'ok-2': 'f2' }, [folder('f1', 'Пачка'), folder('f2', 'Вторая')]);
  board.idle = [account('ok-1', 'idle'), account('ok-2', 'idle')];
  startStatus = { 'ok-2': 409 };
  renderWithClient(<WarmingPage />);

  await addToFolder(2);

  await waitFor(() => {
    expect(storedAssignment()).toEqual({ 'ok-1': 'f1', 'ok-2': 'f2' });
  });
});

test('a retry after a refusal files the account once the server says yes', async () => {
  seedFolders();
  board.idle = [account('ok-1', 'idle'), account('ok-2', 'idle')];
  startStatus = { 'ok-2': 409 };
  const queryClient = renderWithOwnClient();

  await addToFolder(2);
  await waitFor(() => {
    expect(storedAssignment()).toEqual({ 'ok-1': 'f1' });
  });

  board = { ...board, idle: [account('ok-2', 'idle')], warming: [account('ok-1', 'active')] };
  startStatus = {};
  await act(async () => {
    await queryClient.refetchQueries();
  });
  await addToFolder(1);

  await waitFor(() => {
    expect(storedAssignment()).toEqual({ 'ok-1': 'f1', 'ok-2': 'f1' });
  });
});

test('an account whose single start is in flight is not started again through «В папку»', async () => {
  seedFolders();
  board.idle = [account('ok-1', 'idle'), account('ok-2', 'idle')];
  startStatus = { 'ok-1': 'hang' };
  renderWithClient(<WarmingPage />);

  const [play] = await screen.findAllByRole('button', { name: 'Прогреть' });
  if (!play) throw new Error('no ▶');
  await userEvent.click(play);
  await userEvent.click(
    within(await screen.findByRole('dialog')).getByRole('button', { name: 'Запустить прогрев' }),
  );
  await waitFor(async () => {
    expect(await startBodies()).toHaveLength(1);
  });

  // The busy account is not offered: the batch is ok-2 alone.
  await addToFolder(1);

  await waitFor(async () => {
    expect(await startBodies()).toHaveLength(2);
  });
  const ids = (await startBodies()).map((b) => b.account_id);
  expect(ids.filter((id) => id === 'ok-1')).toHaveLength(1);
});

test('a batch dialog left open re-checks on confirm and skips an account already warming', async () => {
  board.idle = [account('ok-1', 'idle'), account('ok-2', 'idle')];
  const queryClient = renderWithOwnClient();

  await userEvent.click(await screen.findByRole('button', { name: 'Запустить пул' }));
  const dialog = await screen.findByRole('dialog');
  // Meanwhile ok-1 was started from elsewhere and left «Готовы».
  board = { ...board, idle: [account('ok-2', 'idle')], warming: [account('ok-1', 'active')] };
  await act(async () => {
    await queryClient.refetchQueries();
  });
  await userEvent.click(within(dialog).getByRole('button', { name: 'Запустить пул' }));

  await waitFor(async () => {
    expect(await startBodies()).toHaveLength(1);
  });
  expect((await startBodies())[0]?.account_id).toBe('ok-2');
});

test('a folder whose last account graduated shows done, not «Пока пусто»', async () => {
  seedFolders({ 'done-a': 'f1', 'done-b': 'f1' });
  board.warmed = [warmed('done-a'), warmed('done-b')];
  board.idle = [account('ok-1', 'idle')];
  renderWithClient(<WarmingPage />);

  const card = await screen.findByRole('button', { name: /Пачка/ });
  expect(card).toHaveTextContent('все догреты');
  expect(card).not.toHaveTextContent('Пока пусто');

  await userEvent.click(card);
  expect(await screen.findByText(/Все аккаунты этой папки догреты/)).toBeInTheDocument();
});

test('a folder with one account warming and one graduated counts both', async () => {
  seedFolders({ 'done-a': 'f1', 'run-1': 'f1' });
  board.warmed = [warmed('done-a')];
  board.warming = [{ ...account('run-1', 'active'), warmed_days: 5, target_days: 14 }];
  renderWithClient(<WarmingPage />);

  const card = await screen.findByRole('button', { name: /Пачка/ });
  expect(card).toHaveTextContent('догрет 1');
  expect(card).toHaveTextContent('идёт 1');
  expect(within(card).getByText('2')).toBeInTheDocument();
});

test('«Новая папка» from «В папку» comes back to the same batch with the new folder', async () => {
  seedFolders();
  board.idle = [account('ok-1', 'idle'), account('ok-2', 'idle')];
  renderWithClient(<WarmingPage />);

  await userEvent.click(await screen.findByRole('button', { name: 'В папку' }));
  await userEvent.click(
    within(await screen.findByRole('dialog')).getByRole('button', { name: 'Новая папка' }),
  );
  const settings = await screen.findByRole('dialog', { name: 'Прогрев пачки' });
  await userEvent.type(within(settings).getByLabelText('Название папки'), 'Свежая');
  await userEvent.click(within(settings).getByRole('button', { name: 'Создать папку' }));

  const back = await screen.findByRole('dialog', { name: 'Добавить в папку' });
  expect(within(back).getByRole('button', { name: /Свежая/ })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  // Nothing starts until the operator confirms.
  expect(await startBodies()).toHaveLength(0);
  await userEvent.click(within(back).getByRole('button', { name: 'Добавить и запустить 2' }));

  await waitFor(() => {
    expect(Object.values(storedAssignment())).toHaveLength(2);
  });
  const fresh = Object.values(storedAssignment());
  expect(fresh.every((id) => id !== 'f1')).toBe(true);
});

test('an empty board links to the accounts page and keeps the board tools', async () => {
  renderWithClient(<WarmingPage />);

  const link = await screen.findByRole('link', { name: 'К аккаунтам' });
  expect(link).toHaveAttribute('href', '/');
  expect(screen.getByRole('button', { name: 'Каналы прогрева' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Темп прогрева' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Тонкая настройка действий' })).toBeInTheDocument();
});
