// An interrupted cloud-password batch: the step offers to go on with what was left, never
// stores the password, and asks for it again.

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test } from 'vitest';

import { jsonResponse, renderStep, stubBatch, twofaBodies } from './TwoFactorBulkStep.test-helpers';

const KEY = 'telebuba.bulkRun.bulk-twofa';

afterEach(() => {
  window.localStorage.clear();
});

function seedRun(done: string[]): void {
  window.localStorage.setItem(
    KEY,
    JSON.stringify({
      runId: 'r',
      startedAt: Date.now(),
      ids: ['acc-1', 'acc-2', 'acc-3'],
      done,
      failed: [],
    }),
  );
}

test('continuing selects only the accounts that were left', async () => {
  seedRun(['acc-1']);
  stubBatch(() => jsonResponse({ password: 'test-password-unused' }));
  renderStep();

  expect(await screen.findByText('Прошлый запуск прерван: обработано 1 из 3')).toBeVisible();
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Продолжить' })).toBeEnabled();
  });
  await userEvent.click(screen.getByRole('button', { name: 'Продолжить' }));

  expect(screen.queryByText(/Прошлый запуск прерван/)).toBeNull();
  expect(screen.getByText('2 из 3')).toBeInTheDocument();
  expect(await screen.findByRole('button', { name: 'Включить для 2 аккаунтов' })).toBeEnabled();
});

test('resetting forgets the run and leaves the whole batch selected', async () => {
  seedRun(['acc-1']);
  stubBatch(() => jsonResponse({ password: 'test-password-unused' }));
  renderStep();

  await userEvent.click(await screen.findByRole('button', { name: 'Сбросить' }));

  expect(screen.queryByText(/Прошлый запуск прерван/)).toBeNull();
  expect(window.localStorage.getItem(KEY)).toBeNull();
  expect(screen.getByText('3 из 3')).toBeInTheDocument();
});

test('a batch writes only ids and outcomes while it runs, and nothing once it is done', async () => {
  stubBatch(() => jsonResponse({ password: 'test-password-secret' }));
  renderStep();

  await userEvent.click(await screen.findByRole('button', { name: 'Включить для 3 аккаунтов' }));

  await waitFor(() => {
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });
  expect((await twofaBodies()).length).toBe(3);
  expect(JSON.stringify({ ...window.localStorage })).not.toContain('test-password-secret');
});
