// The SELECTION and BATCH halves of the add-wizard's cloud-password step: who is
// picked, what body the two modes send, that the accounts go out strictly one at
// a time, and what «Остановить» leaves behind. The reveal-once password table it
// hands over to is TwoFactorBulkResults.test.tsx; the shared harness is in
// TwoFactorBulkStep.test-helpers.tsx.

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import {
  IDS,
  jsonResponse,
  renderStep,
  stubBatch,
  twofaBodies,
  twofaPosts,
  deferred,
} from './TwoFactorBulkStep.test-helpers';

const ENABLE_3 = 'Включить для 3 аккаунтов';

// Every account's response held open by the test, so "one at a time" is a thing
// the test can observe rather than a thing it has to trust.
function gated(): {
  started: string[];
  release: (accountId: string, body?: unknown, status?: number) => void;
} {
  const started: string[] = [];
  const gates = new Map<string, (response: Response) => void>();
  stubBatch((accountId) => {
    started.push(accountId);
    const gate = deferred();
    gates.set(accountId, gate.resolve);
    return gate.promise;
  });
  return {
    started,
    release: (accountId, body = { password: `test-password-${accountId}` }, status = 200) => {
      gates.get(accountId)?.(jsonResponse(body, status));
    },
  };
}

async function submit(name = ENABLE_3): Promise<void> {
  await userEvent.click(await screen.findByRole('button', { name }));
}

test('every account starts selected, and unticking one drops the count and the label', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-unused' }));
  renderStep();

  expect(await screen.findByText('Аня')).toBeInTheDocument();
  expect(screen.getByText('3 из 3')).toBeInTheDocument();
  const boxes = screen.getAllByRole('checkbox');
  expect(boxes[0]).toHaveAttribute('aria-checked', 'true');

  await userEvent.click(screen.getByRole('checkbox', { name: /Боря/ }));

  expect(screen.getByText('2 из 3')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Включить для 2 аккаунтов' })).toBeEnabled();
  // A check over a partial batch would claim the whole batch is picked.
  expect(screen.getAllByRole('checkbox')[0]).toHaveAttribute('aria-checked', 'mixed');
});

test('the master checkbox clears the whole batch and picks it back up', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-unused' }));
  renderStep();
  const master = () => screen.getAllByRole('checkbox')[0]!;

  await userEvent.click(master());
  expect(screen.getByText('0 из 3')).toBeInTheDocument();
  expect(master()).toHaveAttribute('aria-checked', 'false');
  // Nothing picked is nothing to do — the batch button cannot fire.
  expect(screen.getByRole('button', { name: 'Включить для 0 аккаунтов' })).toBeDisabled();

  await userEvent.click(master());
  expect(screen.getByText('3 из 3')).toBeInTheDocument();
  expect(master()).toHaveAttribute('aria-checked', 'true');
});

test('only the accounts still ticked are sent', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-picked' }));
  renderStep();
  await screen.findByText('Боря');
  await userEvent.click(screen.getByRole('checkbox', { name: /Боря/ }));
  await submit('Включить для 2 аккаунтов');

  await waitFor(() => {
    expect(twofaPosts()).toHaveLength(2);
  });
  expect((await twofaBodies()).map((post) => post.accountId)).toEqual(['acc-1', 'acc-3']);
});

test('the batch goes out one account at a time, in list order', async () => {
  // Setting a password is an SRP computation on a two-thread backend pool, so a
  // second POST before the first answers buys nothing and can only be refused.
  const { started, release } = gated();
  renderStep();
  await submit();

  await waitFor(() => {
    expect(started).toEqual(['acc-1']);
  });
  expect(screen.getByText('отправляем в Telegram…')).toBeInTheDocument();
  expect(screen.getAllByText('в очереди')).toHaveLength(2);

  release('acc-1');
  await waitFor(() => {
    expect(started).toEqual(['acc-1', 'acc-2']);
  });
  expect(screen.getByText('Готово 1 из 3')).toBeInTheDocument();
  expect(screen.getAllByText('в очереди')).toHaveLength(1);

  release('acc-2');
  await waitFor(() => {
    expect(started).toEqual(IDS);
  });
  release('acc-3');

  expect(await screen.findByText('test-password-acc-3')).toBeInTheDocument();
  expect(screen.getByText('test-password-acc-1')).toBeInTheDocument();
});

test('«Сгенерировать» sends no password key at all', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-minted' }));
  const onImported = vi.fn();
  renderStep({ onImported });
  await submit();

  await waitFor(() => {
    expect(twofaPosts()).toHaveLength(3);
  });
  // A bare {} is the documented "mint one for me"; the backend forbids unknown keys.
  expect(await twofaBodies()).toEqual(IDS.map((accountId) => ({ accountId, body: {} })));
  await waitFor(() => {
    expect(onImported).toHaveBeenCalledTimes(1);
  });
});

test('a typed hint travels with the generated passwords, and only the hint', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-minted' }));
  renderStep();
  await userEvent.type(await screen.findByLabelText('Подсказка'), 'котики');
  await submit();

  await waitFor(() => {
    expect(twofaPosts()).toHaveLength(3);
  });
  for (const post of await twofaBodies()) expect(post.body).toEqual({ hint: 'котики' });
});

test('«Свой пароль» sends the SAME typed password to every selected account', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-shared' }));
  renderStep();
  await userEvent.click(await screen.findByText('Свой пароль'));
  await userEvent.type(screen.getAllByLabelText('Пароль')[0]!, 'test-password-shared');
  await submit();

  await waitFor(() => {
    expect(twofaPosts()).toHaveLength(3);
  });
  expect(await twofaBodies()).toEqual(
    IDS.map((accountId) => ({ accountId, body: { password: 'test-password-shared' } })),
  );
  // The plaintext is a body, never a query string that lands in a proxy log.
  for (const request of twofaPosts()) expect(request.url).not.toContain('test-password-shared');
});

test('a short password and a hint quoting it both block the batch', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-unused' }));
  renderStep();
  await userEvent.click(await screen.findByText('Свой пароль'));
  const field = screen.getAllByLabelText('Пароль')[0]!;
  await userEvent.type(field, 'short');
  await userEvent.tab();

  expect(screen.getByText('Не короче 8 символов')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: ENABLE_3 })).toBeDisabled();

  // Telegram shows the hint at the password prompt, so a hint quoting the
  // password publishes it for the whole batch at once.
  await userEvent.clear(field);
  await userEvent.type(field, 'test-password-leaky');
  await userEvent.type(screen.getByLabelText('Подсказка'), 'пароль test-password-leaky');
  await userEvent.tab();

  expect(screen.getByText(/Подсказка содержит сам пароль/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: ENABLE_3 })).toBeDisabled();
  expect(twofaPosts()).toHaveLength(0);
});

test('the eye toggle unmasks the shared password without sending it', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-unused' }));
  renderStep();
  await userEvent.click(await screen.findByText('Свой пароль'));

  const field = screen.getAllByLabelText('Пароль')[0]!;
  expect(field).toHaveAttribute('type', 'password');
  // `off` is documented as ignored on password inputs; only `new-password` keeps
  // the OPERATOR's saved credential out of the ACCOUNTS' password field.
  expect(field).toHaveAttribute('autocomplete', 'new-password');

  await userEvent.click(screen.getByRole('button', { name: 'Показать пароль' }));
  expect(field).toHaveAttribute('type', 'text');
  expect(twofaPosts()).toHaveLength(0);
});

test('«Остановить» leaves the untouched accounts neutral, not failed', async () => {
  const { started, release } = gated();
  renderStep();
  await submit();
  await waitFor(() => {
    expect(started).toEqual(['acc-1']);
  });

  await userEvent.click(screen.getByRole('button', { name: 'Остановить' }));
  // The account already on the wire still finishes: its password exists on
  // Telegram either way, and this response is the only copy of it.
  release('acc-1', { password: 'test-password-stopped' });

  expect(await screen.findByText('test-password-stopped')).toBeInTheDocument();
  expect(screen.getAllByText('не включали — пачку остановили')).toHaveLength(2);
  // Never an error row: nobody refused a request that was never sent.
  expect(screen.queryByText(/Telegram отклонил/)).not.toBeInTheDocument();
  expect(twofaPosts()).toHaveLength(1);
});

test('a refused account lands on the result screen with Telegram’s own reason', async () => {
  stubBatch((accountId) =>
    accountId === 'acc-2'
      ? jsonResponse({ error: { code: 'bad_request', message: 'twofa_settings_invalid' } }, 400)
      : jsonResponse({ password: `test-password-${accountId}` }),
  );
  renderStep();
  await submit();

  expect(await screen.findByText('test-password-acc-1')).toBeInTheDocument();
  expect(screen.getByText('Telegram отклонил новые настройки пароля.')).toBeInTheDocument();
  // A refusal stops nothing: the accounts behind it still get their password.
  expect(screen.getByText('test-password-acc-3')).toBeInTheDocument();
  expect(screen.getByText('2 из 3')).toBeInTheDocument();
});

test('without the accounts list the rows fall back to the raw account ids', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-nameless' }), null);
  renderStep();

  expect(await screen.findByText('acc-1')).toBeInTheDocument();
  expect(screen.queryByText('Аня')).not.toBeInTheDocument();
  // The file it came out of is still there — that is what the operator picked.
  expect(screen.getByText('one.session')).toBeInTheDocument();
});

test('the plaintext never reaches the query client’s mutation cache', async () => {
  // `gcTime: 0` beside the per-row reset(): the response AND the typed password
  // in `variables` would otherwise sit in the cache for the default five minutes.
  stubBatch(() => jsonResponse({ password: 'test-password-cached' }));
  const { queryClient } = renderStep();
  await userEvent.click(await screen.findByText('Свой пароль'));
  await userEvent.type(screen.getAllByLabelText('Пароль')[0]!, 'test-password-cached');
  await submit();

  expect(await screen.findAllByText('test-password-cached')).toHaveLength(3);
  await waitFor(() => {
    expect(queryClient.getMutationCache().getAll()).toHaveLength(0);
  });
  const cached = JSON.stringify(
    queryClient
      .getQueryCache()
      .getAll()
      .map((query) => query.state),
  );
  expect(cached).not.toContain('test-password-cached');
});

test('«Пропустить» leaves the wizard without sending anything', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-unused' }));
  const onDone = vi.fn();
  renderStep({ onDone });

  await userEvent.click(await screen.findByRole('button', { name: 'Пропустить' }));
  expect(onDone).toHaveBeenCalledTimes(1);
  expect(twofaPosts()).toHaveLength(0);
});
