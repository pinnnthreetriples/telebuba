import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import {
  ago,
  ahead,
  callsTo,
  json,
  LIMITED,
  renderWithClient,
  routeApi,
} from './SpamCard.test-helpers';
import { SignalsSection } from './SignalsSection';

vi.mock('@/shared/ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/ui')>()),
  toastError: vi.fn(),
}));

const STATE = '/api/v1/accounts/acc-1/spam-appeal';
const CHECK = '/api/v1/accounts/acc-1/spam-check';

// A Spam-check can end the appeal (a stored @SpamBot clean closes it) or move its re-check; the
// appeal block reads its own query, so that query must be refetched, or the card keeps saying
// "filed" over the `closed` summary the accounts list already carries (audit F-021).
test('after a Spam-check the appeal block reads the appeal again instead of keeping a stale open one', async () => {
  let answered = false;
  const calls = routeApi({
    [`GET ${STATE}`]: () =>
      json(
        answered
          ? { account_id: 'acc-1', status: 'closed', filed_at: ago(5), filed_by: 'anna' }
          : {
              account_id: 'acc-1',
              status: 'open',
              filed_at: ago(5),
              filed_by: 'anna',
              recheck_at: ahead(10),
            },
      ),
    [`POST ${CHECK}`]: () => {
      answered = true;
      return json({
        account_id: 'acc-1',
        status: 'unknown',
        checked_at: 'now',
        served_from: 'probe',
        probed: true,
      });
    },
  });
  renderWithClient(
    <SignalsSection
      account={{ ...LIMITED, spam_appeal_status: 'open', spam_out_of_turn: false }}
    />,
  );
  // The open appeal shows its filing, not the "open the window" buttons.
  await screen.findByText(/anna/);
  expect(screen.queryByRole('button', { name: 'Подать апелляцию' })).toBeNull();

  await userEvent.click(screen.getByRole('button', { name: /Проверить/ }));

  await waitFor(() => {
    expect(callsTo(calls, 'GET', STATE).length).toBeGreaterThanOrEqual(2);
  });
  // The refetched `closed` replaces the stale `open`: the block offers the appeal again.
  expect(await screen.findByText('Подать апелляцию', {}, { timeout: 3000 })).toBeInTheDocument();
});
