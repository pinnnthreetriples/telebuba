import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ChannelPoolFill } from './ChannelPoolFill';

const PROPOSAL = {
  channel: 'futbol_mx',
  label: 'Fútbol de México',
  created_at: '2026-10-13T00:00:00Z',
  subscribers: 40000,
  status: 'pending',
  source: 'discovery',
};

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function calls(path: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname.endsWith(path));
}

function renderFill(onPoolChanged = vi.fn()) {
  vi.mocked(fetch).mockImplementation((input) => {
    const path = new URL((input as Request).url).pathname;
    if (path.endsWith('/channels/find')) {
      return Promise.resolve(json({ status: 'ok', found: 7, checked: 4, proposed: 1 }));
    }
    if (path.endsWith('/approve')) return Promise.resolve(json({ channels: [] }));
    return Promise.resolve(json({ channels: [PROPOSAL] }));
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ChannelPoolFill defaultLanguage="es" onPoolChanged={onPoolChanged} />
    </QueryClientProvider>,
  );
  return onPoolChanged;
}

test('the search sends the language, country and topics and reports what it added', async () => {
  renderFill();
  await userEvent.click(screen.getByRole('button', { name: 'Найти каналы' }));
  await userEvent.type(screen.getByLabelText('Страна (необязательно, например MX)'), 'mx');
  await userEvent.type(screen.getByLabelText('Темы через запятую (до 5)'), 'fútbol, cine');
  await userEvent.click(screen.getByRole('button', { name: 'Искать' }));

  await waitFor(() => {
    expect(calls('/channels/find')).toHaveLength(1);
  });
  expect(await calls('/channels/find')[0]?.json()).toEqual({
    language: 'es',
    country: 'MX',
    topics: ['fútbol', 'cine'],
  });
  expect(await screen.findByText('Найдено 7, на одобрение добавлено 1.')).toBeVisible();
});

test('the operator approves the ticked proposals in a batch', async () => {
  const onPoolChanged = renderFill();
  const box = await screen.findByRole('checkbox', { name: 'Выбрать futbol_mx' });
  expect(screen.getByRole('button', { name: 'Одобрить' })).toBeDisabled();
  await userEvent.click(box);
  await userEvent.click(screen.getByRole('button', { name: 'Одобрить' }));

  await waitFor(() => {
    expect(calls('/proposals/approve')).toHaveLength(1);
  });
  expect(await calls('/proposals/approve')[0]?.json()).toEqual({ channels: ['futbol_mx'] });
  await waitFor(() => {
    expect(onPoolChanged).toHaveBeenCalled();
  });
});
