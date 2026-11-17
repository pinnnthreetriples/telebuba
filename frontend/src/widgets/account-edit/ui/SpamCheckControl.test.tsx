import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';
import { toastError } from '@/shared/ui';

import {
  ago,
  callsTo,
  CLEAN,
  json,
  refusal,
  renderWithClient,
  routeApi,
} from './SpamCard.test-helpers';
import { SpamCheckControl } from './SpamCheckControl';

vi.mock('@/shared/ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/ui')>()),
  toastError: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(toastError).mockClear();
});

const CHECK = '/api/v1/accounts/acc-1/spam-check';
const CLEAN_ANSWER = {
  account_id: 'acc-1',
  status: 'clean',
  checked_at: 'now',
  served_from: 'probe',
  probed: true,
};

function render(account: AccountRead, onResult = vi.fn()) {
  renderWithClient(<SpamCheckControl account={account} onResult={onResult} />);
  return onResult;
}

test('the button shows how long ago the account was last probed', () => {
  routeApi({});
  render({ ...CLEAN, spam_last_probe_at: ago(3) });
  expect(screen.getByTestId('spam-last-probe')).toHaveTextContent('3 часа назад');
  // The tooltip repeats it, so a screen reader hears it with the button.
  const button = screen.getByRole('button', { name: /Проверить/ });
  const tip = document.getElementById(button.getAttribute('aria-describedby') ?? '');
  expect(tip).toHaveTextContent('последний запрос: 3 часа назад');
});

test('an account never probed shows no age', () => {
  routeApi({});
  render(CLEAN);
  expect(screen.queryByTestId('spam-last-probe')).toBeNull();
  expect(screen.getByRole('button', { name: 'Проверить' })).toBeInTheDocument();
});

test('a click in turn sends the check at once and without a confirmation', async () => {
  const calls = routeApi({ [`POST ${CHECK}`]: () => json(CLEAN_ANSWER) });
  const onResult = render({ ...CLEAN, spam_last_probe_at: ago(30), spam_out_of_turn: false });

  await userEvent.click(screen.getByRole('button', { name: /Проверить/ }));

  await waitFor(() => {
    expect(callsTo(calls, 'POST', CHECK)).toHaveLength(1);
  });
  expect(callsTo(calls, 'POST', CHECK)[0]?.body).toBeNull();
  expect(screen.queryByText('Проверка вне очереди')).toBeNull();
  // A real answer clears the held message the previous click may have left.
  expect(onResult).toHaveBeenCalledWith(null);
});

test('out of turn the click asks first, and sends nothing until the operator confirms', async () => {
  const calls = routeApi({ [`POST ${CHECK}`]: () => json(CLEAN_ANSWER) });
  render({ ...CLEAN, spam_last_probe_at: ago(3), spam_out_of_turn: true });

  await userEvent.click(screen.getByRole('button', { name: /Проверить/ }));

  expect(await screen.findByText('Проверка вне очереди')).toBeInTheDocument();
  expect(screen.getByText(/Этот аккаунт опрашивали 3 часа назад/)).toBeInTheDocument();
  expect(callsTo(calls, 'POST', CHECK)).toHaveLength(0);

  await userEvent.click(screen.getByRole('button', { name: 'Проверить сейчас' }));

  await waitFor(() => {
    expect(callsTo(calls, 'POST', CHECK)).toHaveLength(1);
  });
  expect(callsTo(calls, 'POST', CHECK)[0]?.body).toEqual({ confirm_out_of_turn: true });
  await waitFor(() => {
    expect(screen.queryByText('Проверка вне очереди')).toBeNull();
  });
});

test('cancelling the out-of-turn dialog sends nothing', async () => {
  const calls = routeApi({});
  render({ ...CLEAN, spam_last_probe_at: ago(3), spam_out_of_turn: true });

  await userEvent.click(screen.getByRole('button', { name: /Проверить/ }));
  await userEvent.click(await screen.findByRole('button', { name: 'Отмена' }));

  expect(screen.queryByText('Проверка вне очереди')).toBeNull();
  expect(callsTo(calls, 'POST', CHECK)).toHaveLength(0);
});

test('a stale in-turn flag is caught by the server and turned into the same dialog', async () => {
  let answers = 0;
  const calls = routeApi({
    [`POST ${CHECK}`]: () => {
      answers += 1;
      return answers === 1 ? refusal(409, 'conflict', 'needs_confirmation') : json(CLEAN_ANSWER);
    },
  });
  render({ ...CLEAN, spam_out_of_turn: false });

  await userEvent.click(screen.getByRole('button', { name: /Проверить/ }));
  expect(await screen.findByText('Проверка вне очереди')).toBeInTheDocument();
  // The refusal is answered by the dialog, not by a toast on top of it.
  expect(toastError).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole('button', { name: 'Проверить сейчас' }));
  await waitFor(() => {
    expect(callsTo(calls, 'POST', CHECK)).toHaveLength(2);
  });
  expect(callsTo(calls, 'POST', CHECK)[1]?.body).toEqual({ confirm_out_of_turn: true });
});

test('a held answer is handed to the card with its reason instead of an empty result', async () => {
  const held = {
    account_id: 'acc-1',
    status: 'clean',
    checked_at: 'now',
    served_from: 'deferred',
    reason: 'proxy_spacing',
    retry_at: '2026-07-01T10:00:00+00:00',
    probed: false,
  };
  routeApi({ [`POST ${CHECK}`]: () => json(held) });
  const onResult = render(CLEAN);

  await userEvent.click(screen.getByRole('button', { name: 'Проверить' }));

  await waitFor(() => {
    expect(onResult).toHaveBeenCalledWith(expect.objectContaining(held));
  });
});

test('any other refusal is a toast and no dialog', async () => {
  routeApi({ [`POST ${CHECK}`]: () => refusal(404, 'not_found', 'account not found') });
  render(CLEAN);

  await userEvent.click(screen.getByRole('button', { name: 'Проверить' }));

  await waitFor(() => {
    expect(toastError).toHaveBeenCalledTimes(1);
  });
  expect(screen.queryByText('Проверка вне очереди')).toBeNull();
});
