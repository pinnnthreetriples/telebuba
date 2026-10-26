// Accounts whose JSON password the import already verified and stored are listed as
// saved and left out of the cloud-password batch.

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import {
  IDS,
  jsonResponse,
  renderStep,
  stubBatch,
  twofaPosts,
} from './TwoFactorBulkStep.test-helpers';

test('recorded accounts are shown as saved and not offered for the batch', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-unused' }));
  renderStep({ recorded: ['acc-2'] });

  expect(await screen.findByText('пароль сохранён')).toBeInTheDocument();
  expect(screen.getByText('2 из 2')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Включить для 2 аккаунтов' })).toBeEnabled();
  expect(twofaPosts()).toHaveLength(0);
});

test('when every account is recorded there is nothing to set and the step just finishes', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-unused' }));
  const onDone = vi.fn();
  renderStep({ recorded: IDS, onDone });

  expect(await screen.findAllByText('пароль сохранён')).toHaveLength(IDS.length);
  expect(screen.queryByRole('button', { name: /Включить для/ })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Готово' }));
  expect(onDone).toHaveBeenCalledOnce();
});
