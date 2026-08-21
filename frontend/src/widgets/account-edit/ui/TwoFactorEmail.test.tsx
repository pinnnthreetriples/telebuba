// The recovery-email half of the 2FA card, rendered through TwoFactorSection because
// TwoFactorEmail is only ever mounted by it (and only when the password is stored
// here). Split from TwoFactorSection.test.tsx for the 700-line test-source cap.

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import {
  PENDING,
  TWOFA,
  jsonResponse,
  openCard,
  renderSection,
  requests,
  stubApi,
  stubTwofa,
  toastMessages,
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

test('the recovery state is stated once, not twice', async () => {
  // The card's summary row and the email section's own header both said
  // "Резервная почта: не привязана", one under the other; the runtime auditor read the
  // pair as a duplicate. The email leg owns the row now — in every state it can
  // honestly claim, so nothing was lost with the summary row.
  stubTwofa({ has_password: true, has_recovery: false });
  renderSection();
  await openCard();

  expect(await screen.findByLabelText('Адрес почты')).toBeInTheDocument();
  expect(screen.getAllByText(/Резервная почта/)).toHaveLength(1);
});

test('a code typed for an abandoned address does not prefill the next one', async () => {
  // The typed code is lifted to the parent so a status refetch cannot wipe it, and the
  // cost is that it outlives the address it was typed for: Telegram drops the pending
  // verification (expired, or cancelled from the phone), the card falls back to the
  // attach form, and the next attach comes up with Confirm already enabled over a code
  // that can only be refused.
  let pending = true;
  stubApi((request) => {
    const { pathname } = new URL(request.url);
    if (pathname === `${TWOFA}/email` && request.method === 'POST') {
      return jsonResponse({ pending: true, code_length: 6 });
    }
    if (pathname === TWOFA) {
      return viewResponse(pending ? PENDING : { has_password: true, has_recovery: false });
    }
    return undefined;
  });
  const { queryClient } = renderSection();
  await openCard();

  await userEvent.type(await screen.findByLabelText('Код из письма'), '111111');
  pending = false;
  await queryClient.invalidateQueries();

  await userEvent.type(await screen.findByLabelText('Адрес почты'), 'new@example.com');
  await userEvent.click(screen.getByRole('button', { name: 'Привязать почту' }));

  expect(await screen.findByLabelText('Код из письма')).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Подтвердить' })).toBeDisabled();
});

test('a pending address stays finishable after the live read starts failing', async () => {
  // Confirm, resend and cancel need neither the live read nor a stored password, so a
  // pending address must not become unreachable when the read fails. The reachable
  // shape is a read that HAD succeeded and then started failing — react-query keeps
  // the last good data beside the error; the envelope branch (`error` set) carries no
  // status at all, by construction in `_live_status`.
  let fail = false;
  stubApi((request) => {
    const { pathname } = new URL(request.url);
    if (pathname !== TWOFA) return undefined;
    return fail
      ? jsonResponse({ error: { code: 'upstream_error', message: 'twofa_state_unreadable' } }, 503)
      : viewResponse(PENDING);
  });
  const { queryClient } = renderSection();
  await openCard();

  expect(await screen.findByText('Код отправлен на o**@example.com')).toBeInTheDocument();
  fail = true;
  await queryClient.invalidateQueries();

  expect(await screen.findByText(/Состояние 2FA не прочитано/)).toBeInTheDocument();
  expect(screen.getByLabelText('Код из письма')).toBeInTheDocument();
  for (const name of ['Подтвердить', 'Отправить заново', 'Отменить']) {
    expect(screen.getByRole('button', { name })).toBeInTheDocument();
  }
  // Whether an address is CONFIRMED is not knowable here, so neither claim is printed.
  expect(screen.queryByText(/Резервная почта/)).not.toBeInTheDocument();
  // And still nothing that writes a password against a state we could not read.
  expect(screen.queryByRole('button', { name: 'Сменить пароль' })).not.toBeInTheDocument();
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

  // Typed BEFORE the refetch, because that is the whole point: the operator reads
  // the letter and types while the status query is still catching up, and the
  // remount that refetch causes must not eat what they typed. With an empty field
  // this test could not tell a surviving input from a blanked one — which is how
  // the wiped code shipped.
  await userEvent.type(code, '654321');
  reportPending = true;
  await queryClient.invalidateQueries();

  expect(await screen.findByText('Код отправлен на o**@example.com')).toBeInTheDocument();
  expect(screen.getByLabelText('Код из письма')).toHaveAttribute('maxlength', '6');
  expect(screen.getByLabelText('Код из письма')).toHaveValue('654321');
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
  // The kept input is only half of it: the global mutation toast is the only thing
  // that says WHY the code was rejected, and without it this test passed while the
  // operator was told nothing at all.
  expect(await toastMessages()).toContain('Неверный код из письма. Проверьте и введите заново.');
});

test('a good code renders the attached state from the response, not from a refetch', async () => {
  // The confirm response already carries the fresh AccountTwoFactorView, and it used
  // to be discarded: the card nulled its pending override while the `hasRecovery`
  // prop was still the stale `false`, so for one whole `account.getPassword` round
  // trip it invited the operator to attach the address they had just confirmed.
  // Asserting only that the POST fired is how that shipped.
  //
  // The status GET deliberately hangs after the first read, so nothing on screen can
  // come from a refetch — only from the response.
  const firstRead = viewResponse(PENDING);
  const afterConfirm = viewResponse({ has_password: true, has_recovery: true });
  let statusReads = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === `${TWOFA}/email/confirm`) return Promise.resolve(afterConfirm.clone());
    if (pathname === TWOFA) {
      statusReads += 1;
      return statusReads === 1
        ? Promise.resolve(firstRead)
        : new Promise<Response>(() => {
            // never settles
          });
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
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
  expect(await screen.findByText('Резервная почта: привязана')).toBeInTheDocument();
  // Never an invitation to attach what was just confirmed.
  expect(screen.queryByLabelText('Адрес почты')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Код из письма')).not.toBeInTheDocument();
});

test('a pending address stays visible and confirmable with the password not stored', async () => {
  // Reachable when the password was set from the phone, after a `previous_kept`
  // change, or after a failed store. Only attach and detach need the stored password
  // to authorise them — confirm, resend and cancel go straight through on the
  // backend, so hiding the whole leg hid a pending address the operator could then
  // neither see nor finish.
  //
  // The status GET hangs after the first read, so everything asserted after the
  // click can only have come from the confirm RESPONSE. Counting the POST and
  // stopping there is the blind spot that hid the stale-`hasRecovery` bug in round 2:
  // it cannot see what the card renders next.
  const firstRead = viewResponse(PENDING, false);
  const afterConfirm = viewResponse({ has_password: true, has_recovery: true }, false);
  let statusReads = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === `${TWOFA}/email/confirm`) return Promise.resolve(afterConfirm.clone());
    if (pathname === TWOFA) {
      statusReads += 1;
      return statusReads === 1
        ? Promise.resolve(firstRead)
        : new Promise<Response>(() => {
            // never settles
          });
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderSection();
  await openCard();

  expect(await screen.findByText('Код отправлен на o**@example.com')).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText('Код из письма'), '654321');
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));

  await waitFor(() => {
    expect(requests(`${TWOFA}/email/confirm`, 'POST')).toHaveLength(1);
  });
  // The address is attached, the code field is gone, and Detach — the one email
  // control the missing password really does block — is disabled rather than absent.
  expect(await screen.findByText('Резервная почта: привязана')).toBeInTheDocument();
  expect(screen.queryByLabelText('Код из письма')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Отвязать' })).toBeDisabled();
});

test('without a stored password detach is disabled rather than hidden', async () => {
  stubTwofa({ has_password: true, has_recovery: true }, { stored: false });
  renderSection();
  await openCard();

  expect(await screen.findByText('Резервная почта: привязана')).toBeInTheDocument();
  // `clear` authorises with the stored current password, so the backend refuses it —
  // but the operator still has to be able to SEE that an address is attached.
  expect(screen.getByRole('button', { name: 'Отвязать' })).toBeDisabled();
});

test('an address that cannot be an email is refused inline, not by a 422', async () => {
  // The backend enforces `.+@.+` and 254 characters; a 422 comes back as
  // `validation_error`, which resolves through no `shell.code.*` entry, so the
  // operator would get FastAPI prose or the generic fallback instead of the inline
  // error every other form here gives.
  stubTwofa({ has_password: true, has_recovery: false });
  renderSection();
  await openCard();

  const address = await screen.findByLabelText('Адрес почты');
  expect(address).toHaveAttribute('maxlength', '254');
  await userEvent.type(address, 'ops.example.com');

  expect(screen.getByText(/Нужен адрес вида/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Привязать почту' })).toBeDisabled();
  expect(requests(`${TWOFA}/email`, 'POST')).toHaveLength(0);
});

test('the code field is clamped even when no attach response said how long it is', async () => {
  // `code_length` exists only in the attach reply, so after a reload or a reopened
  // card the pattern comes from the status and the length is unknown — the server
  // still bounds the code to 32.
  stubTwofa(PENDING);
  renderSection();
  await openCard();

  expect(await screen.findByLabelText('Код из письма')).toHaveAttribute('maxlength', '32');
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

test('a detached address is rendered detached from the response, not from a refetch', async () => {
  // `clear` answers with a whole fresh AccountTwoFactorView, and discarding it in
  // favour of the refetch left the card saying "attached" with an ENABLED Detach for
  // one live `account.getPassword` round trip — where a second click fires a `clear`
  // that can only refuse. Same defect the confirm path had, same fix.
  //
  // The status GET hangs after the first read, so nothing asserted below can have come
  // from a refetch.
  const firstRead = viewResponse({ has_password: true, has_recovery: true });
  const afterClear = viewResponse({ has_password: true, has_recovery: false });
  let statusReads = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === `${TWOFA}/email/recovery`) return Promise.resolve(afterClear.clone());
    if (pathname === TWOFA) {
      statusReads += 1;
      return statusReads === 1
        ? Promise.resolve(firstRead)
        : new Promise<Response>(() => {
            // never settles
          });
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByRole('button', { name: 'Отвязать' }));
  await userEvent.click(screen.getByRole('button', { name: 'Отвязать почту' }));

  expect(await screen.findByText('Резервная почта: не привязана')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Отвязать' })).not.toBeInTheDocument();
  // The attach form is back, which is the only honest offer once nothing is attached.
  expect(screen.getByLabelText('Адрес почты')).toBeInTheDocument();
});
