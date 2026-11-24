// The 2FA card when the stored password is only the seller's candidate: Telegram has not
// confirmed it yet, or has refused it. Harness shared with TwoFactorSection.test.tsx.

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';

import {
  TWOFA,
  jsonResponse,
  openCard,
  renderSection,
  requests,
  stubApi,
} from './TwoFactorSection.test-helpers';

function stubCandidate(refused: boolean): void {
  stubApi((request) => {
    const { pathname } = new URL(request.url);
    if (pathname !== TWOFA) return undefined;
    return jsonResponse({
      status: { has_password: true, has_recovery: false },
      has_stored_password: false,
      has_unverified_candidate: true,
      candidate_refused: refused,
      error: null,
    });
  });
}

test('an unverified seller password says Telegram has not confirmed it, change stays open', async () => {
  stubCandidate(false);
  renderSection();
  await openCard();

  expect(await screen.findByText(/Telegram его ещё не подтвердил/)).toBeInTheDocument();
  expect(screen.queryByText('Пароль не сохранён')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Сменить пароль' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Отключить 2FA' })).toBeEnabled();
});

test('a refused candidate offers the real password and forgetting, not change or remove', async () => {
  stubCandidate(true);
  renderSection();
  await openCard();

  expect(await screen.findByText('Telegram не принял сохранённый пароль.')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Сменить пароль' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Отключить 2FA' })).not.toBeInTheDocument();
  // The record form is open straight away: it is the primary action here.
  expect(screen.getByRole('button', { name: 'Проверить и сохранить' })).toBeInTheDocument();
});

test('forgetting a refused candidate sends forget_only', async () => {
  stubCandidate(true);
  renderSection();
  await openCard();

  await userEvent.click(await screen.findByRole('button', { name: 'Удалить сохранённый пароль' }));
  expect(await screen.findByText(/в Telegram ничего не изменится/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Удалить' }));

  await waitFor(() => {
    expect(requests(TWOFA, 'DELETE')).toHaveLength(1);
  });
  expect(new URL(requests(TWOFA, 'DELETE')[0]!.url).searchParams.get('forget_only')).toBe('true');
});
