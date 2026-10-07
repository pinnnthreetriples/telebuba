import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, CloseButton, Icon, IconButton, Modal, Textarea } from '@/shared/ui';

export interface PromptAccount {
  account_id: string;
  phone: string;
  channel: string;
  initials: string;
}

// Design modal: campaign-prompt (L1321-1371) — edit the LLM prompt, see the
// accounts attached to the campaign, save with a check→"Сохранено" swap. A
// nested confirm guards removing an account from the campaign.
export function CampaignPromptModal({
  campaignName,
  initialPrompt,
  accounts,
  onClose,
  onSave,
  onRemoveAccount,
}: {
  campaignName: string;
  initialPrompt: string;
  accounts: PromptAccount[];
  onClose: () => void;
  onSave: (prompt: string) => void;
  onRemoveAccount: (accountId: string) => void;
}) {
  const { t } = useTranslation();
  const [prompt, setPrompt] = useState(initialPrompt);
  const [saved, setSaved] = useState(false);
  const [confirm, setConfirm] = useState<PromptAccount | null>(null);

  const save = () => {
    onSave(prompt);
    setSaved(true);
    setTimeout(onClose, 650);
  };

  return (
    <Modal onClose={onClose} size="form" label={t('neurocomment.modal.campaignPrompt.title')}>
      <div className="p-6">
        <div className="mb-2 flex items-center justify-between">
          <span className="type-h2">{t('neurocomment.modal.campaignPrompt.title')}</span>
          <CloseButton aria-label={t('neurocomment.modal.close')} onClick={onClose} />
        </div>
        <div className="mb-4 type-body text-content-subtle">
          {t('neurocomment.modal.campaignPrompt.sub', { name: campaignName })}
        </div>
        <Textarea
          value={prompt}
          onChange={(event) => {
            setPrompt(event.target.value);
          }}
          placeholder={t('neurocomment.modal.campaignPrompt.placeholder')}
          // Its own name, not the dialog's: two elements sharing one accessible
          // name is what made getByLabelText ambiguous, and "Campaign prompt"
          // announced twice tells a screen-reader user nothing about the field.
          aria-label={t('neurocomment.modal.campaignPrompt.promptLabel')}
          className="px-4 py-3 font-[inherit]"
        />

        <div className="my-6 mb-3 flex items-center justify-between">
          <span className="type-body-medium text-content-secondary">
            {t('neurocomment.modal.campaignPrompt.accounts')}
          </span>
          <span className="rounded-full bg-info-tint px-2 text-small font-medium text-info-strong">
            {accounts.length}
          </span>
        </div>
        {accounts.length > 0 ? (
          <div className="tb-scroll flex max-h-feed flex-col gap-2 overflow-y-auto rounded-md border border-canvas bg-surface p-2">
            {accounts.map((account) => (
              <div
                key={account.account_id}
                className="flex items-center gap-3 rounded-sm border border-canvas bg-surface-card px-3 py-2"
              >
                <span className="flex size-icon shrink-0 items-center justify-center rounded-full bg-info-tint text-small font-medium text-info-strong">
                  {account.initials}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate type-h3">{account.phone}</div>
                  <div className="type-small">{account.channel}</div>
                </div>
                <span className="size-dot shrink-0 rounded-full bg-success" />
                <IconButton
                  size="md"
                  tone="danger"
                  aria-label={t('neurocomment.modal.campaignPrompt.removeAccount')}
                  onClick={() => {
                    setConfirm(account);
                  }}
                >
                  <Icon name="trash" size={16} />
                </IconButton>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-line-strong bg-surface p-4 text-center type-body text-content-subtle">
            {t('neurocomment.modal.campaignPrompt.empty')}
          </div>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <Button
            variant="primary"
            onClick={save}
            className={saved ? 'border-success-deep bg-success-deep hover:bg-success-deep' : ''}
          >
            {saved ? (
              <span className="inline-flex items-center gap-2">
                <span className="inline-flex tb-swapin">
                  <Icon name="check" size={16} />
                </span>
                <span className="inline-block tb-swapin-late">{t('neurocomment.modal.saved')}</span>
              </span>
            ) : (
              t('neurocomment.modal.save')
            )}
          </Button>
          <Button onClick={onClose}>{t('neurocomment.modal.cancel')}</Button>
        </div>
      </div>

      {confirm ? (
        <Modal
          onClose={() => {
            setConfirm(null);
          }}
          size="confirm"
          label={t('neurocomment.modal.campaignPrompt.removeTitle')}
        >
          <div className="p-6">
            <div className="mb-2 type-h2">{t('neurocomment.modal.campaignPrompt.removeTitle')}</div>
            <div className="mb-6 type-body text-content-muted">
              {t('neurocomment.modal.campaignPrompt.removeBody', {
                phone: confirm.phone,
                channel: confirm.channel,
              })}
            </div>
            <div className="flex justify-end gap-2">
              <Button
                onClick={() => {
                  setConfirm(null);
                }}
              >
                {t('neurocomment.modal.cancel')}
              </Button>
              <Button
                variant="danger"
                onClick={() => {
                  onRemoveAccount(confirm.account_id);
                  setConfirm(null);
                }}
              >
                {t('neurocomment.modal.campaignPrompt.removeConfirm')}
              </Button>
            </div>
          </div>
        </Modal>
      ) : null}
    </Modal>
  );
}
