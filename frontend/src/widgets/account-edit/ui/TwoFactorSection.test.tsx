// The password half of the 2FA card: reveal-once, change, disable, and the stale
// stored password. The recovery-email half lives in TwoFactorEmail.test.tsx; the
// harness both drive is in TwoFactorSection.test-helpers.tsx.

import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { accountStatsQueryKey, accountTwofaQueryKey, accountsQueryKey } from '@/entities/account';

import {
  ACCOUNT,
  TITLE,
  TWOFA,
  jsonResponse,
  openCard,
  renderSection,
  requests,
  stubTwofa,
  toastMessages,
  urls,
  viewResponse,
} from './TwoFactorSection.test-helpers';

// Any non-secure context — the dashboard reached over http:// by LAN IP rather
// than localhost — has no `navigator.clipboard` at all.
function withoutClipboard(): () => void {
  const original = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
  return () => {
    if (original) Object.defineProperty(navigator, 'clipboard', original);
    else Reflect.deleteProperty(navigator, 'clipboard');
  };
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

test('a rejected clipboard write never claims the password was copied', async () => {
  // writeText rejects on a denied permission and, in Chrome, whenever the document
  // is not focused. "Скопировано" over a rejected write is how the operator's only
  // copy of the credential gets lost: they read it, click Готово, and it is gone.
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: { [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-denied' }) },
    },
  );
  renderSection();
  await openCard();
  await userEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }));
  await screen.findByDisplayValue('test-password-denied');

  const write = vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'));
  await userEvent.click(screen.getByRole('button', { name: 'Копировать' }));

  expect(await screen.findByText(/Скопировать не удалось/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Скопировано' })).not.toBeInTheDocument();
  // Still on screen and still selectable, which is the whole fallback.
  expect(screen.getByDisplayValue('test-password-denied')).toBeInTheDocument();
  write.mockRestore();
});

test('with no clipboard at all the panel asks for a manual copy, not a dead button', async () => {
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: { [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-noclip' }) },
    },
  );
  const restore = withoutClipboard();
  try {
    renderSection();
    // fireEvent, not userEvent: user-event attaches its own clipboard stub on the
    // next interaction, which would undo the very condition under test.
    fireEvent.click(screen.getByText(TITLE));
    fireEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }));

    const field = await screen.findByDisplayValue('test-password-noclip');
    expect(screen.getByText(/Буфер обмена/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Копировать' })).not.toBeInTheDocument();
    // A read-only input IS selectable, so `cursor-not-allowed` would signal the
    // opposite of the instruction just given.
    expect(field).not.toHaveClass('cursor-not-allowed');
  } finally {
    restore();
  }
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

test('an unstored password disables change, removal and attaching an address', async () => {
  stubTwofa({ has_password: true, has_recovery: false }, { stored: false });
  renderSection();
  await openCard();

  expect(await screen.findByText('Пароль не сохранён')).toBeInTheDocument();
  expect(screen.getByText(/Без сохранённого пароля/)).toBeInTheDocument();
  // Without our copy of the current password Telegram authorises none of these.
  expect(screen.getByRole('button', { name: 'Сменить пароль' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Отключить 2FA' })).toBeDisabled();
  // Disabled, not absent: the email state itself stays readable, because three of
  // the five email operations need no stored password at all.
  expect(screen.getByRole('button', { name: 'Привязать почту' })).toBeDisabled();
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
  // plaintext) until mutation gc, minutes after unmount. Run in CUSTOM mode with a
  // typed password on purpose: in generate mode the body is empty, so `variables`
  // held no secret and half of what this test claims to cover was never exercised.
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: { [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-nocache' }) },
    },
  );
  const { queryClient } = renderSection();
  await openCard();

  await userEvent.click(await screen.findByText('Свой пароль'));
  await userEvent.type(screen.getAllByLabelText('Пароль')[0]!, 'test-password-sent');
  await userEvent.click(screen.getByRole('button', { name: 'Включить 2FA' }));
  await screen.findByDisplayValue('test-password-nocache');

  // reset() only SCHEDULES collection, hence the gcTime the form pins to 0.
  await waitFor(() => {
    const cached = JSON.stringify(
      queryClient
        .getMutationCache()
        .getAll()
        .map((mutation) => mutation.state),
    );
    // `variables` first: it is the half this test used to skip.
    expect(cached).not.toContain('test-password-sent');
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
  // The card renders no inline failure of its own: the global mutation toast is the
  // ONLY thing that tells the operator why the dialog is still there.
  expect(await toastMessages()).toContain(
    'Текущий пароль не сохранён в панели, изменить или снять его отсюда нельзя.',
  );
});

test('a stored password Telegram no longer has can be seen and dropped', async () => {
  // 2FA is OFF on Telegram while a plaintext password is still stored here: the
  // operator removed it from their phone, or an earlier removal's post-RPC clear
  // failed. Every control that could drop that copy used to live behind
  // `has_password`, so the row was invisible AND unremovable — and the backend's own
  // stale branch (clear the column, spend no RPC) was unreachable from the UI.
  stubTwofa(
    { has_password: false },
    { routes: { [`DELETE ${TWOFA}`]: () => viewResponse({ has_password: false }, false) } },
  );
  renderSection();
  await openCard();

  expect(await screen.findByText(/здесь он всё ещё сохранён/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Удалить сохранённый пароль' }));

  expect(await screen.findByText('Удалить сохранённый пароль?')).toBeInTheDocument();
  // Its own copy: nothing is being turned off on Telegram, so the SMS-takeover
  // warning the disable dialog carries would be a lie here.
  expect(screen.queryByText(/одним кодом из SMS/)).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Удалить' }));

  await waitFor(() => {
    expect(requests(TWOFA, 'DELETE')).toHaveLength(1);
  });
});

test('a read failure still shows a stored password and can drop it', async () => {
  // `has_stored_password` is a DB fact the backend answers even when the live read
  // failed, and the removal does not need the read to have succeeded — so a
  // transient twofa_state_unreadable used to leave a plaintext cloud password on
  // disk with nothing on screen saying so and no control that could clear it.
  stubTwofa(null, {
    error: 'twofa_state_unreadable',
    routes: { [`DELETE ${TWOFA}`]: () => viewResponse({ has_password: false }, false) },
  });
  renderSection();
  await openCard();

  expect(await screen.findByText(/Состояние 2FA не прочитано/)).toBeInTheDocument();
  expect(screen.getByText('Пароль сохранён в Telebuba')).toBeInTheDocument();
  // Still nothing that writes a password against a state we could not read.
  expect(screen.queryByRole('button', { name: 'Включить 2FA' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Сменить пароль' })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Удалить сохранённый пароль' }));
  // With no live status the backend cannot take its "clear the column, spend no RPC"
  // branch, so this DELETE may really remove the password from Telegram. The stale
  // case's "nothing changes on Telegram" body would be a lie here.
  expect(await screen.findByText(/Если облачный пароль в Telegram есть/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Удалить' }));

  await waitFor(() => {
    expect(requests(TWOFA, 'DELETE')).toHaveLength(1);
  });
});

test('a hint cleared through this card reads as "not set", not as blank', async () => {
  // '' is what the gateway reports after this card clears a hint, and '' is not
  // nullish — so `??` left the row empty instead of saying there is no hint.
  stubTwofa({ has_password: true, has_recovery: false, hint: '' });
  renderSection();
  await openCard();

  expect(await screen.findByText('не задана')).toBeInTheDocument();
});

test('a requested reset is shown even when the account has no password', async () => {
  // Telegram reports the pending reset against an account whose password is already
  // gone, and that is the loudest version of "somebody is taking this account" —
  // rendered inside the 2FA-on arm, it was dropped exactly there.
  stubTwofa({ has_password: false, pending_reset_date: '2026-09-01T10:00:00Z' }, { stored: false });
  renderSection();
  await openCard();

  expect(await screen.findByText('Запрошен сброс пароля: 2026-09-01')).toBeInTheDocument();
  // …without costing the off-state its form.
  expect(screen.getByRole('button', { name: 'Включить 2FA' })).toBeInTheDocument();
});

test('an unconfirmed CHANGE says both passwords are now candidates', async () => {
  // The backend keeps the OLD password stored rather than overwrite a credential
  // known to work, so exactly one of the two is live and only the phone can say
  // which. The generic "may or may not have applied" copy would leave the operator
  // believing the old one is gone.
  stubTwofa(
    { has_password: true, has_recovery: false },
    {
      routes: {
        [`POST ${TWOFA}`]: () =>
          jsonResponse({
            password: 'test-password-candidate',
            confirmed: false,
            stored: false,
            previous_kept: true,
          }),
      },
    },
  );
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByRole('button', { name: 'Сменить пароль' }));
  await userEvent.click(screen.getByRole('button', { name: 'Сменить пароль' }));

  expect(await screen.findByDisplayValue('test-password-candidate')).toBeInTheDocument();
  expect(screen.getByText(/действует либо прежний пароль/)).toBeInTheDocument();
  expect(screen.queryByText(/мог примениться, а мог и нет/)).not.toBeInTheDocument();
  // `stored: false` here means "the old password was kept", not "the write failed",
  // so the store-failure warning must stay away.
  expect(screen.queryByText(/сохранить его в Telebuba не удалось/)).not.toBeInTheDocument();
});

test('emptying the prefilled hint posts an explicit empty one', async () => {
  // An omitted hint now means "keep the one Telegram shows", so the deliberate clear
  // has to be spelled out — otherwise the prefill would make clearing impossible.
  stubTwofa(
    { has_password: true, has_recovery: false, hint: 'котики' },
    { routes: { [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-nohint' }) } },
  );
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByRole('button', { name: 'Сменить пароль' }));
  await userEvent.clear(screen.getByLabelText('Подсказка'));
  await userEvent.click(screen.getByRole('button', { name: 'Сменить пароль' }));

  await waitFor(() => {
    expect(requests(TWOFA, 'POST')).toHaveLength(1);
  });
  expect(await requests(TWOFA, 'POST')[0]!.clone().json()).toEqual({ hint: '' });
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
  // accounts list this view derives its account from. Asserting only that a
  // queryKey is DEFINED lets a wrong key through — an invalidation naming another
  // account passes that, refetches nothing, and leaves this card stale.
  const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
  expect(keys).toContainEqual(accountTwofaQueryKey({ path: { account_id: ACCOUNT.account_id } }));
  expect(keys).toContainEqual(accountsQueryKey());
  expect(keys).toContainEqual(accountStatsQueryKey());
  // The fourth is the proxy list (an account holds a pool slot); four in total, so
  // nothing widened into a cache-wide sweep either.
  expect(keys).toHaveLength(4);
});
