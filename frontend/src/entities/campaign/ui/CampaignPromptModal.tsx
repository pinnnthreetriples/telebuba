import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, IconButton, Modal } from '@/shared/ui';

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
      className="w-[480px]"
      label={t('neurocomment.modal.campaignPrompt.title')}
    >
      <div className="p-6">
        <div className="mb-[6px] flex items-center justify-between">
          <span className="text-title font-bold">
            {t('neurocomment.modal.campaignPrompt.title')}
          </span>
          <IconButton
            size="md"
            aria-label={t('neurocomment.modal.close')}
            onClick={onClose}
            className="text-title"
          >
            ×
          </IconButton>
        </div>
        <div className="mb-[14px] text-body text-ink-subtle">
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
          className="w-full resize-none rounded-lg border border-line-input bg-white px-[13px] py-[11px] font-[inherit] text-lead leading-[1.5] outline-none"
        />

        <div className="my-[18px] mb-[9px] flex items-center justify-between">
          <span className="text-body font-semibold tracking-[.04em] text-ink-body">
            {t('neurocomment.modal.campaignPrompt.accounts')}
          </span>
          <span className="rounded-full bg-primary-tint px-2 py-[2px] text-tiny font-semibold text-primary-deep">
            {accounts.length}
          </span>
        </div>
        {accounts.length > 0 ? (
          <div className="tb-scroll flex max-h-[184px] flex-col gap-sm overflow-y-auto rounded-lg border border-track bg-surface p-[6px]">
            {accounts.map((account) => (
              <div
                key={account.account_id}
                className="flex items-center gap-md rounded-md border border-track bg-white px-[10px] py-2"
              >
                <span className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full bg-primary-tint text-tiny font-bold text-primary-deep">
                  {account.initials}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-lead font-semibold text-ink">{account.phone}</div>
                  <div className="mt-px text-tiny text-ink-muted">{account.channel}</div>
                </div>
                <span className="h-[7px] w-[7px] shrink-0 rounded-full bg-success" />
                <IconButton
                  size="md"
                  tone="danger"
                  aria-label={t('neurocomment.modal.campaignPrompt.removeAccount')}
                  onClick={() => {
                    setConfirm(account);
                  }}
                >
                  <svg
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M3 6h18" />
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                  </svg>
                </IconButton>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-line-strong bg-surface p-[14px] text-center text-body text-ink-subtle">
            {t('neurocomment.modal.campaignPrompt.empty')}
          </div>
        )}

        <div className="mt-[18px] flex justify-end gap-sm">
          <Button
            variant="primary"
            onClick={save}
            className={saved ? 'border-success bg-success hover:bg-success' : ''}
          >
            {saved ? (
              <span className="inline-flex items-center gap-sm">
                <span className="inline-flex [animation:swapin_0.3s_ease_both]">
                  <svg
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.4"
                  >
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
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
          className="w-[380px]"
          label={t('neurocomment.modal.campaignPrompt.removeTitle')}
        >
          <div className="p-6">
            <div className="mb-2 text-title font-bold">
              {t('neurocomment.modal.campaignPrompt.removeTitle')}
            </div>
            <div className="mb-5 text-lead leading-[1.5] text-ink-muted">
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
