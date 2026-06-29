import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Modal } from '@/shared/ui';

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
    <Modal onClose={onClose} className="w-[480px]">
      <div className="p-6">
        <div className="mb-[6px] flex items-center justify-between">
          <span className="text-[16px] font-bold">
            {t('neurocomment.modal.campaignPrompt.title')}
          </span>
          <button
            type="button"
            aria-label={t('neurocomment.modal.close')}
            onClick={onClose}
            className="flex h-[30px] w-[30px] items-center justify-center rounded-full border border-line bg-white text-[16px] text-ink-muted"
          >
            ×
          </button>
        </div>
        <div className="mb-[14px] text-[12.5px] text-ink-subtle">
          {t('neurocomment.modal.campaignPrompt.sub', { name: campaignName })}
        </div>
        <textarea
          value={prompt}
          onChange={(event) => {
            setPrompt(event.target.value);
          }}
          rows={5}
          placeholder={t('neurocomment.modal.campaignPrompt.placeholder')}
          aria-label={t('neurocomment.modal.campaignPrompt.title')}
          className="w-full resize-none rounded-[12px] border border-line-input bg-white px-[13px] py-[11px] font-[inherit] text-[13px] leading-[1.5] outline-none"
        />

        <div className="my-[18px] mb-[9px] flex items-center justify-between">
          <span className="text-[12px] font-semibold tracking-[.04em] text-[#3a3a3a]">
            {t('neurocomment.modal.campaignPrompt.accounts')}
          </span>
          <span className="rounded-full bg-primary-tint px-2 py-[2px] text-[11px] font-semibold text-primary">
            {accounts.length}
          </span>
        </div>
        {accounts.length > 0 ? (
          <div className="tb-scroll flex max-h-[184px] flex-col gap-[6px] overflow-y-auto rounded-[12px] border border-track bg-[#faf9f7] p-[6px]">
            {accounts.map((account) => (
              <div
                key={account.account_id}
                className="flex items-center gap-[10px] rounded-[9px] border border-track bg-white px-[10px] py-2"
              >
                <span className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full bg-primary-tint text-[11px] font-bold text-primary">
                  {account.initials}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-semibold text-ink">{account.phone}</div>
                  <div className="mt-px text-[11.5px] text-ink-muted">{account.channel}</div>
                </div>
                <span className="h-[7px] w-[7px] shrink-0 rounded-full bg-success" />
                <button
                  type="button"
                  aria-label={t('neurocomment.modal.campaignPrompt.removeAccount')}
                  onClick={() => {
                    setConfirm(account);
                  }}
                  className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-lg border border-track bg-white text-ink-subtle hover:border-[#f0c9c5] hover:bg-danger-tint hover:text-danger"
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
                </button>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-[12px] border border-dashed border-[#e0dfdb] bg-[#faf9f7] p-[14px] text-center text-[12.5px] text-ink-subtle">
            {t('neurocomment.modal.campaignPrompt.empty')}
          </div>
        )}

        <div className="mt-[18px] flex justify-end gap-2">
          <button
            type="button"
            onClick={save}
            className={`rounded-full border px-5 py-[9px] text-[13px] font-semibold text-white ${saved ? 'border-success bg-success' : 'border-primary bg-primary'}`}
          >
            {saved ? (
              <span className="inline-flex items-center gap-[6px]">
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
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
          >
            {t('neurocomment.modal.cancel')}
          </button>
        </div>
      </div>

      {confirm ? (
        <Modal
          onClose={() => {
            setConfirm(null);
          }}
          z={80}
          className="w-[380px]"
        >
          <div className="p-6">
            <div className="mb-2 text-[16px] font-bold">
              {t('neurocomment.modal.campaignPrompt.removeTitle')}
            </div>
            <div className="mb-5 text-[13px] leading-[1.5] text-ink-muted">
              {t('neurocomment.modal.campaignPrompt.removeBody', {
                phone: confirm.phone,
                channel: confirm.channel,
              })}
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setConfirm(null);
                }}
                className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
              >
                {t('neurocomment.modal.cancel')}
              </button>
              <button
                type="button"
                onClick={() => {
                  onRemoveAccount(confirm.account_id);
                  setConfirm(null);
                }}
                className="rounded-full border border-[#f0c9c5] bg-danger-tint px-5 py-[9px] text-[13px] font-semibold text-danger"
              >
                {t('neurocomment.modal.campaignPrompt.removeConfirm')}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </Modal>
  );
}
