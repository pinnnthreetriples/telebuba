import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { toastError } from '@/shared/ui';

import { SpamAppealControls } from './SpamAppealControls';
import {
  ago,
  ahead,
  callsTo,
  CLEAN,
  json,
  LIMITED,
  refusal,
  renderWithClient,
  routeApi,
} from './SpamCard.test-helpers';

vi.mock('@/shared/ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/ui')>()),
  toastError: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(toastError).mockClear();
});

const STATE = '/api/v1/accounts/acc-1/spam-appeal';
const OPEN = `${STATE}/open`;
const FILED = `${STATE}/filed`;
const NONE = { account_id: 'acc-1', status: 'none' };

function openState(filedAt: string, recheckAt: string) {
  return {
    account_id: 'acc-1',
    status: 'open',
    filed_at: filedAt,
    filed_by: 'anna',
    filed_verdict: 'limited',
    recheck_at: recheckAt,
  };
}

test('a limited account offers both buttons, a fixed hint and no input to type into', async () => {
  routeApi({ [`GET ${STATE}`]: () => json(NONE) });
  const { container } = renderWithClient(<SpamAppealControls account={LIMITED} />);

  expect(await screen.findByRole('button', { name: 'Подать апелляцию' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Апелляция подана' })).toBeEnabled();
  expect(screen.getByText(/Апелляцию подаёт оператор вручную/)).toBeInTheDocument();
  // Nothing on this block takes text: the operator types in the opened window, not here.
  expect(container.querySelectorAll('input, textarea, select, [contenteditable]')).toHaveLength(0);
  expect(screen.queryAllByRole('textbox')).toHaveLength(0);
});

test('"Подать апелляцию" only opens the window; it records and sends nothing else', async () => {
  const calls = routeApi({
    [`GET ${STATE}`]: () => json(NONE),
    [`POST ${OPEN}`]: () => json({ launched: true, signed_in: true }),
  });
  renderWithClient(<SpamAppealControls account={LIMITED} />);

  await userEvent.click(await screen.findByRole('button', { name: 'Подать апелляцию' }));

  await waitFor(() => {
    expect(callsTo(calls, 'POST', OPEN)).toHaveLength(1);
  });
  expect(callsTo(calls, 'POST', OPEN)[0]?.body).toBeNull();
  // Every other request is the read of the appeal itself: no "filed" record, no probe.
  const writes = calls.filter((call) => call.method !== 'GET');
  expect(writes.map((call) => call.path)).toEqual([OPEN]);
  expect(toastError).not.toHaveBeenCalled();
});

test('a window opened without a finished login says what to do in it', async () => {
  routeApi({
    [`GET ${STATE}`]: () => json(NONE),
    [`POST ${OPEN}`]: () =>
      json({ launched: true, signed_in: false, reason: 'web_login_2fa_required' }),
  });
  renderWithClient(<SpamAppealControls account={LIMITED} />);

  await userEvent.click(await screen.findByRole('button', { name: 'Подать апелляцию' }));

  await waitFor(() => {
    expect(toastError).toHaveBeenCalledWith(expect.stringContaining('ждёт пароль 2FA'));
  });
});

test('without a proxy the open button is disabled and says why', async () => {
  const calls = routeApi({ [`GET ${STATE}`]: () => json(NONE) });
  renderWithClient(<SpamAppealControls account={{ ...LIMITED, proxy_id: null }} />);

  const open = await screen.findByRole('button', { name: 'Подать апелляцию' });
  expect(open).toBeDisabled();
  expect(screen.getByText(/Нужен прокси: окно открывается через прокси аккаунта/)).toBeVisible();
  await userEvent.click(open);
  expect(callsTo(calls, 'POST', OPEN)).toHaveLength(0);
});

test('"Апелляция подана" asks first, and records nothing until it is confirmed', async () => {
  const filedAt = ago(0);
  const calls = routeApi({
    [`GET ${STATE}`]: () => json(NONE),
    [`POST ${FILED}`]: () => json(openState(filedAt, ahead(24))),
  });
  renderWithClient(<SpamAppealControls account={LIMITED} />);

  await userEvent.click(await screen.findByRole('button', { name: 'Апелляция подана' }));
  expect(await screen.findByText('Апелляция подана?')).toBeInTheDocument();
  expect(callsTo(calls, 'POST', FILED)).toHaveLength(0);

  await userEvent.click(screen.getByRole('button', { name: 'Да, подана' }));

  await waitFor(() => {
    expect(callsTo(calls, 'POST', FILED)).toHaveLength(1);
  });
  expect(callsTo(calls, 'POST', FILED)[0]?.body).toBeNull();
  // The card now reads who filed it and when it is re-checked, from the answer itself.
  expect(await screen.findByText(/Подана .*, anna/)).toBeInTheDocument();
  expect(screen.getByText(/Повторная проверка:/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Подать апелляцию' })).toBeNull();
});

test('declining the confirmation records nothing', async () => {
  const calls = routeApi({ [`GET ${STATE}`]: () => json(NONE) });
  renderWithClient(<SpamAppealControls account={LIMITED} />);

  await userEvent.click(await screen.findByRole('button', { name: 'Апелляция подана' }));
  await userEvent.click(await screen.findByRole('button', { name: 'Ещё нет' }));

  expect(screen.queryByText('Апелляция подана?')).toBeNull();
  expect(callsTo(calls, 'POST', FILED)).toHaveLength(0);
});

test('a refused record is a toast with the refusal, and the buttons stay', async () => {
  routeApi({
    [`GET ${STATE}`]: () => json(NONE),
    [`POST ${FILED}`]: () => refusal(400, 'bad_request', 'spam_appeal_not_limited'),
  });
  renderWithClient(<SpamAppealControls account={LIMITED} />);

  await userEvent.click(await screen.findByRole('button', { name: 'Апелляция подана' }));
  await userEvent.click(await screen.findByRole('button', { name: 'Да, подана' }));

  await waitFor(() => {
    expect(toastError).toHaveBeenCalledWith(expect.stringContaining('нет спам-блока'));
  });
  expect(screen.getByRole('button', { name: 'Подать апелляцию' })).toBeInTheDocument();
});

test('a filed appeal shows who, when and the re-check time, and no more buttons', async () => {
  routeApi({ [`GET ${STATE}`]: () => json(openState(ago(5), ahead(19))) });
  renderWithClient(
    <SpamAppealControls
      account={{
        ...LIMITED,
        spam_appeal_status: 'open',
        spam_appeal_filed_at: ago(5),
        spam_appeal_filed_by: 'anna',
      }}
    />,
  );

  expect(await screen.findByText(/Подана .*, anna/)).toBeInTheDocument();
  expect(await screen.findByText(/Повторная проверка:/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Подать апелляцию' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Апелляция подана' })).toBeNull();
  // The hint stays: the appeal is still something the operator does by hand.
  expect(screen.getByText(/Апелляцию подаёт оператор вручную/)).toBeInTheDocument();
});

test('a clean account with no appeal shows no appeal block at all', () => {
  const calls = routeApi({});
  renderWithClient(<SpamAppealControls account={CLEAN} />);
  expect(screen.queryByTestId('spam-appeal')).toBeNull();
  // And it does not even read the appeal: nothing to show.
  expect(calls).toHaveLength(0);
});

test('an appeal that ended in a clean answer is history, with no button', () => {
  routeApi({});
  renderWithClient(
    <SpamAppealControls
      account={{
        ...CLEAN,
        spam_appeal_status: 'closed',
        spam_appeal_filed_at: ago(48),
        spam_appeal_filed_by: 'anna',
      }}
    />,
  );
  expect(screen.getByText(/Последняя апелляция подана .*, anna; ограничение снято/)).toBeVisible();
  expect(screen.queryByRole('button')).toBeNull();
});

test('a new limit after a closed appeal offers a fresh one', async () => {
  routeApi({ [`GET ${STATE}`]: () => json({ account_id: 'acc-1', status: 'closed' }) });
  renderWithClient(
    <SpamAppealControls
      account={{ ...LIMITED, spam_appeal_status: 'closed', spam_appeal_filed_by: 'anna' }}
    />,
  );
  expect(await screen.findByRole('button', { name: 'Подать апелляцию' })).toBeEnabled();
});
