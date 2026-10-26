// The "account already has a password — save it" field of the 2FA card: it posts to
// /2fa/record (Telegram verifies, nothing changes), refuses a wrong password inline
// and keeps the typed value. Harness: TwoFactorSection.test-helpers.tsx.

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';

import {
  TWOFA,
  jsonResponse,
  openCard,
  renderSection,
  requests,
  stubTwofa,
} from './TwoFactorSection.test-helpers';

const RECORD = `${TWOFA}/record`;
const LABEL = 'У аккаунта уже есть пароль — сохранить';
const SUBMIT = 'Проверить и сохранить';

test('a password on Telegram with no stored copy offers the record field', async () => {
  stubTwofa({ has_password: true }, { stored: false });
  renderSection();
  await openCard();

  expect(await screen.findByLabelText(LABEL)).toBeInTheDocument();
});

test('a stored password, or no password at all, does not offer it', async () => {
  stubTwofa({ has_password: true }, { stored: true });
  const first = renderSection();
  await openCard();
  await screen.findByText('Включён');
  expect(screen.queryByLabelText(LABEL)).not.toBeInTheDocument();
  first.unmount();

  stubTwofa({ has_password: false }, { stored: false });
  renderSection();
  await openCard();
  await screen.findByRole('button', { name: 'Включить 2FA' });
  expect(screen.queryByLabelText(LABEL)).not.toBeInTheDocument();
});

test('saving posts the typed password to /2fa/record and refreshes the card', async () => {
  stubTwofa(
    { has_password: true },
    {
      stored: false,
      routes: {
        [`POST ${RECORD}`]: () => jsonResponse({ status: null, has_stored_password: true }),
      },
    },
  );
  renderSection();
  await openCard();

  await userEvent.type(await screen.findByLabelText(LABEL), 'fake-pass-1234567');
  await userEvent.click(screen.getByRole('button', { name: SUBMIT }));

  await waitFor(() => {
    expect(requests(RECORD, 'POST')).toHaveLength(1);
  });
  const sent = requests(RECORD, 'POST')[0];
  expect(await sent?.clone().json()).toEqual({ password: 'fake-pass-1234567' });
  // The status is re-read so the card flips to its stored state.
  await waitFor(() => {
    expect(requests(TWOFA, 'GET').length).toBeGreaterThan(1);
  });
});

test('a wrong password is refused inline', async () => {
  stubTwofa(
    { has_password: true },
    {
      stored: false,
      routes: {
        [`POST ${RECORD}`]: () =>
          jsonResponse(
            { error: { code: 'bad_request', message: 'twofa_current_password_invalid' } },
            400,
          ),
      },
    },
  );
  renderSection();
  await openCard();

  await userEvent.type(await screen.findByLabelText(LABEL), 'wrong-password');
  await userEvent.click(screen.getByRole('button', { name: SUBMIT }));

  expect((await screen.findAllByText(/Telegram не принял текущий пароль/)).length).toBeGreaterThan(
    0,
  );
  expect(screen.getByLabelText(LABEL)).toHaveValue('wrong-password');
});

test('an empty field cannot be submitted', async () => {
  stubTwofa({ has_password: true }, { stored: false });
  renderSection();
  await openCard();

  await screen.findByLabelText(LABEL);
  expect(screen.getByRole('button', { name: SUBMIT })).toBeDisabled();
});
