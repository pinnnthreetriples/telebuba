import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Icon, IconButton, Modal } from '@/shared/ui';

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
    <Modal
      onClose={onClose}
      className="w-form"
      label={t('neurocomment.modal.campaignPrompt.title')}
    >
      <div className="p-2xl">
        <div className="mb-tight flex items-center justify-between">
          <span className="type-dialog-title">{t('neurocomment.modal.campaignPrompt.title')}</span>
          <IconButton
            size="md"
            aria-label={t('neurocomment.modal.close')}
            onClick={onClose}
            className="text-title"
          >
            ×
          </IconButton>
        </div>
        <div className="mb-lg type-prose">
          {t('neurocomment.modal.campaignPrompt.sub', { name: campaignName })}
        </div>
        <textarea
          value={prompt}
          onChange={(event) => {
            setPrompt(event.target.value);
          }}
          rows={5}
          placeholder={t('neurocomment.modal.campaignPrompt.placeholder')}
          // Its own name, not the dialog's: two elements sharing one accessible
          // name is what made getByLabelText ambiguous, and "Campaign prompt"
          // announced twice tells a screen-reader user nothing about the field.
          aria-label={t('neurocomment.modal.campaignPrompt.promptLabel')}
          className="w-full resize-none rounded-lg border border-line bg-white px-lg py-md font-[inherit] text-lead outline-none"
        />

        <div className="my-xl mb-md flex items-center justify-between">
          <span className="type-item-title text-ink-body">
            {t('neurocomment.modal.campaignPrompt.accounts')}
          </span>
          <span className="rounded-full bg-primary-tint px-sm py-hair text-tiny font-semibold text-primary-deep">
            {accounts.length}
          </span>
        </div>
        {accounts.length > 0 ? (
          <div className="tb-scroll flex max-h-feed flex-col gap-sm overflow-y-auto rounded-lg border border-canvas bg-surface p-tight">
            {accounts.map((account) => (
              <div
                key={account.account_id}
                className="flex items-center gap-md rounded-md border border-canvas bg-white px-md py-sm"
              >
                <span className="flex size-icon shrink-0 items-center justify-center rounded-full bg-primary-tint text-tiny font-bold text-primary-deep">
                  {account.initials}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate type-card-title">{account.phone}</div>
                  <div className="mt-px type-caption">{account.channel}</div>
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
          <div className="rounded-lg border border-dashed border-line-strong bg-surface p-lg text-center text-body text-ink-subtle">
            {t('neurocomment.modal.campaignPrompt.empty')}
          </div>
        )}

        <div className="mt-xl flex justify-end gap-sm">
          <Button
            variant="primary"
            onClick={save}
            className={saved ? 'border-success-deep bg-success-deep hover:bg-success-deep' : ''}
          >
            {saved ? (
              <span className="inline-flex items-center gap-sm">
                <span className="inline-flex [animation:swapin_0.3s_ease_both]">
                  <Icon name="check" size={16} />
                </span>
                <span className="inline-block [animation:swapin_0.3s_ease_0.09s_both]">
                  {t('neurocomment.modal.saved')}
                </span>
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
          className="w-confirm"
          label={t('neurocomment.modal.campaignPrompt.removeTitle')}
        >
          <div className="p-2xl">
            <div className="mb-sm type-dialog-title">
              {t('neurocomment.modal.campaignPrompt.removeTitle')}
            </div>
            <div className="mb-xl type-dialog-body">
              {t('neurocomment.modal.campaignPrompt.removeBody', {
                phone: confirm.phone,
                channel: confirm.channel,
              })}
            </div>
            <div className="flex justify-end gap-sm">
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
