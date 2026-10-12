import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState, type ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { StartPreviewItem, StartPreviewRequest } from '@/shared/api';

import { groupStartPreview } from '../lib/startPreview';

import { StartDelayField } from './StartDelayField';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function item(overrides: Partial<StartPreviewItem>): StartPreviewItem {
  return {
    account_id: 'a1',
    time_zone: 'UTC',
    earliest_at: '2026-06-14T08:40:00+00:00',
    latest_at: '2026-06-14T08:40:00+00:00',
    shifted: false,
    ...overrides,
  };
}

// Answers the preview with `items` and records each body the field asked with.
function servePreview(items: StartPreviewItem[]): StartPreviewRequest[] {
  const asked: StartPreviewRequest[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    asked.push((await request.clone().json()) as StartPreviewRequest);
    return new Response(JSON.stringify({ items }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  return asked;
}

function Field({ ids, spread }: { ids: string[]; spread: boolean }) {
  const [hours, setHours] = useState(3);
  return <StartDelayField hours={hours} accountIds={ids} spread={spread} onChange={setHours} />;
}

test('a single start shows its local time, and a night start says it moved to the morning', async () => {
  servePreview([item({ shifted: true })]);
  renderWithClient(<Field ids={['a1']} spread={false} />);

  expect(
    await screen.findByText(
      '≈ 08:40 по времени аккаунта (там в это время ночь, перенесено на утро)',
    ),
  ).toBeInTheDocument();
});

test("a single start moved to the morning shows the daily wobble's span", async () => {
  servePreview([item({ latest_at: '2026-06-14T09:00:00+00:00', shifted: true })]);
  renderWithClient(<Field ids={['a1']} spread={false} />);

  expect(
    await screen.findByText(
      '≈ 08:40–09:00 по времени аккаунта (там в это время ночь, перенесено на утро)',
    ),
  ).toBeInTheDocument();
});

test('a daytime start shows only the time', async () => {
  servePreview([item({})]);
  renderWithClient(<Field ids={['a1']} spread={false} />);

  expect(await screen.findByText('≈ 08:40 по времени аккаунта')).toBeInTheDocument();
});

test('presets and the slider re-ask the preview for the picked delay', async () => {
  const asked = servePreview([item({})]);
  renderWithClient(<Field ids={['a1']} spread={false} />);
  const slider = screen.getByRole('slider', { name: 'Первый заход через' });
  expect(slider).toHaveAttribute('aria-valuenow', '3');
  expect(slider).toHaveAttribute('aria-valuemax', '24');

  await userEvent.click(screen.getByRole('radio', { name: '12 ч' }));
  expect(slider).toHaveAttribute('aria-valuenow', '12');
  expect(slider).toHaveAttribute('aria-valuetext', '12 часов');
  slider.focus();
  await userEvent.keyboard('{End}');
  expect(slider).toHaveAttribute('aria-valuenow', '24');

  await waitFor(() => {
    expect(asked.map((body) => body.start_delay_hours)).toEqual([3, 12, 24]);
  });
  expect(asked[0]).toMatchObject({ account_ids: ['a1'], spread_start: false });
});

test('a pool start reads as a range across its accounts', async () => {
  servePreview([
    item({ account_id: 'a1', earliest_at: '2026-06-13T20:00:00+00:00' }),
    item({ account_id: 'a2', latest_at: '2026-06-14T09:15:00+00:00', shifted: true }),
  ]);
  renderWithClient(<Field ids={['a1', 'a2']} spread />);

  expect(
    await screen.findByText(
      '≈ 20:00–09:15 по времени аккаунтов (ночные заходы перенесены на утро)',
    ),
  ).toBeInTheDocument();
});

test('a pool across time zones gets one line per zone and the night note on its own', async () => {
  servePreview([
    item({ account_id: 'a1' }),
    item({ account_id: 'a2', time_zone: 'Asia/Tokyo', shifted: true }),
  ]);
  renderWithClient(<Field ids={['a1', 'a2']} spread />);

  expect(await screen.findByText('≈ 08:40–08:40 (UTC)')).toBeInTheDocument();
  expect(screen.getByText('≈ 17:40–17:40 (Asia/Tokyo)')).toBeInTheDocument();
  expect(screen.getByText('(ночные заходы перенесены на утро)')).toBeInTheDocument();
});

test('grouping keeps the earliest and latest moment of each zone', () => {
  const zones = groupStartPreview(
    [
      item({ latest_at: '2026-06-14T10:00:00+00:00' }),
      item({ account_id: 'a2', earliest_at: '2026-06-14T07:00:00+00:00' }),
    ],
    'ru',
  );

  expect(zones).toEqual([{ timeZone: 'UTC', from: '07:00', to: '10:00', shifted: false }]);
});
