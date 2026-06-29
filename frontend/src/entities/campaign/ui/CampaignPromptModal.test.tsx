import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { CampaignPromptModal, type PromptAccount } from './CampaignPromptModal';

const ACCOUNTS: PromptAccount[] = [
  { account_id: 'a1', phone: '+79990000001', channel: '@crypto', initials: 'ИВ' },
];

test('edits the textarea, saves with swap, then closes after the delay', async () => {
  const onClose = vi.fn();
  const onSave = vi.fn();
  render(
    <CampaignPromptModal
      campaignName="Крипто"
      initialPrompt="старый"
      accounts={ACCOUNTS}
      onClose={onClose}
      onSave={onSave}
      onRemoveAccount={vi.fn()}
    />,
  );
  expect(screen.getByText('Промт кампании')).toBeInTheDocument();
  expect(screen.getByText('+79990000001')).toBeInTheDocument();

  const textarea = screen.getByLabelText('Промт кампании');
  await userEvent.clear(textarea);
  await userEvent.type(textarea, 'новый промт');

  await userEvent.click(screen.getByText('Сохранить'));
  expect(onSave).toHaveBeenCalledWith('новый промт');
  // swap to the "Сохранено" confirmation
  expect(screen.getByText('Сохранено')).toBeInTheDocument();
  // closes after the 650ms timeout
  await waitFor(() => {
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

test('remove-account opens a nested confirm and fires onRemoveAccount', async () => {
  const onRemoveAccount = vi.fn();
  render(
    <CampaignPromptModal
      campaignName="Крипто"
      initialPrompt=""
      accounts={ACCOUNTS}
      onClose={vi.fn()}
      onSave={vi.fn()}
      onRemoveAccount={onRemoveAccount}
    />,
  );
  await userEvent.click(screen.getByLabelText('Убрать из кампании'));
  expect(screen.getByText('Убрать аккаунт из кампании?')).toBeInTheDocument();

  // cancel the nested confirm first
  await userEvent.click(screen.getAllByText('Отмена')[0]!);
  expect(onRemoveAccount).not.toHaveBeenCalled();

  // reopen and confirm
  await userEvent.click(screen.getByLabelText('Убрать из кампании'));
  await userEvent.click(screen.getByText('Убрать'));
  expect(onRemoveAccount).toHaveBeenCalledWith('a1');
});

test('empty account list shows the empty hint', () => {
  render(
    <CampaignPromptModal
      campaignName="Крипто"
      initialPrompt=""
      accounts={[]}
      onClose={vi.fn()}
      onSave={vi.fn()}
      onRemoveAccount={vi.fn()}
    />,
  );
  expect(screen.getByText('В кампании пока нет аккаунтов')).toBeInTheDocument();
});

test('the close button closes', async () => {
  const onClose = vi.fn();
  render(
    <CampaignPromptModal
      campaignName="Крипто"
      initialPrompt=""
      accounts={[]}
      onClose={onClose}
      onSave={vi.fn()}
      onRemoveAccount={vi.fn()}
    />,
  );
  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(onClose).toHaveBeenCalledTimes(1);
});
