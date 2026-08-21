// The reveal-once panel of the 2FA card: the single copy of the plaintext, the
// clipboard routes that carry it out of here, and the warnings that say what state
// Telegram is actually in. Split from TwoFactorSection.test.tsx for the 700-line
// test-source cap; the harness all three 2FA files drive is in
// TwoFactorSection.test-helpers.tsx.

import { act, fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import {
  TITLE,
  TWOFA,
  jsonResponse,
  openCard,
  renderSection,
  stubTwofa,
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

test('a successful copy does not erase the warning from a later failed one', async () => {
  // The success schedules a 2400ms reset to 'idle'. Ungated, that timer fires over a
  // LATER rejected write and silently reverts the warning to neutral — leaving no
  // signal at all about the only copy of the credential.
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: { [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-timer' }) },
    },
  );
  // `shouldAdvanceTime`, because @testing-library's waitFor probes on a real
  // setInterval it cannot know is faked (its fake-timer detection is jest-only), so a
  // plain useFakeTimers() hangs every findBy* in the file. The clock still only moves
  // when advanced explicitly, give or take the milliseconds the test itself spends.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    renderSection();
    await userEvent.click(screen.getByText(TITLE));
    await userEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }));
    await screen.findByDisplayValue('test-password-timer');

    await userEvent.click(screen.getByRole('button', { name: 'Копировать' }));
    expect(screen.getByRole('button', { name: 'Скопировано' })).toBeInTheDocument();

    // A second copy 400ms later, and this time the write rejects.
    const write = vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    await userEvent.click(screen.getByRole('button', { name: 'Скопировано' }));
    expect(await screen.findByText(/Скопировать не удалось/)).toBeInTheDocument();

    // The first copy's timer comes due here and must not touch the failure.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2400);
    });
    expect(screen.getByText(/Скопировать не удалось/)).toBeInTheDocument();
    write.mockRestore();
  } finally {
    vi.useRealTimers();
  }
});

test('the one-time password is shown in full and at readable contrast', async () => {
  // Measured in Chrome on the pre-fix field: scrollWidth 196 against clientWidth 160
  // with `overflow: clip` and no ellipsis, and 2.64:1 contrast (#9a9893 on #f6f5f2)
  // against a 4.5:1 AA floor — on the one string in this feature shown exactly once,
  // next to an instruction to select and copy it by hand.
  stubTwofa(
    { has_password: false },
    {
      stored: false,
      routes: {
        [`POST ${TWOFA}`]: () => jsonResponse({ password: 'test-password-0123456789' }),
      },
    },
  );
  renderSection();
  await openCard();
  await userEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }));

  const field = await screen.findByDisplayValue('test-password-0123456789');
  // A textarea wraps its value; an <input> can only clip it.
  expect(field.tagName).toBe('TEXTAREA');
  expect(field).toHaveAttribute('readonly');
  expect(field).toHaveClass('text-ink');
  expect(field).not.toHaveClass('text-ink-subtle');
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
