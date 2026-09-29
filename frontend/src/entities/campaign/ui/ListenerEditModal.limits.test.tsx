import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ListenerEditModal } from './ListenerEditModal';

// The "Лимиты" tab: the fleet-wide limits moved here from the Settings page. Its own file
// because `ListenerEditModal.test.tsx` already covers the account + comment-mode tab.

const SETTINGS = {
  max_comments_per_hour: 10,
  max_comments_per_channel_per_day: 3,
  reply_delay_min_seconds: 3,
  reply_delay_max_seconds: 10,
  min_trust_score: 45,
  comment_mode: 'first',
  reply_wait_minutes: 10,
  updated_at: 'now',
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function puts(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.method === 'PUT');
}

async function openLimitsTab(stored: Partial<typeof SETTINGS> = {}) {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(jsonResponse({ ...SETTINGS, ...stored })),
  );
  const onClose = vi.fn();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ListenerEditModal
        options={[{ id: 'a1', name: 'Ivan Petrov' }]}
        selected="a1"
        onClose={onClose}
        onSave={vi.fn(async () => true)}
      />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole('tab', { name: 'Лимиты' }));
  await waitFor(() => {
    expect(screen.getByRole('textbox', { name: 'Мин. trust-score для работы' })).toBeEnabled();
  });
  return { onClose };
}

const field = (name: string) => screen.getByRole('textbox', { name });

test('the limits tab shows the stored fleet limits', async () => {
  await openLimitsTab();

  expect(screen.getByRole('tab', { name: 'Лимиты' })).toHaveAttribute('aria-selected', 'true');
  expect(field('Комментариев в день на канал')).toHaveValue('3');
  expect(field('Задержка ответа, от (сек)')).toHaveValue('3');
  expect(field('Задержка ответа, до (сек)')).toHaveValue('10');
  expect(field('Комментариев в час (на аккаунт)')).toHaveValue('10');
  expect(field('Мин. trust-score для работы')).toHaveValue('45');
});

test('a changed limit is sent alone', async () => {
  const { onClose } = await openLimitsTab();

  await userEvent.clear(field('Мин. trust-score для работы'));
  await userEvent.type(field('Мин. trust-score для работы'), '60');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(puts()).toHaveLength(1);
  });
  expect(await puts()[0]!.clone().json()).toEqual({ min_trust_score: 60 });
  await waitFor(() => {
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

test('one changed delay sends the pair, which the backend checks together', async () => {
  await openLimitsTab();

  await userEvent.clear(field('Задержка ответа, до (сек)'));
  await userEvent.type(field('Задержка ответа, до (сек)'), '20.5');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(puts()).toHaveLength(1);
  });
  expect(await puts()[0]!.clone().json()).toEqual({
    reply_delay_min_seconds: 3,
    reply_delay_max_seconds: 20.5,
  });
});

// The old Settings form required 1–100 and whole seconds up to 3600, so a stored 0 ("no
// cap") or 2.5 made it invalid on open and Save stayed disabled for good.
test('values the backend accepts are valid here too: 0 and a fractional delay', async () => {
  const { onClose } = await openLimitsTab({
    max_comments_per_channel_per_day: 0,
    reply_delay_min_seconds: 2.5,
    reply_delay_max_seconds: 7200,
  });

  expect(field('Комментариев в день на канал')).toHaveValue('0');
  expect(field('Задержка ответа, от (сек)')).toHaveValue('2.5');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(onClose).toHaveBeenCalledTimes(1);
  });
  expect(puts()).toHaveLength(0); // nothing changed, nothing sent
});

test('an invalid limit blocks the save and brings the operator back to it', async () => {
  const { onClose } = await openLimitsTab();

  await userEvent.clear(field('Комментариев в час (на аккаунт)'));
  await userEvent.type(field('Комментариев в час (на аккаунт)'), '0');
  expect(screen.getByText('Введите целое число от 1')).toBeInTheDocument();
  expect(field('Комментариев в час (на аккаунт)')).toHaveAttribute('aria-invalid', 'true');

  // From the other tab, Save must not quietly drop the broken draft or send it.
  await userEvent.click(screen.getByRole('tab', { name: 'Комментирование' }));
  await userEvent.click(screen.getByText('Сохранить'));

  expect(screen.getByRole('tab', { name: 'Лимиты' })).toHaveAttribute('aria-selected', 'true');
  expect(puts()).toHaveLength(0);
  expect(onClose).not.toHaveBeenCalled();
});

test('a "to" below "from" is an error on "to"', async () => {
  await openLimitsTab();

  await userEvent.clear(field('Задержка ответа, до (сек)'));
  await userEvent.type(field('Задержка ответа, до (сек)'), '1');

  const error = screen.getByText('«До» должно быть не меньше «от»');
  expect(field('Задержка ответа, до (сек)')).toHaveAttribute('aria-describedby', error.id);
  expect(field('Задержка ответа, от (сек)')).not.toHaveAttribute('aria-invalid');
});

test('an edited limit survives a tab switch and is thrown away by cancel', async () => {
  const { onClose } = await openLimitsTab();

  await userEvent.clear(field('Мин. trust-score для работы'));
  await userEvent.type(field('Мин. trust-score для работы'), '70');
  await userEvent.click(screen.getByRole('tab', { name: 'Комментирование' }));
  await userEvent.click(screen.getByRole('tab', { name: 'Лимиты' }));
  expect(field('Мин. trust-score для работы')).toHaveValue('70');

  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(puts()).toHaveLength(0);
});
