import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { Phone, PhoneList } from '@/shared/api';

import { DevicesPage } from './DevicesPage';

// The screen itself is covered in features/phone-screen; here it only has to open.
vi.mock('@/features/phone-screen', () => ({
  PhoneScreen: ({ phoneId, mode }: { phoneId: string; mode: string }) => (
    <div>{`screen ${phoneId} ${mode}`}</div>
  ),
}));

function phone(id: string, status: Phone['status'], extra: Partial<Phone> = {}): Phone {
  return {
    phone_id: id,
    created: true,
    status,
    device_model: 'Samsung Galaxy S23',
    system_version: 'SDK 34',
    app_version: '11.5.0',
    lang_code: 'es',
    proxy: { proxy_type: 'socks5', host: '10.0.0.5', port: 1080, country_code: 'MX' },
    apps: [{ platform: 'telegram', account_id: id.replace('telebuba-', ''), username: 'ana' }],
    ...extra,
  };
}

const posted: string[] = [];

function serve(list: PhoneList) {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (request.method === 'POST') {
      posted.push(url.pathname);
      const status = url.pathname.endsWith('/start') ? 'starting' : 'asleep';
      return Promise.resolve(Response.json({ phone_id: 'x', status }));
    }
    return Promise.resolve(Response.json(list));
  });
}

function renderPage(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  posted.length = 0;
});

test('every phone shows its status and only the buttons that status allows', async () => {
  serve({
    enabled: true,
    phones: [
      phone('telebuba-a', 'asleep', { created: false }),
      phone('telebuba-b', 'free'),
      phone('telebuba-c', 'warming'),
    ],
  });
  renderPage(<DevicesPage />);

  const [asleep, free, warming] = (await screen.findAllByRole('listitem')) as [
    HTMLElement,
    HTMLElement,
    HTMLElement,
  ];
  expect(within(asleep).getByText('Спит')).toBeTruthy();
  expect(within(asleep).getByText('ещё не создан')).toBeTruthy();
  expect(within(asleep).getByRole('button', { name: 'Запустить' })).toBeTruthy();
  expect(within(asleep).queryByRole('button', { name: 'Управление' })).toBeNull();

  expect(within(free).getByText('Свободен')).toBeTruthy();
  expect(within(free).getByRole('button', { name: 'Управление' })).toBeTruthy();
  expect(within(free).getByRole('button', { name: 'Усыпить' })).toBeTruthy();

  expect(within(warming).getByText('Идёт прогрев')).toBeTruthy();
  expect(within(warming).getByRole('button', { name: 'Смотреть' })).toBeTruthy();
  expect(within(warming).queryByRole('button', { name: 'Усыпить' })).toBeNull();
  expect(within(warming).getByText(/@ana/)).toBeTruthy();
});

test('start and sleep call the phone actions', async () => {
  serve({ enabled: true, phones: [phone('telebuba-a', 'asleep'), phone('telebuba-b', 'free')] });
  renderPage(<DevicesPage />);
  const [asleep, free] = (await screen.findAllByRole('listitem')) as [HTMLElement, HTMLElement];

  await userEvent.click(within(asleep).getByRole('button', { name: 'Запустить' }));
  await userEvent.click(within(free).getByRole('button', { name: 'Усыпить' }));

  await waitFor(() => {
    expect(posted).toEqual(['/api/v1/devices/telebuba-a/start', '/api/v1/devices/telebuba-b/stop']);
  });
});

test('control opens the screen in control mode, watch in view mode', async () => {
  serve({ enabled: true, phones: [phone('telebuba-b', 'free'), phone('telebuba-c', 'warming')] });
  renderPage(<DevicesPage />);
  const [free, warming] = (await screen.findAllByRole('listitem')) as [HTMLElement, HTMLElement];

  await userEvent.click(within(free).getByRole('button', { name: 'Управление' }));
  expect(screen.getByText('screen telebuba-b control')).toBeTruthy();
  await userEvent.keyboard('{Escape}');
  await userEvent.click(within(warming).getByRole('button', { name: 'Смотреть' }));

  expect(screen.getByText('screen telebuba-c view')).toBeTruthy();
});

test('details show where the traffic goes, never a credential', async () => {
  serve({ enabled: true, phones: [phone('telebuba-b', 'free')] });
  renderPage(<DevicesPage />);
  const [free] = (await screen.findAllByRole('listitem')) as [HTMLElement];

  await userEvent.click(within(free).getByRole('button', { name: 'Параметры' }));

  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText('socks5 10.0.0.5:1080')).toBeTruthy();
  expect(within(dialog).getByText('telebuba-b')).toBeTruthy();
});

test('a switched-off emulator says so and starts nothing', async () => {
  serve({
    enabled: false,
    problem: 'mobile_emulator_disabled',
    phones: [phone('telebuba-a', 'asleep')],
  });
  renderPage(<DevicesPage />);

  expect(await screen.findByText(/EMULATOR__ENABLED=false/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Запустить' })).toHaveProperty('disabled', true);
});

test('no phones yet explains where they come from', async () => {
  serve({ enabled: true, phones: [] });
  renderPage(<DevicesPage />);

  expect(await screen.findByText(/мобильное устройство/)).toBeTruthy();
});
