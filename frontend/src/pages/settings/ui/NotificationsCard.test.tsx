import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { NotificationsCard } from './NotificationsCard';

const STORED = {
  enabled: true,
  has_bot_token: true,
  bot_token_hint: '1234…fXYZ',
  chat_id: '-100500',
  updated_at: 'now',
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

type Route = (request: Request) => Response | undefined;

function serve(stored: typeof STORED | Record<string, unknown>, route?: Route) {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    return Promise.resolve(route?.(request) ?? json(stored));
  });
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NotificationsCard />
    </QueryClientProvider>,
  );
}

function requests(method: string, path: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((r) => r.method === method && new URL(r.url).pathname === path);
}

test('shows the stored token only as a mask and never asks for it back', async () => {
  serve(STORED);
  renderCard();
  const field = await screen.findByPlaceholderText(
    'Токен задан — оставьте пустым, чтобы сохранить',
  );
  expect(field).toHaveValue('');
  expect(screen.getByRole('switch', { name: 'Отправлять уведомления' })).toBeChecked();
  expect(screen.getByLabelText('Chat ID')).toHaveValue('-100500');

  await userEvent.click(screen.getByRole('button', { name: 'Показать/скрыть токен' }));
  expect(
    screen.getByPlaceholderText('Токен задан: 1234…fXYZ — оставьте пустым, чтобы сохранить'),
  ).toBeInTheDocument();
});

test('saves a typed token, the chat and the switch; blank keeps the stored token', async () => {
  serve(STORED);
  renderCard();
  const save = await screen.findByRole('button', { name: 'Сохранить уведомления' });
  expect(save).toBeDisabled();

  await userEvent.click(screen.getByRole('switch', { name: 'Отправлять уведомления' }));
  const chat = screen.getByLabelText('Chat ID');
  await userEvent.clear(chat);
  await userEvent.type(chat, '@ops_room');
  await userEvent.click(save);
  await waitFor(() => {
    expect(requests('PUT', '/api/v1/notifications/settings')).toHaveLength(1);
  });
  const body: unknown = JSON.parse(
    await requests('PUT', '/api/v1/notifications/settings')[0]!.clone().text(),
  );
  expect(body).toEqual({
    enabled: false,
    bot_token: null,
    clear_bot_token: false,
    chat_id: '@ops_room',
  });
  expect(await screen.findByRole('status')).toHaveTextContent('Уведомления сохранены');
});

test('clearing the token sends the clear flag', async () => {
  serve(STORED);
  renderCard();
  await userEvent.click(await screen.findByText('Очистить токен'));
  expect(screen.getByPlaceholderText('Токен будет удалён при сохранении')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить уведомления' }));
  await waitFor(() => {
    expect(requests('PUT', '/api/v1/notifications/settings')).toHaveLength(1);
  });
  const body = JSON.parse(
    await requests('PUT', '/api/v1/notifications/settings')[0]!.clone().text(),
  ) as Record<string, unknown>;
  expect(body.clear_bot_token).toBe(true);
  expect(body.bot_token).toBeNull();
});

test('an invalid chat id blocks the save and says why', async () => {
  serve(STORED);
  renderCard();
  const chat = await screen.findByLabelText('Chat ID');
  await userEvent.clear(chat);
  await userEvent.type(chat, 'my chat');
  expect(chat).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByText(/Нужен числовой ID/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Сохранить уведомления' })).toBeDisabled();
});

test('the test button reports a delivered message', async () => {
  serve(STORED, (request) => (request.method === 'POST' ? json({ sent: true }) : undefined));
  renderCard();
  await userEvent.click(await screen.findByRole('button', { name: 'Отправить тест' }));
  expect(await screen.findByRole('status')).toHaveTextContent('Тестовое сообщение отправлено');
  expect(requests('POST', '/api/v1/notifications/test')).toHaveLength(1);
});

test('the test button explains a Telegram refusal in place', async () => {
  serve(STORED, (request) =>
    request.method === 'POST'
      ? json({ error: { code: 'bad_request', message: 'telegram_rejected' } }, 400)
      : undefined,
  );
  renderCard();
  await userEvent.click(await screen.findByRole('button', { name: 'Отправить тест' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Telegram отклонил сообщение');
});

test('unsaved changes hold the test back until they are saved', async () => {
  serve(STORED);
  renderCard();
  await userEvent.type(
    await screen.findByPlaceholderText('Токен задан — оставьте пустым, чтобы сохранить'),
    '1:abc',
  );
  expect(screen.getByRole('button', { name: 'Отправить тест' })).toBeDisabled();
  expect(screen.getByText('Сохраните изменения, прежде чем отправлять тест')).toBeInTheDocument();
});

test('a failed load says so', async () => {
  serve(STORED, () => json({ error: { code: 'internal', message: 'boom' } }, 500));
  renderCard();
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить настройки');
});

test('the form is locked while a save is in flight', async () => {
  let finish: (response: Response) => void = () => undefined;
  // The PUT is held open so the test can look at the form mid-save.
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (request.method === 'PUT') {
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    }
    return Promise.resolve(json(STORED));
  });
  renderCard();
  const chat = await screen.findByLabelText('Chat ID');
  await userEvent.clear(chat);
  await userEvent.type(chat, '@ops_room');
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить уведомления' }));

  await waitFor(() => {
    expect(screen.getByLabelText('Chat ID')).toBeDisabled();
  });
  expect(screen.getByRole('switch', { name: 'Отправлять уведомления' })).toBeDisabled();

  finish(json(STORED));
  await waitFor(() => {
    expect(screen.getByLabelText('Chat ID')).toBeEnabled();
  });
});

test('the form follows settings changed elsewhere instead of keeping stale drafts', async () => {
  let stored: Record<string, unknown> = STORED;
  vi.mocked(fetch).mockImplementation(() => Promise.resolve(json(stored)));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <NotificationsCard />
    </QueryClientProvider>,
  );
  expect(await screen.findByLabelText('Chat ID')).toHaveValue('-100500');

  // Another tab saved a new chat and switched notifications off.
  stored = { ...STORED, enabled: false, chat_id: '@other', updated_at: 'later' };
  await client.invalidateQueries();

  await waitFor(() => {
    expect(screen.getByLabelText('Chat ID')).toHaveValue('@other');
  });
  expect(screen.getByRole('switch', { name: 'Отправлять уведомления' })).not.toBeChecked();
  expect(screen.getByRole('button', { name: 'Сохранить уведомления' })).toBeDisabled();
});
