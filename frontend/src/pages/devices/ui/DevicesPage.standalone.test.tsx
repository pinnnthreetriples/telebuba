import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { Phone } from '@/shared/api';

import { DevicesPage } from './DevicesPage';

vi.mock('@/features/phone-screen', () => ({ PhoneScreen: () => null }));

const STANDALONE: Phone = {
  phone_id: 'telebuba_sa-e1',
  kind: 'standalone',
  label: 'Германия 1',
  created: false,
  status: 'asleep',
  maker: 'Google',
  model_name: 'Pixel 8',
  device_model: 'GooglePixel 8',
  system_version: 'SDK 34',
  app_version: '11.5.0',
  lang_code: 'de',
  proxy: { proxy_type: 'socks5', host: '1.1.1.1', port: 1080, country_code: 'DE' },
  proxy_id: 'p1',
  apps: [],
};

function proxy(id: string, host: string, free: number) {
  return {
    id,
    proxy_type: 'socks5',
    host,
    port: 1080,
    country_code: 'DE',
    has_password: false,
    status: 'tcp_working',
    created_at: 'now',
    updated_at: 'now',
    used: 3 - free,
    capacity: 3,
    free,
  };
}

const calls: { method: string; path: string; body: unknown }[] = [];

function serve(phones: Phone[]) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    const text = request.method === 'GET' ? '' : await request.text();
    if (request.method !== 'GET') {
      calls.push({ method: request.method, path: pathname, body: text ? JSON.parse(text) : null });
    }
    if (pathname === '/api/v1/proxies') {
      return Response.json({
        proxies: [proxy('p1', '1.1.1.1', 2), proxy('p2', '2.2.2.2', 1), proxy('p3', '3.3.3.3', 0)],
      });
    }
    if (pathname === '/api/v1/devices/standalone') return Response.json(STANDALONE);
    if (request.method === 'PUT' || request.method === 'DELETE') {
      return new Response(null, { status: 204 });
    }
    return Response.json({ enabled: true, phones });
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DevicesPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls.length = 0;
});

test('the add button creates an independent emulator on the picked pool proxy', async () => {
  serve([]);
  renderPage();

  await userEvent.click(screen.getByRole('button', { name: '+ Добавить независимый эмулятор' }));
  const dialog = await screen.findByRole('dialog');
  await userEvent.type(within(dialog).getByPlaceholderText('Например, Германия 1'), 'DE-1');
  await userEvent.click(within(dialog).getByRole('combobox', { name: 'Прокси-пул' }));
  // A full proxy is not offered.
  expect(screen.queryByRole('option', { name: /3\.3\.3\.3/ })).toBeNull();
  await userEvent.click(screen.getByRole('option', { name: 'DE · 2.2.2.2:1080 · SOCKS5' }));
  await userEvent.click(within(dialog).getByRole('button', { name: 'Создать' }));

  await waitFor(() => {
    expect(calls).toEqual([
      {
        method: 'POST',
        path: '/api/v1/devices/standalone',
        body: { label: 'DE-1', proxy_id: 'p2' },
      },
    ]);
  });
  await waitFor(() => {
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

test('the add-proxy button opens the usual proxy form', async () => {
  serve([]);
  renderPage();

  await userEvent.click(screen.getByRole('button', { name: '+ Добавить независимый эмулятор' }));
  await userEvent.click(await screen.findByRole('button', { name: '+ Добавить прокси' }));

  expect(await screen.findByText('Хост')).toBeTruthy();
});

test('a standalone phone shows its name and no app', async () => {
  serve([STANDALONE]);
  renderPage();

  const [card] = (await screen.findAllByRole('listitem')) as [HTMLElement];
  expect(within(card).getByText('Германия 1')).toBeTruthy();
  expect(within(card).getByText('DE')).toBeTruthy();
  expect(within(card).queryByText(/Telegram/)).toBeNull();
});

test('its details move it to another proxy, detach it, and delete it', async () => {
  serve([STANDALONE]);
  renderPage();
  const [card] = (await screen.findAllByRole('listitem')) as [HTMLElement];

  await userEvent.click(within(card).getByRole('button', { name: 'Параметры' }));
  expect(screen.queryByText('Ждать SMS')).toBeNull();
  await userEvent.click(await screen.findByRole('combobox', { name: 'Прокси-пул' }));
  await userEvent.click(screen.getByRole('option', { name: 'DE · 2.2.2.2:1080 · SOCKS5' }));
  await userEvent.click(await screen.findByRole('button', { name: 'Отвязать прокси' }));
  await userEvent.click(screen.getByRole('button', { name: 'Удалить эмулятор' }));
  const confirm = await screen.findByRole('dialog', { name: 'Удалить эмулятор?' });
  await userEvent.click(within(confirm).getByRole('button', { name: 'Удалить эмулятор' }));

  await waitFor(() => {
    expect(calls).toEqual([
      {
        method: 'PUT',
        path: '/api/v1/devices/standalone/telebuba_sa-e1/proxy',
        body: { proxy_id: 'p2' },
      },
      { method: 'DELETE', path: '/api/v1/devices/standalone/telebuba_sa-e1/proxy', body: null },
      { method: 'DELETE', path: '/api/v1/devices/standalone/telebuba_sa-e1', body: null },
    ]);
  });
});
