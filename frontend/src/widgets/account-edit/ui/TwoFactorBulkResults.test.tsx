// The RESULT half of the add-wizard's cloud-password step: the reveal-once
// password table, the warnings that say what state Telegram is actually in, and
// the two routes the plaintext leaves by — the clipboard and the .csv. The
// selection form and the batch itself are in TwoFactorBulkStep.test.tsx.

import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountTwoFactorCreated } from '@/shared/api';

import { IDS, NAMES } from './TwoFactorBulkStep.test-helpers';
import { TwoFactorBulkResults } from './TwoFactorBulkResults';
import type { BulkTwofaRow } from './useBulkTwofa';

const label = (accountId: string) => NAMES[IDS.indexOf(accountId)] ?? accountId;

function ok(accountId: string, created: Partial<AccountTwoFactorCreated> = {}): BulkTwofaRow {
  return {
    accountId,
    state: 'ok',
    created: { password: `test-password-${accountId}`, ...created },
    error: null,
  };
}

// The refusal the batch stores verbatim — the same envelope the global mutation
// toast resolves, so the row and the toast cannot drift apart.
function failed(accountId: string, message = 'twofa_settings_invalid'): BulkTwofaRow {
  return {
    accountId,
    state: 'error',
    created: null,
    error: { error: { code: 'bad_request', message } },
  };
}

function notRun(accountId: string): BulkTwofaRow {
  return { accountId, state: 'queued', created: null, error: null };
}

function renderResults(rows: BulkTwofaRow[], onDone = vi.fn()) {
  return { onDone, ...render(<TwoFactorBulkResults rows={rows} label={label} onDone={onDone} />) };
}

// Any non-secure context — the dashboard over http:// by LAN IP rather than
// localhost — has no `navigator.clipboard` at all.
function withoutClipboard(): () => void {
  const original = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
  return () => {
    if (original) Object.defineProperty(navigator, 'clipboard', original);
    else Reflect.deleteProperty(navigator, 'clipboard');
  };
}

test('every created password is on screen under the one-time warning', async () => {
  const { onDone } = renderResults([ok('acc-1'), ok('acc-2'), ok('acc-3')]);

  expect(screen.getByRole('alert')).toHaveTextContent('Пароли показаны один раз');
  for (const accountId of IDS) {
    expect(screen.getByText(`test-password-${accountId}`)).toBeInTheDocument();
  }
  expect(screen.getByText('Аня')).toBeInTheDocument();
  expect(screen.getByText('3 из 3')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Готово' }));
  expect(onDone).toHaveBeenCalledTimes(1);
});

test('a password Telegram took but Telebuba could not store says so beside it', () => {
  renderResults([ok('acc-1', { stored: false })]);

  expect(screen.getByText('test-password-acc-1')).toBeInTheDocument();
  expect(screen.getByText(/сохранить его в Telebuba не удалось/)).toBeInTheDocument();
});

test('a password whose confirmation never came back says so beside it', () => {
  renderResults([ok('acc-1', { confirmed: false })]);

  expect(screen.getByText(/Связь оборвалась до ответа Telegram/)).toBeInTheDocument();
});

test('a refused account is an error row with the reason and no password', () => {
  renderResults([ok('acc-1'), failed('acc-2'), notRun('acc-3')]);

  expect(screen.getByText('Telegram отклонил новые настройки пароля.')).toBeInTheDocument();
  expect(screen.getByText('Боря')).toBeInTheDocument();
  expect(screen.queryByText('test-password-acc-2')).not.toBeInTheDocument();
  // The stopped account is neutral — `mutationErrorText` over its null error
  // would accuse Telegram of refusing a request nobody sent.
  expect(screen.getByText('не включали — пачку остановили')).toBeInTheDocument();
  expect(screen.getByText('1 из 3')).toBeInTheDocument();
});

test('copy-all writes one name-and-password line per created account', async () => {
  renderResults([ok('acc-1'), failed('acc-2'), ok('acc-3')]);

  await userEvent.click(screen.getByRole('button', { name: 'Скопировать все' }));

  expect(await navigator.clipboard.readText()).toBe(
    'Аня\ttest-password-acc-1\nВера\ttest-password-acc-3',
  );
  expect(screen.getByRole('button', { name: 'Скопировано' })).toBeInTheDocument();
});

test('a single row copies just its own password', async () => {
  renderResults([ok('acc-1'), ok('acc-2')]);

  await userEvent.click(screen.getAllByRole('button', { name: 'Копировать' })[1]!);

  expect(await navigator.clipboard.readText()).toBe('test-password-acc-2');
  expect(screen.getByRole('button', { name: 'Скопировано' })).toBeInTheDocument();
});

test('a rejected clipboard write never claims the passwords were copied', async () => {
  // writeText rejects on a denied permission and, in Chrome, whenever the
  // document is not focused. These passwords are the only copy there is.
  renderResults([ok('acc-1')]);
  const write = vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'));

  await userEvent.click(screen.getByRole('button', { name: 'Скопировать все' }));

  expect(await screen.findByText(/Скопировать не удалось/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Скопировано' })).not.toBeInTheDocument();
  expect(screen.getByText('test-password-acc-1')).toBeInTheDocument();
  write.mockRestore();
});

test('a successful copy clears its own label and never erases a later failure', async () => {
  // `shouldAdvanceTime`, because @testing-library's waitFor probes on a real
  // setInterval it cannot know is faked, so a plain useFakeTimers() hangs every
  // findBy* in the file.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    renderResults([ok('acc-1')]);
    await userEvent.click(screen.getByRole('button', { name: 'Скопировать все' }));
    expect(screen.getByRole('button', { name: 'Скопировано' })).toBeInTheDocument();

    // A row copy 400ms later, and this time the write rejects.
    const write = vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    await userEvent.click(screen.getByRole('button', { name: 'Копировать' }));
    expect(await screen.findByText(/Скопировать не удалось/)).toBeInTheDocument();

    // The copy-all timer comes due here: it reverts its OWN label and leaves the
    // rejection — the one signal that a copy never reached the clipboard — alone.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2400);
    });
    expect(screen.getByRole('button', { name: 'Скопировать все' })).toBeInTheDocument();
    expect(screen.getByText(/Скопировать не удалось/)).toBeInTheDocument();
    write.mockRestore();
  } finally {
    vi.useRealTimers();
  }
});

test('with no clipboard at all the panel asks for a manual copy, not a dead button', () => {
  const restore = withoutClipboard();
  try {
    // fireEvent, not userEvent: user-event attaches its own clipboard stub on the
    // next interaction, which would undo the very condition under test.
    renderResults([ok('acc-1')]);

    expect(screen.getByText(/Буфер обмена/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Скопировать все' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Копировать' })).not.toBeInTheDocument();
    // Still on screen and still selectable, which is the whole fallback.
    expect(screen.getByText('test-password-acc-1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Скачать .csv' })).toBeInTheDocument();
  } finally {
    restore();
  }
});

test('the .csv carries every password, quoted, and its url outlives the click', async () => {
  const blobs: Blob[] = [];
  const create = vi.spyOn(URL, 'createObjectURL').mockImplementation((blob: Blob | MediaSource) => {
    blobs.push(blob as Blob);
    return 'blob:test-csv';
  });
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
  vi.useFakeTimers();
  try {
    // A name carrying a comma must not split the row its password sits in.
    renderResults([ok('acc-1'), failed('acc-2'), ok('acc-3')]);
    fireEvent.click(screen.getByRole('button', { name: 'Скачать .csv' }));

    expect(await blobs[0]!.text()).toBe(
      '"Аня","test-password-acc-1"\n"Вера","test-password-acc-3"',
    );
    expect(create).toHaveBeenCalledTimes(1);
    // `click()` only STARTS the download: a url torn down in the same frame can
    // be gone before the browser has read the blob.
    expect(revoke).not.toHaveBeenCalled();
    vi.advanceTimersByTime(0);
    expect(revoke).toHaveBeenCalledWith('blob:test-csv');
  } finally {
    vi.useRealTimers();
    create.mockRestore();
    revoke.mockRestore();
  }
});
