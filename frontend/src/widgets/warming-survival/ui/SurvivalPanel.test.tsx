import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { type ReactElement, useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { expectNoAxeViolations } from '@/shared/ui/axe.test-helpers';

import type { SurvivalReport } from '@/shared/api';

import { SurvivalPanel } from './SurvivalPanel';
import { SurvivalTile } from './SurvivalTile';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const REPORT: SurvivalReport = {
  cohort: 'week',
  windows: [
    { window: 'warming', alive: 9, dead: 1, pending: 2 },
    { window: 'd7', alive: 7, wounded: 1, dead: 2, unchecked: 2 },
    { window: 'd30', alive: 2, dead: 1, pending: 6 },
  ],
  cohorts: [
    {
      key: '2026-W37',
      windows: [
        { window: 'warming', alive: 4 },
        { window: 'd7', alive: 3, dead: 1 },
        { window: 'd30', alive: 1 },
      ],
    },
    { key: null, windows: [{ window: 'd7', alive: 1 }] },
  ],
  causes: [
    { cause: 'account_frozen', count: 6 },
    { cause: 'session_dead', count: 1 },
  ],
  lives: [
    {
      account_id: 'a1',
      label: 'Main',
      started_at: '2026-09-07T00:00:00+00:00',
      status: 'dead',
      end_day: 19,
      release_day: 14,
      death_day: 19,
      cause: 'account_frozen',
      wound_days: [16],
      waves: [
        { wave: 1, start_day: 0, release_day: 10 },
        { wave: 2, start_day: 12 },
      ],
    },
    {
      account_id: 'a2',
      label: 'Second',
      started_at: '2026-09-08T00:00:00+00:00',
      status: 'warming',
      end_day: 5,
    },
  ],
  releases: 12,
  full_data_since: '2026-09-11',
  daily: [
    { day: '2026-10-12', accounts: 3, cycles: 12, floods: 1 },
    { day: '2026-10-13', accounts: 3, cycles: 0, floods: 0 },
  ],
};

function routeSurvival(byCohort: Partial<Record<string, SurvivalReport>> = {}) {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    const cohort = url.searchParams.get('cohort') ?? 'week';
    return Promise.resolve(jsonResponse(byCohort[cohort] ?? REPORT));
  });
}

function TileAndPanel() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <SurvivalTile
        expanded={open}
        controls="panel"
        onToggle={() => {
          setOpen((value) => !value);
        }}
      />
      {open ? <SurvivalPanel id="panel" /> : null}
    </>
  );
}

test('the tile says how many are alive after seven days and how many are unchecked', async () => {
  routeSurvival();
  renderWithClient(<TileAndPanel />);

  const tile = await screen.findByRole('button', { name: /7 из 9/ });
  expect(tile).toHaveTextContent('Живы через 7 дн · 2 без проверки');
  // Nine decided is under the percentage floor: no percentage yet.
  expect(tile).not.toHaveTextContent('%');
  expect(tile).toHaveAttribute('aria-expanded', 'false');
});

test('the tile says so when nobody was released yet', async () => {
  routeSurvival({ week: { cohort: 'week', windows: [] } });
  renderWithClient(<TileAndPanel />);

  expect(await screen.findByRole('button', { name: /пока никого/ })).toBeInTheDocument();
});

test('a click opens the panel with counters, rows, cohorts, causes and history', async () => {
  routeSurvival();
  renderWithClient(<TileAndPanel />);

  await userEvent.click(await screen.findByRole('button', { name: /7 из 9/ }));

  expect(await screen.findByText('Выживаемость прогретых')).toBeInTheDocument();
  expect(screen.getByText('Полные данные с 11.09.2026')).toBeInTheDocument();
  const warming = screen.getByTestId('survival-window-warming');
  // Ten decided during warming: the grey percentage joins the count.
  expect(warming).toHaveTextContent('9 из 10');
  expect(warming).toHaveTextContent('90 %');
  expect(screen.getByTestId('survival-window-d7')).toHaveTextContent(
    '2 без проверки · с ранением: 1',
  );
  expect(screen.getByLabelText('Main: умер, 19 дн')).toBeInTheDocument();
  expect(screen.getByTitle('Смерть на 19-й день: аккаунт заморожен')).toBeInTheDocument();
  expect(screen.getByTitle('Ранение на 16-й день')).toBeInTheDocument();
  // A re-warm is a tick on the same row, never a second row.
  expect(screen.getByTitle('Волна 2 с 12-го дня')).toBeInTheDocument();
  expect(screen.getByText('нед. 37')).toBeInTheDocument();
  expect(screen.getByText('не известно')).toBeInTheDocument();
  expect(screen.getByText('аккаунт заморожен 6 · сессия умерла 1')).toBeInTheDocument();
  expect(screen.getAllByRole('img', { name: /12/ }).length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: /7 из 9/ })).toHaveAttribute('aria-expanded', 'true');
});

test('a death found later names both the day it died and the day it was found', async () => {
  const [first, ...rest] = REPORT.lives ?? [];
  routeSurvival({ week: { ...REPORT, lives: [{ ...first, found_day: 22 }, ...rest] } });
  renderWithClient(<TileAndPanel />);

  await userEvent.click(await screen.findByRole('button', { name: /7 из 9/ }));

  expect(
    await screen.findByTitle('Смерть на 19-й день, обнаружена на 22-й: аккаунт заморожен'),
  ).toBeInTheDocument();
});

test('another cohort cut asks the backend for it', async () => {
  routeSurvival({
    persona: {
      ...REPORT,
      cohort: 'persona',
      cohorts: [{ key: 'calm', windows: [{ window: 'd7', alive: 2, dead: 1 }] }],
    },
  });
  renderWithClient(<SurvivalPanel id="panel" />);

  await userEvent.click(await screen.findByRole('radio', { name: 'Персоне' }));

  await waitFor(() => {
    expect(screen.getByText('Спокойный')).toBeInTheDocument();
  });
  const calls = vi.mocked(fetch).mock.calls.map(([input]) => new URL((input as Request).url));
  expect(calls.some((url) => url.searchParams.get('cohort') === 'persona')).toBe(true);
});

test('an empty journal shows empty states, not zeros dressed as data', async () => {
  routeSurvival({ week: { cohort: 'week', windows: [] } });
  const { container } = renderWithClient(<SurvivalPanel id="panel" />);

  expect(await screen.findByText('Ещё ни один аккаунт не прогревался')).toBeInTheDocument();
  expect(screen.getByText('Когорт пока нет')).toBeInTheDocument();
  expect(screen.getByText('Смертей нет')).toBeInTheDocument();
  expect(screen.getByText('Истории пока нет')).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

test('a failed read says so', async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'boom' }, 500));
  renderWithClient(<SurvivalPanel id="panel" />);

  expect(await screen.findByText('Не удалось загрузить выживаемость')).toBeInTheDocument();
});

test('the open panel passes axe', async () => {
  routeSurvival();
  const { container } = renderWithClient(<SurvivalPanel id="panel" />);

  const table = await screen.findByText('нед. 37');
  expect(within(container).getByText('Жизнь каждого аккаунта')).toBeInTheDocument();
  expect(table).toBeInTheDocument();
  await expectNoAxeViolations(container);
});
