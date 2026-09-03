import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import '@/shared/i18n';

import { discoveryAccountsQueryOptions } from '@/entities/campaign';

import {
  ACCOUNTS,
  newQueryClient,
  renderModal,
  route,
  startSearch,
  submitButton,
  typeKeywords,
} from './ChannelDiscoveryModal.testHelpers';

const postedAccounts = (calls: { path: string; body: unknown }[]) =>
  (calls.find((call) => call.path.endsWith('/discovery/search'))?.body as { account_ids: string[] })
    .account_ids;

// The trigger shows the picked names, so it is found by them; the options carry the
// same names under their own role (the premium one with its badge appended).
const openPicker = async (label: string | RegExp) => {
  await userEvent.click(await screen.findByRole('button', { name: label }));
};

describe('ChannelDiscoveryModal account picker', () => {
  it('posts the operator pick instead of the premium default', async () => {
    const calls = route();
    renderModal();

    await openPicker('Prem');
    await userEvent.click(screen.getByRole('option', { name: /^Prem/ }));
    await userEvent.click(screen.getByRole('option', { name: 'Plain' }));
    await startSearch();

    await waitFor(() => {
      expect(postedAccounts(calls)).toEqual(['acc-n']);
    });
  });

  it('drops a picked account that went busy between the pick and the click', async () => {
    const calls = route();
    const queryClient = newQueryClient();
    renderModal(undefined, queryClient);

    await openPicker('Prem');
    await userEvent.click(screen.getByRole('option', { name: 'Plain' }));
    expect(screen.getByText('выбрано 2')).toBeInTheDocument();
    // The list refreshed underneath the pick: the request must read the LATEST set,
    // not the one the operator clicked on.
    queryClient.setQueryData(discoveryAccountsQueryOptions().queryKey, {
      items: ACCOUNTS.map((account) =>
        account.account_id === 'acc-n'
          ? { ...account, busy_reason: 'account_busy' as const }
          : account,
      ),
    });
    await startSearch();

    await waitFor(() => {
      expect(postedAccounts(calls)).toEqual(['acc-p']);
    });
  });

  it('keeps the search dead while the account list failed to load', async () => {
    route({ accounts: 'fail' });
    renderModal();

    expect(await screen.findByText(/Не удалось загрузить аккаунты/)).toBeInTheDocument();
    await typeKeywords();
    expect(submitButton()).toBeDisabled();
  });

  it('warns and keeps the search dead when no account is eligible', async () => {
    route({ accounts: [ACCOUNTS[2]!] });
    renderModal();

    expect(await screen.findByText(/Нет свободных аккаунтов/)).toBeInTheDocument();
    await typeKeywords();
    expect(submitButton()).toBeDisabled();
  });

  it('names the account a refusal is about', async () => {
    route({ startStatus: 'account_cooling', refusedAccountId: 'acc-p' });
    renderModal();

    await startSearch();

    // The id means nothing on screen; the name is what the operator can act on.
    expect(await screen.findByText(/пережидает лимит Telegram.*Аккаунт: Prem/)).toBeInTheDocument();
  });

  it('restores the premium default on reset', async () => {
    const calls = route();
    renderModal();

    await openPicker('Prem');
    await userEvent.click(screen.getByRole('option', { name: /^Prem/ }));
    await userEvent.click(screen.getByRole('option', { name: 'Plain' }));
    expect(screen.getByRole('button', { name: 'Plain' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Сбросить' }));

    expect(screen.getByRole('button', { name: 'Prem' })).toBeInTheDocument();
    expect(screen.getByText('выбран 1')).toBeInTheDocument();
    await startSearch();
    await waitFor(() => {
      expect(postedAccounts(calls)).toEqual(['acc-p']);
    });
  });
});
