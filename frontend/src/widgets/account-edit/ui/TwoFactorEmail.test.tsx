// The recovery-email half of the 2FA card, rendered through TwoFactorSection because
// TwoFactorEmail is only ever mounted by it (and only when the password is stored
// here). Split from TwoFactorSection.test.tsx for the 700-line test-source cap.

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';

import {
  PENDING,
  TWOFA,
  jsonResponse,
  openCard,
  renderSection,
  requests,
  stubApi,
  stubTwofa,
  urls,
  viewResponse,
} from './TwoFactorSection.test-helpers';

test('the unattached email state offers an address input and states the trade', async () => {
  stubTwofa({ has_password: true, has_recovery: false });
  renderSection();
  await openCard();

  expect(await screen.findByLabelText('Адрес почты')).toBeInTheDocument();
  expect(screen.getByText(/сможет сбросить пароль и забрать аккаунт/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Привязать почту' })).toBeInTheDocument();
  expect(screen.queryByLabelText('Код из письма')).not.toBeInTheDocument();
});

test('the attach response drives the pending state, and its code length outlives the refetch', async () => {
  // Two phases, because the bug lived between them. Phase one: the status GET still
  // reports nothing pending, so everything on screen comes from the write's RESPONSE —
  // the operator gets the code field immediately instead of waiting a round trip to be
  // told what they just did. Phase two: the status starts reporting the masked pattern,
  // which flips this component's key and REMOUNTS it. The override carrying the code
  // length dies there, so the length has to be held by the parent — otherwise the
  // number plumbed through three backend layers survives exactly one refetch.
  let reportPending = false;
  stubApi((request) => {
    const { pathname } = new URL(request.url);
    if (pathname === `${TWOFA}/email` && request.method === 'POST') {
      return jsonResponse({ pending: true, code_length: 6 });
    }
    if (pathname === TWOFA) {
      return viewResponse(reportPending ? PENDING : { has_password: true, has_recovery: false });
    }
    return undefined;
  });
  const { queryClient } = renderSection();
  await openCard();

  await userEvent.type(await screen.findByLabelText('Адрес почты'), 'ops@example.com');
  await userEvent.click(screen.getByRole('button', { name: 'Привязать почту' }));

  const code = await screen.findByLabelText('Код из письма');
  expect(code).toHaveAttribute('maxlength', '6');
  expect(code).toHaveAttribute('autocomplete', 'one-time-code');
  expect(screen.getByText('Код отправлен на ops@example.com')).toBeInTheDocument();
  expect(await requests(`${TWOFA}/email`, 'POST')[0]!.clone().json()).toEqual({
    email: 'ops@example.com',
  });
  for (const url of urls()) expect(url).not.toContain('ops@example.com');

  reportPending = true;
  await queryClient.invalidateQueries();

  expect(await screen.findByText('Код отправлен на o**@example.com')).toBeInTheDocument();
  expect(screen.getByLabelText('Код из письма')).toHaveAttribute('maxlength', '6');
});

test('a confirmed address AND a newly pending one both stay actionable', async () => {
  // Telegram reports both whenever the operator swaps the recovery address from the
  // app. `has_recovery` used to win the branch, so the card showed only
  // "attached / Detach" and the pending verification could never be completed here.
  stubTwofa({
    has_password: true,
    has_recovery: true,
    email_unconfirmed_pattern: 'n**@example.com',
  });
  renderSection();
  await openCard();

  expect(await screen.findByText('Резервная почта: привязана')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Отвязать' })).toBeInTheDocument();
  expect(screen.getByText('Код отправлен на n**@example.com')).toBeInTheDocument();
  expect(screen.getByLabelText('Код из письма')).toBeInTheDocument();
  // The attach form stays away: there is nothing to attach in either state.
  expect(screen.queryByLabelText('Адрес почты')).not.toBeInTheDocument();
});

test('a wrong code keeps the input mounted with what was typed', async () => {
  stubTwofa(PENDING, {
    routes: {
      [`POST ${TWOFA}/email/confirm`]: () =>
        jsonResponse(
          { error: { code: 'invalid_request', message: 'twofa_email_code_invalid' } },
          400,
        ),
    },
  });
  renderSection();
  await openCard();

  expect(await screen.findByText('Код отправлен на o**@example.com')).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText('Код из письма'), '123456');
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));

  await waitFor(() => {
    expect(requests(`${TWOFA}/email/confirm`, 'POST')).toHaveLength(1);
  });
  // Retyping the address after a mistyped digit would be a second round trip.
  expect(screen.getByLabelText('Код из письма')).toHaveValue('123456');
  for (const url of urls()) expect(url).not.toContain('123456');
});

test('a good code confirms the address', async () => {
  stubTwofa(PENDING, {
    routes: {
      [`POST ${TWOFA}/email/confirm`]: () => viewResponse({ has_password: true }),
    },
  });
  renderSection();
  await openCard();

  await userEvent.type(await screen.findByLabelText('Код из письма'), '654321');
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));

  await waitFor(() => {
    expect(requests(`${TWOFA}/email/confirm`, 'POST')).toHaveLength(1);
  });
  expect(await requests(`${TWOFA}/email/confirm`, 'POST')[0]!.clone().json()).toEqual({
    code: '654321',
  });
});

test('resend mails the code again and does not confirm anything', async () => {
  stubTwofa(PENDING, {
    routes: {
      [`POST ${TWOFA}/email/resend`]: () => jsonResponse({ pending: true, code_length: null }),
    },
  });
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByRole('button', { name: 'Отправить заново' }));

  await waitFor(() => {
    expect(requests(`${TWOFA}/email/resend`, 'POST')).toHaveLength(1);
  });
  expect(requests(`${TWOFA}/email/confirm`, 'POST')).toHaveLength(0);
  expect(screen.getByLabelText('Код из письма')).toBeInTheDocument();
});

test('cancelling a pending address asks first, then DELETEs it', async () => {
  stubTwofa(PENDING, {
    routes: { [`DELETE ${TWOFA}/email`]: () => viewResponse({ has_password: true }) },
  });
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByRole('button', { name: 'Отменить' }));
  expect(await screen.findByText('Отменить привязку почты?')).toBeInTheDocument();
  expect(requests(`${TWOFA}/email`, 'DELETE')).toHaveLength(0);

  await userEvent.click(screen.getByRole('button', { name: 'Отменить привязку' }));
  await waitFor(() => {
    expect(requests(`${TWOFA}/email`, 'DELETE')).toHaveLength(1);
  });
});

test('a CONFIRMED recovery email is detached through the clear route, not cancel', async () => {
  // `cancelPasswordEmail` only abandons a verification still in flight; a confirmed
  // address comes off with `updatePasswordSettings` and an empty email, which is a
  // different endpoint. Firing the cancel one here would do nothing at all.
  stubTwofa(
    { has_password: true, has_recovery: true },
    {
      routes: {
        [`DELETE ${TWOFA}/email/recovery`]: () => viewResponse({ has_password: true }),
      },
    },
  );
  renderSection();
  await openCard();

  expect(await screen.findByText('Резервная почта: привязана')).toBeInTheDocument();
  expect(screen.queryByLabelText('Адрес почты')).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Отвязать' }));
  expect(await screen.findByText('Отвязать резервную почту?')).toBeInTheDocument();
  expect(requests(`${TWOFA}/email/recovery`, 'DELETE')).toHaveLength(0);

  await userEvent.click(screen.getByRole('button', { name: 'Отвязать почту' }));
  await waitFor(() => {
    expect(requests(`${TWOFA}/email/recovery`, 'DELETE')).toHaveLength(1);
  });
  expect(requests(`${TWOFA}/email`, 'DELETE')).toHaveLength(0);
});
