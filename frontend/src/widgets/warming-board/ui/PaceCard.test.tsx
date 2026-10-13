import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';
import type { CadencePreview, WarmingTuning } from '@/shared/api';

import { PaceCard } from './PaceCard';

const PHASES = ['intro', 'settling', 'warming', 'active', 'warmed'] as const;
const PERSONAS = ['calm', 'normal', 'active'] as const;

function cadence(visits: number): CadencePreview[] {
  return PERSONAS.flatMap((persona) =>
    PHASES.map((phase) => ({
      persona,
      phase,
      visits_min: visits,
      visits_max: visits + 1,
      actions_per_visit: 3,
    })),
  );
}

const NORMAL: WarmingTuning = {
  preset: 'normal',
  custom: null,
  daily_caps: { intro: 15, settling: 15, warming: 20, active: 40, warmed: 80 },
  pause_factor: 1,
  dm_factor: 1,
  cadence: cadence(5),
  warnings: [],
};

const CAREFUL: WarmingTuning = {
  ...NORMAL,
  preset: 'careful',
  daily_caps: { intro: 10, settling: 12, warming: 15, active: 30, warmed: 60 },
  pause_factor: 1.5,
  dm_factor: 0.5,
  cadence: cadence(3),
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

// Answers GET with `current` and every PUT with what `reply` makes of its body.
function routeApi(current: WarmingTuning, reply: (body: unknown) => WarmingTuning) {
  const puts: unknown[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    if (new URL(request.url).pathname !== '/api/v1/warming/tuning') {
      return jsonResponse({});
    }
    if (request.method === 'PUT') {
      const body: unknown = await request.clone().json();
      puts.push(body);
      return jsonResponse(reply(body));
    }
    return jsonResponse(current);
  });
  return puts;
}

async function renderOpen() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PaceCard />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole('button', { name: 'Темп прогрева' }));
  await screen.findByText('знакомство');
}

function cellsOf(phase: string): string[] {
  const row = screen.getByText(phase).closest('tr');
  expect(row).not.toBeNull();
  return within(row as HTMLElement)
    .getAllByRole('cell')
    .map((cell) => cell.textContent ?? '');
}

test('the table shows each phase limit and the visits every persona makes', async () => {
  routeApi(NORMAL, () => NORMAL);
  await renderOpen();

  expect(screen.getByRole('radio', { name: 'Обычно' })).toHaveAttribute('aria-checked', 'true');
  expect(cellsOf('прогрет')).toEqual(['прогрет', '80', '5–6', '5–6', '5–6']);
  expect(screen.getByText('Паузы ×1 · личные сообщения ×1')).toBeInTheDocument();
});

test('a preset is one click and the table follows it', async () => {
  const puts = routeApi(NORMAL, () => CAREFUL);
  await renderOpen();

  await userEvent.click(screen.getByRole('radio', { name: 'Осторожно' }));

  await waitFor(() => {
    expect(cellsOf('прогрет')).toEqual(['прогрет', '60', '3–4', '3–4', '3–4']);
  });
  expect(puts).toEqual([{ preset: 'careful' }]);
  expect(screen.getByText('Паузы ×1.5 · личные сообщения ×0.5')).toBeInTheDocument();
});

test('custom limits are edited in the table and refused while they drop', async () => {
  const saved: WarmingTuning = {
    ...NORMAL,
    preset: 'custom',
    daily_caps: { intro: 5, settling: 15, warming: 20, active: 40, warmed: 80 },
    warnings: [{ code: 'low_daily_cap', phase: 'intro', daily_cap: 5 }],
  };
  const puts = routeApi(NORMAL, () => saved);
  await renderOpen();

  await userEvent.click(screen.getByRole('radio', { name: 'Свой' }));
  const intro = screen.getByRole('spinbutton', { name: 'Лимит в день, фаза «знакомство»' });
  await userEvent.clear(intro);
  await userEvent.type(intro, '50');
  const save = screen.getByRole('button', { name: 'Сохранить свои лимиты' });
  expect(save).toBeDisabled();
  expect(screen.getByText(/не меньше, чем в предыдущей фазе/)).toBeInTheDocument();

  await userEvent.clear(intro);
  await userEvent.type(intro, '5');
  await userEvent.click(save);

  await screen.findByText(/всего 5 действий в день/);
  expect(puts).toEqual([
    {
      preset: 'custom',
      custom: {
        daily_caps: { intro: 5, settling: 15, warming: 20, active: 40, warmed: 80 },
        pause_factor: 1,
        dm_factor: 1,
      },
    },
  ]);
  expect(screen.queryByRole('spinbutton')).toBeNull();
});

test('a failed save keeps the draft, and editing it clears the stale error', async () => {
  let release: (() => void) | undefined;
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    if (request.method !== 'PUT') return jsonResponse(NORMAL);
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    return jsonResponse({ error: { code: 'internal_error', message: 'tuning_row_locked' } }, 500);
  });
  await renderOpen();
  await userEvent.click(screen.getByRole('radio', { name: 'Свой' }));
  const warmed = screen.getByRole('spinbutton', { name: 'Лимит в день, фаза «прогрет»' });

  await userEvent.click(screen.getByRole('button', { name: 'Сохранить свои лимиты' }));
  // While the PUT is out the draft it carries cannot change under it.
  await waitFor(() => {
    expect(warmed).toBeDisabled();
  });
  release?.();
  await screen.findByRole('alert');
  expect(warmed).toBeEnabled();

  await userEvent.clear(warmed);
  await userEvent.type(warmed, '70');

  expect(screen.queryByRole('alert')).toBeNull();
});

test('a reply without the cadence leaves the card waiting rather than broken', async () => {
  vi.mocked(fetch).mockImplementation(() => Promise.resolve(jsonResponse({})));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PaceCard />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole('button', { name: 'Темп прогрева' }));

  await waitFor(() => {
    expect(vi.mocked(fetch)).toHaveBeenCalled();
  });
  expect(screen.getByRole('radio', { name: 'Обычно' })).toBeDisabled();
  expect(screen.queryByRole('table')).toBeNull();
});
