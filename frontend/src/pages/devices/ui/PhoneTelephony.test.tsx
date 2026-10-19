import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { Phone, PhoneTelephonyStatus, SmsInboxMessage } from '@/shared/api';

import { PhoneDetails } from './PhoneDetails';

const PHONE_ID = 'telebuba-b';

const phone: Phone = {
  phone_id: PHONE_ID,
  created: true,
  status: 'free',
  maker: 'Samsung',
  model_name: 'Galaxy S23',
  device_model: 'samsungSM-S911B',
  system_version: 'SDK 34',
  app_version: '11.5.0',
  lang_code: 'es',
  proxy: null,
  apps: [{ platform: 'telegram', account_id: 'b', username: 'ana' }],
};

function message(id: string, code: string): SmsInboxMessage {
  return {
    id,
    phone: '+79991234567',
    phone_id: PHONE_ID,
    sender: 'Telegram',
    text: `Код Telegram: ${code}`,
    code,
    platform: 'telegram',
    matched: true,
    timestamp: '2026-10-19T12:00:00Z',
  };
}

type Line = Partial<PhoneTelephonyStatus> & Pick<PhoneTelephonyStatus, 'status'>;

let line: Line = { status: 'idle' };
let inbox: SmsInboxMessage[] = [];
let manualInput: () => Response = () =>
  Response.json({ status: 'ok', phone_id: PHONE_ID, code: '0000', line_status: 'completed' });
const requests: string[] = [];

function serve() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    requests.push(`${request.method} ${url.pathname}`);
    if (url.pathname.endsWith('/manual-input')) return Promise.resolve(manualInput());
    if (url.pathname.endsWith('/wait')) {
      line = { status: 'waiting_otp', phone_number: '+79991234567', remaining_seconds: 179 };
      return Promise.resolve(Response.json({ phone_id: PHONE_ID, ...line }));
    }
    if (url.pathname.endsWith('/telephony/inbox')) {
      return Promise.resolve(Response.json({ messages: inbox }));
    }
    return Promise.resolve(Response.json({ phone_id: PHONE_ID, ...line }));
  });
}

function statusReads(): number {
  return requests.filter((r) => r === `GET /api/v1/telephony/phones/${PHONE_ID}/status`).length;
}

function renderDetails() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PhoneDetails phone={phone} />
    </QueryClientProvider>,
  );
}

const realClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');

function stubClipboard(writeText: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
}

beforeEach(() => {
  line = { status: 'idle' };
  inbox = [];
  requests.length = 0;
  manualInput = () =>
    Response.json({ status: 'ok', phone_id: PHONE_ID, code: '0000', line_status: 'completed' });
  serve();
});

afterEach(() => {
  vi.useRealTimers();
  if (realClipboard) Object.defineProperty(navigator, 'clipboard', realClipboard);
});

test('an idle line is read once and not polled', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  renderDetails();
  expect(await screen.findByText('Линия свободна')).toBeTruthy();

  await vi.advanceTimersByTimeAsync(10_000);

  expect(statusReads()).toBe(1);
});

test('a waiting line is polled until it ends', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  line = { status: 'waiting_otp', remaining_seconds: 90 };
  renderDetails();
  expect(await screen.findByText('Ожидание SMS (01:30)...')).toBeTruthy();

  await vi.advanceTimersByTimeAsync(2_100);
  expect(statusReads()).toBe(2);

  line = { status: 'completed', last_code: '48291' };
  await vi.advanceTimersByTimeAsync(2_100);
  expect(await screen.findByText('Код введён')).toBeTruthy();
  const settled = statusReads();
  await vi.advanceTimersByTimeAsync(10_000);
  expect(statusReads()).toBe(settled);
});

test('"wait for SMS" opens the wait and shows its timer', async () => {
  renderDetails();
  await screen.findByText('Линия свободна');

  await userEvent.click(screen.getByRole('button', { name: 'Ждать SMS' }));

  expect(await screen.findByText('Ожидание SMS (02:59)...')).toBeTruthy();
  expect(requests).toContain(`POST /api/v1/telephony/phones/${PHONE_ID}/wait`);
  expect(screen.getByRole('button', { name: 'Ждать SMS' })).toHaveProperty('disabled', true);
});

test('a received code and a failed typing read as such', async () => {
  line = { status: 'otp_received', last_code: '48291' };
  const { unmount } = renderDetails();
  expect(await screen.findByText('Код получен: 48291')).toBeTruthy();
  unmount();

  line = { status: 'failed', last_code: '48291', error_code: 'emulator_field_missing' };
  renderDetails();
  expect(await screen.findByText(/^Код не введён: /)).toBeTruthy();
});

test('copying a code says when the clipboard refused it', async () => {
  inbox = [message('m1', '48291')];
  stubClipboard(() => Promise.reject(new Error('denied')));
  renderDetails();
  await userEvent.click(await screen.findByRole('button', { name: 'Входящие SMS' }));

  await userEvent.click(await screen.findByRole('button', { name: 'Скопировать код' }));

  expect(await screen.findByRole('button', { name: 'Не удалось скопировать' })).toBeTruthy();
});

test('copying a code confirms it, then the label comes back', async () => {
  inbox = [message('m1', '48291')];
  const writeText = vi.fn(() => Promise.resolve());
  stubClipboard(writeText);
  renderDetails();
  await userEvent.click(await screen.findByRole('button', { name: 'Входящие SMS' }));
  vi.useFakeTimers({ shouldAdvanceTime: true });

  await userEvent.click(await screen.findByRole('button', { name: 'Скопировать код' }));

  expect(await screen.findByRole('button', { name: 'Скопировано' })).toBeTruthy();
  expect(writeText).toHaveBeenCalledWith('48291');
  await vi.advanceTimersByTimeAsync(2_100);
  expect(await screen.findByRole('button', { name: 'Скопировать код' })).toBeTruthy();
});

test('only the message whose code is being typed spins', async () => {
  inbox = [message('m1', '48291'), message('m2', '48291')];
  let release: () => void = () => undefined;
  manualInput = () =>
    Response.json({ status: 'ok', phone_id: PHONE_ID, code: '48291', line_status: 'completed' });
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    requests.push(`${request.method} ${url.pathname}`);
    if (url.pathname.endsWith('/manual-input')) {
      return new Promise((resolve) => {
        release = () => resolve(manualInput());
      });
    }
    if (url.pathname.endsWith('/telephony/inbox')) {
      return Promise.resolve(Response.json({ messages: inbox }));
    }
    return Promise.resolve(Response.json({ phone_id: PHONE_ID, ...line }));
  });
  renderDetails();
  await userEvent.click(await screen.findByRole('button', { name: 'Входящие SMS' }));
  await screen.findAllByRole('button', { name: 'Скопировать код' });

  const typeButtons = screen.getAllByRole('button', { name: 'Напечатать в телефон' });
  await userEvent.click(typeButtons[1]!);

  await waitFor(() => expect(typeButtons[1]!.getAttribute('aria-busy')).toBe('true'));
  expect(typeButtons[2]!.getAttribute('aria-busy')).toBeNull();
  expect(typeButtons[2]).toHaveProperty('disabled', true);
  release();
  await waitFor(() => expect(typeButtons[1]!.getAttribute('aria-busy')).toBeNull());
});

test('a refused manual input keeps the typed code for another try', async () => {
  manualInput = () =>
    Response.json(
      { error: { code: 'bad_request', message: 'emulator_field_missing' } },
      { status: 400 },
    );
  renderDetails();
  await userEvent.click(await screen.findByRole('button', { name: 'Входящие SMS' }));
  const field = await screen.findByRole('textbox', { name: 'Введите код вручную' });

  await userEvent.type(field, '12a34٥5');
  expect(field).toHaveProperty('value', '12345');
  const [typeCustom] = screen.getAllByRole('button', { name: 'Напечатать в телефон' });
  await userEvent.click(typeCustom!);

  await waitFor(() =>
    expect(requests).toContain(`POST /api/v1/telephony/phones/${PHONE_ID}/manual-input`),
  );
  await waitFor(() => expect(typeCustom).toHaveProperty('disabled', false));
  expect(field).toHaveProperty('value', '12345');
});

test('the inbox has its own close label', async () => {
  renderDetails();
  await userEvent.click(await screen.findByRole('button', { name: 'Входящие SMS' }));

  await userEvent.click(await screen.findByRole('button', { name: 'Закрыть входящие' }));

  await waitFor(() =>
    expect(screen.queryByRole('button', { name: 'Закрыть входящие' })).toBeNull(),
  );
});
