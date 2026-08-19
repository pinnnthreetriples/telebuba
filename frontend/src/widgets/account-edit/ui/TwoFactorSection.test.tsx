import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead, TwoFactorStatusResult } from '@/shared/api';

import { TwoFactorSection } from './TwoFactorSection';

// Its own file rather than more of AccountEdit.test.tsx, which is already at
// ~643 lines against the 700-line cap.

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  label: 'Main',
  status: 'alive',
  phone: '+79051184490',
  created_at: 'now',
  updated_at: 'now',
};

const TWOFA = '/api/v1/accounts/acc-1/2fa';
const TITLE = 'Облачный пароль (2FA)';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

// GET /2fa answers the live status plus whether OUR copy of the password exists.
function viewResponse(
  status: TwoFactorStatusResult | null,
  hasStoredPassword = true,
  error: string | null = null,
): Response {
  return jsonResponse({ status, has_stored_password: hasStoredPassword, error });
}

// Anything the route does not answer falls through to an empty page (the
// accounts list / proxy list an invalidation refetches).
function stubApi(route: (request: Request) => Response | undefined): void {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    return Promise.resolve(route(request) ?? jsonResponse({ items: [], next_cursor: null }));
  });
}

// The switch every test in here needs: GET /2fa answers `status`, and `routes`
// overrides one `"<METHOD> <pathname>"` at a time. Thunks, not Responses — a body
// can only be read once, and a refetch hits the same key again.
function stubTwofa(
  status: TwoFactorStatusResult | null,
  {
    stored = true,
    error = null,
    routes = {},
  }: {
    stored?: boolean;
    error?: string | null;
    routes?: Record<string, () => Response>;
  } = {},
): void {
  stubApi((request) => {
    const { pathname } = new URL(request.url);
    const route = routes[`${request.method} ${pathname}`];
    if (route) return route();
    return pathname === TWOFA ? viewResponse(status, stored, error) : undefined;
  });
}

// 2FA on, no confirmed recovery address, one pending and waiting for its code.
const PENDING: TwoFactorStatusResult = {
  has_password: true,
  has_recovery: false,
  email_unconfirmed_pattern: 'o**@example.com',
};

function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <TwoFactorSection account={ACCOUNT} />
      </QueryClientProvider>,
    ),
  };
}

// Every card is collapsed by default and a collapsed body is `hidden`, so the
// controls do not exist for a role query until the title is clicked.
async function openCard(): Promise<void> {
  await userEvent.click(screen.getByText(TITLE));
}

function requests(pathname: string, method?: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter(
      (request) =>
        new URL(request.url).pathname === pathname && (!method || request.method === method),
    );
}

function urls(): string[] {
  return vi.mocked(fetch).mock.calls.map(([input]) => (input as Request).url);
}

test('the header pill answers on/off before the card is opened', async () => {
  stubTwofa({ has_password: true });
  renderSection();

  expect(await screen.findByText('Включён')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Включить 2FA' })).not.toBeInTheDocument();
});

test('a failed read offers nothing to write', async () => {
  // A write against an account whose live state could not be read is a guess.
  stubTwofa(null, { stored: false, error: 'twofa_password_not_set' });
  renderSection();
  await openCard();

  expect(await screen.findByText(/Состояние 2FA не прочитано/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Включить 2FA' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Сменить пароль' })).not.toBeInTheDocument();
});

test('generate mode posts an empty body and reveals the password once', async () => {
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: {
        [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-plain', hint: null }),
      },
    },
  );
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }));

  expect(await screen.findByDisplayValue('test-password-plain')).toBeInTheDocument();
  expect(screen.getByText(/больше он не покажется/)).toBeInTheDocument();
  const posted = requests(TWOFA, 'POST');
  expect(posted).toHaveLength(1);
  // No password key at all is the documented "generate one for me".
  expect(await posted[0]!.clone().json()).toEqual({});
  // The plaintext is a response, never a request: it must never ride in a URL.
  for (const url of urls()) expect(url).not.toContain('test-password-plain');
});

test('a store failure is called out loudly on the reveal panel', async () => {
  // Telegram took the password but the DB write did not: this response is then
  // the only copy in existence, and change/removal are gone.
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: {
        [`POST ${TWOFA}`]: () =>
          jsonResponse({ password: 'test-password-not-stored', stored: false }),
      },
    },
  );
  renderSection();
  await openCard();
  await userEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }));

  expect(await screen.findByDisplayValue('test-password-not-stored')).toBeInTheDocument();
  expect(screen.getByText(/сохранить его в Telebuba не удалось/)).toBeInTheDocument();
});

test('custom mode posts the typed password', async () => {
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: {
        [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-typed', hint: 'котики' }),
      },
    },
  );
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByText('Свой пароль'));
  await userEvent.type(screen.getAllByLabelText('Пароль')[0]!, 'test-password-typed');
  await userEvent.type(screen.getByLabelText('Подсказка'), 'котики');
  await userEvent.click(screen.getByRole('button', { name: 'Включить 2FA' }));

  await waitFor(() => {
    expect(requests(TWOFA, 'POST')).toHaveLength(1);
  });
  expect(await requests(TWOFA, 'POST')[0]!.clone().json()).toEqual({
    password: 'test-password-typed',
    hint: 'котики',
  });
  for (const url of urls()) expect(url).not.toContain('test-password-typed');
});

test('the eye toggle unmasks the typed password without submitting it', async () => {
  stubTwofa({ has_password: false }, { stored: false });
  renderSection();
  await openCard();
  await userEvent.click(await screen.findByText('Свой пароль'));

  const field = screen.getAllByLabelText('Пароль')[0]!;
  expect(field).toHaveAttribute('type', 'password');
  // `off` is documented as ignored on password inputs; only `new-password` keeps
  // the OPERATOR's saved credential out of the ACCOUNT's password field.
  expect(field).toHaveAttribute('autocomplete', 'new-password');
  await userEvent.click(screen.getByRole('button', { name: 'Показать пароль' }));
  expect(field).toHaveAttribute('type', 'text');
  expect(requests(TWOFA, 'POST')).toHaveLength(0);
});

test('a hint that quotes the password blocks the submit', async () => {
  // Telegram shows the hint to whoever is at the password prompt, so such a hint
  // publishes the password.
  stubTwofa({ has_password: false }, { stored: false });
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByText('Свой пароль'));
  await userEvent.type(screen.getAllByLabelText('Пароль')[0]!, 'test-password-typed');
  await userEvent.type(screen.getByLabelText('Подсказка'), 'пароль test-password-typed');
  await userEvent.tab();

  expect(screen.getByText(/Подсказка содержит сам пароль/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Включить 2FA' })).toBeDisabled();
  expect(requests(TWOFA, 'POST')).toHaveLength(0);
});

test('a password shorter than eight characters blocks the submit', async () => {
  stubTwofa({ has_password: false }, { stored: false });
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByText('Свой пароль'));
  await userEvent.type(screen.getAllByLabelText('Пароль')[0]!, 'short');
  await userEvent.tab();

  expect(screen.getByText('Не короче 8 символов')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Включить 2FA' })).toBeDisabled();
  // Back to "generate" the typed password stops mattering, so submit reopens.
  await userEvent.click(screen.getByText('Сгенерировать'));
  expect(screen.getByRole('button', { name: 'Включить 2FA' })).toBeEnabled();
});

test('Готово drops the plaintext, and collapsing the card does not bring it back', async () => {
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: { [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-once' }) },
    },
  );
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }));
  expect(await screen.findByDisplayValue('test-password-once')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Готово' }));
  expect(screen.queryByDisplayValue('test-password-once')).not.toBeInTheDocument();

  // A collapsed body is hidden, NOT unmounted, so the card has to clear the
  // secret itself — reopening must not hand it back.
  await userEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }));
  expect(await screen.findByDisplayValue('test-password-once')).toBeInTheDocument();
  await userEvent.click(screen.getByText(TITLE));
  await userEvent.click(screen.getByText(TITLE));
  expect(screen.queryByDisplayValue('test-password-once')).not.toBeInTheDocument();
});

test('the reveal panel copies the password to the clipboard', async () => {
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: { [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-copy' }) },
    },
  );
  renderSection();
  await openCard();
  await userEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }));
  await screen.findByDisplayValue('test-password-copy');

  await userEvent.click(screen.getByRole('button', { name: 'Копировать' }));

  expect(screen.getByRole('button', { name: 'Скопировано' })).toBeInTheDocument();
  expect(await navigator.clipboard.readText()).toBe('test-password-copy');
});

test('the on-state lists the live facts, including a requested reset', async () => {
  stubTwofa({
    has_password: true,
    hint: 'котики',
    has_recovery: false,
    pending_reset_date: '2026-09-01T10:00:00Z',
  });
  renderSection();
  await openCard();

  expect(await screen.findByText('котики')).toBeInTheDocument();
  expect(screen.getByText('Пароль сохранён в Telebuba')).toBeInTheDocument();
  // Somebody is trying to take the account with a password reset.
  expect(screen.getByText('Запрошен сброс пароля: 2026-09-01')).toBeInTheDocument();
});

test('an unstored password disables change, removal and the whole email leg', async () => {
  stubTwofa({ has_password: true, has_recovery: false }, { stored: false });
  renderSection();
  await openCard();

  expect(await screen.findByText('Пароль не сохранён')).toBeInTheDocument();
  expect(screen.getByText(/Без сохранённого пароля/)).toBeInTheDocument();
  // Without our copy of the current password Telegram authorises none of these.
  expect(screen.getByRole('button', { name: 'Сменить пароль' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Отключить 2FA' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Привязать почту' })).not.toBeInTheDocument();
});

test('change password reuses the set form and ends in the same reveal panel', async () => {
  stubTwofa(
    { has_password: true, has_recovery: false, hint: 'котики' },
    {
      routes: { [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-rolled' }) },
    },
  );
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByRole('button', { name: 'Сменить пароль' }));
  // Prefilled from the live status, so a blank field means "no hint" rather than
  // "keep it": the backend always writes the field, so an empty one would erase it.
  expect(screen.getByLabelText('Подсказка')).toHaveValue('котики');
  await userEvent.click(screen.getByRole('button', { name: 'Сменить пароль' }));

  expect(await screen.findByDisplayValue('test-password-rolled')).toBeInTheDocument();
  expect(await requests(TWOFA, 'POST')[0]!.clone().json()).toEqual({ hint: 'котики' });
});

test('an unconfirmed write says so as loudly as a failed store', async () => {
  // The request was on the wire and only the answer was lost, so Telegram may or
  // may not hold this password — and it is still the only copy of it.
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: {
        [`POST ${TWOFA}`]: () =>
          jsonResponse({ password: 'test-password-unconfirmed', confirmed: false }),
      },
    },
  );
  renderSection();
  await openCard();
  await userEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }));

  expect(await screen.findByDisplayValue('test-password-unconfirmed')).toBeInTheDocument();
  expect(screen.getByText(/мог примениться, а мог и нет/)).toBeInTheDocument();
});

test('the plaintext does not outlive the reveal panel in the mutation cache', async () => {
  // useMutation retains `variables` (the typed password) and `data` (the returned
  // plaintext) until mutation gc, minutes after unmount.
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: { [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-nocache' }) },
    },
  );
  const { queryClient } = renderSection();
  await openCard();

  await userEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }));
  await screen.findByDisplayValue('test-password-nocache');

  // reset() only SCHEDULES collection, hence the gcTime the form pins to 0.
  await waitFor(() => {
    const cached = JSON.stringify(
      queryClient
        .getMutationCache()
        .getAll()
        .map((mutation) => mutation.state),
    );
    expect(cached).not.toContain('test-password-nocache');
  });
});

test('disabling 2FA asks first, then fires the DELETE', async () => {
  stubTwofa(
    { has_password: true, has_recovery: false },
    {
      routes: { [`DELETE ${TWOFA}`]: () => viewResponse({ has_password: false }) },
    },
  );
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByRole('button', { name: 'Отключить 2FA' }));
  expect(await screen.findByText('Отключить облачный пароль?')).toBeInTheDocument();
  // The confirm body has to say what the account goes back to being.
  expect(screen.getByText(/одним кодом из SMS/)).toBeInTheDocument();
  expect(requests(TWOFA, 'DELETE')).toHaveLength(0);

  await userEvent.click(screen.getByRole('button', { name: 'Отключить' }));
  await waitFor(() => {
    expect(requests(TWOFA, 'DELETE')).toHaveLength(1);
  });
});

test('a failed removal keeps the confirm dialog open', async () => {
  stubTwofa(
    { has_password: true, has_recovery: false },
    {
      routes: {
        [`DELETE ${TWOFA}`]: () =>
          jsonResponse(
            { error: { code: 'invalid_request', message: 'twofa_password_not_stored' } },
            400,
          ),
      },
    },
  );
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByRole('button', { name: 'Отключить 2FA' }));
  await userEvent.click(await screen.findByRole('button', { name: 'Отключить' }));

  await waitFor(() => {
    expect(requests(TWOFA, 'DELETE')).toHaveLength(1);
  });
  expect(screen.getByText('Отключить облачный пароль?')).toBeInTheDocument();
});

test('the unattached email state offers an address input and states the trade', async () => {
  stubTwofa({ has_password: true, has_recovery: false });
  renderSection();
  await openCard();

  expect(await screen.findByLabelText('Адрес почты')).toBeInTheDocument();
  expect(screen.getByText(/сможет сбросить пароль и забрать аккаунт/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Привязать почту' })).toBeInTheDocument();
  expect(screen.queryByLabelText('Код из письма')).not.toBeInTheDocument();
});

test('submitting an address moves to the pending state from the response', async () => {
  // The status GET never reports a pending address here: the card must believe
  // the write's response rather than wait for a refetch to tell it what it did.
  stubTwofa(
    { has_password: true, has_recovery: false },
    {
      routes: {
        [`POST ${TWOFA}/email`]: () => jsonResponse({ pending: true, code_length: 6 }),
      },
    },
  );
  renderSection();
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

test('every 2FA write invalidates by key, never the whole cache', async () => {
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: { [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-invalidate' }) },
    },
  );
  const { queryClient } = renderSection();
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  await openCard();
  await userEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }));
  await screen.findByDisplayValue('test-password-invalidate');

  // A bare invalidateQueries() refetches the warming board, the logs and the
  // accounts list this view derives its account from.
  expect(invalidate).toHaveBeenCalled();
  for (const [filters] of invalidate.mock.calls) {
    expect(filters?.queryKey).toBeDefined();
  }
});
